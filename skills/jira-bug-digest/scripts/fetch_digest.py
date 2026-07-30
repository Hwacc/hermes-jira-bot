#!/usr/bin/env python3
"""Jira Bug Daily Digest — fetch assigned bugs and format a daily report.

Uses Jira REST API via Basic Auth (JIRA_API_TOKEN + JIRA_USER_EMAIL).
Designed for cron jobs: prints formatted report to stdout.

Env vars:
  JIRA_USER_EMAIL  — Atlassian email
  JIRA_API_TOKEN   — API token (1yr validity)
  JIRA_CLOUD_ID    — Jira cloud instance ID
"""
import json, os, base64, urllib.request, urllib.error
from datetime import datetime

CLOUD_ID = os.environ.get("JIRA_CLOUD_ID", "")
JIRA_SITE_URL = os.environ.get("JIRA_SITE_URL", "https://razersw.atlassian.net")

def get_auth():
    email = os.environ.get("JIRA_USER_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")
    if not email or not token or not CLOUD_ID:
        raise RuntimeError("Missing JIRA_USER_EMAIL, JIRA_API_TOKEN, or JIRA_CLOUD_ID")
    return "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()

def fetch_bugs(auth):
    jql = "issuetype = Bug AND assignee = currentUser() AND statusCategory = 'To Do' ORDER BY priority DESC, created DESC"
    url = f"https://api.atlassian.com/ex/jira/{CLOUD_ID}/rest/api/3/search/jql"
    body = json.dumps({
        "jql": jql,
        "maxResults": 30,
        "fields": ["key", "summary", "priority", "created", "status"]
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": auth, "Content-Type": "application/json", "Accept": "application/json"
    })
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["issues"]

def format_report(issues):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = len(issues)
    high = [i for i in issues if i["fields"]["priority"]["name"] in ("Highest", "High")]

    lines = []
    lines.append(f"📊 Jira 待办 Bug · {now}")
    lines.append("━" * 30)
    lines.append(f"🐛 总数 {total} · ⚠️ 高优 {len(high)}")
    lines.append("")

    if high:
        lines.append("⚠️ 高优先级")
        for i in high:
            f = i["fields"]
            lines.append(f"  {i['key']} {f['summary']} ({f.get('created','')[:10]})")
        lines.append("")

    rest = [i for i in issues if i not in high]
    if rest:
        lines.append("其余")
        for i in rest:
            f = i["fields"]
            created = f.get("created", "")[:10]
            lines.append(f"  {i['key']} {f['summary']} ({created})")

    lines.append("")
    lines.append(f"🔗 {JIRA_SITE_URL}")

    # Compact mode: if > 10, only show first 5 detail + rest as keys
    if total > 10:
        compact = []
        compact.append(f"📊 Jira 待办 Bug · {now}")
        compact.append("━" * 30)
        compact.append(f"🐛 总数 {total} · ⚠️ 高优 {len(high)}")
        compact.append("")
        if high:
            compact.append("⚠️ 高优先级")
            for i in high:
                f = i["fields"]
                compact.append(f"  {i['key']} {f['summary']} ({f.get('created','')[:10]})")
            compact.append("")
        normal_keys = ", ".join(i["key"] for i in rest[:3])
        compact.append(f"📋 普通: {normal_keys} ...（共{len(rest)}个）")
        compact.append("")
        compact.append(f"🔗 {JIRA_SITE_URL}")
        lines = compact

    return "\n".join(lines)

if __name__ == "__main__":
    auth = get_auth()
    issues = fetch_bugs(auth)
    if not issues:
        print("✅ 没有待办 Bug")
    else:
        print(format_report(issues))
