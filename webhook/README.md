# Bitbucket Webhook 薄适配层

接收 Bitbucket Cloud：

| 事件 | `X-Event-Key` | Hermes `event_type` |
|------|---------------|---------------------|
| PR 合入 | `pullrequest:fulfilled` | `bitbucket_pr_fulfilled` |
| PR 拒绝 | `pullrequest:rejected` | `bitbucket_pr_rejected`（含 `reason`） |

**职责划分（重要）：**

| 组件 | 做什么 |
|------|--------|
| Cloudflare Tunnel | 把公网 HTTPS 指到本机适配层（无需开端口 / 自建域名反代） |
| 适配层 | 验 Bitbucket 签、解析 KEY、幂等/重试、**转发本机 Hermes webhook** |
| Hermes webhook | 跑 `pr_lifecycle.py` **写 Jira 评论**，并把结果投递 QQ |

适配层**不持有、不使用** Jira Token。Hermes webhook **不要**直接挂到 Tunnel（Bitbucket 签名格式 Hermes 不认；签名改写在适配层完成）。

## 推荐拓扑

```
Bitbucket Cloud
    │  POST https://<tunnel-host>/bitbucket
    ▼
cloudflared (Tunnel)
    │  → http://127.0.0.1:8787
    ▼
bitbucket_adapter.py          ← 验签 / 归一化 / 幂等
    │  POST http://127.0.0.1:8644/webhooks/bitbucket-pr-merged
    │  (X-Hub-Signature-256 + X-GitHub-Event)
    ▼
Hermes webhook 路由
    → jira-fix / pr_lifecycle.py → Jira 评论
    → deliver: qqbot → QQ
```

同机部署即可：Tunnel + adapter + Hermes 都在 Hermes 所在机器。

## 快速启动

### 1) Hermes webhook 路由

见下文「Hermes 路由」。先保证本机 `http://127.0.0.1:8644/webhooks/bitbucket-pr-merged` 可用。

### 2) 适配层（只监听本机）

```bash
export BITBUCKET_WEBHOOK_SECRET='...'      # 与 Bitbucket Webhook 配置一致
export HERMES_WEBHOOK_URL='http://127.0.0.1:8644/webhooks/bitbucket-pr-merged'
export HERMES_WEBHOOK_SECRET='...'         # 与 Hermes 路由 secret 一致

python webhook/bitbucket_adapter.py --host 127.0.0.1 --port 8787
```

### 3) Cloudflare Tunnel 指到适配层

临时试通（随机域名）：

```bash
cloudflared tunnel --url http://127.0.0.1:8787
```

稳定部署（命名 Tunnel + 自有域名，示例）：

```bash
# 一次性：登录、创建 tunnel、DNS 路由
cloudflared tunnel login
cloudflared tunnel create hermes-jira-webhook
cloudflared tunnel route dns hermes-jira-webhook jira-hook.example.com

# config.yml 片段
# tunnel: <TUNNEL_ID>
# credentials-file: ...
# ingress:
#   - hostname: jira-hook.example.com
#     service: http://127.0.0.1:8787
#   - service: http_status:404

cloudflared tunnel run hermes-jira-webhook
```

### 4) Bitbucket 仓库 Webhook

| 项 | 值 |
|----|-----|
| URL | `https://<tunnel-host>/bitbucket` |
| Triggers | **Pull request merged** + **Pull request declined** |
| Secret | 与 `BITBUCKET_WEBHOOK_SECRET` 相同 |

健康检查：`GET https://<tunnel-host>/health`

### 幂等与重试（适配层 → Hermes）

| 机制 | 行为 |
|------|------|
| 幂等键 | `X-Request-Uuid` 或 `{lifecycle}:{repo}:{pr_id}:{KEY}` |
| 状态目录 | `$HERMES_HOME/jira-webhook-state/` |
| 重试 | 默认 3 次转发 Hermes（`ADAPTER_HERMES_RETRIES`），退避翻倍 |
| HTTP | 转发成功 → 200；失败 → 502（Bitbucket 可再投） |

## Hermes 路由（不要用 deliver_only）

需要 **Agent 跑脚本写 Jira**，再把回复投递 QQ。

**重要：默认 `hermes-webhook` 工具集不含 `terminal`（防注入）**。合入写评论必须显式开启：

```bash
hermes tools enable terminal file skills --platform webhook
hermes gateway restart
```

`config.yaml` 中应出现类似：

```yaml
platform_toolsets:
  webhook:
    - clarify
    - file
    - skills
    - terminal
    - vision
    - web
```

然后配置路由（同一路由同时收合入与拒绝；`events` 含两者）：

```yaml
# webhook_subscriptions / hermes webhook subscribe
# events: bitbucket_pr_fulfilled, bitbucket_pr_rejected
# skills: jira-fix
# deliver: qqbot
# prompt 用适配层下发的 {instruction} + payload 字段：
#   key, pr_url, branch, base, repository, pr_id,
#   merged_by / declined_by, reason, message_qq
```

示例 prompt：

```text
{instruction}
请用中文回复。
payload: key={key} event={event_type} pr_url={pr_url} branch={branch} base={base}
repo={repository} merged_by={merged_by} declined_by={declined_by} reason={reason} pr_id={pr_id}
参考 QQ 文案: {message_qq}
```

CLI 示例：

```bash
hermes webhook subscribe bitbucket-pr-merged \
  --events bitbucket_pr_fulfilled,bitbucket_pr_rejected \
  --prompt '{instruction}. 请用中文回复。payload: key={key}, event={event_type}, pr_url={pr_url}, branch={branch}, base={base}, repo={repository}, merged_by={merged_by}, declined_by={declined_by}, reason={reason}, pr_id={pr_id}, message_qq={message_qq}' \
  --skills jira-fix \
  --deliver qqbot \
  --deliver-chat-id "你的QQ聊天ID" \
  --secret "your-hermes-route-secret"
```

## 密钥放哪里？

推荐 **全部同机**（Tunnel + adapter + Hermes）：

| 密钥 | 需要 |
|------|------|
| `BITBUCKET_WEBHOOK_SECRET` | ✅ |
| `HERMES_WEBHOOK_URL` / `SECRET` | ✅（URL 用本机 `127.0.0.1:8644`） |
| `JIRA_*` | ✅（仅 Hermes / skill 用） |
| QQ / Hermes 主配置 | ✅ |
| Cloudflare Tunnel token / credentials | ✅（`cloudflared`） |

若日后拆机：公网只暴露 adapter（经 Tunnel）；Hermes 仍只本机或内网；adapter 主机**不必**放 `JIRA_*`。
