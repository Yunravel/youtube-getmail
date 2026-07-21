# 第三方账号准备清单

> 开始发信前,按本清单准备所有第三方账号和配置。
> 建议在 warmup 的 14 天里逐步搞定。

---

## 🔴 必须项(不发信就跑不起来)

### 1. 二级域名 ×3-5 个

**为什么**: 不能用主域名发 cold email,否则主域名被降权全公司邮件废掉。

**在哪买**: Namecheap / Cloudflare / 阿里云

**买什么**:
```
❌ yourcompany.com              (主域名,别碰)
✅ getyourcompany.com           (买)
✅ yourcompany-mail.com         (买)
✅ try-yourcompany.com          (买)
```

**费用**: ~$10/个/年

**配置**: 每个域名都要在 DNS 加:
```
# SPF(声明谁有权发信)
TXT  @   v=spf1 include:_spf.google.com ~all

# DKIM(Google Workspace 后台会给具体值)

# DMARC
TXT  _dmarc   v=DMARC1; p=none; rua=mailto:你@主域名.com
```

> ⚠️ 用 https://mail-tester.com 测试,**必须 9-10 分**才能开始发。

---

### 2. Google Workspace ×20 邮箱

**为什么**: Gmail 后端送达率最高,cold email 圈共识。

**在哪买**: https://workspace.google.com

**怎么配**:
- 1 个域名建 4 个邮箱(共 5 域名 × 4 邮箱 = 20 邮箱)
- 每个邮箱用真实人名(alice@, bob@, sarah@...)

**费用**: $6/邮箱/月 × 20 = $120/月

> 这钱别省。免费 Gmail 发 cold email 必被封。

---

### 3. Instantly 账号

**为什么**: 发信平台,负责 warmup + 轮发 + 跟进序列 + webhook 推回信。

**在哪注册**: https://instantly.ai

**选什么套餐**: Growth $37/月起(支持多 campaign + webhook)

**费用**: $37-97/月

**接入中台**:
```
# backend/.env
INSTANTLY_API_KEY=你的key
INSTANTLY_WEBHOOK_TOKEN=随机字符串
```

详见 [INSTANTLY_SETUP.md](./INSTANTLY_SETUP.md)

---

### 4. OpenAI API Key

**为什么**: AI 意向分析 + 个性化开场白生成的核心。

**在哪拿**: https://platform.openai.com

**费用预估**(1000 KOL 规模):
- 开场白生成(GPT-4o): ~$20
- 意向分析(GPT-4o-mini,按回信量): ~$10-30/月

**接入中台**:
```
# backend/.env
OPENAI_API_KEY=sk-xxxxx
```

---

## 🟡 推荐项(让流程更顺)

### 5. 云服务器(部署中台用)

**配置**: 2核4G 起步,带公网 IP(Instantly webhook 要能访问)

**选择**:
- 阿里云/腾讯云 ECS: ~¥100/月
- AWS Lightsail / DigitalOcean: ~$10-20/月
- 必须有公网 IP(webhook 才能收到)

**域名 + HTTPS**:
- 给中台配一个子域名(如 `kol.yourcompany.com`)
- 用 Caddy 或 nginx + Let's Encrypt 配 HTTPS
- Instantly webhook 必须是 HTTPS

---

### 6. ngrok(本地开发联调用)

开发时用 ngrok 把本地 8000 端口暴露出去,测试 webhook:
```bash
ngrok http 8000
# 拿到 https://xxx.ngrok.io
# 填到 Instantly webhook URL
```

---

## 📋 总费用估算(月)

| 项 | 费用 |
|----|------|
| Google Workspace ×20 | $120 |
| Instantly | $37-97 |
| 域名 ×5(摊销) | ~$5 |
| OpenAI API | $10-50 |
| 云服务器 | ¥100 (~$15) |
| **合计** | **~$190-290/月** |

---

## ✅ 准备完毕的自检清单

开始发信前,逐项打勾:

- [ ] 买了 3-5 个二级域名
- [ ] 每个域名配了 SPF/DKIM/DMARC
- [ ] mail-tester 测试 9-10 分
- [ ] Google Workspace 建了 20 个邮箱
- [ ] Instantly 加了 20 个邮箱
- [ ] Instantly 每个邮箱开启了 Warmup
- [ ] Warmup 跑了至少 14 天
- [ ] 创建了 Campaign + 写了 3 封序列
- [ ] 配了 Webhook 指向中台
- [ ] 中台 `.env` 填了 OPENAI_API_KEY + INSTANTLY_API_KEY
- [ ] 用 curl 测试 webhook 能收到
- [ ] 爬虫产出了 CSV 并导入中台
- [ ] 选中 KOL 生成了个性化开场白

全部打勾后,就可以在中台「推送到 Instantly」开始发了。
