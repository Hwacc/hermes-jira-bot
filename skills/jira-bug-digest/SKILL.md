---
name: jira-bug-digest
description: "定时推送 Jira 工作日报——查询所有类型待办，生成 HTML 可视化报表并部署到 Cloudflare Pages，推送链接到消息平台。配合 cron job 使用。"
---

# Jira Bug 日报

查询分配给当前用户的工作项（Bug / Task / Improvement），生成可视化 HTML 报表并部署到 Cloudflare Pages，推送报告链接。

## 触发方式

本 skill 设计为 **cron job 驱动**，不响应用户对话触发。

## 依赖

与 `jira-analyze` 共享凭证。HTML 报表部署需要额外配置：

| 变量 | 说明 |
|------|------|
| `JIRA_USER_EMAIL` | Atlassian 邮箱 |
| `JIRA_API_TOKEN` | API Token（1年有效） |
| `JIRA_CLOUD_ID` | Jira Cloud 实例 ID |
| `JIRA_ASSIGNEE` | （可选）Account ID 过滤，默认 currentUser() |
| `JIRA_SITE_URL` | （可选）Jira 站点 URL |
| `JIRA_USER_DISPLAY` | （可选）报表中的显示名 |
| `CLOUDFLARE_ACCOUNT_ID` | （可选）Cloudflare account ID，跳过自动发现 |

## 两种模式

### 模式 A：HTML 可视化报表（推荐）

```
cron 触发 → jira_report.py → jira_query.py 查询 → HTML → Cloudflare Pages → 推送链接
```

脚本：`{skillDir}/scripts/jira_report.py`

**特性**：
- 暗色/亮色模式自动切换 + 手动开关
- 待办 / 进行中 Tab 切换
- 近一周时间筛选
- Bug / Task / Improvement 类型筛选
- Checkbox 勾选 → Ready To Do 一键复制 `/jira-analyze` 命令
- 移动端响应式
- 剪贴板复制时随机激励语

**Cron 配置**：

```
schedule: 0 9 * * *
skills: ["jira-bug-digest"]
prompt: |
  python "{skillDir}/scripts/jira_report.py"
  解析输出：
  - NO_BUGS → 回复 "✅ 没有待办"
  - PAGES_URL|<url>|<count> → 格式化日报 + 链接
```

### 模式 B：纯文本摘要

脚本：`{skillDir}/scripts/fetch_digest.py`

简单文本输出，适合不需要可视化报表的场景。查询 Bug 类型每日待办，输出格式化文本。

## 输出

HTML 报表示例：https://master.hermes-jira-bot.pages.dev

## 可移植性

- 所有凭证通过环境变量注入
- 脚本自包含在 `scripts/` 目录
- HTML 模板内嵌，无外部依赖
- Cloudflare Pages 项目名：`hermes-jira-bot`
