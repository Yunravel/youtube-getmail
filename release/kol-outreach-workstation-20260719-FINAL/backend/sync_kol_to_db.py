"""把 Snov 里的 KOL 同步到中台数据库(这样回信能匹配到 KOL)"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import csv
from db import init_db, SessionLocal
from models import Kol

init_db()
db = SessionLocal()

# 导入 Dola 38 + Mango 194
csvs = [
    (r'C:\Users\29012\ZCodeProject\Dola_KOL_for_snov.csv', 'Dola'),
    (r'C:\Users\29012\ZCodeProject\Mango_KOL_all.csv', 'Mango'),
]

total_new = 0
total_skip = 0

for csv_path, client in csvs:
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        email = row.get('Email') or row.get('email') or ''
        email = email.strip()
        if not email or '@' not in email:
            continue

        # 去重
        exists = db.query(Kol).filter(Kol.email == email).first()
        if exists:
            total_skip += 1
            continue

        name = (row.get('First name') or row.get('first_name') or '').strip()
        niche = row.get('Niche') or row.get('niche') or ''
        product = row.get('product') or ''

        kol = Kol(
            name=name or email.split('@')[0],
            email=email,
            niche=f"{niche} [{product}]" if product else niche,
            status='pending',
        )
        db.add(kol)
        total_new += 1

db.commit()
db.close()

print(f'✅ 完成')
print(f'新增: {total_new}')
print(f'跳过(重复): {total_skip}')
