from db import SessionLocal
from models import Kol

# 按 email 匹配回填 7 个手动查到的博主（生产库 kol id 不同，用邮箱唯一键）
updates = {
    "contact@aifilmmakingacademy.com": {
        "channel_url": "https://www.youtube.com/@AIFilmmakingAcademy",
        "subscribers": 62000, "country": "United States", "niche": "AI 影像/电影制作教学",
        "platform": "youtube", "content_category": "AI 影像/电影制作教学",
    },
    "franklin.aiart@gmail.com": {
        "channel_url": "https://www.youtube.com/@franklin_francis",
        "subscribers": 0, "country": "India", "niche": "AI 创作教程(Malayalam)",
        "platform": "youtube", "content_category": "AI 创作教程(Malayalam)",
    },
    "muhayyo.collaborations@gmail.com": {
        "channel_url": "https://www.youtube.com/@muhayyooo",
        "subscribers": 5500, "country": "Norway", "niche": "职业/财务/挪威生活",
        "platform": "youtube", "content_category": "职业/财务/挪威生活",
    },
    "enquiries@theworldofchelsea.co.uk": {
        "channel_url": "https://www.instagram.com/theworld_of_chelsea/",
        "subscribers": 84000, "country": "United Kingdom", "niche": "生活方式/旅行",
        "platform": "instagram", "content_category": "生活方式/旅行",
    },
    "hello@alignlifestyle.co.uk": {
        "channel_url": "https://www.youtube.com/channel/UCn14M26mhUvh_u-At3g7xqQ",
        "subscribers": 0, "country": "United Kingdom", "niche": "健康/冥想/女性身心",
        "platform": "youtube", "content_category": "健康/冥想/女性身心",
    },
    "hello@sianvictoria.co.uk": {
        "channel_url": "https://www.youtube.com/@SianVictoria",
        "subscribers": 163000, "country": "United Kingdom", "niche": "生活方式/美妆/旅行",
        "platform": "youtube", "content_category": "生活方式/美妆/旅行",
    },
    "lauren.jackson95@hotmail.com": {
        "channel_url": "https://www.tiktok.com/@laurenjfitnesstravels",
        "subscribers": 232000, "country": "United Kingdom", "niche": "UGC/孕期/理疗",
        "platform": "tiktok", "content_category": "UGC/孕期/理疗",
    },
}

db = SessionLocal()
ok = 0
for email, fields in updates.items():
    k = db.query(Kol).filter(Kol.email == email).first()
    if not k:
        print("!! 未找到:", email)
        continue
    k.channel_url = fields["channel_url"]
    k.subscribers = fields["subscribers"]
    k.country = fields["country"]
    k.niche = fields["niche"]
    k.platform = fields["platform"]
    k.content_category = fields["content_category"]
    k.source = "manual_lookup"
    ok += 1
    print("已回填:", k.name[:20], "|", k.channel_url[:40])
db.commit()
db.close()
print("回填完成:", ok, "/", len(updates))
