# KOL-Find Configuration Template

Copy and modify this block for each new KOL prospecting task.

```yaml
task:
  project_name: ""
  output_directory: ""
  output_language: "Chinese"
  final_file_name: "kol_results.xlsx"

targets:
  total_target_count: 100
  platform_target_count:
    YouTube: 50
    TikTok: 30
    Instagram: 20
    X: 0
  platform_priority:
    - YouTube
    - TikTok
    - Instagram
    - X

campaigns:
  - name: "Campaign or Product A"
    enabled: true
    niche_priority_1:
      - ""
    niche_priority_2:
      - ""
    niche_priority_3:
      - ""
    target_regions_include:
      - "United States"
      - "United Kingdom"
      - "Canada"
      - "Australia"
      - "Western Europe"
    target_regions_exclude:
      - "Southeast Asia"
      - "South Asia"
      - "Middle East"
      - "Africa"
      - "war zones"
      - "remote or low-income countries"
    languages:
      - "English"
    allow_unknown_region: false
    minimum_followers:
      YouTube: 5000
      TikTok: 5000
      Instagram: 5000
      X: 3000
    minimum_recent_average_views:
      YouTube: 1000
      TikTok: 2000
      Instagram: 1000
      X: 500
    special_notes:
      - ""

global_exclusions:
  account_types:
    - official account
    - company account
    - product account
    - platform account
    - institution account
    - media account
    - repost account
    - clip compilation account
    - quote compilation account
    - globally famous founder or CEO
  risky_content:
    - crypto trading
    - forex
    - trading signals
    - gambling
    - adult content
    - extremist politics
    - professional medical diagnosis
    - investment advice
    - legal case advice
  region_exclusions:
    - Southeast Asia
    - South Asia
    - Middle East
    - Africa
    - war zones
    - remote or low-income countries

seed_accounts:
  YouTube: []
  TikTok: []
  Instagram: []
  X: []

discovery_keywords:
  core_keywords: []
  use_case_keywords: []
  competitor_or_product_keywords: []
  hashtags: []

output_schema:
  include_audit_sheets: false
  include_rejected_accounts: false
  columns:
    - 平台
    - 账号
    - 昵称
    - 主页链接
    - 粉丝数
    - 10天平均浏览量
    - 国家/地区
    - 语言
    - 内容赛道
    - 适配产品
    - 主要推荐产品
    - 数据更新时间
    - 联系方式类型
    - 联系方式
```

## Minimal Configuration

If speed matters, the user only needs to provide:

```yaml
task:
  project_name: ""
  output_directory: ""

targets:
  total_target_count: 100
  platform_priority: [YouTube, TikTok, Instagram]

campaigns:
  - name: ""
    niche_priority_1: []
    target_regions_include: []
    target_regions_exclude: []
    languages: [English]

seed_accounts:
  YouTube: []
  TikTok: []
  Instagram: []
  X: []
```

