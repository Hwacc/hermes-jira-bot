#!/usr/bin/env python3
"""Numbered fix session after /jira-analyze (TTL 30 minutes).

Storage: $HERMES_HOME/jira-fix-sessions/<session_id>.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

DEFAULT_TTL = 30 * 60
DEFAULT_SESSION = "default"


def hermes_home() -> Path:
    if os.environ.get("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"])
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes"
    if local.is_dir():
        return local
    return Path.home() / ".hermes"


def session_path(session_id: str = DEFAULT_SESSION) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id) or DEFAULT_SESSION
    d = hermes_home() / "jira-fix-sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe}.json"


def save_session(
    items: list[dict[str, Any]],
    *,
    session_id: str = DEFAULT_SESSION,
    ttl_seconds: int = DEFAULT_TTL,
) -> Path:
    """items: [{key, summary?}, ...] — numbered 1..n in order."""
    payload = {
        "updated_at": time.time(),
        "ttl_seconds": ttl_seconds,
        "items": [
            {
                "n": i + 1,
                "key": str(it["key"]).upper(),
                "summary": it.get("summary") or "",
            }
            for i, it in enumerate(items)
            if it.get("key")
        ],
    }
    path = session_path(session_id)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_session(session_id: str = DEFAULT_SESSION) -> dict:
    path = session_path(session_id)
    if not path.is_file():
        raise FileNotFoundError(
            f"no fix session ({session_id}). Run /jira-analyze first, or use explicit KEY."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    updated = float(data.get("updated_at") or 0)
    ttl = int(data.get("ttl_seconds") or DEFAULT_TTL)
    age = time.time() - updated
    if age > ttl:
        raise TimeoutError(
            f"fix session expired ({int(age)}s > {ttl}s TTL). "
            "Re-run /jira-analyze or use explicit KEY."
        )
    return data


def resolve_numbers(numbers: list[int], session_id: str = DEFAULT_SESSION) -> list[str]:
    data = load_session(session_id)
    by_n = {int(it["n"]): it["key"] for it in data.get("items") or []}
    keys: list[str] = []
    missing: list[int] = []
    for n in numbers:
        if n in by_n:
            keys.append(by_n[n])
        else:
            missing.append(n)
    if missing:
        available = sorted(by_n.keys())
        raise KeyError(
            f"unknown number(s) {missing}; session has {available}. "
            "Re-run /jira-analyze or use explicit KEY."
        )
    return keys


def format_session_hint(session_id: str = DEFAULT_SESSION) -> str:
    try:
        data = load_session(session_id)
    except Exception as e:
        return str(e)
    lines = ["需要修复哪些？回复编号（如 1,2）或 /fix KEY："]
    for it in data.get("items") or []:
        lines.append(f"  {it['n']}. {it['key']}  {(it.get('summary') or '')[:60]}")
    lines.append(f"（编号 {int(data.get('ttl_seconds') or DEFAULT_TTL) // 60} 分钟内有效）")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="jira-fix numbered session")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_save = sub.add_parser("save", help="save analyze list as numbered session")
    p_save.add_argument("keys", nargs="+", help="issue keys in display order")
    p_save.add_argument("--session", default=DEFAULT_SESSION)
    p_save.add_argument("--ttl", type=int, default=DEFAULT_TTL)
    p_save.add_argument(
        "--summary",
        action="append",
        default=[],
        help="optional summary per key (repeatable, same order)",
    )

    p_show = sub.add_parser("show", help="show current session")
    p_show.add_argument("--session", default=DEFAULT_SESSION)

    p_res = sub.add_parser("resolve", help="map numbers → keys")
    p_res.add_argument("numbers", nargs="+", help="e.g. 1 2 or 1,2")
    p_res.add_argument("--session", default=DEFAULT_SESSION)

    args = ap.parse_args()
    try:
        if args.cmd == "save":
            items = []
            for i, k in enumerate(args.keys):
                sm = args.summary[i] if i < len(args.summary) else ""
                items.append({"key": k, "summary": sm})
            path = save_session(items, session_id=args.session, ttl_seconds=args.ttl)
            print(json.dumps({"ok": True, "path": str(path), "count": len(items)}, ensure_ascii=False))
            return 0
        if args.cmd == "show":
            data = load_session(args.session)
            print(json.dumps({**data, "hint": format_session_hint(args.session)}, ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "resolve":
            nums: list[int] = []
            for tok in args.numbers:
                for part in tok.replace(",", " ").split():
                    if part.strip():
                        nums.append(int(part.strip()))
            keys = resolve_numbers(nums, args.session)
            print(json.dumps({"ok": True, "keys": keys}, ensure_ascii=False, indent=2))
            return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
