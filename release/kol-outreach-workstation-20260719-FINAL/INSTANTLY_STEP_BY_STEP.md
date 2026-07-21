# Instantly 从零到发出第一封邮件 — 保姆级教学

> 照着这份文档一步步做,2 小时内发出你的第一封 KOL 邮件。
> 每一步都写清楚"点哪个按钮、填什么、怎么判断对错"。

---

## 整体流程(7 大步)

```
1. 注册账号 + 选套餐
2. 接入你的 Gmail(发信邮箱)
3. 开启 Warmup(养号)
4. 准备 KOL 名单 CSV
5. 创建 Campaign(发信活动)
6. 写邮件内容(3 封序列)
7. 点 Launch 开始发
```

---

## 第 1 步:注册账号 + 选套餐(15 分钟)

### 1.1 打开注册页

浏览器访问: **https://instantly.ai**

点右上角 **"Get Started"** 或 **"Start Free"**

### 1.2 注册账号

```
填邮箱 + 设密码  →  验证邮箱  →  进入后台
```

> 💡 用你自己的工作邮箱注册,别用要发信的 Gmail 注册。

### 1.3 选套餐

进入后台会弹出售价页面。**选 Growth $47/月**(月付先试)。

```
不要选:
  ❌ Hypergrowth $97  (你用不到)
  ❌ Light Speed $358  (大企业才用)

要选:
  ✅ Growth $47/月
     → 无限邮箱账号(你的30个Gmail都能接)
     → 无限 warmup
     → 1000+ 联系人(你500个绰绰有余)
     → Campaign + 序列 + Webhook 全都有
```

**填信用卡付完,回到主界面。** 你会看到一个空的后台,像这样:

```
左侧菜单:
├── Dashboard       (总览)
├── Campaigns       (发信活动)← 待会主要用这个
├── Email Accounts  (邮箱账号)← 第一步要配
├── Leads           (联系人名单)
├── Inbox           (统一收件箱)
├── Analytics       (数据)
└── Settings        (设置)
```

---

## 第 2 步:接入你的 Gmail(30 分钟,分批接)

### ⚠️ 重要:别一次接 30 个!

今晚先接 **5 个**,跑通了明天再接更多。原因:
- 一次接太多,Google 会判定异常,触发风控
- 你的号是指纹浏览器养的,要稳妥

### 2.1 进入邮箱账号管理

点左侧 **"Email Accounts"** → 右上角 **"Add Account"**

### 2.2 选择 Google

弹出选项,选 **"Google Workspace"** 或 **"Gmail"**(两者都行,看你号是哪种)

### 2.3 OAuth 授权登录

```
点 "Connect Google" 
→ 跳转到 Google 登录页
→ 输入你的 Gmail 地址
→ 输入密码
→ Google 问 "Instantly 想访问你的账号,允许吗?"
→ 点 "Allow"(允许)
```

### 2.4 设置发信人信息

授权成功后,Instantly 让你填发信人信息:

```
Sender Name:    填一个真实人名,如 "Alice"
                (这是 KOL 收到邮件看到的发件人名)
                ⚠️ 别填公司名!填人名!像真人才不会被当垃圾

Email Signature: 填签名,如:
                Alice Smith
                Partnership Manager
                YourCompany
                https://yourcompany.com
```

### 2.5 ⚠️ 关键:立刻开启 Warmup

添加完邮箱,会看到一个 **"Warmup"** 开关。**一定要打开!**

```
Warmup: [ON]  ← 打开!
```

**Warmup 是什么?** Instantly 让它的几十万个邮箱互相发邮件、互相回复,模拟真实邮件行为,把你的号信誉养高。

**为什么必须开?**
- 你的号虽然老,但接入新工具会让 Google 警惕
- Warmup 能告诉 Google "这个号在正常使用"
- 边发边养,降低封号概率

### 2.6 重复:今晚接 5 个

接完第一个,**等 10-15 分钟再接第二个**(别连续接)。

```
今晚目标:接 5 个 Gmail,每个都开 Warmup
接法:每个之间间隔 10-15 分钟
```

> 💡 如果你的号在指纹浏览器里,就在指纹浏览器里操作 Instantly 完成 OAuth 授权。授权完成后,后面发信就不需要指纹了(Instantly 用 token 发)。

### ✅ 自检:接入成功的标志

```
Email Accounts 页面,你会看到:
┌─────────────────────────────────────┐
│ alice@gmail.com     ✓ Active        │
│ Warmup: ON  健康                    │
├─────────────────────────────────────┤
│ bob@gmail.com       ✓ Active        │
│ Warmup: ON  健康                    │
└─────────────────────────────────────┘
```

如果显示红色/警告,说明该号没接通,删掉重接。

---

## 第 3 步:确认 Warmup 在跑(5 分钟)

### 3.1 看 Warmup 状态

左侧 **Email Accounts** → 每个邮箱后面有 Warmup 状态:

