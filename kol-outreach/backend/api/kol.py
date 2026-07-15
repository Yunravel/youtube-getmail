"""
KOL 接口 - 列表/详情/CSV批量导入
爬虫产出 CSV 后,通过这里导入中台
"""
import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db
from models import Kol

router = APIRouter()


class KolOut(BaseModel):
    id: int
    channel_id: Optional[str] = None
    name: str
    channel_url: Optional[str] = None
    email: Optional[str] = None
    subscribers: int = 0
    country: Optional[str] = None
    niche: Optional[str] = None
    recent_videos: Optional[list] = None
    personal_intro: Optional[str] = None
    status: str = "pending"

    class Config:
        from_attributes = True


@router.get("", response_model=list[KolOut])
def list_kols(
    status: Optional[str] = None,
    niche: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """KOL 列表(分页 + 筛选)"""
    q = db.query(Kol)
    if status:
        q = q.filter(Kol.status == status)
    if niche:
        q = q.filter(Kol.niche == niche)
    total = q.count()
    items = q.order_by(Kol.id.desc()).offset((page - 1) * size).limit(size).all()
    return items


@router.get("/{kol_id}", response_model=KolOut)
def get_kol(kol_id: int, db: Session = Depends(get_db)):
    kol = db.query(Kol).get(kol_id)
    if not kol:
        raise HTTPException(404, "KOL not found")
    return kol


@router.post("/import-csv")
async def import_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    批量导入 KOL(爬虫产出 CSV → 中台入库)

    CSV 必填列: name, email
    可选列: channel_id, channel_url, subscribers, country, niche, recent_videos

    recent_videos 用 | 分隔多个标题:
      "标题1|标题2|标题3"

    返回: {imported: N, skipped: M}
    """
    if not file.filename.endswith(".csv"):
        raise HTTPException(400, "只支持 .csv 文件")

    content = await file.read()
    text = content.decode("utf-8-sig")  # 兼容 Excel BOM
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    skipped = 0

    for row in reader:
        email = (row.get("email") or "").strip()
        name = (row.get("name") or "").strip()

        # 必填校验
        if not email or not name:
            skipped += 1
            continue

        # 去重(按 email)
        exists = db.query(Kol).filter(Kol.email == email).first()
        if exists:
            skipped += 1
            continue

        # recent_videos: "标题1|标题2" → ["标题1", "标题2"]
        videos_str = row.get("recent_videos", "")
        recent_videos = [v.strip() for v in videos_str.split("|") if v.strip()] if videos_str else None

        try:
            subscribers = int((row.get("subscribers") or "0").replace(",", ""))
        except (TypeError, ValueError):
            skipped += 1
            continue

        kol = Kol(
            channel_id=row.get("channel_id", "").strip() or None,
            name=name,
            channel_url=row.get("channel_url", "").strip() or None,
            email=email,
            subscribers=subscribers,
            country=row.get("country", "").strip() or None,
            niche=row.get("niche", "").strip() or None,
            recent_videos=recent_videos,
            status="pending",
        )
        db.add(kol)
        imported += 1

    db.commit()
    return {"imported": imported, "skipped": skipped}


@router.delete("/{kol_id}")
def delete_kol(kol_id: int, db: Session = Depends(get_db)):
    kol = db.query(Kol).get(kol_id)
    if not kol:
        raise HTTPException(404, "KOL not found")
    db.delete(kol)
    db.commit()
    return {"deleted": kol_id}


class GenerateIntroIn(BaseModel):
    """批量生成个性化开场白"""
    kol_ids: list[int]
    our_product: str = "our product"   # 我们在推广什么


@router.post("/generate-intros")
def generate_intros(body: GenerateIntroIn, db: Session = Depends(get_db)):
    """
    批量为选中的 KOL 生成 GPT 个性化开场白
    - 没有最近视频的 KOL 会跳过(GPT 没素材)
    - 已有开场白的会覆盖
    """
    from services.ai_personalize import generate_intro, analyze_niche

    kols = db.query(Kol).filter(Kol.id.in_(body.kol_ids)).all()
    generated = 0
    skipped = 0

    for kol in kols:
        # 没有视频标题就没法个性化,跳过
        if not kol.recent_videos:
            skipped += 1
            continue

        # 没赛道的顺便分析一下
        if not kol.niche:
            kol.niche = analyze_niche(kol.recent_videos)

        intro = generate_intro(
            kol_name=kol.name,
            niche=kol.niche,
            recent_videos=kol.recent_videos,
            our_product=body.our_product,
        )
        if intro:
            kol.personal_intro = intro
            generated += 1
        else:
            skipped += 1

    db.commit()
    return {"generated": generated, "skipped": skipped}
