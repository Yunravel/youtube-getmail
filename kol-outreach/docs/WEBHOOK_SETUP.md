# Snov Webhook 接通指南

> 为什么需要:中台的 webhook 接收代码一直是现成的,但 Snov 侧从未登记订阅,
> 所以运行至今 **0 条 webhook 推送**,全部数据靠 2 分钟轮询兜底。轮询只能拉到
> 回信且字段残缺(无收件邮箱、无附件),"已发送邮件"完全进不来。接通 webhook
> 后这些问题一次性解决。

## 原理一句话

Webhook = 在 Snov 登记一个"你的服务器网址";之后每次发信/收信,Snov 会在事件
发生当秒把完整数据 POST 到这个网址。前提:**这个网址必须是公网可访问的 HTTPS**。

中台接收端点(代码已就绪,无需改动):

```
POST /api/webhook/snov?token=<SNOV_WEBHOOK_TOKEN>
```

订阅三类事件:

| event_object   | event_action        | 作用 |
|----------------|---------------------|------|
| campaign_email | sent                | 已发送邮件 → outbound 消息(邮箱页可见发件) |
| campaign_reply | received            | 普通回信 → inbound + AI 意向分析 |
| campaign_reply | autoreply_received  | 自动回复 → inbound(AI 标 auto/ooo) |

## 方式一:正式接通(NAS / 服务器部署后)

1. 按 `docs/NAS_DEPLOY.md` 部署中台,配好域名 + HTTPS(反向代理)。
2. 确认 `.env.prod` 里 `SNOV_WEBHOOK_TOKEN` 是强随机值(不是占位符)。
3. 在 backend 目录执行一键订阅脚本(幂等,重复执行安全):

   ```bash
   python -m scripts.subscribe_snov_webhooks --base-url https://kol.你的域名.com
   ```

4. 验证:
   - `python -m scripts.subscribe_snov_webhooks --list` 应显示 3 条订阅;
   - 给自己发一封测试回信,中台日志出现 `api.webhook` 请求,
     新消息的 `message_id` 不再是 `snov:history:` 前缀;
   - 下一封 Campaign 发信后,邮箱页面能看到 outbound 消息。

## 方式二:本机模拟公网(没有服务器时临时用)

用内网穿透把工作站的 8000 端口临时暴露成一个公网 HTTPS 地址,效果等同部署在
服务器上。推荐 Cloudflare Tunnel(免费、无需注册即可用临时域名):

1. 下载 cloudflared(Windows):
   https://github.com/cloudflare/cloudflared/releases 下载 `cloudflared-windows-amd64.exe`,
   重命名为 `cloudflared.exe` 放到任意目录。
2. 启动隧道(保持这个窗口开着):

   ```powershell
   .\cloudflared.exe tunnel --url http://localhost:8000
   ```

   输出里会给一个 `https://随机词.trycloudflare.com` 地址。
3. 用这个地址订阅:

   ```powershell
   cd backend
   python -m scripts.subscribe_snov_webhooks --base-url https://随机词.trycloudflare.com
   ```

4. 验证方法同上。

**注意**:trycloudflare 临时域名每次重启 cloudflared 都会变,变了要重新执行
订阅脚本(旧订阅可在 Snov 后台或 `--list` 查看后手动清理)。它适合验证链路,
正式使用请走方式一,或注册 Cloudflare 账号建固定域名的 Named Tunnel。

## 常见问题

- **订阅脚本报连不上 api.snov.io**:本机代理拦截了请求。脚本已内置
  `NO_PROXY=*`,若仍失败,检查代理软件的"排除列表"。
- **订阅成功但收不到推送**:确认隧道/反代还在运行、`SNOV_WEBHOOK_TOKEN`
  与订阅 URL 里的 token 一致(改过 token 要重新订阅)。
- **webhook 和轮询会不会重复入库?** 不会。webhook 有独立 message_id,
  轮询侧入库前会按"发件人+主题+正文+时间"做内容比对,同一封只存一次。
- **接通后轮询还要吗?** 要,保留作兜底(webhook 偶发丢投递时,轮询 2 分钟
  内补齐),两者幂等共存。
