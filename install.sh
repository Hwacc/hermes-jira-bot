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
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_ENV="$HERMES_HOME/.env"
QUIET=false
[[ "${1:-}" == "--quiet" ]] && QUIET=true

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
_find_hermes() {
    # WSL: Windows 侧路径
    if grep -qi microsoft /proc/version 2>/dev/null; then
        for p in "/mnt/c/Users/chuancheng.hua/AppData/Local/hermes/bin" \
                 "/mnt/c/Users/chuancheng.hua/.hermes/bin"; do
            [ -x "$p/hermes" ] && export PATH="$p:$PATH" && return 0
        done
    fi
    # 标准路径
    for p in "$HOME/AppData/Local/hermes/bin" "$HOME/.hermes/bin"; do
        [ -x "$p/hermes" ] && export PATH="$p:$PATH" && return 0
    done
    command -v hermes &>/dev/null
}
HERMES_PRE_PATH="$PATH"
if _find_hermes; then
    echo "  ✅ Hermes CLI 已安装: $(hermes --version 2>&1 | head -1)"
else
    echo "  ❌ Hermes CLI 未找到，请先安装 Hermes Agent"
    echo "     curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"
    exit 1
fi

# Check Python
if command -v python3 &>/dev/null || command -v python &>/dev/null; then
    echo "  ✅ Python 可用"
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
    # Interactive mode
    for var in JIRA_USER_EMAIL JIRA_API_TOKEN JIRA_CLOUD_ID; do
        current=$(grep "^${var}=" "$HERMES_ENV" 2>/dev/null | cut -d= -f2- || echo "")
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

if [ -n "${JIRA_API_TOKEN:-}" ] && [ -n "${JIRA_CLOUD_ID:-}" ] && [ -n "${JIRA_USER_EMAIL:-}" ]; then
    # Reload from .env for verification
    source_env() {
        while IFS='=' read -r key val; do
            [[ "$key" =~ ^(JIRA_USER_EMAIL|JIRA_API_TOKEN|JIRA_CLOUD_ID)$ ]] && export "$key=$val"
        done < "$HERMES_ENV"
    }
    source_env

    if command -v python3 &>/dev/null; then
        PY=python3
    else
        PY=python
    fi

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
