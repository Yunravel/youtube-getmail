---
name: kol-find
description: Use when the user wants to find, scrape, screen, dedupe, score, or export KOL, influencer, creator, UGC, affiliate, or sponsorship prospect lists across YouTube, TikTok, Instagram, X/Twitter, or similar platforms. Use especially when criteria include niche, product fit, country/region, language, followers, recent views, contactability, paid-promotion suitability, or spreadsheet delivery. Also trigger when the user says KOL-Find.
---

# KOL-Find

KOL-Find is a reusable workflow for creator prospecting. The goal is not to collect many accounts; the goal is to find creators who are relevant, reachable, regionally suitable, and likely to be usable for brand collaboration.

## First Step

If the user provides a complete configuration, execute it.

If the user does not know how to configure the task, guide them with the intake questions in `references/intake-guide.md`. Ask only for missing necessary fields. Do not block on optional fields; use reasonable defaults and state them.

If the user asks for a reusable configuration, provide the template in `references/config-template.md`.

## Core Workflow

1. Parse the task configuration:
   - target niche or product
   - target platforms
   - target countries/regions and excluded regions
   - language requirements
   - follower and recent-view thresholds
   - account type exclusions
   - output format and save location

2. Build the candidate pool:
   - seed creators from the user's examples
   - spotlight discovery from high-performing posts, hashtags, product announcements, or competitor collaborations
   - snowball expansion from followings, similar channels, collaborators, commenters, tagged creators, and recommended accounts
   - keyword expansion from niche terms, use cases, pain points, audience identity, and product category

3. Screen candidates:
   - remove official, corporate, platform, product, institution, media, and pure repost accounts
   - remove globally famous founders, CEOs, celebrities, or public figures who are unlikely to accept normal sponsorships
   - remove region/language mismatches when the platform or public signals support that conclusion
   - remove risky categories listed in the config
   - keep uncertain-region creators only if the config allows unknown region

4. Validate each kept creator:
   - platform, handle, display name, profile URL
   - followers or subscribers
   - country/region and language
   - content niche
   - recent average views, preferably over the last 10 days of original content
   - contact path and contact type
   - product or campaign fit

5. Export a result-only workbook or CSV:
   - avoid process-only fields unless the user asks for audit sheets
   - dedupe by platform plus normalized handle
   - include one all-records sheet and optional per-product/per-platform sheets

## Tool Preferences

Use the best available local skill or API for each platform:

- For X/Twitter, prefer the user's local Twitter RapidAPI setup if available.
- For YouTube, TikTok, and Instagram, prefer the available social scraping API skill or configured API.
- For spreadsheet outputs, use the spreadsheet workflow available in the environment.
- Never store API keys in new files. Read keys from the existing environment or configured local files.

## Decision Rules

Use `references/screening-rules.md` for fit, exclusion, region, contactability, and recent-view rules.

When multiple products or campaigns are configured, first identify creators who satisfy the shared baseline, then mark every product/campaign they fit. A creator may fit more than one product, but `primary_recommendation` must contain only one.

## Default Output Columns

Use these columns unless the user provides a different schema:

```text
platform
handle
display_name
profile_url
followers
recent_average_views
country_region
language
content_niche
fit_products
primary_recommendation
data_updated_at
contact_type
contact
```

For Chinese-language deliverables, use:

```text
平台
账号
昵称
主页链接
粉丝数
10天平均浏览量
国家/地区
语言
内容赛道
适配产品
主要推荐产品
数据更新时间
联系方式类型
联系方式
```

## Quality Bar

Before final delivery:

- sample-check obvious bad inclusions
- verify target-count summaries
- verify platform/product counts
- verify that key seed examples are included, excluded, or explained
- verify that the final sheet contains result fields only
- state remaining uncertainty, especially region inference and incomplete recent-view data

