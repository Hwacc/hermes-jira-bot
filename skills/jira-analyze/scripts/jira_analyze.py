#!/usr/bin/env python3
"""Fetch Jira bug details via Basic Auth. Portable — no hardcoded paths.

Requires env vars:
  JIRA_API_TOKEN     — Atlassian API token (from https://id.atlassian.com/manage-profile/security/api-tokens)
  JIRA_USER_EMAIL    — Atlassian account email
  JIRA_CLOUD_ID      — Jira cloud instance ID (find at https://<site>.atlassian.net/secure/admin/cloudid)
"""
import json, os, sys, base64, urllib.request, urllib.error

CLOUD_ID = os.environ.get("JIRA_CLOUD_ID", "")

def get_auth_header():
    email = os.environ.get("JIRA_USER_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")
    if not email or not token:
        raise RuntimeError("JIRA_USER_EMAIL or JIRA_API_TOKEN not set. See README for setup.")
    if not CLOUD_ID:
        raise RuntimeError("JIRA_CLOUD_ID not set. See README for setup.")
    creds = base64.b64encode(f"{email}:{token}".encode()).decode()
    return f"Basic {creds}"

def get_issue(auth, key):
    url = f"https://api.atlassian.com/ex/jira/{CLOUD_ID}/rest/api/3/issue/{key}?fields=summary,description,priority,status,created,reporter,comment,issuetype,project"
    req = urllib.request.Request(url, headers={"Authorization": auth, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} fetching {key}: {e.read().decode()[:300]}")
    f = data["fields"]

    desc = ""
    if f.get("description"):
        for c in f["description"].get("content", []):
            for p in c.get("content", []):
                if p.get("text"):
                    desc += p["text"] + "\n"

    comments = []
    for c in f.get("comment", {}).get("comments", [])[-5:]:
        body = ""
        for cb in c.get("body", {}).get("content", []):
            for cp in cb.get("content", []):
                if cp.get("text"):
                    body += cp["text"] + " "
        comments.append({
            "author": c.get("author", {}).get("displayName", "?"),
            "body": body.strip(),
            "created": c.get("created", "")[:10]
        })

    return {
        "key": data["key"],
        "summary": f["summary"],
        "description": desc.strip()[:500],
        "priority": f.get("priority", {}).get("name", "N/A"),
        "status": f.get("status", {}).get("name", "N/A"),
        "created": f.get("created", "")[:10],
        "reporter": (f.get("reporter") or {}).get("displayName", "N/A"),
        "project": f.get("project", {}).get("name", "?"),
        "comments": comments,
    }

def post_comment(auth, key, text):
    """Post an Atlassian Document Format comment to a Jira issue."""
    url = f"https://api.atlassian.com/ex/jira/{CLOUD_ID}/rest/api/3/issue/{key}/comment"
    body = {
        "type": "doc", "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]
    }
    data = json.dumps({"body": body}).encode()
    req = urllib.request.Request(url, data=data, method="POST",
        headers={"Authorization": auth, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} posting comment: {e.read().decode()[:300]}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: jira_analyze.py KEY1 KEY2 ..."}))
        sys.exit(1)
    auth = get_auth_header()
    # If --post flag is present, also post analysis comments
    do_post = "--post" in sys.argv
    keys = [a for a in sys.argv[1:] if not a.startswith("--")]
    bugs = [get_issue(auth, k) for k in keys]
    if do_post:
        bugs_json = json.dumps(bugs, ensure_ascii=False, indent=2)
        # Return with post flag so caller can use it
        print(json.dumps({"bugs": bugs, "post_ready": True, "auth_ok": True}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(bugs, ensure_ascii=False, indent=2))
