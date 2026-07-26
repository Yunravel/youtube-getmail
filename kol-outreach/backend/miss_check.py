from db import SessionLocal
from models import Message, Kol
db = SessionLocal()
msgs = db.query(Message).filter(Message.direction == "inbound").all()
kol_ids = set()
for m in msgs:
    if m.thread and m.thread.kol_id:
        kol_ids.add(m.thread.kol_id)
print("回信 message 总数:", len(msgs))
print("涉及 distinct KOL 数:", len(kol_ids))
kols = db.query(Kol).filter(Kol.id.in_(kol_ids)).all()
print("=== 各列缺失 KOL 数（共 %d 个）===" % len(kols))
for f, label in [("channel_url", "频道链接"), ("subscribers", "粉丝数"), ("country", "国家"), ("niche", "赛道"), ("platform", "平台")]:
    if f == "subscribers":
        n = sum(1 for k in kols if not (k.subscribers or 0))
    else:
        n = sum(1 for k in kols if not (getattr(k, f, None) or "").strip())
    print("  %s: 缺 %d" % (label, n))
print("=== 缺频道链接的 KOL ===")
for k in kols:
    if not (k.channel_url or "").strip():
        print("  id=%s %s | %s" % (k.id, (k.name or "")[:22], k.email))
db.close()
