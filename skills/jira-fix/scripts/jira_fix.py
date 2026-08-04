#!/usr/bin/env python3
"""Single-ticket /fix orchestrator (PoC).

Flow:
  Jira → repos.json → worktree fix/<KEY> → agent → validate → push → PR → Jira comment

Env:
  JIRA_USER_EMAIL, JIRA_API_TOKEN, JIRA_CLOUD_ID
  BITBUCKET_USERNAME, BITBUCKET_APP_PASSWORD  (provider=bitbucket)
  Optional: HERMES_HOME for loading .env
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from repos_resolve import (  # noqa: E402
    ResolveResult,
    load_repos,
    project_key_from_issue,
    resolve,
)
from fix_session import (  # noqa: E402
    DEFAULT_SESSION,
    resolve_numbers,
)

ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$", re.I)
DECLINED_MARKERS = (
    "PR 已被拒绝",
    "Declined · Bitbucket → Hermes",
)
DECLINED_REASON_RE = re.compile(r"(?m)^原因:\s*(.+)$")
DECLINED_PR_URL_RE = re.compile(r"(?m)^PR:\s*(\S+)")

DEFAULT_TIMEOUT = 30 * 60
FIX_BRANCH_PREFIX = "fix/"
ATTACHMENT_DIR = ".jira-fix-attachments"
MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024  # keep screenshots small for agent token cost
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


class InfraFailure(Exception):
    """CLI missing / crash / timeout / empty failure — may fallback agent."""


class BusinessFailure(Exception):
    """Agent ran but no compliant commit / lint·test failed — no fallback."""


# ── env / jira ──────────────────────────────────────────────────────────────


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def maybe_load_hermes_env() -> None:
    home = os.environ.get("HERMES_HOME")
    if home:
        load_dotenv(Path(home) / ".env")
        return
    # Common locations (Windows / WSL-friendly)
    for p in (
        Path.home() / ".hermes" / ".env",
        Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / ".env",
    ):
        if p.is_file():
            load_dotenv(p)
            return


def jira_auth_header() -> str:
    email = os.environ.get("JIRA_USER_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")
    cloud = os.environ.get("JIRA_CLOUD_ID", "")
    if not email or not token or not cloud:
        raise RuntimeError("JIRA_USER_EMAIL / JIRA_API_TOKEN / JIRA_CLOUD_ID required")
    creds = base64.b64encode(f"{email}:{token}".encode()).decode()
    return f"Basic {creds}"


def _adf_text(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        parts = []
        if node.get("text"):
            parts.append(node["text"])
        for c in node.get("content") or []:
            parts.append(_adf_text(c))
        if node.get("type") in ("paragraph", "heading", "bulletList", "orderedList", "listItem"):
            parts.append("\n")
        return "".join(parts)
    if isinstance(node, list):
        return "".join(_adf_text(x) for x in node)
    return ""


def fetch_issue(key: str) -> dict:
    cloud = os.environ["JIRA_CLOUD_ID"]
    fields = "summary,description,fixVersions,priority,status,issuetype,project,attachment"
    url = (
        f"https://api.atlassian.com/ex/jira/{cloud}/rest/api/3/issue/{key}"
        f"?fields={fields}"
    )
    req = urllib.request.Request(
        url, headers={"Authorization": jira_auth_header(), "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Jira HTTP {e.code}: {e.read().decode()[:400]}") from e

    f = data["fields"]
    desc = _adf_text(f.get("description")).strip()
    versions = f.get("fixVersions") or []
    version_name = versions[0]["name"] if versions else None
    attachments = []
    for a in f.get("attachment") or []:
        attachments.append(
            {
                "id": str(a.get("id") or ""),
                "filename": a.get("filename") or "attachment",
                "mime": a.get("mimeType") or "",
                "size": int(a.get("size") or 0),
                "content": a.get("content") or "",
            }
        )
    return {
        "key": data["key"],
        "summary": f.get("summary") or "",
        "description": desc,
        "version": version_name,
        "priority": (f.get("priority") or {}).get("name", "N/A"),
        "status": (f.get("status") or {}).get("name", "N/A"),
        "project_key": (f.get("project") or {}).get("key") or project_key_from_issue(data["key"]),
        "attachments": attachments,
    }


def fetch_comments(key: str) -> list[dict]:
    """Fetch Jira issue comments (oldest → newest)."""
    cloud = os.environ["JIRA_CLOUD_ID"]
    url = (
        f"https://api.atlassian.com/ex/jira/{cloud}/rest/api/3/issue/{key}/comment"
        f"?maxResults=100&orderBy=created"
    )
    req = urllib.request.Request(
        url, headers={"Authorization": jira_auth_header(), "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Jira comments HTTP {e.code}: {e.read().decode()[:400]}") from e
    return list(data.get("comments") or [])


def parse_latest_declined(comments: list[dict]) -> Optional[dict]:
    """Return latest Hermes bot Declined comment fields, or None."""
    latest: Optional[dict] = None
    for c in comments:
        text = _adf_text(c.get("body")).strip()
        if not text or not any(m in text for m in DECLINED_MARKERS):
            continue
        reason_m = DECLINED_REASON_RE.search(text)
        pr_m = DECLINED_PR_URL_RE.search(text)
        latest = {
            "reason": (reason_m.group(1).strip() if reason_m else ""),
            "pr_url": (pr_m.group(1).strip() if pr_m else ""),
            "created": c.get("created") or "",
            "comment_id": str(c.get("id") or ""),
        }
    return latest


def fetch_latest_declined(key: str) -> Optional[dict]:
    return parse_latest_declined(fetch_comments(key))


def _safe_filename(name: str) -> str:
    base = Path(name).name
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .")
    return cleaned or "attachment"


def _worktree_git_dir(wt: Path) -> Path:
    gitfile = wt / ".git"
    if gitfile.is_file():
        text = gitfile.read_text(encoding="utf-8").strip()
        if text.lower().startswith("gitdir:"):
            return Path(text.split(":", 1)[1].strip())
    return gitfile


def exclude_attachment_dir(wt: Path) -> None:
    """Keep downloaded Jira attachments out of commits."""
    info = _worktree_git_dir(wt) / "info"
    info.mkdir(parents=True, exist_ok=True)
    excl = info / "exclude"
    marker = f"{ATTACHMENT_DIR}/"
    existing = excl.read_text(encoding="utf-8") if excl.is_file() else ""
    if marker not in existing:
        with excl.open("a", encoding="utf-8") as f:
            f.write(f"\n# jira-fix orchestrator\n{marker}\n")


def download_attachments(wt: Path, attachments: list[dict]) -> list[dict]:
    """Download Jira attachments into worktree; prefer images. Returns local file infos."""
    if not attachments:
        return []
    dest_root = wt / ATTACHMENT_DIR
    dest_root.mkdir(parents=True, exist_ok=True)
    exclude_attachment_dir(wt)
    auth = jira_auth_header()
    saved: list[dict] = []
    # Prefer images first, then others
    ordered = sorted(
        attachments,
        key=lambda a: (
            0
            if (a.get("mime") or "").startswith("image/")
            or Path(a.get("filename") or "").suffix.lower() in IMAGE_SUFFIXES
            else 1,
            a.get("filename") or "",
        ),
    )
    for a in ordered[:MAX_ATTACHMENTS]:
        url = a.get("content") or ""
        if not url:
            continue
        size = int(a.get("size") or 0)
        if size > MAX_ATTACHMENT_BYTES:
            continue
        fname = _safe_filename(a.get("filename") or f"att-{a.get('id')}")
        dest = dest_root / fname
        # avoid overwrite collisions
        if dest.exists():
            dest = dest_root / f"{a.get('id')}_{fname}"
        req = urllib.request.Request(
            url, headers={"Authorization": auth, "Accept": "*/*"}
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read(MAX_ATTACHMENT_BYTES + 1)
        except Exception as e:
            saved.append(
                {
                    "filename": fname,
                    "error": str(e)[:200],
                    "path": None,
                }
            )
            continue
        if len(data) > MAX_ATTACHMENT_BYTES:
            continue
        dest.write_bytes(data)
        rel = f"{ATTACHMENT_DIR}/{dest.name}"
        saved.append(
            {
                "filename": a.get("filename") or fname,
                "path": rel,
                "mime": a.get("mime") or "",
                "bytes": len(data),
            }
        )
    return saved


def post_jira_comment(key: str, text: str) -> None:
    cloud = os.environ["JIRA_CLOUD_ID"]
    url = f"https://api.atlassian.com/ex/jira/{cloud}/rest/api/3/issue/{key}/comment"
    body = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": text}],
                }
            ],
        }
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": jira_auth_header(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Jira comment HTTP {e.code}: {e.read().decode()[:400]}") from e


# ── git ─────────────────────────────────────────────────────────────────────


def run(
    args: list[str],
    *,
    cwd: Optional[Path] = None,
    timeout: Optional[int] = None,
    check: bool = False,
    env: Optional[dict] = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=check,
        env=env,
    )


def git(repo: Path, *args: str, check: bool = True) -> str:
    r = run(["git", *args], cwd=repo)
    if check and r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()[:500]
        raise RuntimeError(f"git {' '.join(args)} failed: {err}")
    return (r.stdout or "").strip()


def resolve_base_ref(repo: Path, branch: str) -> str:
    for ref in (f"origin/{branch}", branch):
        r = run(["git", "rev-parse", "--verify", ref], cwd=repo)
        if r.returncode == 0:
            return ref
    raise RuntimeError(
        f"base branch {branch!r} not found in {repo} (tried origin/{branch} and local)"
    )


def prepare_worktree(repo: Path, key: str, base_branch: str, wt_root: Path) -> tuple[Path, str]:
    """Create worktree on fix/<KEY> from base. Returns (worktree_path, base_ref)."""
    if not (repo / ".git").exists() and not (repo / ".git").is_file():
        # allow worktree .git file; for main repo need .git
        if not any((repo / p).exists() for p in (".git",)):
            raise RuntimeError(f"not a git repo: {repo}")

    fix_branch = f"{FIX_BRANCH_PREFIX}{key}"
    wt_path = wt_root / f"fix-{key.replace('/', '_')}"

    run(["git", "worktree", "prune"], cwd=repo)
    # Remove stale worktree dir / branch if present
    listed = git(repo, "worktree", "list", "--porcelain", check=False)
    if str(wt_path.resolve()) in listed.replace("\\", "/"):
        run(["git", "worktree", "remove", "--force", str(wt_path)], cwd=repo)
    if wt_path.exists():
        shutil.rmtree(wt_path, ignore_errors=True)

    # Drop leftover local branch (not checked out)
    br = run(["git", "show-ref", "--verify", f"refs/heads/{fix_branch}"], cwd=repo)
    if br.returncode == 0:
        run(["git", "branch", "-D", fix_branch], cwd=repo)

    # Best-effort fetch
    run(["git", "fetch", "origin", base_branch, "--prune"], cwd=repo, timeout=120)
    base_ref = resolve_base_ref(repo, base_branch)

    r = run(
        ["git", "worktree", "add", "-b", fix_branch, str(wt_path), base_ref],
        cwd=repo,
        timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError(f"worktree add failed: {(r.stderr or r.stdout)[:500]}")
    return wt_path, base_ref


def cleanup_worktree(repo: Path, wt_path: Path, fix_branch: str, *, delete_branch: bool) -> None:
    if wt_path.exists():
        run(["git", "worktree", "remove", "--force", str(wt_path)], cwd=repo)
        shutil.rmtree(wt_path, ignore_errors=True)
    run(["git", "worktree", "prune"], cwd=repo)
    if delete_branch:
        run(["git", "branch", "-D", fix_branch], cwd=repo)


def worktree_has_changes(wt: Path) -> bool:
    """True if there are staged/unstaged/untracked changes (excl. ignored)."""
    r = run(["git", "status", "--porcelain"], cwd=wt)
    return bool((r.stdout or "").strip())


def ensure_commit_after_agent(wt: Path, key: str, base_ref: str) -> Optional[str]:
    """If Agent edited files but forgot to commit, create a commit for the ticket.

    Returns a note when an orchestrator commit was created; None otherwise.
    """
    commits = git(wt, "log", f"{base_ref}..HEAD", "--pretty=%H", check=False)
    if commits.strip():
        return None
    if not worktree_has_changes(wt):
        return None

    # Stage everything except ignored paths (attachments are in info/exclude)
    run(["git", "add", "-A"], cwd=wt)
    # Drop attachment dir if somehow staged
    run(["git", "reset", "-q", "--", ATTACHMENT_DIR], cwd=wt)
    staged = run(["git", "diff", "--cached", "--name-only"], cwd=wt)
    if not (staged.stdout or "").strip():
        return None

    msg = f"fix: {key} auto-fix by jira-fix orchestrator"
    # Skip husky/corepack/pre-commit: agent already edited; this is a fallback commit.
    # Hooks often assume yarn while repos may use pnpm (e.g. cortex10-frontend).
    r = run(["git", "commit", "--no-verify", "-m", msg], cwd=wt)
    if r.returncode != 0:
        err = ((r.stderr or "") + (r.stdout or ""))[:400]
        raise BusinessFailure(f"orchestrator auto-commit failed: {err}")
    return msg


def validate_commit(wt: Path, key: str, base_ref: str) -> dict:
    branch = git(wt, "rev-parse", "--abbrev-ref", "HEAD")
    expect = f"{FIX_BRANCH_PREFIX}{key}"
    if branch != expect:
        raise BusinessFailure(f"branch is {branch!r}, expected {expect!r}")

    commits = git(wt, "log", f"{base_ref}..HEAD", "--pretty=%H%x09%s")
    if not commits.strip():
        raise BusinessFailure("no new commits on fix branch")

    msgs = []
    for line in commits.splitlines():
        parts = line.split("\t", 1)
        msgs.append(parts[1] if len(parts) > 1 else parts[0])
    if not any(key in m for m in msgs):
        raise BusinessFailure(f"no commit message contains ticket key {key}")

    diff = git(wt, "diff", f"{base_ref}...HEAD", "--stat")
    if not diff.strip():
        raise BusinessFailure("empty diff vs base")

    tip = git(wt, "rev-parse", "HEAD")
    return {"branch": branch, "tip": tip, "messages": msgs, "diff_stat": diff}


def run_gate_cmd(wt: Path, label: str, cmd: str) -> None:
    if not cmd or not str(cmd).strip():
        return
    # shell=True so repos.json can use simple strings
    r = subprocess.run(
        cmd,
        cwd=str(wt),
        shell=True,
        capture_output=True,
        text=True,
        timeout=DEFAULT_TIMEOUT,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "")[:800]
        raise BusinessFailure(f"{label} failed (exit {r.returncode}): {err}")


# ── agents ──────────────────────────────────────────────────────────────────


def build_prompt(
    issue: dict,
    local_attachments: Optional[list[dict]] = None,
    declined: Optional[dict] = None,
) -> str:
    """Build Fix Agent prompt. Summary is authoritative when description is empty."""
    key = issue["key"]
    summary = (issue.get("summary") or "").strip()
    desc = (issue.get("description") or "").strip()
    if len(desc) > 12000:
        desc = desc[:12000] + "\n…(truncated)"

    lines = [
        f"Fix Bug {key}",
        "",
        "IMPORTANT — ticket context is ALREADY provided below by the orchestrator:",
        "- Do NOT call Jira / Atlassian MCP (or any remote ticket API).",
        "- Do NOT ask the user to paste Summary/Description/steps.",
        "- Do NOT refuse for lack of MCP or ticket access — use the fields below only.",
        "- If Description is empty, fix from Summary (+ attachments if listed).",
        "",
        f"Summary (primary source of truth): {summary}",
        "",
    ]
    if desc:
        lines += ["Description:", desc, ""]
    else:
        lines += [
            "Description: (empty in Jira — rely on the Summary and any screenshot attachments below.)",
            "",
        ]

    if declined:
        lines += [
            "Previous fix was declined on Bitbucket (from Jira bot comment):",
            f"- PR: {declined.get('pr_url') or '(unknown)'}",
            f"- Reason: {declined.get('reason') or '(no reason provided)'}",
            "Address this feedback in the new minimal fix. Do not reopen process debates.",
            "",
        ]

    att_ok = [a for a in (local_attachments or []) if a.get("path")]
    if att_ok:
        lines.append("Attachments downloaded into this worktree (open/view these files):")
        for a in att_ok:
            mime = a.get("mime") or ""
            lines.append(f"- {a['path']}" + (f" ({mime})" if mime else ""))
        lines.append(
            f"Do NOT git-add or commit anything under {ATTACHMENT_DIR}/; they are reference only."
        )
        lines.append("")
    elif issue.get("attachments"):
        names = ", ".join(a.get("filename") or "?" for a in issue["attachments"][:MAX_ATTACHMENTS])
        lines += [
            f"Jira has attachments but download failed or skipped: {names}",
            "",
        ]

    lines += [
        "Requirements:",
        f"- You are already in a git worktree on branch {FIX_BRANCH_PREFIX}{key}.",
        "- Make a minimal code fix for the bug described by the Summary (and attachments if any).",
        f"- After editing, you MUST run git add and git commit; message MUST include {key}.",
        "- Leaving only uncommitted edits is a FAILURE. Commit before you finish.",
        "- Asking for ticket details or stopping without a commit is a FAILURE.",
        "- Do NOT push, do NOT open a pull request, do NOT rename the branch.",
        "- Do not modify unrelated files.",
        "",
    ]
    return "\n".join(lines)


def extract_agent_result_text(proc: Optional[subprocess.CompletedProcess]) -> str:
    """Best-effort extract human-readable agent reply from CLI stdout/stderr."""
    if proc is None:
        return ""
    raw = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if not raw:
        return ""
    # Claude / Cursor often emit a final JSON object with "result"
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            for key in ("result", "message", "text", "content"):
                val = obj.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
    return raw[:2000]


_ASKING_FOR_TICKET_RE = re.compile(
    r"(paste the .{0,60}ticket|share the .{0,60}ticket|"
    r"no way to pull the ticket|ticket details\??|"
    r"Jira/Atlassian MCP|Atlassian MCP|"
    r"Could you (share|paste).{0,100}(Summary|ticket|Description))",
    re.I,
)


def agent_asked_for_ticket_details(text: str) -> bool:
    return bool(text and _ASKING_FOR_TICKET_RE.search(text))


def save_agent_log(
    key: str,
    proc: subprocess.CompletedProcess,
    agent: str,
    prompt: str = "",
) -> str:
    log_dir = Path(tempfile.gettempdir()) / "jira-fix-agent-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{key}-{agent}-{int(time.time())}.log"
    parts = [
        f"agent={agent}",
        f"returncode={proc.returncode}",
        "",
        "--- prompt ---",
        prompt or "(empty)",
        "",
        "--- stdout ---",
        proc.stdout or "",
        "",
        "--- stderr ---",
        proc.stderr or "",
        "",
    ]
    path.write_text("\n".join(parts), encoding="utf-8", errors="replace")
    return str(path)


def which_agent(name: str) -> Optional[str]:
    if name == "claude":
        return shutil.which("claude")
    if name == "cursor":
        return shutil.which("agent") or shutil.which("agent.cmd") or shutil.which("cursor-agent")
    return shutil.which(name)


def run_claude(wt: Path, prompt: str, timeout: int) -> subprocess.CompletedProcess:
    exe = which_agent("claude")
    if not exe:
        raise InfraFailure("claude CLI not found on PATH")
    cmd = [
        exe,
        "-p",
        "--no-session-persistence",
        "--add-dir",
        str(wt),
        "--permission-mode",
        "bypassPermissions",
        "--output-format",
        "json",
        prompt,
    ]
    try:
        return run(cmd, cwd=wt, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise InfraFailure(f"claude timed out after {timeout}s") from e


def run_cursor(wt: Path, prompt: str, timeout: int) -> subprocess.CompletedProcess:
    exe = which_agent("cursor")
    if not exe:
        raise InfraFailure("cursor agent CLI not found on PATH")
    cmd = [exe, "-p", "-f", "--output-format", "json", prompt]
    try:
        return run(cmd, cwd=wt, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise InfraFailure(f"cursor agent timed out after {timeout}s") from e


def run_agent(agent: str, wt: Path, prompt: str, timeout: int) -> tuple[str, subprocess.CompletedProcess]:
    if agent == "cursor":
        return "cursor", run_cursor(wt, prompt, timeout)
    return "claude", run_claude(wt, prompt, timeout)


def classify_agent_result(proc: subprocess.CompletedProcess) -> None:
    """Raise InfraFailure if looks like infra; else allow validation to decide business."""
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0 and not out:
        raise InfraFailure(f"agent exited {proc.returncode} with no output")
    # Non-zero with output: treat as completed run → business path (validate may fail)


# ── VCS PR ──────────────────────────────────────────────────────────────────


def _bitbucket_basic_auth() -> str:
    user = os.environ.get("BITBUCKET_USERNAME", "")
    pw = os.environ.get("BITBUCKET_APP_PASSWORD", "")
    if not user or not pw:
        raise RuntimeError("BITBUCKET_USERNAME / BITBUCKET_APP_PASSWORD required")
    return base64.b64encode(f"{user}:{pw}".encode()).decode()


def _bitbucket_http(
    method: str,
    url: str,
    *,
    body: Optional[dict] = None,
    timeout: int = 60,
) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Basic {_bitbucket_basic_auth()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Bitbucket HTTP {e.code}: {err_body}") from e


def _is_transient_bitbucket_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, urllib.error.URLError, ConnectionError, OSError)):
        return True
    msg = str(exc).lower()
    if any(
        s in msg
        for s in (
            "timed out",
            "timeout",
            "temporarily unavailable",
            "connection reset",
            "connection aborted",
            "connection refused",
            "network is unreachable",
            "broken pipe",
            "eof occurred",
            "remote end closed",
            "10054",  # WinError connection reset
            "10060",  # WinError timeout
        )
    ):
        return True
    # HTTP status in our RuntimeError message
    for code in ("429", "500", "502", "503", "504"):
        if f"bitbucket http {code}" in msg or f"http {code}" in msg:
            return True
    return False


def find_open_bitbucket_pr(workspace: str, repo: str, source_branch: str) -> Optional[str]:
    """Return HTML URL of an OPEN PR from source_branch, if any."""
    # Bitbucket Cloud query language
    q = f'source.branch.name="{source_branch}" AND state="OPEN"'
    url = (
        f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pullrequests"
        f"?q={quote(q)}&pagelen=5"
    )
    data = _bitbucket_http("GET", url, timeout=45)
    for pr in data.get("values") or []:
        html = ((pr.get("links") or {}).get("html") or {}).get("href") or ""
        if html:
            return html
        # fallback fields
        if pr.get("id") is not None:
            return (
                f"https://bitbucket.org/{workspace}/{repo}/pull-requests/{pr['id']}"
            )
    return None


def _create_bitbucket_pr_once(
    workspace: str,
    repo: str,
    title: str,
    source_branch: str,
    dest_branch: str,
    description: str,
) -> str:
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pullrequests"
    payload = {
        "title": title,
        "description": description,
        "source": {"branch": {"name": source_branch}},
        "destination": {"branch": {"name": dest_branch}},
        "close_source_branch": False,
    }
    resp = _bitbucket_http("POST", url, body=payload, timeout=60)
    return (
        resp.get("links", {}).get("html", {}).get("href")
        or resp.get("url")
        or ""
    )


def _try_find_open_bitbucket_pr(
    workspace: str, repo: str, source_branch: str
) -> Optional[str]:
    """Lookup OPEN PR; ignore transient lookup failures so create can still proceed."""
    try:
        return find_open_bitbucket_pr(workspace, repo, source_branch)
    except Exception as e:
        if _is_transient_bitbucket_error(e):
            return None
        # Auth / permission errors should surface
        msg = str(e).lower()
        if "bitbucket http 401" in msg or "bitbucket http 403" in msg:
            raise
        return None


def create_bitbucket_pr(
    workspace: str,
    repo: str,
    title: str,
    source_branch: str,
    dest_branch: str,
    description: str,
    *,
    retries: int = 3,
    backoff_sec: float = 1.5,
) -> str:
    """Find existing OPEN PR or create one, with retries on transient API errors."""
    existing = _try_find_open_bitbucket_pr(workspace, repo, source_branch)
    if existing:
        return existing

    last_err: Optional[BaseException] = None
    attempts = max(1, retries)
    for i in range(attempts):
        # Another attempt / lost response may have already created the PR
        existing = _try_find_open_bitbucket_pr(workspace, repo, source_branch)
        if existing:
            return existing
        try:
            url = _create_bitbucket_pr_once(
                workspace, repo, title, source_branch, dest_branch, description
            )
            if url:
                return url
            # empty URL — try find
            existing = _try_find_open_bitbucket_pr(workspace, repo, source_branch)
            if existing:
                return existing
            raise RuntimeError("Bitbucket PR create returned empty URL")
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            # Duplicate / already exists → resolve by lookup
            if "already" in msg or "duplicate" in msg or "bitbucket http 400" in msg:
                existing = _try_find_open_bitbucket_pr(workspace, repo, source_branch)
                if existing:
                    return existing
            if i + 1 < attempts and _is_transient_bitbucket_error(e):
                time.sleep(backoff_sec * (2**i))
                continue
            if i + 1 < attempts and "bitbucket http 400" in msg:
                # brief retry then give up (race / validation)
                time.sleep(backoff_sec)
                continue
            break

    existing = _try_find_open_bitbucket_pr(workspace, repo, source_branch)
    if existing:
        return existing
    raise RuntimeError(
        f"Bitbucket PR failed after {attempts} attempt(s): {last_err}"
    ) from last_err


def create_github_pr(
    repo_slug: str,
    title: str,
    source_branch: str,
    dest_branch: str,
    body: str,
) -> str:
    """repo_slug like owner/name. Uses gh CLI."""
    if not shutil.which("gh"):
        raise RuntimeError("gh CLI not found (required for provider=github)")
    r = run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            repo_slug,
            "--base",
            dest_branch,
            "--head",
            source_branch,
            "--title",
            title,
            "--body",
            body,
        ],
        timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError(f"gh pr create failed: {(r.stderr or r.stdout)[:500]}")
    return (r.stdout or "").strip().splitlines()[-1].strip()


def push_branch(wt: Path, fix_branch: str) -> None:
    r = run(["git", "push", "-u", "origin", fix_branch], cwd=wt, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"git push failed: {(r.stderr or r.stdout)[:500]}")


# ── orchestrate ─────────────────────────────────────────────────────────────


def find_repos_path(cli: Optional[str]) -> Path:
    if cli:
        p = Path(cli)
        if not p.is_file():
            raise FileNotFoundError(p)
        return p
    root = Path(__file__).resolve().parents[3]
    for candidate in (
        root / "config" / "repos.json",
        Path(os.environ.get("HERMES_HOME", "")) / "jira-bot" / "config" / "repos.json",
    ):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "repos.json not found. Copy config/repos.template.json → config/repos.json"
    )


def known_product_ids(repos_path: Optional[Path] = None) -> set[str]:
    ids = {"overlay"}
    try:
        cfg = load_repos(find_repos_path(str(repos_path) if repos_path else None))
    except Exception:
        return ids
    for project in cfg.values():
        if not isinstance(project, dict):
            continue
        for o in project.get("overrides") or []:
            if o.get("id"):
                ids.add(str(o["id"]).lower())
    return ids


AGENT_NAMES = frozenset({"claude", "cursor"})


def normalize_agent_token(token: str) -> Optional[str]:
    """Recognize natural / CLI-ish agent tokens: cursor, 使用cursor, 用claude, agent=cursor."""
    s = (token or "").strip().lower()
    if not s:
        return None
    s = re.sub(r"^(使用|用|--agent[=:\s]*|agent[=:\s]*)", "", s).strip()
    return s if s in AGENT_NAMES else None


def parse_fix_targets(
    tokens: list[str],
    *,
    session_id: str = DEFAULT_SESSION,
    product_cli: Optional[str] = None,
    repos: Optional[str] = None,
) -> tuple[list[str], Optional[str], Optional[str]]:
    """Parse KEY / numbers / trailing product / agent → (keys, product, agent)."""
    flat: list[str] = []
    for tok in tokens:
        for part in re.split(r"[\s,]+", tok.strip()):
            if part:
                flat.append(part)
    if not flat:
        raise ValueError("no targets; pass issue KEY(s) or session number(s)")

    agent: Optional[str] = None
    kept: list[str] = []
    for t in flat:
        a = normalize_agent_token(t)
        if a:
            agent = a
        else:
            kept.append(t)
    flat = kept
    if not flat:
        raise ValueError("no issue keys; only agent token was provided")

    product = product_cli
    products = known_product_ids(Path(repos) if repos else None)
    if (
        product is None
        and len(flat) >= 2
        and flat[-1].lower() in products
        and not ISSUE_KEY_RE.match(flat[-1])
        and not flat[-1].isdigit()
    ):
        product = flat.pop().lower()

    keys: list[str] = []
    numbers: list[int] = []
    for t in flat:
        if ISSUE_KEY_RE.match(t):
            keys.append(t.upper())
        elif t.isdigit():
            numbers.append(int(t))
        else:
            raise ValueError(
                f"unrecognized target {t!r}; expected KEY (CG-123), session number, "
                f"product id, or agent (cursor/claude)"
            )

    if numbers:
        keys.extend(resolve_numbers(numbers, session_id))

    # de-dupe preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            ordered.append(k)
    if not ordered:
        raise ValueError("no issue keys resolved")
    return ordered, product, agent


def orchestrate(args: argparse.Namespace, issue_key: Optional[str] = None) -> dict:
    maybe_load_hermes_env()
    raw_key = issue_key or getattr(args, "issue_key", None)
    if not raw_key:
        raise ValueError("missing issue key")
    key = str(raw_key).upper()
    if not ISSUE_KEY_RE.match(key):
        raise ValueError(f"invalid issue key: {raw_key}")

    issue = fetch_issue(key)
    declined: Optional[dict] = None
    try:
        declined = fetch_latest_declined(key)
    except Exception as e:
        # Non-fatal: continue without declined context
        declined = None
        declined_err = str(e)
    else:
        declined_err = ""

    version = args.version or issue.get("version")
    cfg = load_repos(find_repos_path(args.repos))
    target: ResolveResult = resolve(
        cfg,
        issue["project_key"],
        summary=issue["summary"],
        version_name=version,
        force_product=args.product,
    )
    if args.agent:
        target.agent = args.agent

    fix_branch = f"{FIX_BRANCH_PREFIX}{key}"
    warnings: list[str] = []
    if not (issue.get("description") or "").strip():
        warnings.append("description_empty: Fix Agent will rely on summary + attachments")
    att_meta = issue.get("attachments") or []
    if att_meta:
        warnings.append(f"attachments_on_issue: {len(att_meta)}")
    if declined_err:
        warnings.append(f"declined_lookup_failed: {declined_err}")
    elif declined:
        warnings.append("previous_pr_declined: injecting reason into Fix Agent prompt")

    plan = {
        "key": key,
        "summary": issue["summary"],
        "description_empty": not bool((issue.get("description") or "").strip()),
        "attachments": [
            {"filename": a.get("filename"), "mime": a.get("mime"), "size": a.get("size")}
            for a in att_meta
        ],
        "declined": declined,
        "version_raw": version,
        "version": target.version,
        "version_extracted": target.version_extracted,
        "resolve": asdict(target),
        "fix_branch": fix_branch,
        "base_branch": target.branch,
        "warnings": warnings,
    }

    if args.dry_run:
        return {"ok": True, "dry_run": True, **plan}

    repo = Path(target.path)
    if not repo.is_dir():
        raise RuntimeError(f"repo path does not exist: {repo}")

    wt_root = Path(tempfile.mkdtemp(prefix="jira-fix-wt-"))
    wt_path: Optional[Path] = None
    base_ref = ""
    used_agent = target.agent
    success = False
    pr_url = ""
    validate_info: dict = {}
    agent_log = ""
    local_atts: list[dict] = []
    agent_proc: Optional[subprocess.CompletedProcess] = None

    try:
        wt_path, base_ref = prepare_worktree(repo, key, target.branch, wt_root)
        local_atts = download_attachments(wt_path, att_meta)
        prompt = build_prompt(issue, local_atts, declined=declined)

        if not args.skip_agent:
            try:
                used_agent, agent_proc = run_agent(target.agent, wt_path, prompt, args.timeout)
                agent_log = save_agent_log(key, agent_proc, used_agent, prompt=prompt)
                classify_agent_result(agent_proc)
            except InfraFailure:
                if target.agent != "claude":
                    raise
                used_agent, agent_proc = run_agent("cursor", wt_path, prompt, args.timeout)
                agent_log = save_agent_log(key, agent_proc, used_agent, prompt=prompt)
                classify_agent_result(agent_proc)

            reply = extract_agent_result_text(agent_proc)
            if agent_asked_for_ticket_details(reply):
                raise BusinessFailure(
                    "agent asked for ticket details / tried Jira MCP instead of using "
                    "the Summary/Description already in the prompt. "
                    f"Agent reply (truncated): {reply[:500]}"
                )

        auto_commit = ensure_commit_after_agent(wt_path, key, base_ref)
        try:
            validate_info = validate_commit(wt_path, key, base_ref)
        except BusinessFailure as e:
            if "no new commits" in str(e).lower():
                reply = extract_agent_result_text(agent_proc)
                extra = f" Agent reply (truncated): {reply[:500]}" if reply else ""
                raise BusinessFailure(
                    f"{e}. Agent finished without a code commit.{extra}"
                ) from e
            raise
        if auto_commit:
            validate_info["orchestrator_commit"] = auto_commit
        run_gate_cmd(wt_path, "lint", target.lint or "")
        run_gate_cmd(wt_path, "test", target.test or "")

        if args.no_push:
            success = True
            return {
                "ok": True,
                "key": key,
                "agent": used_agent,
                "pr_url": "",
                "skipped": "push",
                "validate": validate_info,
                "resolve": asdict(target),
                "attachments_local": local_atts,
                "agent_log": agent_log,
                "message_qq": f"✅ {key} 本地修复完成（未 push）\n分支 {fix_branch} ← {target.branch}",
                "message_jira": f"✅ 自动修复完成（未 push）\n分支: {fix_branch}\nBase: {target.branch}",
            }

        push_branch(wt_path, fix_branch)

        title = f"Fix {key}: {issue['summary'][:80]}"
        desc = (
            f"Automated fix for [{key}]({os.environ.get('JIRA_SITE_URL', '')}/browse/{key}).\n\n"
            f"**Summary:** {issue['summary']}\n\n"
            f"Agent: {used_agent}\n"
            f"Base: `{target.branch}`\n"
        )
        pr_error = ""
        if not args.no_pr:
            try:
                if target.provider == "github":
                    # workspace may be owner; repo is name → owner/name
                    slug = (
                        target.repo
                        if "/" in target.repo
                        else f"{target.workspace}/{target.repo}"
                    )
                    pr_url = create_github_pr(
                        slug, title, fix_branch, target.branch, desc
                    )
                else:
                    pr_url = create_bitbucket_pr(
                        target.workspace,
                        target.repo,
                        title,
                        fix_branch,
                        target.branch,
                        desc,
                    )
            except Exception as e:
                # Push already succeeded — do not treat as full fix failure
                pr_error = str(e)

        if pr_error:
            jira_msg = (
                f"⚠️ 代码已推送，但 PR 创建失败\n"
                f"分支: `{fix_branch}` → `{target.branch}`\n"
                f"（远端已有 commit，可手动建 PR 或重跑 /fix）\n"
                f"错误: {pr_error}\n"
                f"Agent: {used_agent}"
            )
            qq_msg = (
                f"⚠️ {key} 已 push，PR 失败\n"
                f"分支 {fix_branch} → {target.branch}\n"
                f"{pr_error}"
            )
        else:
            jira_msg = (
                f"✅ 已自动修复 → PR: {pr_url or '(no PR)'}\n"
                f"分支: {fix_branch} → {target.branch}\n"
                f"Agent: {used_agent}"
            )
            qq_msg = f"✅ {key} 已修复 → {pr_url or fix_branch}"

        if not args.no_jira_comment:
            try:
                post_jira_comment(key, jira_msg)
            except Exception as e:
                qq_msg += f"\n⚠️ Jira 评论失败: {e}"

        # Push succeeded: keep local branch cleanup soft (delete_branch=False)
        success = True
        return {
            "ok": not bool(pr_error),
            "partial": bool(pr_error),
            "pushed": True,
            "key": key,
            "agent": used_agent,
            "pr_url": pr_url,
            "pr_error": pr_error or None,
            "fix_branch": fix_branch,
            "base_branch": target.branch,
            "validate": validate_info,
            "resolve": asdict(target),
            "declined": declined,
            "attachments_local": local_atts,
            "agent_log": agent_log,
            "message_qq": qq_msg,
            "message_jira": jira_msg,
        }

    except (BusinessFailure, InfraFailure, RuntimeError, ValueError) as e:
        err = str(e)
        jira_msg = f"❌ 自动修复失败\n{err}"
        qq_msg = f"❌ {key} 修复失败: {err}"
        if agent_log:
            qq_msg += f"\n(agent log: {agent_log})"
        if not args.dry_run and not args.no_jira_comment and args.comment_on_failure:
            try:
                post_jira_comment(key, jira_msg)
            except Exception:
                pass
        return {
            "ok": False,
            "key": key,
            "error": err,
            "agent": used_agent,
            "resolve": asdict(target) if target else None,
            "attachments_local": local_atts,
            "agent_log": agent_log,
            "message_qq": qq_msg,
            "message_jira": jira_msg,
        }
    finally:
        if wt_path and not args.keep_worktree:
            try:
                cleanup_worktree(repo, wt_path, fix_branch, delete_branch=not success)
            except Exception:
                pass
        if wt_root.exists() and not args.keep_worktree:
            shutil.rmtree(wt_root, ignore_errors=True)


def _configure_stdio() -> None:
    """Avoid UnicodeEncodeError on Windows GBK consoles / pipes."""
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
                continue
            except Exception:
                pass
        buf = getattr(stream, "buffer", None)
        if buf is not None:
            try:
                import io

                setattr(
                    sys,
                    stream_name,
                    io.TextIOWrapper(buf, encoding="utf-8", errors="replace", line_buffering=True),
                )
            except Exception:
                pass


def _print_json(obj: dict) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))


def _batch_message(results: list[dict]) -> str:
    ok_n = sum(1 for r in results if r.get("ok"))
    lines = [f"汇总: {ok_n}/{len(results)} 成功"]
    for r in results:
        lines.append(r.get("message_qq") or f"{'✅' if r.get('ok') else '❌'} {r.get('key', '?')}")
    return "\n".join(lines)


def main() -> int:
    _configure_stdio()
    ap = argparse.ArgumentParser(
        description="Jira /fix orchestrator (single or batch serial)"
    )
    ap.add_argument(
        "targets",
        nargs="+",
        help="issue KEY(s) and/or session numbers; trailing product id ok (e.g. overlay)",
    )
    ap.add_argument("--product", default=None, help="force override id, e.g. overlay")
    ap.add_argument("--session", default=DEFAULT_SESSION, help="numbered session id")
    ap.add_argument(
        "--resolve-only",
        action="store_true",
        help="only resolve numbers/KEYS; print started message (no fix)",
    )
    ap.add_argument("--version", default=None, help="override fixVersion name")
    ap.add_argument("--repos", default=None, help="path to repos.json")
    ap.add_argument("--agent", default=None, choices=["claude", "cursor"], help="force agent")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="agent timeout seconds")
    ap.add_argument("--dry-run", action="store_true", help="resolve only, print plan")
    ap.add_argument("--skip-agent", action="store_true", help="skip agent (validate will fail unless commits exist)")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--no-pr", action="store_true")
    ap.add_argument("--no-jira-comment", action="store_true")
    ap.add_argument(
        "--no-comment-on-failure",
        action="store_true",
        help="do not post Jira comment when fix fails",
    )
    ap.add_argument("--keep-worktree", action="store_true")
    args = ap.parse_args()
    args.comment_on_failure = not args.no_comment_on_failure

    try:
        keys, product, agent_from_targets = parse_fix_targets(
            args.targets,
            session_id=args.session,
            product_cli=args.product,
            repos=args.repos,
        )
        if product:
            args.product = product
        if agent_from_targets and not args.agent:
            args.agent = agent_from_targets

        started = f"🔧 已开始修复 {', '.join(keys)}" + (
            f"（product={args.product}）" if args.product else ""
        )
        if args.agent:
            started += f"（agent={args.agent}）"
        started += "…"

        if args.resolve_only:
            maybe_load_hermes_env()
            declined_by_key: dict[str, dict] = {}
            for k in keys:
                try:
                    d = fetch_latest_declined(k)
                    if d:
                        declined_by_key[k] = d
                except Exception:
                    pass
            if declined_by_key:
                bits = []
                for k, d in declined_by_key.items():
                    r = d.get("reason") or "(无原因)"
                    bits.append(f"{k} 曾被拒: {r}")
                started += "\n⚠️ " + "; ".join(bits)
            result = {
                "ok": True,
                "resolve_only": True,
                "keys": keys,
                "product": args.product,
                "agent": args.agent,
                "declined": declined_by_key or None,
                "message_qq": started,
            }
            _print_json(result)
            return 0

        if len(keys) == 1:
            result = orchestrate(args, issue_key=keys[0])
            result.setdefault("keys", keys)
            _print_json(result)
            return 0 if result.get("ok") else 1

        # Strict serial batch
        results: list[dict] = []
        for i, key in enumerate(keys):
            print(f"# progress {i + 1}/{len(keys)} start {key}", file=sys.stderr, flush=True)
            one = orchestrate(args, issue_key=key)
            results.append(one)
            print(
                f"# progress {i + 1}/{len(keys)} done {key} ok={one.get('ok')}",
                file=sys.stderr,
                flush=True,
            )

        result = {
            "ok": all(r.get("ok") for r in results),
            "batch": True,
            "keys": keys,
            "product": args.product,
            "started_message": started,
            "results": results,
            "message_qq": _batch_message(results),
        }
    except Exception as e:
        result = {"ok": False, "error": str(e), "message_qq": f"❌ 修复失败: {e}"}

    _print_json(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
