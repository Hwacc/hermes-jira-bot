#!/usr/bin/env python3
"""Jira query via REST API (Basic Auth). Outputs JSON for HTML generator.

Env vars:
  JIRA_API_TOKEN    — Atlassian API token
  JIRA_USER_EMAIL   — Atlassian email
  JIRA_CLOUD_ID     — Jira cloud instance ID
  JIRA_ASSIGNEE     — (optional) Account ID filter; defaults to currentUser()
"""
import json, os, urllib.request, base64

CLOUD_ID = os.environ["JIRA_CLOUD_ID"]
ASSIGNEE = os.environ.get("JIRA_ASSIGNEE", "currentUser()")
BASE = f"https://api.atlassian.com/ex/jira/{CLOUD_ID}/rest/api/3"

def _auth():
    token = os.environ["JIRA_API_TOKEN"]
    email = os.environ["JIRA_USER_EMAIL"]
    return base64.b64encode(f"{email}:{token}".encode()).decode()

def _req(method, path, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Basic {_auth()}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def search_all(status_category):
    all_issues = []
    for itype in ["Bug", "Task", "Improvement"]:
        jql = f"issuetype = '{itype}' AND assignee = {ASSIGNEE} AND statusCategory = '{status_category}' ORDER BY priority DESC, created DESC"
        r = _req("POST", "/search/jql", {
            "jql": jql,
            "maxResults": 50,
            "fields": ["summary", "priority", "status", "created", "reporter", "issuetype", "project"]
        })
        for i in r.get("issues", []):
            i["_type"] = itype
            all_issues.append(i)
    return all_issues

if __name__ == "__main__":
    todo = search_all("To Do")
    inprog = search_all("In Progress")
    print(json.dumps({"todo": todo, "inprogress": inprog}, ensure_ascii=False))
