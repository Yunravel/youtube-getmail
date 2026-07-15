# 飞牛 NAS 部署中台 + 配置 Snov Webhook 完整指南

> 把中台部署到你的飞牛 NAS,接收 Snov 回信 webhook,实现 AI 意向分析。

---

## 总览(5 步)

```
Step 1: DNS 加子域名(kol.你的域名.com → NAS公网IP)
Step 2: 路由器端口转发(443/80 → NAS内网IP)
Step 3: NAS 上 Docker 部署中台
Step 4: 飞牛反向代理 + HTTPS 证书
Step 5: Snov 订阅 webhook → 测试
```

---

## Step 1: DNS 加子域名(2 分钟)

去你域名注册商后台(阿里云/腾讯云/Cloudflare),加一条解析:

```
记录类型: A
主机记录: kol
记录值:   你NAS的公网IP(百度搜"我的IP"查)
TTL:      600
```

**验证:**
```bash
ping kol.你的域名.com
→ 应该返回你 NAS 的公网 IP
```

---

## Step 2: 路由器端口转发

登录你家路由器后台,找"端口转发/虚拟服务器/Port Forwarding":

```
外部端口 80  → 内部 IP: NAS内网IP : 内部端口 80
外部端口 443 → 内部 IP: NAS内网IP : 内部端口 443
```

> ⚠️ 80 和 443 是给 HTTPS 反向代理用的(飞牛自带的反向代理)。

**验证:**
```
手机断开 WiFi,用 4G 访问 https://kol.你的域名.com/health
→ 应该返回 {"status":"ok"}
```

---

## Step 3: NAS 上 Docker 部署中台

### 3.1 把项目文件传到 NAS

把你电脑上的 `kol-outreach` 整个文件夹,传到 NAS 的某个目录,比如:
```
/共享文件夹/docker/kol-outreach/
```

> 用飞牛的文件管理器,或 SMB 共享复制过去。

### 3.2 配置环境变量

在 NAS 上,进入 `kol-outreach` 目录:

```bash
cd /共享文件夹/docker/kol-outreach

# 复制模板
cp .env.prod.example .env.prod

# 编辑 .env.prod,填入真实值(重点改这几项):
nano .env.prod
```

**必须改的 5 项:**
```
POSTGRES_PASSWORD=改成你自己的强密码
SNOV_WEBHOOK_TOKEN=改成至少 32 位的随机字符串
OPENAI_API_KEY=填你的 OpenAI key(给 AI 分析用)
DASHBOARD_USERNAME=设置仅团队知道的用户名
DASHBOARD_PASSWORD=设置强密码
```

### 3.3 用 Docker Compose 启动

```bash
cd /共享文件夹/docker/kol-outreach

docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

**等待构建完成(第一次 5-10 分钟,下载镜像 + 编译)。**

### 3.4 验证中台跑起来了

```bash
# 看容器状态(都应该 Up)
docker compose -f docker-compose.prod.yml ps

# 测试健康检查
curl http://localhost:8080/health
→ {"status":"ok"}

# 检查中台 API
curl -u "你的看板用户名:你的看板密码" http://localhost:8080/api/stats/overview
```

> 联系人会在 Snov 发信或回信时自动创建。若要提前导入，可从看板的「导入 KOL」上传 CSV；不要运行旧的 `sync_kol_to_db.py`，它只保留了开发机路径。

---

## Step 4: 飞牛反向代理 + HTTPS 证书

这一步让 `https://kol.你的域名.com` 安全访问中台。

### 4.1 飞牛反向代理设置

飞牛系统自带反向代理功能:

```
飞牛桌面 → 系统设置 → 网络/反向代理(或 控制面板 → 反向代理)

新建反向代理规则:
├── 来源协议: HTTPS
├── 来源域名: kol.你的域名.com
├── 来源端口: 443
├── 目标协议: HTTP
├── 目标主机: localhost(或 NAS内网IP)
├── 目标端口: 8080
└── 启用 HTTPS: 打开
```

### 4.2 申请 HTTPS 证书

飞牛反向代理里,应该有"申请证书"或"Let's Encrypt"选项:

```
证书类型: Let's Encrypt(免费)
域名:    kol.你的域名.com
验证方式: DNS 验证 或 HTTP 验证
```

> 如果飞牛没有内置 Let's Encrypt,用以下替代:
> - **Nginx Proxy Manager**(Docker 镜像,自带 Let's Encrypt 自动续期)⭐ 推荐
> - **Caddy**(自动 HTTPS)

### 4.3 验证 HTTPS

```
浏览器访问: https://kol.你的域名.com/health
→ 应该返回 {"status":"ok"}
→ 地址栏显示🔒(证书有效)
```

**到这一步,你的 webhook URL 就准备好了:**
```
https://kol.你的域名.com/api/webhook/snov?token=你的SNOV_WEBHOOK_TOKEN
```

打开 `https://kol.你的域名.com` 时，浏览器会弹出账号密码框；填写 `.env.prod` 中的 `DASHBOARD_USERNAME` 和 `DASHBOARD_PASSWORD`。

---

## Step 5: Snov 订阅 webhook

中台上线 + HTTPS 通了后，在 Snov API 创建两条 webhook，地址均为：
`https://kol.你的域名.com/api/webhook/snov?token=你的SNOV_WEBHOOK_TOKEN`

```json
{ "event_object": "campaign_email", "event_action": "sent", "endpoint_url": "你的地址" }
{ "event_object": "campaign_reply", "event_action": "received", "endpoint_url": "你的地址" }
```

第一条同步已发送邮件，第二条同步回信并触发 AI 意向分析。

---

## 部署后的验证流程

```
1. Snov 发信给 KOL
2. KOL 回信
3. Snov 推 webhook → 你 NAS 的中台
4. 中台收到 → AI 分析意向
5. 你访问 https://kol.你的域名.com → 看 Hot Lead 看板

测试 webhook 是否通:
curl -X POST "https://kol.你的域名.com/api/webhook/snov?token=你的SNOV_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"event_object":"campaign_reply","event_action":"received","email":"test@example.com","body":"interested"}'
→ 返回 {"status":"accepted"} 说明通了
```

---

## 常见问题

**Q: docker-compose 命令找不到?**
A: 飞牛新版用 `docker compose`(空格,不是横线)。

**Q: 外网访问不了 8000 端口?**
A: 检查路由器端口转发是否生效;检查 NAS 防火墙。

**Q: HTTPS 证书申请失败?**
A: 确认 DNS 已生效(ping kol.域名 能返回 NAS IP);用 DNS 验证方式(比 HTTP 验证稳)。

**Q: 飞牛反向代理在哪?**
A: 不同版本位置不同,找"控制面板 → 网络 → 反向代理"或"系统 → 应用"。

**Q: 我家是动态公网 IP?**
A: 配合 DDNS(动态域名解析),飞牛系统里一般有 DDNS 设置。
