---
name: jira-analyze
description: "处理 /jira-analyze 指令——拉取 Jira Bug 详情、LLM 分析难度/工时/根因、回帖 Jira，并写入编号 session 供 /fix 1,2 使用。触发词：/jira-analyze"
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
| `JIRA_CLOUD_ID` | Jira Cloud 实例 ID | https://\<site\>.atlassian.net/secure/admin/cloudid |

## Step 1：拉取 Bug 详情（并保存编号 session）

```
python "{skillDir}/scripts/jira_analyze.py" KEY1 KEY2 KEY3 ...
```

脚本会：

1. 拉取每个 Bug 的 JSON  
2. **默认**写入 fix 编号 session（供随后 `/fix 1` / `1,2`），TTL **30 分钟**  
3. 输出中带 `fix_session.hint`（若保存成功）

不需要 session 时加 `--no-fix-session`。

输出字段：`bugs` 数组（或旧版纯数组）+ 可选 `fix_session`。

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

## 回复格式（用户侧）

每个 Bug 分析可按原格式回帖 Jira。对用户的汇总**必须带编号**，并提示可 `/fix`：

```
✅ 已完成 N 个 Bug 分析：
  1. CG-xxx ⭐★★★☆☆ ⏱2h — 一句话根因
  2. CG-yyy ⭐★★☆☆☆ ⏱1h — …
需要修复哪些？回复编号（如 1,2）或 /fix CG-xxx
（编号 30 分钟内有效）
```

若脚本返回了 `fix_session.hint`，可直接附在汇总后。

## 认证

Jira 查询和评论回帖均使用 **Jira API Token** (Basic Auth)。

## 可移植性

本 skill 所有路径均为相对路径。编号 session 依赖同级安装的 `jira-fix` skill（`skills/jira-fix/scripts/fix_session.py`）。
