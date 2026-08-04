---
name: jira-fix
description: "处理 /fix 与 Bitbucket PR 合入/拒绝反馈——编号修 Bug 建 PR；webhook 合入或 declined 时写 Jira 评论并回复 QQ。触发：/fix、纯编号、bitbucket_pr_fulfilled、bitbucket_pr_rejected"
triggers:
  - "/fix"
  - "jira-fix"
  - "修复bug"
  - "修 bug"
  - "bitbucket_pr_fulfilled"
  - "bitbucket_pr_rejected"
---

# Jira Bug 自动修复（编号 + 批量串行）

当用户发送以下任一形式时执行本 skill：

- `/fix CG-xxx` / `/fix CG-a CG-b`
- `/fix 1` / `/fix 1 2` / `/fix 1,2`（依赖上一轮 `/jira-analyze` 的编号 session，TTL 30 分钟）
- 用户**只回复** `1` / `1,2` / `1 2`（无其它文字）→ 同等视为 `/fix` 编号
- 末尾可加产品线：`/fix CG-xxx overlay` 或 `/fix 1 overlay`
- **指定 Fix Agent（单次，不改默认）**：自然语言或命令末尾带上即可  
  - 「修复 CG-xxx 使用 cursor」「/fix CG-xxx 用 cursor」「/fix 1 cursor」  
  - 「使用 claude」同理  
  - 等价 CLI：`--agent cursor` / `--agent claude`

**不要**把 Hermes 分析出的根因/建议喂给 Fix Agent；脚本只读 Jira 原始字段。  
例外：若评论中有本 bot 写入的最新 **PR Declined** 记录，会把拒绝原因注入 Fix Agent prompt（指导重修）。

## 前置条件

| 变量 | 说明 |
|------|------|
| `JIRA_USER_EMAIL` / `JIRA_API_TOKEN` / `JIRA_CLOUD_ID` | 读票 + 评论 |
| `BITBUCKET_USERNAME` / `BITBUCKET_APP_PASSWORD` | Bitbucket 建 PR |
| 本机仓库根目录 `config/repos.json` | 从 template 复制 |
| `claude` CLI | 默认 Agent；基建失败回退 Cursor |
| `agent` / Cursor Agent CLI | 用户指定 `cursor` 时需要 |

## 交互（必须遵守）

若用户指定了 Agent（cursor / claude），**两步命令都必须带上** `--agent <name>`（或把 `cursor`/`claude`/`使用cursor` 作为 targets 末尾 token，脚本会识别）。

1. **先**用脚本解析目标（不跑修复），立刻回复用户「已开始」：

```
python "{skillDir}/scripts/jira_fix.py" <targets...> [--agent cursor|claude] --resolve-only
```

把 JSON 里的 `message_qq` 发给用户（例如 `🔧 已开始修复 CG-20926（agent=cursor）…`）。

2. **再**跑全流程（可多 KEY，脚本内严格串行）：

```
python "{skillDir}/scripts/jira_fix.py" <targets...> [--agent cursor|claude]
```

示例：

```
python "{skillDir}/scripts/jira_fix.py" CG-20926
python "{skillDir}/scripts/jira_fix.py" CG-20926 --agent cursor
python "{skillDir}/scripts/jira_fix.py" CG-20926 使用cursor
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

worktree `fix/<KEY>` → 下载 Jira 附件到 `.jira-fix-attachments/`（不入 commit）→ agent（**summary 为主**；description 可空；**禁止**再走 Jira MCP / 向用户索要票详情）→ 校验 commit（无 commit 时编排层可兜底）→ push → PR → Jira 评论。  
失败时 JSON 含 `agent_log` 路径（日志含完整 prompt）。Review Gate 二期不做。

## 失败时

不要手动换 Agent 重试（脚本已处理基建回退）。直接转发 `message_qq`。

## Bitbucket PR 合入 / 拒绝（webhook → Hermes）

由薄适配层转发（**不是**适配层写 Jira）。按 `event_type` 执行：

### `bitbucket_pr_fulfilled`（合入）

```
python "{skillDir}/scripts/pr_lifecycle.py" fulfilled {key} --pr-url "{pr_url}" --pr-id "{pr_id}" --branch "{branch}" --base "{base}" --repo "{repository}" --actor "{merged_by}"
```

用中文简短回复（可参考 `message_qq`）。不要再改代码、不要再开 PR。

### `bitbucket_pr_rejected`（Declined）

```
python "{skillDir}/scripts/pr_lifecycle.py" rejected {key} --pr-url "{pr_url}" --pr-id "{pr_id}" --branch "{branch}" --base "{base}" --repo "{repository}" --actor "{declined_by}" --reason "{reason}"
```

**必须**在 Jira 评论与 QQ 回复中带上 `reason`（若非空）。可参考 `message_qq`。不要自动重修，除非用户明确要求。
