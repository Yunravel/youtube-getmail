"""
KOL 接口 - 列表/详情/CSV批量导入
爬虫产出 CSV 后,通过这里导入中台
"""
import csv
import io
from datetime import datetime
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
    snov_prospect_id: Optional[str] = None
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    locality: Optional[str] = None
    position: Optional[str] = None
    company_name: Optional[str] = None
    company_site: Optional[str] = None
    phones: Optional[str] = None
    linkedin_url: Optional[str] = None
    platform: Optional[str] = None
    social_handle: Optional[str] = None
    profile_url: Optional[str] = None
    followers: int = 0
    priority: Optional[str] = None
    content_category: Optional[str] = None
    source: Optional[str] = None
    contact_notes: Optional[str] = None
    snov_list_id: Optional[str] = None
    snov_list_name: Optional[str] = None
    snov_list_ids: Optional[list] = None
    subscribers: int = 0
    country: Optional[str] = None
    niche: Optional[str] = None
    recent_videos: Optional[list] = None
    personal_intro: Optional[str] = None
    status: str = "pending"
    # Tier 1 标准化新增列（2026-07，由 contact_notes 拆出）
    avg_views_10d: Optional[int] = None
    language: Optional[str] = None
    email_status: Optional[str] = None
    email_source: Optional[str] = None
    collect_status: Optional[str] = None
    collect_at: Optional[str] = None
    source_link: Optional[str] = None
    fit_project_code: Optional[str] = None

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
        name = (row.get("name") or row.get("fullName") or "").strip()
        if not name:
            name = " ".join(
                value for value in (
                    (row.get("firstName") or "").strip(),
                    (row.get("lastName") or "").strip(),
                ) if value
            )

        # 必填校验
        if not email or not name:
            skipped += 1
            continue

        email = email.lower()

        # 去重(按不区分大小写的 email)
        from sqlalchemy import func
        exists = db.query(Kol).filter(func.lower(Kol.email) == email).first()
        if exists:
            skipped += 1
            continue

        # recent_videos: "标题1|标题2" → ["标题1", "标题2"]
        videos_str = row.get("recent_videos", "")
        recent_videos = [v.strip() for v in videos_str.split("|") if v.strip()] if videos_str else None

        try:
            followers_text = row.get("customFields[followers]") or row.get("subscribers") or "0"
            subscribers = int(followers_text.replace(",", ""))
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
            full_name=name,
            first_name=(row.get("firstName") or "").strip() or None,
            last_name=(row.get("lastName") or "").strip() or None,
            locality=(row.get("locality") or "").strip() or None,
            position=(row.get("position") or "").strip() or None,
            company_name=(row.get("companyName") or "").strip() or None,
            company_site=(row.get("companySite") or "").strip() or None,
            phones=(row.get("phones") or "").strip() or None,
            linkedin_url=(row.get("socialLinks[linkedIn]") or "").strip() or None,
            platform=(row.get("customFields[platform]") or "").strip() or None,
            social_handle=(row.get("customFields[socialHandle]") or "").strip() or None,
            profile_url=(row.get("customFields[profileUrl]") or row.get("channel_url") or "").strip() or None,
            followers=subscribers,
            priority=(row.get("customFields[priority]") or "").strip().upper() or None,
            content_category=(row.get("customFields[contentCategory]") or row.get("niche") or "").strip() or None,
            source=(row.get("customFields[source]") or "").strip() or None,
            contact_notes=(row.get("customFields[notes]") or "").strip() or None,
            snov_list_id=(row.get("listId") or "").strip() or None,
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


@router.post("/import-candidate")
async def import_candidate(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """导入 KOL-Find 多平台候选池 Excel → kol_candidate 大数据库 + 选入 kol/kol_email。

    接受 .xlsx（含「全部候选」表，28 列）。处理：
      1. 全量写入 kol_candidate（按 platform+account 去重）
      2. 有联系邮箱的选入 kol 表（邮箱去重，已存在跳过）
      3. 多邮箱拆分写入 kol_email
    幂等：重复上传只插新行，不产生重复。

    返回统计：candidate_inserted/skipped, kol_inserted/skipped, kol_email_inserted。
    """
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "只支持 .xlsx 文件")

    content = await file.read()
    if not content:
        raise HTTPException(400, "文件为空")

    from scripts.import_kol_candidate import run_import

    batch = f"upload-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    try:
        stats = run_import(content, commit=True, batch=batch, db=db)
    except ValueError as e:
        # Excel 格式错误（缺表/缺列）
        raise HTTPException(422, str(e))
    return stats
