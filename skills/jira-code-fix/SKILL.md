---
name: jira-code-fix
description: "Headless AI code-fixing pipeline: Jira ticket → project mapping → Claude Code / Cursor Agent → git commit → PR → Jira comment update. Triggered by /fix command."
triggers:
  - "/fix"
  - "fix bug"
  - "自动修复"
  - "code fix"
  - "jira-code-fix"
---

# Jira Code Fix

Automated bug-fixing pipeline: Hermes handles Jira context, delegates code changes to headless AI agents (Claude Code / Cursor Agent CLI), and orchestrates PR creation + Jira comment updates.

## Architecture

```
User: /fix CG-xxxx
  │
  ├─→ Project Mapping (config/repos.json)
  │     project.key → overrides[].match.summary → fixVersions[0] → default
  │
  ├─→ Worktree: git worktree add <tmp>/fix-<KEY> -b fix/<KEY> <baseBranch>
  │
  ├─→ Fix Agent (claude -p or agent.cmd -p -f)
  │     Input: Jira original title + description (NOT Hermes guesses)
  │     Output: git commit on fix/<KEY> branch
  │
  ├─→ Validation
  │     Exit 0 + branch fix/<KEY> + new commit with KEY in message + non-empty diff
  │     Optional: repos.json lint/test → must pass if configured
  │
  ├─→ Orchestrator
  │     git push + create PR (Bitbucket REST or gh pr create)
  │     PUT Jira comment: append "🤖 自动修复: ✅ → PR #N"
  │
  └─→ QQ: "✅ CG-xxxx 已修复 → <PR URL>"
```

## Key Design Decisions

| Decision | Detail |
|----------|--------|
| Agent scope | Commit only; orchestrator handles push + PR + comment |
| PR creation | Default Bitbucket REST API; per-repo `provider: github` override |
| Workspace | `git worktree` per ticket, cleaned after |
| Timeout | 30 minutes per ticket, kill + fail on timeout |
| Batch | Strict serial, single ticket at a time |
| Session TTL | 30 min for numbered references from /jira-analyze |
| Review Gate | Optional via `repos.json` `review`; after lint/test, before push; PASS required for PR |
| Agent fallback | Default `claude`; only infrastructure failure → fallback to `cursor` |

## Headless Agent Invocation

### Claude Code (default)
```bash
claude -p --no-session-persistence \
  --add-dir <worktree_path> \
  --permission-mode bypassPermissions \
  --output-format json \
  --model opus \
  "Fix Bug <KEY>: <summary>. Description: <description>. 
   Create a commit on branch fix/<KEY> with message containing <KEY>."
```

### Cursor Agent CLI (fallback)
```bash
agent.cmd -p -f --output-format json \
  --model claude-opus-5-thinking-high \
  "Fix Bug <KEY>: ..."
```

## Cursor Agent CLI Discovery (Pitfall)

Cursor Agent CLI is a **separate install** from Cursor IDE. Do NOT use `cursor agent` (IDE subcommand, interactive GUI). Use:

- Windows: `AppData\Local\cursor-agent\agent.cmd`
- macOS/Linux: `@nothumanwork/cursor-agent-cli` npm package

Key flags: `-p` (print/non-interactive), `-f` (force/skip permissions), `--output-format json`, `--model`, `--list-models`.

If `cursor agent --help` shows IDE flags instead of agent flags, the CLI binary is not on PATH — locate the separate install directory.

## Jira Comment Update

After fix completes, update the existing jira-analyze comment (NOT create new):

```
PUT /rest/api/3/issue/{key}/comment/{id}
Body: append "🤖 自动修复: ✅/❌ → PR #N / <reason>"
```

Comment ID is saved from jira-analyze output.

## Project Mapping (config/repos.json)

Priority chain:
1. `overrides[].match.summary` — regex match on ticket summary (e.g., "IN-GAME OVERLAY|OVERLAY")
2. `fixVersions[0].name` — exact → case-insensitive → strip v/V
3. `version_lines` — longest prefix match → inherit repo/path + branch_pattern
4. `default` — fallback

```json
{
  "CG": {
    "provider": "bitbucket",
    "agent": "claude",
    "overrides": {
      "overlay": {
        "match": { "summary": "IN-GAME OVERLAY|OVERLAY" },
        "path": "C:/workspace/cortex-overlay",
        "branch": "main"
      }
    },
    "branch_pattern": "dev/{version}.x",
    "version_lines": { "11.": { "path": "...", "repo": "..." } },
    "default": { "path": "...", "branch": "main" }
  }
}
```

## Bitbucket PR Creation

No CLI tool available. Use REST API:

```
POST /2.0/repositories/{workspace}/{repo}/pullrequests
Auth: Basic {BITBUCKET_USERNAME}:{BITBUCKET_APP_PASSWORD}
```

Required env vars: `BITBUCKET_USERNAME`, `BITBUCKET_APP_PASSWORD` (scope: Pull requests: Write).

For GitHub repos (`provider: github`), use `gh pr create`.

## Success / Failure Criteria

**Success (creates PR):**
- Exit code 0
- Branch name matches `fix/<KEY>`
- New commit on branch with KEY in message
- `git diff HEAD~1 --stat` non-empty
- Optional lint/test pass (if configured in repos.json)

**Failure (reports to Jira + QQ):**
- Any of the above not met
- Timeout (30 min)
- Agent crash / no output
- Claude explanation posted as Jira comment

## Related Skills

- `jira-analyze` — produces the analysis comment that /fix updates
- `jira-bot-workflow` — cron job setup and Jira MCP reference
- `jira-work-automation` — HTML report pipeline
