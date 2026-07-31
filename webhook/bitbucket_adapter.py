#!/usr/bin/env python3
"""Bitbucket → Cloudflare Tunnel → thin adapter → Hermes webhook.

Recommended topology (same host):
  Bitbucket → cloudflared → 127.0.0.1:8787/bitbucket → Hermes :8644/webhooks/...

Handles:
  pullrequest:fulfilled  (PR merged)
  pullrequest:rejected   (PR declined)

Adapter responsibilities ONLY:
  1. Verify X-Hub-Signature (Bitbucket HMAC-SHA256)
  2. Parse KEY from fix/<KEY> branch or PR title
  3. Forward normalized payload to Hermes webhook (signature rewritten)
     — idempotent + limited retry

Hermes responsibilities (webhook route + jira-fix skill):
  - Run pr_lifecycle.py to post Jira comment
  - Deliver reply to QQ (deliver: qqbot)

Do NOT put Hermes webhook directly on the Tunnel — Bitbucket signature
format is rewritten here for Hermes (GitHub-style X-Hub-Signature-256).

Usage:
  python webhook/bitbucket_adapter.py --host 127.0.0.1 --port 8787
  cloudflared tunnel run <named-tunnel>

Env (see config/env.template / webhook/README.md):
  BITBUCKET_WEBHOOK_SECRET   — required
  HERMES_WEBHOOK_URL         — required (e.g. http://127.0.0.1:8644/webhooks/bitbucket-pr-merged)
  HERMES_WEBHOOK_SECRET      — required
  ADAPTER_STATE_DIR / ADAPTER_HERMES_RETRIES / ADAPTER_RETRY_BACKOFF_SEC
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

ISSUE_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
FIX_BRANCH_RE = re.compile(r"(?i)^fix/([A-Z][A-Z0-9]+-\d+)$")
_STATE_LOCK = threading.Lock()

# Bitbucket X-Event-Key → Hermes X-GitHub-Event + lifecycle verb
EVENT_MAP = {
    "pullrequest:fulfilled": {
        "event_type": "bitbucket_pr_fulfilled",
        "lifecycle": "fulfilled",
        "delivery_prefix": "bb-fulfilled",
    },
    "pullrequest:rejected": {
        "event_type": "bitbucket_pr_rejected",
        "lifecycle": "rejected",
        "delivery_prefix": "bb-rejected",
    },
}


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def maybe_load_dotenv() -> None:
    """Load Hermes .env for webhook secrets (no Jira required on adapter host)."""
    candidates = []
    if os.environ.get("HERMES_HOME"):
        candidates.append(Path(os.environ["HERMES_HOME"]) / ".env")
    candidates.extend(
        [
            Path.home() / ".hermes" / ".env",
            Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / ".env",
        ]
    )
    for p in candidates:
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
        return


def verify_bitbucket_signature(secret: str, body: bytes, header: str) -> bool:
    if not secret or not header or "=" not in header:
        return False
    method, _, sig = header.partition("=")
    if method.lower() != "sha256" or not sig:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def sign_hermes_github_style(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def extract_issue_key(pr: dict) -> Optional[str]:
    branch = ((pr.get("source") or {}).get("branch") or {}).get("name") or ""
    m = FIX_BRANCH_RE.match(branch.strip())
    if m:
        return m.group(1).upper()
    for text in (pr.get("title") or "", pr.get("description") or ""):
        found = ISSUE_KEY_RE.search(text)
        if found:
            return found.group(1).upper()
    return None


def parse_pr_event(payload: dict, event_key: str) -> Optional[dict]:
    meta = EVENT_MAP.get(event_key)
    if not meta:
        return None
    pr = payload.get("pullrequest") or {}
    key = extract_issue_key(pr)
    if not key:
        return None
    repo = payload.get("repository") or {}
    actor = payload.get("actor") or {}
    links = (pr.get("links") or {}).get("html") or {}
    closed_by = ((pr.get("closed_by") or {}).get("display_name") or "")
    actor_name = (
        actor.get("display_name") or actor.get("nickname") or actor.get("username") or ""
    )
    reason = (pr.get("reason") or "").strip()
    info = {
        "event": event_key,
        "event_type": meta["event_type"],
        "lifecycle": meta["lifecycle"],
        "key": key,
        "pr_id": pr.get("id"),
        "pr_title": pr.get("title") or "",
        "pr_url": links.get("href") or "",
        "branch": ((pr.get("source") or {}).get("branch") or {}).get("name") or "",
        "base": ((pr.get("destination") or {}).get("branch") or {}).get("name") or "",
        "repository": repo.get("full_name") or "",
        "actor": actor_name,
        "merged_by": closed_by if meta["lifecycle"] == "fulfilled" else "",
        "declined_by": (closed_by or actor_name) if meta["lifecycle"] == "rejected" else "",
        "reason": reason,
    }
    return info


def build_qq_hint(info: dict) -> str:
    """Suggested user-facing text; Hermes may paraphrase after posting Jira comment."""
    pr_ref = info.get("pr_url") or "#" + str(info.get("pr_id"))
    branch_line = f"{info.get('branch')} → {info.get('base')} · {info.get('repository')}"
    if info.get("lifecycle") == "rejected":
        lines = [
            f"❌ {info['key']} PR 已被拒绝（Declined）",
            f"PR: {pr_ref}",
            branch_line,
        ]
        if info.get("declined_by"):
            lines.append(f"操作者: {info['declined_by']}")
        if info.get("reason"):
            lines.append(f"原因: {info['reason']}")
        return "\n".join(lines)
    return (
        f"✅ {info['key']} 已合入\n"
        f"PR: {pr_ref}\n"
        f"{branch_line}"
    )


def build_instruction(info: dict) -> str:
    life = info.get("lifecycle")
    if life == "rejected":
        return (
            "Bitbucket PR declined (rejected). "
            "1) Post Jira comment via jira-fix script: "
            "pr_lifecycle.py rejected {key} --pr-url ... --reason \"{reason}\" "
            "(include reason in the comment when non-empty). "
            "2) Reply to the user with message_qq (Chinese; include reason if present)."
        ).format(key=info.get("key"), reason=info.get("reason") or "")
    return (
        "Bitbucket PR merged. "
        "1) Post Jira comment via jira-fix script pr_lifecycle.py fulfilled. "
        "2) Reply to the user with message_qq (or equivalent Chinese summary)."
    )


def state_dir() -> Path:
    raw = os.environ.get("ADAPTER_STATE_DIR", "").strip()
    if raw:
        d = Path(raw)
    else:
        home = os.environ.get("HERMES_HOME") or str(
            Path(os.environ.get("LOCALAPPDATA", "")) / "hermes"
        )
        d = Path(home) / "jira-webhook-state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def idempotency_key(info: dict, delivery_id: str = "") -> str:
    if delivery_id:
        return f"delivery:{delivery_id}"
    life = info.get("lifecycle") or "unknown"
    repo = (info.get("repository") or "unknown").replace("/", "_")
    return f"{life}:{repo}:{info.get('pr_id')}:{info.get('key')}"


def _state_path(key: str) -> Path:
    # Windows forbids ':' in filenames (delivery:uuid → broken "delivery" ADS).
    safe = re.sub(r"[^\w.\-+]", "_", key)
    return state_dir() / f"{safe}.json"


def load_state(key: str) -> dict:
    path = _state_path(key)
    with _STATE_LOCK:
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}


def save_state(key: str, state: dict) -> None:
    path = _state_path(key)
    state = dict(state)
    state["updated_at"] = time.time()
    with _STATE_LOCK:
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, str(default))))
    except ValueError:
        return default


def with_retries(
    label: str,
    attempts: int,
    backoff_sec: float,
    fn: Callable[[], dict],
) -> dict:
    last: dict = {"ok": False, "error": "not attempted"}
    for i in range(attempts):
        last = fn()
        if last.get("ok"):
            last["attempts"] = i + 1
            return last
        if i + 1 < attempts and backoff_sec > 0:
            time.sleep(backoff_sec * (2**i))
    last["attempts"] = attempts
    last["error"] = f"{label} failed after {attempts} attempt(s): {last.get('error') or last}"
    return last


def forward_to_hermes(info: dict) -> dict:
    url = os.environ.get("HERMES_WEBHOOK_URL", "").strip()
    secret = os.environ.get("HERMES_WEBHOOK_SECRET", "").strip()
    if not url:
        return {"ok": False, "error": "HERMES_WEBHOOK_URL not set"}
    if not secret:
        return {"ok": False, "error": "HERMES_WEBHOOK_SECRET not set"}

    meta = EVENT_MAP.get(info.get("event") or "", {})
    event_type = info.get("event_type") or meta.get("event_type") or "bitbucket_pr_fulfilled"
    prefix = meta.get("delivery_prefix") or "bb-event"

    body_obj = {
        **info,
        "message_qq": build_qq_hint(info),
        "instruction": build_instruction(info),
    }
    raw = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": sign_hermes_github_style(secret, raw),
        "X-GitHub-Event": event_type,
        "X-GitHub-Delivery": f"{prefix}-{info.get('pr_id')}-{info.get('key')}",
        "User-Agent": "hermes-jira-bot-bitbucket-adapter/1.1",
    }
    req = urllib.request.Request(url, data=raw, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp_body = r.read().decode("utf-8", errors="replace")[:800]
            return {"ok": True, "status": r.status, "body": resp_body}
    except urllib.error.HTTPError as e:
        return {
            "ok": False,
            "status": e.code,
            "error": e.read().decode("utf-8", errors="replace")[:800],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_pr_event(payload: dict, event_key: str, delivery_id: str = "") -> dict:
    info = parse_pr_event(payload, event_key)
    if not info:
        return {"ok": False, "error": "could not extract issue KEY from PR branch/title"}

    ikey = idempotency_key(info, delivery_id)
    prev = load_state(ikey)
    result: dict[str, Any] = {
        "ok": True,
        "info": info,
        "idempotency_key": ikey,
        "hermes": None,
    }

    if prev.get("hermes_ok"):
        result["idempotent"] = True
        result["hermes"] = {"ok": True, "skipped": "already forwarded to Hermes"}
        return result

    retries = _env_int("ADAPTER_HERMES_RETRIES", 3)
    backoff = _env_float("ADAPTER_RETRY_BACKOFF_SEC", 1.0)
    result["hermes"] = with_retries("hermes", retries, backoff, lambda: forward_to_hermes(info))

    if result["hermes"].get("ok"):
        prev["hermes_ok"] = True
        prev["key"] = info.get("key")
        prev["pr_id"] = info.get("pr_id")
        prev["repository"] = info.get("repository")
        prev["lifecycle"] = info.get("lifecycle")
        save_state(ikey, prev)
    else:
        result["ok"] = False
        save_state(
            ikey,
            {
                **prev,
                "hermes_ok": False,
                "last_error": result["hermes"].get("error"),
                "key": info.get("key"),
                "pr_id": info.get("pr_id"),
                "lifecycle": info.get("lifecycle"),
            },
        )
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "BitbucketAdapter/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, obj: dict) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/health", "/healthz"):
            self._send(
                200,
                {
                    "ok": True,
                    "service": "bitbucket-adapter",
                    "handles": list(EVENT_MAP.keys()),
                    "jira_writer": "hermes (not adapter)",
                },
            )
            return
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in ("/bitbucket", "/webhook/bitbucket", "/"):
            self._send(404, {"ok": False, "error": "use POST /bitbucket"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1_000_000:
            self._send(400, {"ok": False, "error": "invalid body size"})
            return
        body = self.rfile.read(length)

        secret = os.environ.get("BITBUCKET_WEBHOOK_SECRET", "").strip()
        if not secret:
            self._send(500, {"ok": False, "error": "BITBUCKET_WEBHOOK_SECRET not configured"})
            return

        sig = self.headers.get("X-Hub-Signature", "")
        if not verify_bitbucket_signature(secret, body, sig):
            self._send(401, {"ok": False, "error": "invalid signature"})
            return

        event_key = self.headers.get("X-Event-Key", "")
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self._send(400, {"ok": False, "error": "invalid json"})
            return

        if event_key not in EVENT_MAP:
            self._send(
                200,
                {
                    "ok": True,
                    "ignored": True,
                    "event": event_key,
                    "hint": "handles: " + ", ".join(EVENT_MAP.keys()),
                },
            )
            return

        delivery_id = (
            self.headers.get("X-Request-Uuid")
            or self.headers.get("X-Request-UUID")
            or self.headers.get("X-Hook-UUID")
            or ""
        ).strip()

        try:
            result = handle_pr_event(payload, event_key, delivery_id=delivery_id)
        except Exception as e:
            self._send(500, {"ok": False, "error": str(e)})
            return

        self._send(200 if result.get("ok") else 502, result)


def main() -> int:
    _configure_stdio()
    maybe_load_dotenv()

    ap = argparse.ArgumentParser(description="Bitbucket webhook thin adapter (forward to Hermes)")
    ap.add_argument("--host", default=os.environ.get("ADAPTER_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("ADAPTER_PORT", "8787")))
    args = ap.parse_args()

    missing = [k for k in ("BITBUCKET_WEBHOOK_SECRET", "HERMES_WEBHOOK_URL", "HERMES_WEBHOOK_SECRET") if not os.environ.get(k)]
    if missing:
        print(f"ERROR: set {', '.join(missing)}", file=sys.stderr)
        return 1

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Bitbucket adapter listening on http://{args.host}:{args.port}/bitbucket")
    print("Handles: pullrequest:fulfilled|rejected → Hermes (Hermes writes Jira + QQ)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
