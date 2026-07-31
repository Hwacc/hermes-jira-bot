#!/usr/bin/env python3
"""Jira PR lifecycle comments — called by Hermes (not the Bitbucket adapter).

Usage:
  python pr_lifecycle.py fulfilled CG-20926 --pr-url URL [--branch fix/CG-20926] [--base main] \\
      [--repo razersw/x] [--actor Name] [--pr-id 19]
  python pr_lifecycle.py rejected CG-20926 --pr-url URL [--reason "..."] [--actor Name] ...

Env: JIRA_USER_EMAIL, JIRA_API_TOKEN, JIRA_CLOUD_ID (Hermes .env)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from jira_fix import maybe_load_hermes_env, post_jira_comment  # noqa: E402


def build_fulfilled_comment(args: argparse.Namespace) -> str:
    lines = [
        "✅ PR 已合入（Bitbucket → Hermes）",
        f"PR: {args.pr_url or args.pr_id or '(unknown)'}",
    ]
    if args.branch or args.base:
        lines.append(f"分支: {args.branch or '?'} → {args.base or '?'}")
    if args.repo:
        lines.append(f"仓库: {args.repo}")
    if args.actor:
        lines.append(f"操作者: {args.actor}")
    lines.append("❤ Hermes Jira Bot")
    return "\n".join(lines)


def build_rejected_comment(args: argparse.Namespace) -> str:
    lines = [
        "❌ PR 已被拒绝（Declined · Bitbucket → Hermes）",
        f"PR: {args.pr_url or args.pr_id or '(unknown)'}",
    ]
    if args.branch or args.base:
        lines.append(f"分支: {args.branch or '?'} → {args.base or '?'}")
    if args.repo:
        lines.append(f"仓库: {args.repo}")
    if args.actor:
        lines.append(f"操作者: {args.actor}")
    reason = (args.reason or "").strip()
    if reason:
        lines.append(f"原因: {reason}")
    lines.append("❤ Hermes Jira Bot")
    return "\n".join(lines)


def main() -> int:
    maybe_load_hermes_env()
    ap = argparse.ArgumentParser(description="Post Jira comment for PR lifecycle events")
    ap.add_argument("event", choices=["fulfilled", "rejected"], help="lifecycle event")
    ap.add_argument("issue_key", help="e.g. CG-20926")
    ap.add_argument("--pr-url", default="")
    ap.add_argument("--pr-id", default="")
    ap.add_argument("--branch", default="")
    ap.add_argument("--base", default="")
    ap.add_argument("--repo", default="")
    ap.add_argument("--actor", default="")
    ap.add_argument("--reason", default="", help="decline reason (rejected only)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    key = args.issue_key.upper().strip()
    if args.event == "fulfilled":
        text = build_fulfilled_comment(args)
    elif args.event == "rejected":
        text = build_rejected_comment(args)
    else:
        print(json.dumps({"ok": False, "error": f"unsupported event {args.event}"}))
        return 1

    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "key": key, "comment": text}, ensure_ascii=False, indent=2))
        return 0

    try:
        post_jira_comment(key, text)
    except Exception as e:
        print(json.dumps({"ok": False, "key": key, "error": str(e)}, ensure_ascii=False))
        return 1

    print(json.dumps({"ok": True, "key": key, "comment": text}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
