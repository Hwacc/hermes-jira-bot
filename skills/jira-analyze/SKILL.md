---
name: jira-analyze
description: "处理 /jira-analyze 指令——拉取 Jira Bug 详情、LLM 分析难度/工时/根因、生成格式化回复并回帖到 Jira 评论。触发词：/jira-analyze"
triggers:
  - "/jira-analyze"
  - "jira-analyze"
  - "分析bug"
  - "分析 Bug"
  - "bug分析"
---

# Jira Bug 分析处理器

当用户发送 `/jira-analyze KEY1 KEY2 ...` 时，执行以下流程。

## 前置条件

需要以下环境变量（存在 Hermes `.env` 中自动注入）：

| 变量 | 说明 | 获取方式 |
|------|------|---------|
| `JIRA_USER_EMAIL` | Atlassian 账号邮箱 | 你的登录邮箱 |
| `JIRA_API_TOKEN` | API Token（1年有效） | https://id.atlassian.com/manage-profile/security/api-tokens |
| `JIRA_CLOUD_ID` | Jira Cloud 实例 ID | https://<site>.atlassian.net/secure/admin/cloudid |

## Step 1：拉取 Bug 详情

使用 skill 自带的脚本：

```
python "{skillDir}/scripts/jira_analyze.py" KEY1 KEY2 KEY3 ...
```

输出是 JSON 数组，每个 bug 包含：key, summary, description, priority, status, created, reporter, project, comments。

## Step 2：分析每个 Bug

对每个 Bug 进行以下维度分析：

| 维度 | 分析方式 | 输出 |
|------|---------|------|
| ⭐ 难度 | 基于描述复杂度、涉及范围、复现难度 | 1-5 星 |
| ⏱️ 预计工时 | 基于问题类型和描述 | 如 1h / 2-4h / 1d |
| 🔍 根因分析 | 基于描述推断可能原因 | 一句话 |
| 💡 修复建议 | 基于分析给出方向 | 一句话（可选） |

## Step 3：生成回复并回帖到 Jira

对每个 Bug，用 Jira REST API (Basic Auth) 添加评论：

```
POST https://api.atlassian.com/ex/jira/{JIRA_CLOUD_ID}/rest/api/3/issue/{KEY}/comment
Authorization: Basic base64({JIRA_USER_EMAIL}:{JIRA_API_TOKEN})
Content-Type: application/json

{
  "body": {
    "type": "doc",
    "version": 1,
    "content": [{
      "type": "paragraph",
      "content": [{"type": "text", "text": "回复内容"}]
    }]
  }
}
```

## 回复格式

```
收到 🎯
⭐ 难度: ★★★☆☆  ⏱ 预计: 2-4h
🔍 根因: xxx
💡 建议: xxx
❤ 来自 {用户名} 的 Hermes Jira Bot
```

## 认证

Jira 查询和评论回帖均使用 **Jira API Token** (Basic Auth)，通过环境变量注入。Token 有效期 1 年，无需刷新流程。首次使用前运行 `scripts/setup.sh` 引导配置。

## 回复给用户

分析完成后，回复一个简洁的汇总：

```
✅ 已完成 N 个 Bug 分析：
  CG-xxx ⭐★★★☆☆ ⏱2h
  CG-yyy ⭐★★☆☆☆ ⏱1h
详情见 Jira 评论
```

## 可移植性

本 skill 所有路径均为相对路径，脚本自带。安装后只需配好 3 个环境变量即可在任何 Hermes 环境使用。
