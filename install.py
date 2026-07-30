#!/usr/bin/env python3
"""Hermes Jira Bot — 一键安装脚本 (Python 版)

用法:
  python install.py           交互式安装
  python install.py --quiet   静默安装（需环境变量已设）

安装内容:
  1. 复制 skills 到 Hermes 目录
  2. 交互式配置 Jira 凭证
  3. 创建 cron job 指引
  4. 验证 Jira API 连通性
"""
import os, sys, json, shutil, base64, getpass, subprocess
from pathlib import Path

# Windows 控制台默认常是 GBK，避免打印 emoji/中文时 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_DIR = Path(__file__).resolve().parent
QUIET = "--quiet" in sys.argv
HERMES_CMD = None

CRED_VARS = ["JIRA_USER_EMAIL", "JIRA_API_TOKEN", "JIRA_CLOUD_ID"]

def _resolve_hermes_home() -> Path:
    """Resolve Hermes home across Linux ~/.hermes and Windows AppData\\Local\\hermes."""
    if os.environ.get("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"])
    candidates = [
        Path.home() / ".hermes",
        Path.home() / "AppData" / "Local" / "hermes",
    ]
    # WSL: Windows install under /mnt/c/Users/<name>/AppData/Local/hermes
    users = Path("/mnt/c/Users")
    if users.is_dir():
        for user_dir in users.iterdir():
            win_home = user_dir / "AppData" / "Local" / "hermes"
            if win_home.is_dir():
                candidates.append(win_home)
                break
    for c in candidates:
        if c.is_dir():
            return c
    return Path.home() / ".hermes"

HERMES_HOME = _resolve_hermes_home()
HERMES_ENV = HERMES_HOME / ".env"
SKILLS_DST = HERMES_HOME / "skills"

# ── helpers ────────────────────────────────────────────

def _ok(msg):    print(f"  ✅ {msg}")
def _skip(msg):  print(f"  ⏭️  {msg}")
def _warn(msg):  print(f"  ⚠️  {msg}")
def _err(msg):
    print(f"  ❌ {msg}")
    sys.exit(1)

def _load_env():
    """Read key=value pairs from .env file."""
    env = {}
    if HERMES_ENV.exists():
        for line in HERMES_ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

def _save_env(key, val):
    """Append a line to .env."""
    with open(HERMES_ENV, "a", encoding="utf-8") as f:
        f.write(f"{key}={val}\n")

def _find_hermes_cmd():
    """Resolve Hermes CLI across Linux/macOS/Windows layouts."""
    for name in ("hermes", "hermes.exe"):
        path = shutil.which(name)
        if path:
            return path
    candidates = [
        Path.home() / ".local" / "bin" / "hermes",
        Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "hermes",
        Path("/usr/local/bin/hermes"),
        Path.home() / "AppData" / "Local" / "hermes" / "hermes-agent" / "venv" / "Scripts" / "hermes.exe",
    ]
    users = Path("/mnt/c/Users")
    if users.is_dir():
        for user_dir in users.iterdir():
            candidates.append(
                user_dir / "AppData" / "Local" / "hermes" / "hermes-agent" / "venv" / "Scripts" / "hermes.exe"
            )
            break
    for c in candidates:
        if c.is_file() or c.is_symlink():
            return str(c)
    return None

# ── banner ─────────────────────────────────────────────

def banner():
    print("")
    print("  ╔══════════════════════════════════════════╗")
    print("  ║     🧭  Hermes Jira Bot Installer        ║")
    print("  ╚══════════════════════════════════════════╝")
    print("")

# ── Step 0: pre-flight ─────────────────────────────────

