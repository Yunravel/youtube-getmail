# Instantly 配置指南

> 本文档教你如何把 Instantly 发信平台接入中台。

---

## 一、为什么用 Instantly

Instantly 是 cold email 圈的事实标准,核心能力:
- **邮箱 warmup**:自动养域名信誉(2 周后才能真发)
- **多邮箱轮发**:20 个邮箱自动分配发送量,避免单邮箱被封
- **序列跟进**:配置 3-5 封自动跟进,不用手动
- **Webhook 推送**:KOL 回信自动推到中台 → AI 分析

---

## 二、配置步骤

### Step 1: 注册 Instantly + 拿 API Key

1. 注册 https://instantly.ai ($37/月起)
2. 进入 Profile → API → 生成 API Key
3. 复制到中台 `.env`:
   ```
   INSTANTLY_API_KEY=你的key
   ```

### Step 2: 添加 20 个发信邮箱

在 Instantly → Email Accounts → 逐个添加你的 Google Workspace 邮箱(OAuth 登录)。

> ⚠️ 每个邮箱务必打开 **Warmup** 开关,跑满 14 天再发信。

### Step 3: 创建 Campaign

Campaigns → New Campaign:
- **Campaign Name**: KOL-Outreach-Batch1
- **Email Account**: 选 20 个邮箱
- **Sending Schedule**: 工作日,收件人时区 9:00-17:00
- **Daily Limit**: 每邮箱 30 封(保守起步)

### Step 4: 写邮件序列(用个性化变量)

在 Campaign 的 Sequence 里写 3 封:

**第 1 封(首询):**
```
Subject: quick question about {{channel_topic}}

{{personal_intro}}

We're putting together a partnership and your audience seems like
a perfect fit. Would you be open to seeing a quick breakdown?

Best,
{{sender_first_name}}
```

> 关键:`{{personal_intro}}` 是 GPT 生成的个性化开场白,
> `{{channel_topic}}` 是赛道。这些变量在推送 KOL 时会自动填充。

**第 2 封(跟进,+3 天):**
```
Subject: re: quick question about {{channel_topic}}

Hey {{first_name}},

Just bumping this up. Happy to send over details + numbers
whenever you have 2 minutes.
```

**第 3 封(分手信,+5 天):**
```
Subject: re: quick question about {{channel_topic}}

Hey {{first_name}},

I'll stop here so I don't keep cluttering your inbox.
If timing's ever right, my calendar's open.
Either way, keep up the great content 👍
```

### Step 5: 配置 Webhook(接回信)

Instantly → Settings → Webhooks:
- Event: **Reply Received**
- URL:
  ```
  https://你的中台域名/api/webhook/instantly?token=你的token
  ```
- token 在 `.env` 的 `INSTANTLY_WEBHOOK_TOKEN` 设置(改成随机字符串)

### Step 6: 拿 Campaign ID

在 Campaign 列表页,URL 里能看到 campaign ID(类似 `a1b2c3d4-...`),
中台推送 KOL 时需要这个 ID。

---

## 三、在中台里推送 KOL 发信

1. 爬虫产出 CSV → 在中台「导入 KOL」上传
2. 在「KOL 列表」勾选要发的 KOL
3. 点「生成开场白」(GPT 根据视频标题生成个性化文案)
4. 点「推送到 Instantly」→ 选 Campaign → 确认
5. Instantly 开始按节奏自动发送

---

## 四、验证 Webhook 是否通

在中台服务器上,用 curl 模拟一封回信测试:

```bash
curl -X POST "http://localhost:8000/api/webhook/instantly?token=你的token" \
  -H "Content-Type: application/json" \
  -d '{
    "from_email": "test@kol.com",
    "to_email": "alice@getcompany.com",
    "subject": "re: partnership",
    "body_text": "Interested! Send pricing.",
    "message_id": "test-001"
  }'
```

预期返回:`{"status": "ok", "thread_id": N}`

---

## 五、常见问题

**Q: Webhook 收不到回信?**
- 确认中台服务器有公网 IP / 用了 ngrok
- Instantly 后台看 webhook 日志
- 检查 token 是否匹配

**Q: 推送 KOL 到 Campaign 失败?**
- 确认 INSTANTLY_API_KEY 正确
- 确认 campaign_id 正确
- 看 backend 日志的 error_message

**Q: 邮件进垃圾箱?**
- warmup 没跑够(至少 14 天)
- DNS 没配齐(SPF/DKIM/DMARC)
- 单日发送量过高
- 用 mail-tester.com 测试评分(必须 9-10 分)
