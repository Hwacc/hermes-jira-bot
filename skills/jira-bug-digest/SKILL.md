---
name: jira-bug-digest
description: "定时推送 Jira 待办 Bug 日报——查询分配给当前用户的待办 Bug，生成格式化报告并通过 Hermes 投递到消息平台。配合 cron job 使用。"
---

# Jira Bug 日报

查询分配给当前用户、处于「待办」状态的 Bug，生成格式化日报。

## 触发方式

本 skill 设计为 **cron job 驱动**，不响应用户对话触发。通过 Hermes cron 调度器定时执行。

## 依赖

与 `jira-analyze` 共享同一套凭证：

| 变量 | 说明 |
|------|------|
| `JIRA_USER_EMAIL` | Atlassian 邮箱 |
| `JIRA_API_TOKEN` | API Token |
| `JIRA_CLOUD_ID` | Jira Cloud 实例 ID |
| `JIRA_SITE_URL` | Jira 站点 URL（可选，默认 https://razersw.atlassian.net） |

## 执行流程

```
cron 触发 → fetch_digest.py 查询 Jira → 格式化输出 → Hermes 投递到消息平台
```

### 脚本调用

```
python "{skillDir}/scripts/fetch_digest.py"
```

- 查询 JQL：`issuetype = Bug AND assignee = currentUser() AND statusCategory = 'To Do'`
- 如果没有待办 Bug，输出 `✅ 没有待办 Bug`
- 如果有，输出格式化日报

## 输出格式

```
📊 Jira 待办 Bug · 2026-07-30 09:00
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🐛 总数 13 · ⚠️ 高优 1

⚠️ 高优先级
  CG-5624 UI前端需要打印关键日志 (2019-12-29)

其余
  CG-20857 drag点击scan没起作用 (2026-07-20)
  ...

🔗 https://razersw.atlassian.net
```

总数 > 10 个时自动切换为紧凑模式（高优详情 + 其余 Key 列表）。

## Cron 配置

推荐在 Hermes 中创建 cron job：

```
schedule: 0 9,18 * * *
skills: ["jira-bug-digest"]
prompt: 运行 fetch_digest.py 并输出日报
```

## 可移植性

- 所有凭证通过环境变量注入，无硬编码
- 脚本自包含在 `scripts/` 目录
- 与 `jira-analyze` 共享凭证配置（只需配一次）
