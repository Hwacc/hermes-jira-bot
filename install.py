#!/usr/bin/env python3
"""Hermes Jira Bot — 一键安装脚本 (Python 版)

用法:
  python install.py           交互式安装 / 升级
  python install.py --quiet   静默安装（需环境变量已设）

安装内容:
  1. 安装/更新 skills（jira-analyze / jira-bug-digest / jira-fix 等）
  2. 配置 Jira 凭证
  3. 创建 Bug 日报 cron
  3.5 Cloudflare Pages（HTML 日报，可选）
  3.6 Bitbucket + repos.json（/fix 建 PR，可选）
  4. 验证 Jira（及可选 Bitbucket）连通性
"""
import os, sys, json, shutil, base64, getpass, subprocess, re
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
    """Upsert key=value in Hermes .env (avoid duplicate keys on re-install)."""
    lines = []
    found = False
    if HERMES_ENV.exists():
        for line in HERMES_ENV.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#") or "=" not in line:
                lines.append(line)
                continue
            k = line.split("=", 1)[0].strip()
            if k == key:
                lines.append(f"{key}={val}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={val}")
    HERMES_ENV.parent.mkdir(parents=True, exist_ok=True)
    HERMES_ENV.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

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

DIGEST_CRON_NAME = "jira-bug-daily-digest"
DIGEST_CRON_PROMPT = "运行 jira_report.py 生成 HTML 日报"
_FRIENDLY_TIME_RE = re.compile(r"^([01]?\d|2[0-3])(?::([0-5]\d))?$")

def parse_digest_schedule(raw):
    """Parse friendly time (9:00) or 5-field cron; empty → 0 9 * * *."""
    text = (raw or "").strip()
    if not text:
        return "0 9 * * *"
    m = _FRIENDLY_TIME_RE.match(text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or "0")
        return f"{minute} {hour} * * *"
    if len(text.split()) == 5:
        return text
    raise ValueError(f"invalid schedule: {raw!r}")

def _print_manual_cron(schedule, deliver=""):
    deliver = deliver or os.environ.get("JIRA_DELIVER", "")
    print("  ℹ️  可手动创建：")
    print("")
    print(f"     hermes cron create '{schedule}' \\")
    print(f"       --name {DIGEST_CRON_NAME} \\")
    print("       --skill jira-bug-digest \\")
    if deliver:
        print(f"       --deliver '{deliver}' \\")
    print(f"       --prompt '{DIGEST_CRON_PROMPT}'")
    print("")
    print("  📄 Cron 模板参考: cron/jobs.template.json")

def _create_digest_cron(schedule, deliver=None) -> bool:
    cmd = HERMES_CMD or _find_hermes_cmd() or "hermes"
    if deliver is None:
        deliver = os.environ.get("JIRA_DELIVER", "")
    args = [cmd, "cron", "create", schedule,
            "--name", DIGEST_CRON_NAME,
            "--skill", "jira-bug-digest"]
    if deliver:
        args += ["--deliver", deliver]
    args += ["--prompt", DIGEST_CRON_PROMPT]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            if deliver:
                _ok(f"已创建 cron job: {DIGEST_CRON_NAME} ({schedule}, deliver={deliver})")
            else:
                _ok(f"已创建 cron job: {DIGEST_CRON_NAME} ({schedule})")
            return True
    except Exception:
        pass
    _warn("自动创建失败，请手动执行：")
    _print_manual_cron(schedule, deliver)
    return False

def _resolve_digest_deliver():
    """Quiet: env/.env value. Interactive: optional override; empty keeps current; 'origin' clears."""
    creds = _load_env()
    current = os.environ.get("JIRA_DELIVER") or creds.get("JIRA_DELIVER", "")
    if QUIET:
        return current
    hint = current or "origin"
    print("  投递目标（可选）: origin / local / telegram / discord / signal / platform:chat_id")
    raw = input(f"  日报投递目标 [{hint}]: ").strip()
    if not raw:
        return current
    if raw == "origin":
        return ""
    return raw

# ── banner ─────────────────────────────────────────────

def banner():
    print("")
    print("  ╔══════════════════════════════════════════╗")
    print("  ║     🧭  Hermes Jira Bot Installer        ║")
    print("  ║     日报 · /jira-analyze · /fix          ║")
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

    # /fix needs Claude Code CLI (optional warning)
    claude = shutil.which("claude")
    if claude:
        _ok(f"claude CLI 可用（/fix）: {claude}")
    else:
        _warn("未检测到 claude CLI — /fix 自动修复需要 Claude Code（OAuth 已登录）")
    print("")

# ── Step 1: install skills ─────────────────────────────

