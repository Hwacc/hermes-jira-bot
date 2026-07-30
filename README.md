# 🧭 Hermes Jira Bot

> 基于 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的 Jira 自动化助手。
> 安装后即可在 Hermes 对话中分析 Bug、每日自动推送待办日报。

## ✨ 功能

| 功能 | 触发方式 | 说明 |
|------|---------|------|
| 📊 **Bug 日报** | Cron（早 9） | 自动推送待办 Bug 汇总，使用 `jira-bug-digest` skill |
| 🔍 **Bug 分析** | `/jira-analyze CG-xxx` | LLM 分析难度/工时/根因，回帖到 Jira，使用 `jira-analyze` skill |
| 🧪 **一键安装** | `bash install.sh` | 自动安装双 skill + 配置凭证 |

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
```

安装脚本会：
1. 复制 skills 到 `~/.hermes/skills/`
2. 交互式引导配置 Jira 凭证
3. 验证 Jira API 连通性
4. 提示创建 cron 日报任务

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
```

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

### Bug 日报

创建 cron job（每天 9:00 自动推送）：

```bash
hermes cron create "0 9 * * *"                                             \
  --skills jira-bug-digest                                                 \
  --prompt "运行 fetch_digest.py 生成并输出日报"
```

## 📁 项目结构

```
hermes-jira-bot/
├── README.md
├── install.sh                       # 一键安装脚本
├── skills/
│   ├── jira-analyze/                # Bug 分析 skill
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       ├── jira_analyze.py      # Jira API 客户端
│   │       └── setup.sh             # 凭证配置向导
│   └── jira-bug-digest/             # 日报 skill
│       ├── SKILL.md
│       └── scripts/
│           └── fetch_digest.py      # JQL 查询 + 格式化日报
├── config/
│   └── env.template                 # 环境变量模板
└── cron/
    └── jobs.template.json           # Cron job 配置参考
```

## 🔧 环境变量

| 变量 | 说明 | 获取 |
|------|------|------|
| `JIRA_USER_EMAIL` | Atlassian 邮箱 | — |
| `JIRA_API_TOKEN` | API Token（1年有效） | [生成 Token](https://id.atlassian.com/manage-profile/security/api-tokens) |
| `JIRA_CLOUD_ID` | Jira Cloud 实例 ID | [查看 Cloud ID](https://admin.atlassian.com) |

## 🗺️ 路线图

- [x] Bug 分析 + 回帖 (`/jira-analyze`)
- [x] 待办 Bug 日报 (cron)
- [x] 一键安装脚本
- [ ] 桌面 TodoList 小组件
- [ ] Claude Code / Copilot CLI 联动（AI 自动修 Bug）
- [ ] 多 Jira Site 支持

## 📄 License

MIT
