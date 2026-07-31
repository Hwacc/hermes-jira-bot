# 🧭 Hermes Jira Bot

> 基于 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的 Jira 自动化助手。  
> **v1.1.0** — Bug 日报 / 分析 / `/fix` PR，以及 Bitbucket **merged + declined** Webhook 闭环（Jira 评论 + QQ）。

## ✨ 功能

| 功能 | 触发方式 | 说明 |
|------|---------|------|
| 📊 **Bug 日报** | Cron（早 9） | 自动推送待办 Bug 汇总，使用 `jira-bug-digest` skill |
| 🔍 **Bug 分析** | `/jira-analyze CG-xxx` | LLM 分析难度/工时/根因，回帖到 Jira，使用 `jira-analyze` skill |
| 🔧 **自动修复（PoC）** | `/fix CG-xxx` | 编排层：worktree → Claude/Cursor → push → Bitbucket PR，使用 `jira-fix` skill |
| 🔁 **PR 合入/拒绝反馈** | Bitbucket Webhook | Tunnel → 适配层 → Hermes：`fulfilled` / `rejected` → Jira 评论 + QQ（不做 approve） |
| 🧪 **一键安装** | `bash install.sh` | 自动安装 skills + 配置凭证 |

## 📦 安装

### 前置条件

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) 已安装
- [Jira API Token](https://id.atlassian.com/manage-profile/security/api-tokens)（1 年有效，无需刷新）

### 一键安装

```bash
git clone https://github.com/Hwacc/hermes-jira-bot.git
cd hermes-jira-bot

# Linux / macOS / git-bash
bash install.sh

# Windows / 跨平台
python install.py

# 静默安装（预设环境变量）
python install.py --quiet
# 可选: JIRA_DIGEST_CRON='9:00' 或 '0 9 * * *'
# 可选: JIRA_DELIVER='qqbot:<chat_id>'（默认 origin）
```

安装 / 升级脚本会：
1. **安装或覆盖更新** skills（含 `jira-analyze` / `jira-bug-digest` / `jira-fix`）
2. 交互式引导配置 Jira 凭证
3. 询问日报执行时间与投递目标，并创建 cron job
4. 提示 Cloudflare Pages（HTML 日报可选）
5. 配置 Bitbucket（`/fix` 建 PR）并引导生成 `config/repos.json`
6. 验证 Jira / Bitbucket API 连通性；检测 `claude` CLI

静默安装需预设 `JIRA_USER_EMAIL` / `JIRA_API_TOKEN` / `JIRA_CLOUD_ID`；可用 `JIRA_DIGEST_CRON` 定制调度、`JIRA_DELIVER` 定制投递。

### 手动配置

如果不想用交互式安装，手动在 `~/.hermes/.env` 中添加：

```env
JIRA_USER_EMAIL=your-email@example.com
JIRA_API_TOKEN=your-jira-api-token
JIRA_CLOUD_ID=your-cloud-instance-id
```

然后复制 skills：

```bash
cp -r skills/jira-analyze ~/.hermes/skills/
cp -r skills/jira-bug-digest ~/.hermes/skills/
cp -r skills/jira-fix ~/.hermes/skills/
cp -r skills/jira-code-fix ~/.hermes/skills/
cp -r skills/jira-bot-engineering ~/.hermes/skills/
```

`/fix` 另需：本机 `config/repos.json`、Bitbucket App Password（见 `config/env.template`）、已登录的 `claude` CLI。

## 🚀 使用

### Bug 分析

在 Hermes 对话中直接使用：

```
/jira-analyze CG-12345               # 分析单个 Bug
分析bug CG-12345 CG-12346             # 分析多个 Bug
```

分析完成后自动回帖到 Jira，格式如下：

```
收到 🎯
⭐ 难度: ★★☆☆☆  ⏱ 预计: 2-4h
🔍 根因: UI实现与设计稿不一致
💡 建议: 对照设计稿逐项对比调整CSS
❤ 来自 Hwacc 的 Hermes Jira Bot
```

### 自动修复（编号 + 批量）

```
/jira-analyze CG-1 CG-2        # 分析并写入编号 session（30 分钟）
1                              # 或 /fix 1  /fix 1,2
/fix CG-12345                 # 显式 KEY
/fix CG-12345 overlay         # 强制 Overlay
/fix 1 2                      # 批量串行
```

本地脚本：

```bash
python skills/jira-fix/scripts/jira_fix.py 1 --resolve-only    # 只解析编号
python skills/jira-fix/scripts/jira_fix.py CG-12345 --dry-run  # 只看映射
python skills/jira-fix/scripts/fix_session.py show             # 查看 session
```

### PR 合入反馈（Bitbucket Webhook）

推荐：**Cloudflare Tunnel → 适配层 → Hermes webhook**（详见 `webhook/README.md`）。  
适配层只验签/转发；**Jira 评论由 Hermes** 跑 `pr_lifecycle.py`。

```bash
# 1) Hermes webhook：skills=jira-fix，deliver=qqbot（勿用 deliver_only）
# 2) 适配层监听本机
python webhook/bitbucket_adapter.py --host 127.0.0.1 --port 8787
# 3) Tunnel 暴露适配层
cloudflared tunnel --url http://127.0.0.1:8787
# 4) Bitbucket Webhook → https://<tunnel-host>/bitbucket
#    Triggers: Pull request merged + Pull request declined
```

### Bug 日报

安装时会引导创建 cron（默认每天 09:00）。也可手动创建：

```bash
hermes cron create "0 9 * * *" \
  --name jira-bug-daily-digest \
  --skill jira-bug-digest \
  --deliver "qqbot:<chat_id>" \
  --prompt "运行 jira_report.py 生成 HTML 日报"
```

已存在同名 job 时安装脚本会跳过；改时间/投递请用 `hermes cron edit`。
## 📁 项目结构

```
hermes-jira-bot/
├── README.md
├── VERSION
├── install.sh / install.py             # 一键安装脚本
├── skills/
│   ├── jira-analyze/                   # Bug 分析（难度/工时/根因）
│   ├── jira-bug-digest/                # 定时日报
│   ├── jira-fix/                       # /fix 编排（repos 解析 + worktree + PR）
│   ├── jira-code-fix/                  # /fix 流程定义（skill 层面）
│   └── jira-bot-engineering/           # 工程化规范
├── webhook/
│   ├── bitbucket_adapter.py            # PR merged/declined → 转发 Hermes（Hermes 写 Jira + QQ）
│   └── README.md
├── config/
│   ├── env.template                    # 环境变量模板
│   ├── repos.template.json             # Ticket→仓库映射模板（可提交）
│   └── repos.json                      # 本机映射（gitignore，从 template 复制）
└── cron/
    └── jobs.template.json              # Cron job 配置参考
```

### Ticket → 仓库映射（Claude Code /fix 用）

```bash
cp config/repos.template.json config/repos.json
# 编辑 repos.json：填 path / branch / Bitbucket workspace·repo
```

按 Jira `project.key` 解析。可用 `overrides[]` 按 summary 正则分流产品（如 Overlay）；再用 `versions` / `version_lines` / `branch_pattern`。详见项目状态笔记。
## 🔧 环境变量

| 变量 | 说明 | 获取 |
|------|------|------|
| `JIRA_USER_EMAIL` | Atlassian 邮箱 | — |
| `JIRA_API_TOKEN` | API Token（1年有效） | [生成 Token](https://id.atlassian.com/manage-profile/security/api-tokens) |
| `JIRA_CLOUD_ID` | Jira Cloud 实例 ID | [查看 Cloud ID](https://admin.atlassian.com) |
| `JIRA_DIGEST_CRON` | （可选）日报调度，如 `9:00` 或 `0 9 * * *` | 安装交互 / quiet 环境变量 |
| `JIRA_DELIVER` | （可选）日报投递目标，如 `qqbot:<id>`；默认 origin | Hermes `--deliver` |
| `CLOUDFLARE_API_TOKEN` | （可选）HTML 日报 Pages 部署 | [API Tokens](https://dash.cloudflare.com/profile/api-tokens) |
| `CLOUDFLARE_ACCOUNT_ID` | （可选）Cloudflare Account ID | Cloudflare Dashboard |
| `BITBUCKET_USERNAME` | （`/fix`）Bitbucket 用户名 | Bitbucket 账号 |
| `BITBUCKET_APP_PASSWORD` | （`/fix`）App Password（PR Write） | Bitbucket → App passwords |
| `BITBUCKET_WEBHOOK_SECRET` | （合入反馈）仓库 Webhook 共享密钥 | Bitbucket Webhook 配置 |
| `HERMES_WEBHOOK_URL` | （合入反馈）Hermes QQ 路由 URL | Hermes webhook subscribe |
| `HERMES_WEBHOOK_SECRET` | （合入反馈）与 Hermes 路由 secret 一致 | 同上 |

## 🗺️ 路线图

- [x] Bug 分析 + 回帖 (`/jira-analyze`)
- [x] 待办 Bug 日报 (cron)
- [x] 一键安装脚本
- [x] `/fix` 单票编排 PoC（worktree → agent → Bitbucket PR）
- [x] `/fix` 编号 session（TTL 30m）+ 批量串行
- [x] Bitbucket `fulfilled` → 适配层转发 Hermes → Jira 评论 + QQ
- [x] Bitbucket `rejected`（Declined，含 reason）→ 同上
- [x] ~~Webhook：approve~~（不做：与 merged 反馈重复）
- [ ] Review Gate
- [ ] 桌面 TodoList 小组件

## 📄 License

MIT