def step_0_check():
    print("━━━ Step 0: 检查前置条件 ━━━")

    # Hermes CLI
    global HERMES_CMD
    HERMES_CMD = _find_hermes_cmd()
    if not HERMES_CMD:
        _err("Hermes CLI 未找到，请先安装 Hermes Agent\n"
              "     curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash")
    try:
        r = subprocess.run([HERMES_CMD, "--version"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            _ok(f"Hermes CLI 已安装: {r.stdout.strip().split(chr(10))[0]}")
        else:
            _err("Hermes CLI 未找到，请先安装 Hermes Agent\n"
                  "     curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash")
    except FileNotFoundError:
        _err("Hermes CLI 未找到，请先安装 Hermes Agent\n"
              "     curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash")

    # Python (we're already running it)
    _ok(f"Python 可用: {sys.version.split()[0]}")

    # Hermes home dir
    if not HERMES_HOME.is_dir():
        _err(f"Hermes 目录未找到: {HERMES_HOME}\n"
              f"     请先运行 hermes 一次以初始化配置")
    _ok(f"Hermes 目录: {HERMES_HOME}")
    print("")

# ── Step 1: install skills ─────────────────────────────

def step_1_install_skills():
    print("━━━ Step 1: 安装 Skills ━━━")
    src_dir = SCRIPT_DIR / "skills"
    SKILLS_DST.mkdir(parents=True, exist_ok=True)

    for skill_dir in sorted(src_dir.iterdir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        name = skill_dir.name
        dest = SKILLS_DST / name
        if dest.exists():
            _skip(f"{name} — 已存在，跳过")
        else:
            shutil.copytree(skill_dir, dest)
            _ok(f"{name} — 已安装")
    print("")

# ── Step 2: credentials ────────────────────────────────

def step_2_credentials():
    print("━━━ Step 2: 配置 Jira 凭证 ━━━")

    if QUIET:
        missing = [v for v in CRED_VARS if not os.environ.get(v)]
        if missing:
            _err(f"静默模式需要预先设置: {', '.join(missing)}")
        _ok("环境变量已设置（静默模式）")
        print("")
        return

    current = _load_env()
    for var in CRED_VARS:
        existing = current.get(var)
        if existing:
            _ok(f"{var} — 已配置")
            continue

        prompts = {
            "JIRA_USER_EMAIL": "  请输入你的 Atlassian 邮箱: ",
            "JIRA_API_TOKEN": "  请输入 Jira API Token (https://id.atlassian.com/manage-profile/security/api-tokens): ",
            "JIRA_CLOUD_ID": "  请输入 Jira Cloud ID (https://<site>.atlassian.net/secure/admin/cloudid): ",
        }

        if var == "JIRA_API_TOKEN":
            val = getpass.getpass(prompts[var])
        else:
            val = input(prompts[var])

        if val.strip():
            _save_env(var, val.strip())
            _ok(f"{var} — 已保存")
    print("")

# ── Step 3: cron job ───────────────────────────────────

def step_3_cron():
    print("━━━ Step 3: 创建 Cron Job ━━━")

    try:
        cmd = HERMES_CMD or _find_hermes_cmd() or "hermes"
        r = subprocess.run([cmd, "cron", "list"], capture_output=True, text=True, timeout=10)
        if "jira-bug-daily-digest" in (r.stdout + r.stderr):
            _skip("jira-bug-daily-digest cron job 已存在")
            print("")
            return
    except Exception:
        pass

    print("  配置 Bug 日报 cron job (推荐)...")
    print("")
    print("  ℹ️  Cron job 需手动创建：")
    print("")
    print("     hermes cron create '0 9 * * *' --skills jira-bug-digest \\")
    print("       --prompt '运行 jira_report.py 生成 HTML 日报'")
    print("")
    print("     或在 Hermes 对话中输入：")
    print("     「帮我创建一个 Jira Bug 日报 cron job，每天 9:00 执行」")
    print("")
    print("  📄 Cron 模板参考: cron/jobs.template.json")
    print("")

# ── Step 4: verify ─────────────────────────────────────

def step_4_verify():
    print("━━━ Step 4: 验证 Jira API 连通性 ━━━")

    creds = _load_env()
    email = os.environ.get("JIRA_USER_EMAIL") or creds.get("JIRA_USER_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN") or creds.get("JIRA_API_TOKEN", "")
    cid   = os.environ.get("JIRA_CLOUD_ID") or creds.get("JIRA_CLOUD_ID", "")

    if not (email and token and cid):
        _skip("跳过（凭证未完全配置）")
        print("")
        return

    try:
        import urllib.request
        auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        req = urllib.request.Request(
            f"https://api.atlassian.com/ex/jira/{cid}/rest/api/3/myself",
            headers={"Authorization": f"Basic {auth}", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        _ok(f"Jira API 连通！已认证用户: {data.get('displayName', 'OK')}")
    except Exception:
        _warn("Jira API 验证失败，请检查凭证是否正确")
    print("")

# ── done ───────────────────────────────────────────────

def done():
    print("  ╔══════════════════════════════════════════╗")
    print("  ║     ✅  安装完成！                        ║")
    print("  ╚══════════════════════════════════════════╝")
    print("")
    print("  使用方法:")
    print("    /jira-analyze CG-12345    分析指定 Bug")
    print("    分析bug CG-12345           同上")
    print("")
    print("  Cron 日报创建后将在每天 9:00 自动推送。")
    print(f"  详情参考: {SCRIPT_DIR / 'README.md'}")
    print("")

# ── main ───────────────────────────────────────────────

if __name__ == "__main__":
    banner()
    step_0_check()
    step_1_install_skills()
    step_2_credentials()
    step_3_cron()
    step_4_verify()
    done()