```
Warmup Status:
✅ 正常 = 显示绿色,数字在增长(说明在互相发邮件养号)
⚠️ 异常 = 显示红色,数字不动(检查连接)
```

### 3.2 等 Warmup 跑几天(老号可以边发边养)

```
老 Gmail 优势:本身已有信誉,不用等 14 天
今晚策略:Warmup 开着 + 直接小量发(每号每天 20 封)
        边发边养,风险可控
```

---

## 第 4 步:准备 KOL 名单 CSV(15 分钟)

### 4.1 CSV 必须有这 4 列(列名要英文,小写)

```
email,first_name,channel_topic,personal_intro
```

| 列名 | 意思 | 示例 | 必填 |
|------|------|------|------|
| `email` | KOL 邮箱 | jimmy@xx.com | ✅ 必须 |
| `first_name` | KOL 名字 | Jimmy | 建议有 |
| `channel_topic` | 频道主题 | gaming | 建议有 |
| `personal_intro` | 定制开场白 | "Hey Jimmy, your video..." | ✅ 必须 |

### 4.2 CSV 长这样(举例 2 行)

```csv
email,first_name,channel_topic,personal_intro
kol1@example.com,Jimmy,gaming,"Hey Jimmy, your Minecraft series was insane"
kol2@example.com,Sarah,beauty,"Hi Sarah, loved your makeup tutorial on Sunday"
```

### 4.3 ⚠️ CSV 格式注意

```
1. 开场白里有逗号? → 必须用双引号包起来
   ✅  "Hey Jimmy, your video..."  (双引号包住)
   ❌   Hey Jimmy, your video...   (逗号会断列)

2. 用 Excel 编辑? → 另存为 "CSV UTF-8" 格式

3. 没有某列数据? → 留空,别删列名
   ✅  kol1@xx.com,,gaming,"Hey..."  (first_name 空)
   ❌  删掉 first_name 整列
```

### 4.4 如果你的开场白里已经有名字(比如 "Hey Jimmy...")

那 `first_name` 这列可以留空,邮件模板里不用 `{{first_name}}`,开场白里的名字就够了。详见第 6 步。

---

## 第 5 步:创建 Campaign(10 分钟)

### 5.1 新建 Campaign

左侧 **Campaigns** → 右上角 **"New Campaign"**

### 5.2 填基本信息

```
Campaign Name:  KOL-Outreach-Batch1   (随便起,自己认识就行)
```

### 5.3 选发信邮箱

```
Email Accounts: 勾选你接入的 5 个 Gmail
                (5 个号会轮着发,自动分配)
```

### 5.4 ⚠️ 关键:发信设置(防封核心)

```
Sending Schedule(发信时间表):

┌─ Days(星期)─────────────────────────┐
│  ☑ Mon  ☑ Tue  ☑ Wed  ☑ Thu  ☑ Fri  │  ← 工作日发
│  ☐ Sat  ☐ Sun                        │  ← 周末别发
└──────────────────────────────────────┘

┌─ Hours(时段)────────────────────────┐
│  从 09:00 到 17:00                   │  ← 工作时间发
│  ⚠️ 选收件人时区,不是你的时区!      │
│  (KOL 在美国就选美国时区)            │
└──────────────────────────────────────┘

┌─ Daily Limit(每号每天发多少)─────────┐
│  Max emails per day: 20              │  ← ⚠️ 别超 20!
│  (5个号 × 20封 = 100封/天)          │
└──────────────────────────────────────┘

┌─ Interval(发信间隔)─────────────────┐
│  Min: 5 分钟                         │
│  Max: 15 分钟                        │  ← 模拟真人节奏
│  (两封邮件之间随机停 5-15 分钟)      │
└──────────────────────────────────────┘
```

**这 4 个设置是防封的关键,严格按上面的填,别贪量。**

### 5.5 保存

点 **"Save"** 或 **"Next"** 进入下一步(写邮件)。

---

## 第 6 步:写邮件内容(20 分钟)

### 6.1 进入 Sequence(序列)编辑器

Campaign 创建后,会进入 **Sequence** 页面。这是写邮件的地方。

**默认会有 Step 1(第1封),你可以加 Step 2、Step 3。**

### 6.2 写第 1 封(首询邮件)

点 **Step 1** → 编辑邮件内容:

```
Subject(主题):
quick question about {{channel_topic}}

Body(正文):
{{personal_intro}}

We're putting together a partnership for [你的产品/品牌],
and your audience seems like a perfect fit. Would you be
open to seeing a quick breakdown?

Best,
{{sender_first_name}}
```

**变量说明(Instantly 自动替换):**

```
{{channel_topic}}    → 替换成你 CSV 里该 KOL 的 channel_topic
{{personal_intro}}   → 替换成定制开场白
{{sender_first_name}}→ 替换成你的发信人名(第2步设的)
{{first_name}}       → 替换成 KOL 名字(可选,开场白有名字就不用)
```

