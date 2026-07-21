# KOL-Find Intake Guide

Use this guide when the user wants to find KOLs but has not provided enough configuration.

Ask concise questions. Prefer one batch of questions, then proceed with defaults for optional details.

## Required Questions

1. What niche, product, or campaign should the creators fit?
2. Which platforms should be searched, and which platform is most important?
3. Which countries/regions and languages are required?
4. How many qualified creators are needed per platform or in total?
5. Where should the final file be saved?

## Strongly Recommended Questions

6. What follower or subscriber range is acceptable?
7. What minimum recent average views are required?
8. Should unknown-region accounts be allowed?
9. Are there seed accounts, competitor accounts, hashtags, or example creators?
10. Which account types or content categories must be excluded?

## Default Assumptions

Use these defaults when the user does not specify:

- Platforms: YouTube first, then TikTok, then Instagram, then X.
- Recent views: average views from original content in the last 10 days.
- Minimum followers: 5,000 for YouTube/TikTok/Instagram, 3,000 for X.
- Minimum recent average views: 1,000 for YouTube/Instagram, 2,000 for TikTok, 500 for X.
- Contactability: prefer public email, business inquiry text, collab/sponsor links, media kit, Linktree/Komi/Pillar, or visible paid partnership history.
- Unknown region: reject if strict region targeting is required; otherwise keep with `unknown` and flag uncertainty.
- Output: one result-only workbook with an all-records sheet and optional per-campaign sheets.

## Compact Intake Prompt

If the user asks for guidance, ask:

```text
请补充这 5 项，我就可以开始配置 KOL-Find：
1. 目标赛道/产品是什么？
2. 要抓哪些平台？哪个平台最重要？
3. 目标国家/地区和语言是什么？未知地区是否允许？
4. 每个平台或总共需要多少个合格 KOL？
5. 结果表保存到哪里？

可选：给我 3-10 个你认为合格的种子账号/竞品账号/hashtag，我会用它们滚雪球扩展。
```

