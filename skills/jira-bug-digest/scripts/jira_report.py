#!/usr/bin/env python3
"""Jira Work List → HTML Report → Cloudflare Pages Deploy.

Env vars:
  JIRA_API_TOKEN        — Atlassian API token
  JIRA_USER_EMAIL       — Atlassian email
  JIRA_CLOUD_ID         — Jira cloud instance ID
  JIRA_ASSIGNEE         — (optional) Account ID filter
  JIRA_SITE_URL         — (optional) Jira site URL for links; defaults to env-based guess
  JIRA_USER_DISPLAY     — (optional) Display name in header; defaults to "User"
  CLOUDFLARE_API_TOKEN  — (optional) Cloudflare API token for wrangler Pages deploy
  CLOUDFLARE_ACCOUNT_ID — (optional) wrangler account ID
"""
import json, os, sys, time, subprocess, base64, urllib.request
from datetime import datetime

NOW = datetime.now()
SITE_URL = os.environ.get("JIRA_SITE_URL", "")

def _fetch_display_name():
    """Fetch display name from Jira /myself API."""
    try:
        email = os.environ["JIRA_USER_EMAIL"]
        token = os.environ["JIRA_API_TOKEN"]
        cid = os.environ["JIRA_CLOUD_ID"]
        auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        req = urllib.request.Request(
            f"https://api.atlassian.com/ex/jira/{cid}/rest/api/3/myself",
            headers={"Authorization": f"Basic {auth}", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("displayName", "User")
    except Exception:
        return os.environ.get("JIRA_USER_DISPLAY", "User")

USER_NAME = _fetch_display_name()

# Resolve jira_query.py relative to this file
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
QUERY_SCRIPT = os.path.join(SCRIPT_DIR, "jira_query.py")

def search_all(status_category):
    r = subprocess.run(
        ["python", QUERY_SCRIPT],
        capture_output=True, text=True, timeout=300,
        env=os.environ
    )
    if r.returncode != 0:
        raise Exception(f"jira_query failed: {r.stderr[:300]}")
    data = json.loads(r.stdout)
    return data.get(status_category.lower().replace(" ", ""), [])

HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jira Work List</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&#38;display=swap" rel="stylesheet">
<style>
:root{{--bg:#fafbfc;--text:#1a1a1a;--muted:#6b7280;--rule:#e5e7eb;--amber:#c6901a;--danger:#c0392b;--hover:#f3f4f6;--hd-rule:#1a1a1a;--switch-bg:#e5e7eb;--switch-dot:#fff}}
@media(prefers-color-scheme:dark){{:root{{--bg:#141414;--text:#e0e0e0;--muted:#888;--rule:#2a2a2a;--amber:#e5a820;--danger:#e0554a;--hover:#1e1e1e;--hd-rule:#e0e0e0;--switch-bg:#3a3a3a;--switch-dot:#e0e0e0}}}}
[data-theme="light"]{{--bg:#fafbfc;--text:#1a1a1a;--muted:#6b7280;--rule:#e5e7eb;--amber:#c6901a;--danger:#c0392b;--hover:#f3f4f6;--hd-rule:#1a1a1a;--switch-bg:#e5e7eb;--switch-dot:#fff}}
[data-theme="dark"]{{--bg:#141414;--text:#e0e0e0;--muted:#888;--rule:#2a2a2a;--amber:#e5a820;--danger:#e0554a;--hover:#1e1e1e;--hd-rule:#e0e0e0;--switch-bg:#3a3a3a;--switch-dot:#e0e0e0}}
*,*::before,*::after{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);padding:clamp(12px,3vw,32px);max-width:960px;margin:0 auto;-webkit-font-smoothing:antialiased;transition:background .15s,color .15s}}

/* Header */
.hd{{padding-bottom:clamp(12px,2vw,20px);margin-bottom:clamp(8px,1.5vw,16px);border-bottom:2px solid var(--hd-rule)}}
.hd-row{{display:flex;flex-wrap:wrap;align-items:center;gap:clamp(8px,2vw,16px)}}
.hd h1{{font-size:clamp(18px,4vw,24px);font-weight:600;letter-spacing:-0.02em}}
.hd h1 .ct{{font-family:'JetBrains Mono',monospace;font-weight:600}}
.hd .meta{{font-size:clamp(11px,2vw,13px);color:var(--muted)}}
.stats{{display:flex;gap:clamp(12px,2vw,24px);margin-top:clamp(6px,1vw,12px)}}
.stat{{font-family:'JetBrains Mono',monospace;font-size:clamp(13px,2.5vw,15px)}}
.stat .val{{font-weight:600}}.stat .val.high{{color:var(--danger)}}.stat .lbl{{color:var(--muted);font-size:.8em;margin-left:4px}}

/* Color mode switch */
.switch-wrap{{margin-left:auto;display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted)}}
.switch{{position:relative;width:36px;height:20px}}
.switch input{{opacity:0;width:0;height:0}}
.switch .slider{{position:absolute;cursor:pointer;inset:0;background:var(--switch-bg);border-radius:20px;transition:.2s}}
.switch .slider::before{{content:'';position:absolute;height:14px;width:14px;left:3px;bottom:3px;background:var(--switch-dot);border-radius:50%;transition:.2s}}
.switch input:checked+.slider::before{{transform:translateX(16px)}}

/* Tabs */
.tabs{{display:flex;align-items:center;gap:0;margin:clamp(8px,1.5vw,14px) 0}}
.tabs .fswitch{{margin-left:auto;display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted);cursor:pointer;user-select:none;white-space:nowrap}}
.tabs .fswitch input{{width:16px;height:16px;accent-color:var(--amber);cursor:pointer;margin:0;flex-shrink:0}}
.fcount{{font-family:'JetBrains Mono',monospace;font-size:11px;margin-left:2px}}

/* Type filter chips */
.type-filters{{display:flex;gap:6px;margin-top:clamp(6px,1vw,10px);flex-wrap:wrap}}
.tchip{{padding:3px 10px;font-size:clamp(11px,1.8vw,12px);border:1px solid var(--rule);background:var(--bg);color:var(--muted);cursor:pointer;font-family:system-ui,sans-serif;transition:color .15s,border-color .15s}}
.tchip:hover{{color:var(--text);border-color:var(--muted)}}
.tchip.active{{color:var(--text);border-color:var(--text);font-weight:500}}
.tab{{padding:6px 16px;font-size:clamp(12px,2vw,14px);font-weight:500;border:none;background:none;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;transition:color .15s,border-color .15s;font-family:system-ui,sans-serif}}
.tab:hover{{color:var(--text)}}
.tab.active{{color:var(--text);border-bottom-color:var(--text);font-weight:600}}

/* Table */
.tbl{{width:100%;border-collapse:collapse;font-size:clamp(12px,2vw,14px)}}
.tbl thead{{display:none}}
.tbl td{{padding:clamp(6px,1vw,10px) clamp(8px,1.5vw,12px);border-bottom:1px solid var(--rule);vertical-align:top;line-height:1.4}}
.tbl tr:hover td{{background:var(--hover)}}
.tbl tr.checked td{{opacity:.5}}
.tbl tr.checked:hover td{{opacity:.7}}
.tbl tr.hidden{{display:none}}
.tbl .chk{{width:36px;text-align:center;padding:clamp(8px,1.5vw,10px) clamp(4px,1vw,8px)!important}}
.tbl .chk input{{appearance:none;-webkit-appearance:none;width:18px;height:18px;border:1.5px solid var(--muted);border-radius:2px;background:var(--bg);cursor:pointer;position:relative;vertical-align:middle;margin:0;flex-shrink:0}}
.tbl .chk input:checked{{background:var(--text);border-color:var(--text)}}
.tbl .chk input:checked::after{{content:'';position:absolute;left:5px;top:2px;width:5px;height:9px;border:solid var(--bg);border-width:0 2px 2px 0;transform:rotate(45deg)}}
.tbl .key{{font-family:'JetBrains Mono',monospace;font-weight:600;white-space:nowrap;color:var(--text);text-decoration:none}}
.tbl .key:hover{{color:var(--amber)}}
.tbl .pri{{font-family:'JetBrains Mono',monospace;font-size:.85em;white-space:nowrap}}
.tbl .pri.hi{{color:var(--danger);font-weight:600}}
.tbl .sum{{min-width:0}}
.tbl .date,.tbl .rep{{color:var(--muted);font-size:.9em;white-space:nowrap}}

@media(min-width:640px){{.tbl thead{{display:table-header-group}}.tbl th{{text-align:left;padding:6px 12px;font-size:10px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;border-bottom:1px solid var(--hd-rule)}}.tbl .idx{{width:32px;text-align:right;color:var(--muted);font-family:'JetBrains Mono',monospace;font-size:.85em}}.tbl .key-cell{{width:90px}}.tbl .pri-cell{{width:72px}}.tbl .date-cell{{width:84px}}.tbl .rep-cell{{width:80px}}}}
@media(max-width:639px){{.tbl,.tbl tbody,.tbl tr{{display:block}}.tbl tr{{padding:clamp(8px,2vw,12px) 0;border-bottom:1px solid var(--rule)}}.tbl tr:last-child{{border-bottom:none}}.tbl td{{display:inline;padding:0;border:none;line-height:1.6}}.tbl .idx,.tbl .rep{{display:none}}.tbl .chk{{display:inline;margin-right:6px}}.tbl .key{{font-size:1.05em}}.tbl .pri::before{{content:' · '}}.tbl .sum{{display:block;margin-top:2px;font-size:.95em;line-height:1.45;margin-left:24px}}.tbl .date{{display:inline}}.tbl .date::before{{content:' · '}}}}

/* Footer */
.ft{{margin-top:clamp(16px,3vw,28px);padding-top:clamp(8px,1.5vw,12px);border-top:1px solid var(--rule);font-size:10px;color:var(--muted);display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}}
.ft a{{color:var(--muted);text-decoration:none}}.ft a:hover{{color:var(--text)}}

/* Ready button */
.btn{{padding:8px 20px;font-size:clamp(12px,2vw,14px);font-weight:500;border:1.5px solid var(--text);background:var(--bg);color:var(--text);cursor:pointer;font-family:system-ui,sans-serif;transition:background .15s,color .15s}}
.btn:hover:not(:disabled){{background:var(--text);color:var(--bg)}}
.btn:disabled{{opacity:.3;cursor:not-allowed;border-color:var(--muted);color:var(--muted)}}
.toast{{font-size:12px;color:var(--amber);margin-left:12px;opacity:0;transition:opacity .3s}}
.toast.show{{opacity:1}}
</style>
</head>
<body>

<div class="hd">
  <div class="hd-row">
    <h1>Jira Work List · <span class="ct" id="totalCount">{todo_total}</span></h1>
    <span class="meta">{now_str} · {user_name}</span>
    <span class="switch-wrap">
      <span>☀️</span>
      <label class="switch"><input type="checkbox" id="themeToggle"><span class="slider"></span></label>
      <span>🌙</span>
    </span>
  </div>
  <div class="stats" id="stats">
    <span class="stat"><span class="val" id="statOpen">{todo_total}</span><span class="lbl">open</span></span>
    <span class="stat"><span class="val high" id="statHigh">{todo_high}</span><span class="lbl">high</span></span>
  </div>
  <div class="tabs">
    <button class="tab active" data-tab="todo">待办</button>
    <button class="tab" data-tab="inprogress">正在进行</button>
    <label class="fswitch"><input type="checkbox" id="weekFilter" checked> 近一周 <span class="fcount" id="filterCount"></span></label>
  </div>
  <div class="type-filters">
    <button class="tchip active" data-type="all">全部</button>
    <button class="tchip" data-type="Bug">🐛 缺陷</button>
    <button class="tchip" data-type="Task">✅ 任务</button>
    <button class="tchip" data-type="Improvement">💡 改进</button>
  </div>
</div>

{todo_table}
{inprogress_table}

<div class="ft">
  <button id="readyBtn" class="btn" disabled>Ready To Do</button>
  <span class="toast" id="toast"></span>
</div>

<div class="ft">
  <span>Hermes Jira Bot · {now_str}</span>
  <a href="{site_url}">{site_label}</a>
</div>

<script>
// Color mode
var toggle=document.getElementById('themeToggle');
var stored=localStorage.getItem('jira-theme');
if(stored){{document.documentElement.setAttribute('data-theme',stored);toggle.checked=stored==='dark'}}
toggle.onchange=function(){{var t=toggle.checked?'dark':'light';document.documentElement.setAttribute('data-theme',t);localStorage.setItem('jira-theme',t)}}

// Tabs
var tabs=document.querySelectorAll('.tab');
var todoTbl=document.getElementById('todoTable');
var inprogTbl=document.getElementById('inprogressTable');
var curTab='todo';
function switchTab(v){{
  curTab=v;
  tabs.forEach(function(x){{x.classList.toggle('active',x.dataset.tab===v)}});
  todoTbl.style.display=v==='todo'?'':'none';
  inprogTbl.style.display=v==='inprogress'?'':'none';
  document.getElementById('totalCount').textContent=v==='todo'?'{todo_total}':'{inprog_total}';
  document.getElementById('statOpen').textContent=v==='todo'?'{todo_total}':'{inprog_total}';
  document.getElementById('statHigh').textContent=v==='todo'?'{todo_high}':'{inprog_high}';
  applyFilter();
}}
tabs.forEach(function(t){{t.onclick=function(){{switchTab(t.dataset.tab)}}}});

// Week filter
var weekCb=document.getElementById('weekFilter');
var weekAgo=new Date();weekAgo.setDate(weekAgo.getDate()-7);
var curType='all';

// Type chips
document.querySelectorAll('.tchip').forEach(function(c){{
  c.onclick=function(){{
    document.querySelectorAll('.tchip').forEach(function(x){{x.classList.remove('active')}});
    c.classList.add('active');
    curType=c.dataset.type;
    applyFilter();
  }}
}});

function applyFilter(){{
  var active=curTab==='todo'?document.getElementById('todoTable'):document.getElementById('inprogressTable');
  var rows=active.querySelectorAll('tbody tr');
  var count=0;
  rows.forEach(function(r){{
    var d=new Date(r.dataset.created);
    var typeMatch=curType==='all'||r.dataset.type===curType;
    var weekMatch=!weekCb.checked||d>=weekAgo;
    var show=typeMatch && weekMatch;
    r.classList.toggle('hidden',!show);
    if(show)count++;
  }});
  document.getElementById('filterCount').textContent=weekCb.checked?count+' recent':''; 
  document.getElementById('totalCount').textContent=count;
  document.getElementById('statOpen').textContent=count;
}}
weekCb.onchange=applyFilter;
applyFilter();

// Checkboxes
document.querySelectorAll('.tbl .chk input').forEach(function(cb){{
  var id='jira-chk-'+cb.value;
  cb.checked=localStorage.getItem(id)==='1';
  cb.parentElement.parentElement.classList.toggle('checked',cb.checked);
  cb.addEventListener('click',function(e){{e.stopPropagation()}});
  cb.onchange=function(){{localStorage.setItem(id,cb.checked?'1':'0');cb.parentElement.parentElement.classList.toggle('checked',cb.checked)}}
}});
inprogTbl.style.display='none';

// Ready button
var readyBtn=document.getElementById('readyBtn');
var toast=document.getElementById('toast');
var msgs=['干得漂亮 🚀','一步一个脚印 👣','消灭它们 💪','又是高效的一天 ⚡','稳扎稳打 🎯','冲冲冲 🏃','今天要清空待办 ✨','Keep shipping 🛳️','一个一个来 🔨','拿下它们 🎖️'];
function updateReadyBtn(){{
  var sel=(curTab==='todo'?'#todoTable':'#inprogressTable')+' .chk input:checked';
  var cbs=document.querySelectorAll(sel);
  readyBtn.disabled=cbs.length===0;
}}
readyBtn.onclick=function(){{
  var sel=(curTab==='todo'?'#todoTable':'#inprogressTable')+' .chk input:checked';
  var cbs=document.querySelectorAll(sel);
  var keys=[];cbs.forEach(function(c){{keys.push(c.value)}});
  var text='/jira-analyze '+keys.join(' ');
  var msg=msgs[Math.floor(Math.random()*msgs.length)];
  navigator.clipboard.writeText(text).then(function(){{
    toast.textContent=msg+'  (Copied '+keys.length+')';
    toast.classList.add('show');
    setTimeout(function(){{toast.classList.remove('show')}},2500);
  }});
}};
document.querySelectorAll('.tbl .chk input').forEach(function(cb){{
  cb.addEventListener('change',updateReadyBtn);
}});
var _switchTab=switchTab;
switchTab=function(v){{_switchTab(v);updateReadyBtn()}};
updateReadyBtn();
</script>
</body></html>'''

def gen_table(issues, site_url):
    high = [i for i in issues if i["fields"]["priority"]["name"] in ("Highest", "High")]
    normal = [i for i in issues if i not in high]
    all_sorted = high + normal
    def row(i, is_high, idx):
        f = i["fields"]; key = i["key"]
        summary = f["summary"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        pri = f["priority"]["name"]; created = f["created"][:10]
        itype = i.get("_type", f.get("issuetype", {}).get("name", "Bug"))
        url = f"{site_url}/browse/{key}"
        pc = " hi" if is_high else ""
        icon = {"Bug":"🐛","Task":"✅","Improvement":"💡"}.get(itype, "🐛")
        return f'<tr data-created="{created}" data-type="{itype}"><td class="chk"><input type="checkbox" value="{key}"></td><td class="idx">{idx}</td><td class="key-cell"><a class="key" href="{url}">{icon}&nbsp;{key}</a></td><td class="sum">{summary}</td><td class="pri-cell"><span class="pri{pc}">{pri}</span></td><td class="date-cell"><span class="date">{created}</span></td><td class="rep-cell"><span class="rep">{(f.get("reporter") or {}).get("displayName","N/A")}</span></td></tr>'
    rows = "".join(row(i, i in high, idx+1) for idx,i in enumerate(all_sorted))
    return rows, len(high), len(all_sorted)

def deploy_to_pages(html):
    import re
    tmpdir = os.path.join(os.environ.get("TEMP", "/tmp"), "jira-pages-deploy")
    os.makedirs(tmpdir, exist_ok=True)
    with open(os.path.join(tmpdir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    cmd = ["npx.cmd", "wrangler", "pages", "deploy", tmpdir,
           "--project-name=hermes-jira-bot", "--commit-dirty=true"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    m = re.search(r"https://([a-f0-9]+)\.hermes-jira-bot\.pages\.dev", r.stdout + r.stderr)
    if m: return f"https://{m.group(1)}.hermes-jira-bot.pages.dev"
    return f"https://master.hermes-jira-bot.pages.dev?t={int(time.time())}"

def _derive_site_url():
    if SITE_URL: return SITE_URL
    cloud_id = os.environ.get("JIRA_CLOUD_ID", "")
    if cloud_id:
        return f"https://api.atlassian.com/ex/jira/{cloud_id}"
    return "https://<your-site>.atlassian.net"

if __name__ == "__main__":
    todo = search_all("To Do")
    inprog = search_all("In Progress")
    todo_issues = todo if isinstance(todo, list) else []
    inprog_issues = inprog if isinstance(inprog, list) else []
    if not todo_issues and not inprog_issues:
        print("NO_BUGS"); sys.exit(0)
    site_url = _derive_site_url()
    site_label = SITE_URL.replace("https://","") if SITE_URL else "Jira"
    now_str = NOW.strftime("%Y-%m-%d %H:%M")
    t_rows, t_high, t_total = gen_table(todo_issues, site_url)
    i_rows, i_high, i_total = gen_table(inprog_issues, site_url)
    html = HTML.format(
        now_str=now_str, user_name=USER_NAME,
        site_url=site_url, site_label=site_label,
        todo_total=t_total, todo_high=t_high,
        inprog_total=i_total, inprog_high=i_high,
        todo_table=f'<table class="tbl" id="todoTable"><thead><tr><th class="chk"></th><th>#</th><th>Key</th><th>Title</th><th>Priority</th><th>Created</th><th>Reporter</th></tr></thead><tbody>{t_rows}</tbody></table>',
        inprogress_table=f'<table class="tbl" id="inprogressTable"><thead><tr><th class="chk"></th><th>#</th><th>Key</th><th>Title</th><th>Priority</th><th>Created</th><th>Reporter</th></tr></thead><tbody>{i_rows}</tbody></table>',
    )
    url = deploy_to_pages(html)
    print(f"PAGES_URL|{url}|{t_total + i_total}")
