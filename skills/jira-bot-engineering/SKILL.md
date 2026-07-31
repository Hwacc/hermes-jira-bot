---
name: jira-bot-engineering
description: "Jira Bot 工程化——从零搭建可移植的 Hermes Jira 自动化助手。覆盖 Skill 设计、认证选型、Cron 配置、项目打包和发布。"
---

# Jira Bot 工程化

将 Jira 自动化能力打包为可安装、可移植的 Hermes Bot 项目。

## 架构概览

```
hermes-jira-bot/
├── install.sh / install.py       # 一键安装
├── skills/
│   ├── jira-analyze/            # Bug 分析 + 回帖 + fix_session（v2）
│   ├── jira-bug-digest/         # 定时日报
│   ├── jira-code-fix/           # /fix 自动修复流水线
│   └── jira-fix/                # fix session 管理（编号映射 + TTL）
├── config/env.template
├── config/repos.template.json
├── cron/jobs.template.json
└── README.md
```

## 认证选型（核心决策）

### ❌ OAuth PKCE + mcporter — 不适合自动化

在 cron/headless 环境中的致命缺陷：
- Access Token 1h 过期 → Refresh 不稳定 → 回退浏览器 OAuth
- 无浏览器 → 60s 超时 → cron 执行卡死
- 每次 token 过期需手动桌面终端干预

### ✅ API Token (Basic Auth) — 唯一可靠方案

```env
JIRA_USER_EMAIL=xxx@example.com
JIRA_API_TOKEN=your-token
JIRA_CLOUD_ID=your-cloud-id
```

- 1 年有效期，无需刷新
- 纯 HTTP Basic Auth，无浏览器依赖
- 获取：https://id.atlassian.com/manage-profile/security/api-tokens

## Skill 设计原则

### jira-analyze — 交互式 Bug 分析

- 触发：`/jira-analyze KEY` 或自然语言 "分析bug"
- 流程：拉取详情 → LLM 分析（难度/工时/根因） → 格式化回帖 Jira
- 脚本：`scripts/jira_analyze.py`（自包含，便携）

### jira-bug-digest — 定时日报

- 触发：cron job 驱动（非用户对话触发）
- 流程：JQL 查询 → `scripts/fetch_digest.py` 格式化 → Hermes 投递
- 紧凑模式：>10 个 bug 时只展示高优详情 + 其余 key 列表

### jira-code-fix — 自动修复流水线

- 触发：`/fix CG-xxx` 或 `/fix 1,2`（编号引用来自 jira-analyze 的 fix_session）
- 流程：Jira ticket → repos.json 映射 → git worktree → AI agent（claude / cursor）→ git commit → PR（Bitbucket/GitHub）→ Jira 回帖
- Review Gate 推二期，PoC 验证通过直接建 PR
- 详见 `jira-code-fix` skill

### jira-fix — Fix Session 管理

- 为 `jira-analyze` → `/fix 1,2` 链路提供编号映射
- `scripts/fix_session.py`：保存/读取/清理编号 session，TTL 30 分钟
- 被 `jira-analyze` 和 `jira-code-fix` 依赖

## Cron Job 配置

```bash
hermes cron create "0 9,18 * * *" \
  --skill jira-bug-digest \
  --prompt "运行 fetch_digest.py 生成并输出日报"
```

## 踩坑记录

### `gh auth login --web` 在 Gateway 中超时

QQ Bot / 消息平台等 headless gateway 环境下，`gh auth login --web` 会卡在等待浏览器授权步骤超时。应改用 `gh auth login --with-token` 或让用户在桌面终端手动执行。

### Enterprise Managed User 限制

GitHub Enterprise Managed User 账号不能创建 Public repo，只能用 Private。需要用个人 GitHub 账号发布公开项目。

### `execute_code` 在 Cron 中被拦截

cron 运行时 `execute_code` 会被 blocked（安全策略），应使用 `terminal` 调 Python 脚本替代。

### GitHub 仓库同步滞后

GitHub `Hwacc/hermes-jira-bot` 目前只含 `jira-analyze`（v1，无 fix_session）和 `jira-bug-digest`。以下内容仅存在于本地 `~/AppData/Local/hermes/skills/`：
- `jira-code-fix` — `/fix` 自动修复流水线
- `jira-fix` — fix session 管理（`scripts/fix_session.py`）
- `jira-bot-engineering` — 本 skill 自身
- `jira-analyze` v2 — 含 fix_session 集成、`/fix 1,2` 编号引用

推送前确认：本地领先的变更是否要合入 GitHub，还是 GitHub 作为稳定发布版、本地作为开发版。

## 项目发布 Checklist

- [ ] Skill 脚本自包含在 `scripts/` 目录
- [ ] 所有路径使用 `{skillDir}` 相对引用
- [ ] 凭证通过环境变量注入，无硬编码
- [ ] `install.sh` 覆盖安装 + 配置 + 验证全流程
- [ ] `env.template` 提供配置模板
- [ ] `README.md` 包含安装/使用/结构说明