### 6.3 加第 2 封(跟进,3 天后)

点 **"Add Step"** → 选 **Email**:

```
等待时间(Delay): 3 days after previous step

Subject:
re: quick question about {{channel_topic}}

Body:
Hi {{first_name}},

Just bumping this up — wanted to make sure you saw it.
Happy to send over the details + numbers whenever you
have 2 minutes.

{{sender_first_name}}
```

> 💡 主题用 `re:` 开头,让 KOL 以为是已经在聊的对话,打开率翻倍。

### 6.4 加第 3 封(分手信,5 天后)

点 **"Add Step"** → 选 **Email**:

```
等待时间(Delay): 5 days after previous step

Subject:
re: quick question about {{channel_topic}}

Body:
Hi {{first_name}},

I'll stop here so I don't keep cluttering your inbox.
If the timing's ever right, my calendar's open:
[你的 Calendly 链接或邮箱]

Either way, keep up the great content 👍

{{sender_first_name}}
```

### 6.5 保存序列

点 **"Save Sequence"**。

---

## 第 7 步:导入 KOL 名单 + 启动(15 分钟)

### 7.1 导入 CSV

左侧 **Leads** → **Import CSV** → 上传你准备好的 CSV

```
映射列(让 Instantly 知道哪列对应哪个变量):
CSV 的 email 列         → Instantly 的 Email
CSV 的 first_name 列    → Instantly 的 First Name
CSV 的 channel_topic 列 → Custom Variable: channel_topic
CSV 的 personal_intro 列→ Custom Variable: personal_intro
```

> ⚠️ 这一步很重要:列名要对应起来,否则 `{{personal_intro}}` 替换不了。

### 7.2 把名单分配给 Campaign

导入后,选中这些联系人 → **"Add to Campaign"** → 选你刚建的 `KOL-Outreach-Batch1`

### 7.3 启动!

进入 Campaign → 右上角 **"Launch"** 按钮 → 点它

```
🎉 开始发信了!
```

---

## 第 8 步:监控(发出去后)

### 8.1 看哪些指标

Campaign 页面会实时显示:

```
Sent(已发)         → 发出去多少封
Opened(打开)       → 打开率(健康值 >40%)
Replied(回复)      → 回复率(健康值 >3%)
Bounced(退信)      → 退信率(健康值 <3%)
```

### 8.2 ⚠️ 危险信号(出现立刻停!)

```
🔴 打开率 < 25%
   → 邮件进垃圾箱了!
   → 立刻 Pause Campaign
   → 检查:Warmup 有没有开 / 内容像不像群发

🔴 退信率 > 5%
   → 名单质量差(邮箱无效)
   → 立刻 Pause,清理名单再发

🔴 某邮箱显示 "Account Paused" 或 "Suspended"
   → 该号被 Google 限制了
   → 立刻在 Instantly 停用该号
   → 去指纹浏览器里看看是不是被锁了
```

### 8.3 健康指标参考

```
打开率   40-70%  → 正常,继续
打开率   70%+    → 很好
回复率   3-8%    → 正常
回复率   8%+     → 优秀(开场白写得棒)
```

---

## 常见问题

**Q: 接入 Gmail 时提示 "Google couldn't verify this account"?**
A: 该号触发了 Google 风控。换一个号试。如果是指纹浏览器养的号,在指纹环境里操作 OAuth。

**Q: 邮件发出去了但打开率很低(<20%)?**
A: 三个可能:
1. Warmup 没开/时间不够(老号至少 warmup 几天)
2. 内容像群发模板(开场白要够个性化)
3. 主题行太营销(别用 "Business Opportunity / Collab?")

**Q: KOL 回复了,我在哪看?**
A: 左侧 **Inbox**,所有回信聚合在一个收件箱。
后续配置 Webhook 后,回信会自动推到你的中台做 AI 分析。

**Q: 每个号每天发多少安全?**
A: 老号控制在 20 封以内,新号 5-10 封起步慢慢加。

**Q: 发出去的邮件能撤回吗?**
A: 不能。所以发之前用 Instantly 的 **"Preview"** 功能检查每封邮件的变量替换效果。

**Q: 我想中途停怎么办?**
A: Campaign 页面 → **"Pause"**,立即停止后续发送。

---

## 今晚的最小行动清单

```
□ 1. 注册 Instantly + 选 Growth $47        (15分钟)
□ 2. 接入 5 个 Gmail(每个开 Warmup)      (1小时,分批接)
□ 3. 准备 CSV(email + first_name + 开场白)(看你的数据)
□ 4. 创建 Campaign + 写 3 封序列           (30分钟)
□ 5. 导入名单 + Launch                     (15分钟)
□ 6. 发完看打开率,>40% 继续,<25% 暂停排查
```

**祝发信顺利!有卡住的地方截图发我。**