def step_1_install_skills():
    print("━━━ Step 1: 安装 / 更新 Skills ━━━")
    src_dir = SCRIPT_DIR / "skills"
    SKILLS_DST.mkdir(parents=True, exist_ok=True)

    installed = []
    for skill_dir in sorted(src_dir.iterdir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        name = skill_dir.name
        dest = SKILLS_DST / name
        if dest.exists():
            shutil.rmtree(dest)
            shutil.copytree(skill_dir, dest)
            _ok(f"{name} — 已更新")
        else:
            shutil.copytree(skill_dir, dest)
            _ok(f"{name} — 已安装")
        installed.append(name)

    if "jira-fix" not in installed:
        _warn("未找到 jira-fix skill（/fix 将不可用）")
    print(f"  → skills 目录: {SKILLS_DST}")
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

    # 从 .env 补齐 JIRA_DIGEST_CRON / JIRA_DELIVER（不覆盖已有 export）
    for key, val in _load_env().items():
        if key in ("JIRA_DIGEST_CRON", "JIRA_DELIVER") and not os.environ.get(key):
            os.environ[key] = val

    cmd = HERMES_CMD or _find_hermes_cmd() or "hermes"
    try:
        r = subprocess.run([cmd, "cron", "list"], capture_output=True, text=True, timeout=10)
        if DIGEST_CRON_NAME in (r.stdout + r.stderr):
            _skip(f"{DIGEST_CRON_NAME} cron job 已存在")
            print("")
            return
    except Exception:
        pass

    if QUIET:
        try:
            schedule = parse_digest_schedule(os.environ.get("JIRA_DIGEST_CRON"))
        except ValueError:
            _err("JIRA_DIGEST_CRON 无效，请使用 '9:00' 或标准 cron（如 '0 9 * * *'）")
        deliver = _resolve_digest_deliver()
        print(f"  使用调度: {schedule}")
        if deliver:
            print(f"  投递目标: {deliver}")
        _create_digest_cron(schedule, deliver)
        print("")
        return

    print("  配置 Bug 日报 cron job（默认每天 09:00）")
    print("  可输入友好时间（如 9:00 / 09:30）或 cron（如 0 9 * * *）")
    while True:
        raw = input("  日报执行时间 [0 9 * * *]: ")
        try:
            schedule = parse_digest_schedule(raw)
            break
        except ValueError:
            print("  ⚠️  格式无效，请重试（例: 9:00 或 0 9 * * *）")
    deliver = _resolve_digest_deliver()
    print(f"  使用调度: {schedule}")
    print(f"  投递目标: {deliver or 'origin（默认）'}")
    _create_digest_cron(schedule, deliver)
    print("")

# ── Step 3.5: Cloudflare Pages（可选）───────────────────

def step_3_5_cloudflare():
    print("━━━ Step 3.5: Cloudflare Pages（HTML 日报） ━━━")

    creds = _load_env()
    cf_token = os.environ.get("CLOUDFLARE_API_TOKEN") or creds.get("CLOUDFLARE_API_TOKEN", "")
    cf_account = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or creds.get("CLOUDFLARE_ACCOUNT_ID", "")

    if cf_token and cf_account:
        _ok("Cloudflare 凭证已配置")
    else:
        print("  ℹ️  HTML 可视化日报需要 Cloudflare Pages（纯文本日报无需配置）")
        print(f"     如需启用，请在 {HERMES_ENV} 中添加：")
        print("")
        print("     CLOUDFLARE_API_TOKEN=your-cf-api-token")
        print("     CLOUDFLARE_ACCOUNT_ID=your-cf-account-id")
        print("")
        print("     Token 创建: https://dash.cloudflare.com/profile/api-tokens")
        print("     权限选 Account → Cloudflare Pages → Edit")
    print("")

# ── Step 3.6: Bitbucket + repos.json（/fix）────────────

def _repos_missing_v12_fields(repos_path: Path) -> list[str]:
    """Return human lines for project blocks missing models/review (v1.2)."""
    try:
        data = json.loads(repos_path.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"(无法解析 repos.json: {e})"]
    if not isinstance(data, dict):
        return ["(repos.json 根节点不是对象)"]
    lines: list[str] = []
    for key, proj in data.items():
        if str(key).startswith("_") or not isinstance(proj, dict):
            continue
        lack = [f for f in ("models", "review") if f not in proj]
        if lack:
            lines.append(f"{key}: 缺少 {', '.join(lack)}")
    return lines


def step_3_6_bitbucket():
    print("━━━ Step 3.6: Bitbucket + repos.json（/fix） ━━━")

    creds = _load_env()
    bb_user = os.environ.get("BITBUCKET_USERNAME") or creds.get("BITBUCKET_USERNAME", "")
    bb_pass = os.environ.get("BITBUCKET_APP_PASSWORD") or creds.get("BITBUCKET_APP_PASSWORD", "")

    if bb_user and bb_pass:
        _ok("Bitbucket 凭证已配置")
    elif QUIET:
        print("  ℹ️  /fix 建 PR 需预设 BITBUCKET_USERNAME + BITBUCKET_APP_PASSWORD")
        print("     见 config/env.template（App Password: Repositories Read + Pull requests Write）")
    else:
        print("  /fix 自动创建 Bitbucket PR 需要 App Password（回车可跳过）")
        print("  创建: Bitbucket → Personal settings → App passwords")
        print("  权限: Repositories Read + Pull requests Write")
        if not bb_user:
            val = input("  BITBUCKET_USERNAME: ").strip()
            if val:
                _save_env("BITBUCKET_USERNAME", val)
                bb_user = val
                _ok("BITBUCKET_USERNAME — 已保存")
        else:
            _ok("BITBUCKET_USERNAME — 已配置")
        if not bb_pass:
            val = getpass.getpass("  BITBUCKET_APP_PASSWORD: ").strip()
            if val:
                _save_env("BITBUCKET_APP_PASSWORD", val)
                bb_pass = val
                _ok("BITBUCKET_APP_PASSWORD — 已保存")
        else:
            _ok("BITBUCKET_APP_PASSWORD — 已配置")
        if not (bb_user and bb_pass):
            print("  ⏭️  已跳过 Bitbucket（之后可写入 Hermes .env）")

    # repos.json：本机映射（gitignore）；缺失则从 template 复制一份供编辑
    repos = SCRIPT_DIR / "config" / "repos.json"
    template = SCRIPT_DIR / "config" / "repos.template.json"
    if repos.is_file():
        _ok(f"repos.json 已存在: {repos}")
        lack = _repos_missing_v12_fields(repos)
        if lack:
            _warn("repos.json 建议对照 template 合并 v1.2 字段（不会自动覆盖本机 path）:")
            for line in lack:
                print(f"     - {line}")
            if template.is_file():
                print(f"     参考: {template}")
            print(
                '     示例: "models": {"claude":"sonnet","cursor":"grok"}, '
                '"review": {"enabled":true,"agent":"claude","model":"opus",'
                '"timeout_minutes":10,"on_infra_fail":"reject"}'
            )
    elif template.is_file():
        if QUIET:
            shutil.copy2(template, repos)
            _ok(f"已从 template 生成 repos.json（请编辑 path）: {repos}")
        else:
            ans = input("  尚未配置 repos.json，是否从 template 复制一份？ [Y/n]: ").strip().lower()
            if ans in ("", "y", "yes"):
                shutil.copy2(template, repos)
                _ok(f"已生成: {repos}")
                print("     请编辑其中的 path / workspace / repo 后再用 /fix")
            else:
                print(f"  ⏭️  跳过。需要时: copy {template} → {repos}")
    else:
        _warn("未找到 config/repos.template.json")
    print("")

# ── Step 4: verify ─────────────────────────────────────

def step_4_verify():
    print("━━━ Step 4: 验证连通性 ━━━")

    creds = _load_env()
    email = os.environ.get("JIRA_USER_EMAIL") or creds.get("JIRA_USER_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN") or creds.get("JIRA_API_TOKEN", "")
    cid   = os.environ.get("JIRA_CLOUD_ID") or creds.get("JIRA_CLOUD_ID", "")

    if not (email and token and cid):
        _skip("Jira：凭证未完全配置，跳过")
    else:
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

    bb_user = os.environ.get("BITBUCKET_USERNAME") or creds.get("BITBUCKET_USERNAME", "")
    bb_pass = os.environ.get("BITBUCKET_APP_PASSWORD") or creds.get("BITBUCKET_APP_PASSWORD", "")
    if bb_user and bb_pass:
        try:
            import urllib.request
            auth = base64.b64encode(f"{bb_user}:{bb_pass}".encode()).decode()
            req = urllib.request.Request(
                "https://api.bitbucket.org/2.0/user",
                headers={"Authorization": f"Basic {auth}", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            _ok(f"Bitbucket API 连通！用户: {data.get('display_name') or data.get('username') or 'OK'}")
        except Exception:
            _warn("Bitbucket API 验证失败，请检查 App Password 权限")
    else:
        _skip("Bitbucket：未配置，/fix 建 PR 将失败（可稍后补）")
    print("")

# ── done ───────────────────────────────────────────────

def done():
    print("  ╔══════════════════════════════════════════╗")
    print("  ║     ✅  安装完成！                        ║")
    print("  ╚══════════════════════════════════════════╝")
    print("")
    print("  功能与用法:")
    print("    📊 日报     cron 每天推送待办 Bug（jira-bug-digest）")
    print("    🔍 分析     /jira-analyze CG-1 CG-2   → 编号列表 + Jira 评论")
    print("    🔧 修复     /fix CG-xxx / 1,2 → Fix Agent（可选 --model）→ PR")
    print("    🔎 审查     repos.json review，或 --review / --no-review / 需要审查")
    print("    🧩 Overlay  /fix KEY overlay          → 强制产品线")
    print("")
    print("  /fix 依赖: Bitbucket 凭证 + config/repos.json + claude CLI")
    print("  v1.2: models + review 写在 repos.json（见 repos.template.json）")
    print("  HTML 日报: Cloudflare Pages（可选）")
    print(f"  详情: {SCRIPT_DIR / 'README.md'}")
    print("")

# ── main ───────────────────────────────────────────────

if __name__ == "__main__":
    banner()
    step_0_check()
    step_1_install_skills()
    step_2_credentials()
    step_3_cron()
    step_3_5_cloudflare()
    step_3_6_bitbucket()
    step_4_verify()
    done()
