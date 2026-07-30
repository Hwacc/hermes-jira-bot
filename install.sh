#!/usr/bin/env bash
# install.sh — Hermes Jira Bot 一键安装脚本
#
# 用法:
#   bash install.sh          # 交互式安装（推荐）
#   bash install.sh --quiet  # 静默安装（需要环境变量已配好）
#
# 安装内容:
#   1. 复制 skills (jira-analyze + jira-bug-digest) 到 ~/.hermes/skills/
#   2. 交互式配置 Jira 凭证（JIRA_API_TOKEN / JIRA_USER_EMAIL / JIRA_CLOUD_ID）
#   3. 创建推荐 cron job（早9 Bug 日报，使用 jira-bug-digest）
#   4. 验证 Jira API 连通性

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
QUIET=false
[[ "${1:-}" == "--quiet" ]] && QUIET=true

# ============================================================
# Platform helpers (Linux / macOS / WSL / Windows)
# ============================================================

_is_wsl() {
    grep -qi microsoft /proc/version 2>/dev/null
}

# Windows 用户主目录的 WSL/MSYS 路径（勿硬编码用户名）
_win_user_home() {
    local win_home=""
    if [ -n "${USERPROFILE:-}" ]; then
        if command -v wslpath &>/dev/null; then
            win_home=$(wslpath -u "$USERPROFILE" 2>/dev/null || true)
        elif [[ "$USERPROFILE" == [A-Za-z]:* ]]; then
            # Git Bash: C:\Users\foo -> /c/Users/foo
            win_home="/${USERPROFILE:0:1}/${USERPROFILE:3}"
            win_home="${win_home//\\//}"
        fi
    fi
    if [ -z "$win_home" ] && _is_wsl; then
        local d
        for d in /mnt/c/Users/*; do
            [ -d "$d/AppData/Local/hermes" ] && win_home="$d" && break
        done
    fi
    [ -n "$win_home" ] && printf '%s\n' "$win_home"
}

# 解析 HERMES_HOME：显式环境变量 > 本机 ~/.hermes > Windows AppData\Local\hermes
_resolve_hermes_home() {
    if [ -n "${HERMES_HOME:-}" ]; then
        printf '%s\n' "$HERMES_HOME"
        return
    fi
    if [ -d "$HOME/.hermes" ]; then
        printf '%s\n' "$HOME/.hermes"
        return
    fi
    if [ -d "$HOME/AppData/Local/hermes" ]; then
        printf '%s\n' "$HOME/AppData/Local/hermes"
        return
    fi
    local win_home
    win_home=$(_win_user_home || true)
    if [ -n "$win_home" ] && [ -d "$win_home/AppData/Local/hermes" ]; then
        printf '%s\n' "$win_home/AppData/Local/hermes"
        return
    fi
    # 默认值（用于报错提示）；Linux 官方布局
    printf '%s\n' "$HOME/.hermes"
}

HERMES_HOME="$(_resolve_hermes_home)"
HERMES_ENV="$HERMES_HOME/.env"

_load_jira_creds_from_env_file() {
    # 仅填充尚未设置的变量，避免覆盖已有 export / quiet 模式凭证
    [ -f "$HERMES_ENV" ] || return 0
    local key val
    while IFS='=' read -r key val || [ -n "$key" ]; do
        [[ "$key" =~ ^(JIRA_USER_EMAIL|JIRA_API_TOKEN|JIRA_CLOUD_ID)$ ]] || continue
        if [ -z "${!key:-}" ]; then
            export "$key=$val"
        fi
    done < "$HERMES_ENV"
}

_find_python() {
    local c
    for c in python3 python python3.exe python.exe; do
        if command -v "$c" &>/dev/null; then
            printf '%s\n' "$c"
            return 0
        fi
    done
    return 1
}

# ============================================================
# Banner
# ============================================================
echo ""
echo "  ╔══════════════════════════════════════════╗"
echo "  ║     🧭  Hermes Jira Bot Installer        ║"
echo "  ╚══════════════════════════════════════════╝"
echo ""

# ============================================================
# Step 0: Prerequisites
# ============================================================
echo "━━━ Step 0: 检查前置条件 ━━━"

# Check Hermes（多平台探测）
# Windows / WSL 上 CLI 通常是 hermes.exe（venv Scripts），不是无扩展名的 hermes。
_hermes_wrap_exe() {
    # 让后续脚本里的 `hermes ...` 在只有 hermes.exe 时也能用
    hermes() { command hermes.exe "$@"; }
    export -f hermes 2>/dev/null || true
}

_find_hermes() {
    if command -v hermes &>/dev/null; then
        return 0
    fi
    if command -v hermes.exe &>/dev/null; then
        _hermes_wrap_exe
        return 0
    fi

    # Linux / macOS 官方布局优先：~/.local/bin 符号链接、venv launcher、root FHS
    local candidates=(
        "$HOME/.local/bin/hermes"
        "$HOME/.hermes/hermes-agent/venv/bin/hermes"
        "/usr/local/bin/hermes"
        "$HOME/.hermes/bin/hermes"
        # Windows / Git Bash
        "$HOME/AppData/Local/hermes/hermes-agent/venv/Scripts/hermes.exe"
        "$HOME/AppData/Local/hermes/bin/hermes"
        "$HOME/AppData/Local/hermes/bin/hermes.exe"
        "$HOME/.hermes/bin/hermes.exe"
    )

    local win_home
    win_home=$(_win_user_home || true)
    if [ -n "$win_home" ]; then
        candidates+=(
            "$win_home/AppData/Local/hermes/hermes-agent/venv/Scripts/hermes.exe"
            "$win_home/AppData/Local/hermes/bin/hermes"
            "$win_home/AppData/Local/hermes/bin/hermes.exe"
            "$win_home/.hermes/bin/hermes"
            "$win_home/.hermes/bin/hermes.exe"
        )
    fi

    local p
    for p in "${candidates[@]}"; do
        if [ -f "$p" ] || [ -L "$p" ]; then
            export PATH="$(dirname "$p"):$PATH"
            if [[ "$p" == *.exe ]] && ! command -v hermes &>/dev/null; then
                _hermes_wrap_exe
            fi
            return 0
        fi
    done
    return 1
}

if _find_hermes; then
    echo "  ✅ Hermes CLI 已安装: $(hermes --version 2>&1 | head -1)"
else
    echo "  ❌ Hermes CLI 未找到，请先安装 Hermes Agent"
    echo "     curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"
    exit 1
fi

# Check Python（WSL 上可能只有 python.exe）
if PY=$(_find_python); then
    echo "  ✅ Python 可用 ($PY)"
else
    echo "  ❌ Python 未找到"
    exit 1
fi

# Check HERMES_HOME
if [ ! -d "$HERMES_HOME" ]; then
    echo "  ❌ Hermes 目录未找到: $HERMES_HOME"
    echo "     请先运行 hermes 一次以初始化配置"
    exit 1
fi
echo "  ✅ Hermes 目录: $HERMES_HOME"
echo ""

# ============================================================
# Step 1: Install Skills
# ============================================================
echo "━━━ Step 1: 安装 Skills ━━━"

SKILL_DIR="$HERMES_HOME/skills"
mkdir -p "$SKILL_DIR"

for skill in "$SCRIPT_DIR/skills/"*; do
    if [ -f "$skill/SKILL.md" ]; then
        name=$(basename "$skill")
        dest="$SKILL_DIR/$name"
        if [ -d "$dest" ]; then
            echo "  ⏭️  $name — 已存在，跳过"
        else
            cp -r "$skill" "$dest"
            echo "  ✅ $name — 已安装"
        fi
    fi
done
echo ""

# ============================================================
# Step 2: Configure Credentials
# ============================================================
echo "━━━ Step 2: 配置 Jira 凭证 ━━━"

if [ "$QUIET" = true ]; then
    # Quiet mode: check env is already set
    if [ -n "${JIRA_USER_EMAIL:-}" ] && [ -n "${JIRA_API_TOKEN:-}" ] && [ -n "${JIRA_CLOUD_ID:-}" ]; then
        echo "  ✅ 环境变量已设置（静默模式）"
    else
        echo "  ❌ 静默模式需要预先设置 JIRA_USER_EMAIL、JIRA_API_TOKEN、JIRA_CLOUD_ID"
        exit 1
    fi
else
    # Interactive mode：.env 已有，或 shell 环境变量已有，都算配置过
    for var in JIRA_USER_EMAIL JIRA_API_TOKEN JIRA_CLOUD_ID; do
        current=$(grep "^${var}=" "$HERMES_ENV" 2>/dev/null | cut -d= -f2- || echo "")
        if [ -z "$current" ] && [ -n "${!var:-}" ]; then
            current="${!var}"
            # 持久化到 .env，方便后续 Hermes 进程读取
            echo "${var}=${current}" >> "$HERMES_ENV"
        fi
        if [ -n "$current" ]; then
            echo "  ✅ $var — 已配置"
        else
            case $var in
                JIRA_USER_EMAIL)
                    read -r -p "  请输入你的 Atlassian 邮箱: " val
                    ;;
                JIRA_API_TOKEN)
                    read -r -s -p "  请输入 Jira API Token (https://id.atlassian.com/manage-profile/security/api-tokens): " val
                    echo ""
                    ;;
                JIRA_CLOUD_ID)
                    read -r -p "  请输入 Jira Cloud ID (https://<site>.atlassian.net/secure/admin/cloudid): " val
                    ;;
            esac
            if [ -n "$val" ]; then
                echo "${var}=${val}" >> "$HERMES_ENV"
                echo "  ✅ $var — 已保存"
            fi
        fi
    done
fi
echo ""

# ============================================================
# Step 3: Create Cron Job
# ============================================================
echo "━━━ Step 3: 创建 Cron Job ━━━"

CRON_JOBS=$(hermes cron list 2>/dev/null || echo "")
if echo "$CRON_JOBS" | grep -q "jira-bug-daily-digest"; then
    echo "  ⏭️  jira-bug-daily-digest cron job 已存在"
else
    echo "  配置 Bug 日报 cron job (推荐)..."
    echo ""
    echo "  ℹ️  Cron job 需手动创建："
    echo ""
    echo "     hermes cron create '0 9 * * *' --skills jira-bug-digest \\"
    echo "       --prompt '运行 jira_report.py 生成 HTML 日报'"
    echo ""
    echo "     或在 Hermes 对话中输入："
    echo "     「帮我创建一个 Jira Bug 日报 cron job，每天 9:00 执行」"
    echo ""
    echo "  📄 Cron 模板参考: cron/jobs.template.json"
fi
echo ""

# ============================================================
# Step 4: Verify Connectivity
# ============================================================
echo "━━━ Step 4: 验证 Jira API 连通性 ━━━"

# 先从 .env 补齐凭证（交互安装只写入文件、未必 export）
_load_jira_creds_from_env_file

if [ -n "${JIRA_API_TOKEN:-}" ] && [ -n "${JIRA_CLOUD_ID:-}" ] && [ -n "${JIRA_USER_EMAIL:-}" ]; then
    if ! PY=$(_find_python); then
        echo "  ⏭️  跳过（未找到 Python）"
    else
        VERIFY_OUT=$("$PY" -c "
import os,json,base64,urllib.request
email=os.environ.get('JIRA_USER_EMAIL','')
token=os.environ.get('JIRA_API_TOKEN','')
cid=os.environ.get('JIRA_CLOUD_ID','')
auth=base64.b64encode(f'{email}:{token}'.encode()).decode()
req=urllib.request.Request(f'https://api.atlassian.com/ex/jira/{cid}/rest/api/3/myself',headers={'Authorization':f'Basic {auth}','Accept':'application/json'})
with urllib.request.urlopen(req) as r:
    d=json.loads(r.read())
print(d.get('displayName','OK'))
" 2>&1) || VERIFY_OUT="FAILED"

        if [ "$VERIFY_OUT" = "FAILED" ]; then
            echo "  ❌ Jira API 验证失败，请检查凭证是否正确"
        else
            echo "  ✅ Jira API 连通！已认证用户: $VERIFY_OUT"
        fi
    fi
else
    echo "  ⏭️  跳过（凭证未完全配置）"
fi
echo ""

# ============================================================
# Done
# ============================================================
echo "  ╔══════════════════════════════════════════╗"
echo "  ║     ✅  安装完成！                        ║"
echo "  ╚══════════════════════════════════════════╝"
echo ""
echo "  使用方法:"
echo "    /jira-analyze CG-12345    分析指定 Bug"
echo "    分析bug CG-12345           同上"
echo ""
echo "  Cron 日报创建后将在每天 9:00 自动推送。"
echo "  详情参考: $SCRIPT_DIR/README.md"
echo ""
