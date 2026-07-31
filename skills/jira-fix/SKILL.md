---
name: jira-fix
description: "处理 /fix 与编号选票——解析 session 编号或 KEY，串行调用编排脚本修 Bug 并建 PR。触发：/fix、纯编号如 1,2"
triggers:
  - "/fix"
  - "jira-fix"
  - "修复bug"
  - "修 bug"
---

# Jira Bug 自动修复（编号 + 批量串行）

当用户发送以下任一形式时执行本 skill：

- `/fix CG-xxx` / `/fix CG-a CG-b`
- `/fix 1` / `/fix 1 2` / `/fix 1,2`（依赖上一轮 `/jira-analyze` 的编号 session，TTL 30 分钟）
- 用户**只回复** `1` / `1,2` / `1 2`（无其它文字）→ 同等视为 `/fix` 编号
- 末尾可加产品线：`/fix CG-xxx overlay` 或 `/fix 1 overlay`

**不要**把 Hermes 分析出的根因/建议喂给 Fix Agent；脚本只读 Jira 原始字段。

## 前置条件

| 变量 | 说明 |
|------|------|
| `JIRA_USER_EMAIL` / `JIRA_API_TOKEN` / `JIRA_CLOUD_ID` | 读票 + 评论 |
| `BITBUCKET_USERNAME` / `BITBUCKET_APP_PASSWORD` | Bitbucket 建 PR |
| 本机仓库根目录 `config/repos.json` | 从 template 复制 |
| `claude` CLI | 默认 Agent；基建失败回退 Cursor |

## 交互（必须遵守）

1. **先**用脚本解析目标（不跑修复），立刻回复用户「已开始」：

```
python "{skillDir}/scripts/jira_fix.py" <targets...> --resolve-only
```

把 JSON 里的 `message_qq` 发给用户（例如 `🔧 已开始修复 CG-20926…`）。

2. **再**跑全流程（可多 KEY，脚本内严格串行）：

```
python "{skillDir}/scripts/jira_fix.py" <targets...>
```

示例：

```
python "{skillDir}/scripts/jira_fix.py" CG-20926
python "{skillDir}/scripts/jira_fix.py" 1 2
python "{skillDir}/scripts/jira_fix.py" 1,2 overlay
python "{skillDir}/scripts/jira_fix.py" CG-20926 --dry-run
```

3. 结束后用最终 JSON 的 `message_qq` 回复用户。  
   - 单票：成功/失败一条  
   - 批量：含 `汇总: x/y 成功` 与每票结果  

4. 编号过期或不存在：把脚本 `error` / `message_qq` 原样告知，提示重新 `/jira-analyze` 或改用 KEY。不要自行猜编号。

## 会话文件

由 `jira-analyze` 自动写入 `$HERMES_HOME/jira-fix-sessions/default.json`。  
调试：`python "{skillDir}/scripts/fix_session.py" show`

## 脚本职责（单票循环）

worktree `fix/<KEY>` → 下载 Jira 附件到 `.jira-fix-attachments/`（不入 commit）→ agent（**summary 为主**；description 可空）→ 校验 commit（无 commit 时编排层可兜底）→ push → PR → Jira 评论。  
失败时 JSON 含 `agent_log` 路径。Review Gate 二期不做。

## 失败时

不要手动换 Agent 重试（脚本已处理基建回退）。直接转发 `message_qq`。
