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
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from db import get_db
from models import (
    FeishuSyncTask, Kol, KolEmail, Message, Note, ProjectAssessment,
    ScheduledReply, SendLog, Thread,
)
from services.country_normalize import normalize_country_for_storage
from services.niche_normalize import classify_niche
from services.email_utils import ensure_kol_email

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


@router.get("")
def list_kols(
    status: Optional[str] = None,
    niche: Optional[str] = None,
    country: Optional[str] = None,
    min_followers: Optional[int] = Query(None, ge=0),
    max_followers: Optional[int] = Query(None, ge=0),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """KOL 列表(分页 + 筛选)。

    筛选维度：status / niche / country / 粉丝数区间(min_followers, max_followers)。
    niche 与 country 均为子串模糊匹配。country 已在入库时归一为规范名
    （见 services/country_normalize.normalize_country_for_storage），无需查询层再展开别名。
    粉数以 subscribers 为准（与列表展示口径一致）。
    返回 ``{items, total}``，total 为精确总数，供前端分页。
    """
    q = db.query(Kol)
    if status:
        q = q.filter(Kol.status == status)
    if niche:
        q = q.filter(Kol.niche.ilike(f"%{niche}%"))
    if country:
        q = q.filter(Kol.country.ilike(f"%{country}%"))
    if min_followers is not None:
        q = q.filter(Kol.subscribers >= min_followers)
    if max_followers is not None:
        q = q.filter(Kol.subscribers <= max_followers)
    total = q.count()
    items = q.order_by(Kol.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"items": items, "total": total}


@router.get("/filters/options")
def kol_filter_options(db: Session = Depends(get_db)):
    """KOL 列表筛选项的可选值（国家列表 + 赛道列表，供前端下拉）。

    country 已在入库时归一为规范名（无中英文混杂），这里直接去重按频次降序。
    niche 同样去重按频次降序，过滤脏值。
    """
    # 国家：已归一，直接去重 + 频次降序
    country_rows = (
        db.query(Kol.country, func.count(Kol.id))
        .filter(Kol.country.isnot(None))
        .filter(Kol.country != "")
        .group_by(Kol.country)
        .order_by(func.count(Kol.id).desc())
        .all()
    )
    countries = [r[0] for r in country_rows]

    # 赛道：去重 + 频次降序，过滤脏值
    niche_rows = (
        db.query(Kol.niche, func.count(Kol.id))
        .filter(Kol.niche.isnot(None))
        .filter(Kol.niche != "")
        .filter(Kol.niche != "/")
        .group_by(Kol.niche)
        .order_by(func.count(Kol.id).desc())
        .all()
    )
    niches = [r[0] for r in niche_rows]
    return {"countries": countries, "niches": niches}


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

        # 赛道归一：人工填的 niche 可能混入产品名，剥离产品名到 fit_project_code。
        niche_norm, niche_product = classify_niche(row.get("niche", "").strip() or None)

        kol = Kol(
            channel_id=row.get("channel_id", "").strip() or None,
            name=name,
            channel_url=row.get("channel_url", "").strip() or None,
            email=email,
            subscribers=subscribers,
            country=normalize_country_for_storage(row.get("country", "")),
            niche=niche_norm,
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
            content_category=niche_norm,  # 与 niche 同步（归一后的规范赛道）
            source=(row.get("customFields[source]") or "").strip() or None,
            contact_notes=(row.get("customFields[notes]") or "").strip() or None,
            snov_list_id=(row.get("listId") or "").strip() or None,
            fit_project_code=niche_product,  # 产品名剥离到产品归属字段
            status="pending",
        )
        db.add(kol)
        db.flush()  # 需要 kol.id 才能写 kol_email 子表（FK）
        # 同步 kol_email 子表，避免 CSV 导入的 KOL 漂移（规范 §5.1）。
        ensure_kol_email(db, kol.id, email, is_primary=True, source="csv_import")
        imported += 1

    db.commit()
    return {"imported": imported, "skipped": skipped}


@router.delete("/{kol_id}")
def delete_kol(kol_id: int, db: Session = Depends(get_db)):
    kol = db.query(Kol).get(kol_id)
    if not kol:
        raise HTTPException(404, "KOL not found")

    # thread.kol_id / send_log.kol_id / feishu_sync_task.kol_id 等外键没有
    # ON DELETE CASCADE,PostgreSQL 会拒绝直接删 KOL(SQLite 开发库默认不查外键,
    # 所以本地看不出来)。必须先删引用方:
    # 引用 thread/message 的表 → message → thread → 引用 kol 的表 → kol 本体。
    thread_ids = [tid for (tid,) in db.query(Thread.id).filter(Thread.kol_id == kol_id)]
    if thread_ids:
        db.query(ScheduledReply).filter(
            ScheduledReply.thread_id.in_(thread_ids)
        ).delete(synchronize_session=False)
        db.query(Note).filter(Note.thread_id.in_(thread_ids)).delete(synchronize_session=False)
    db.query(FeishuSyncTask).filter(FeishuSyncTask.kol_id == kol_id).delete(synchronize_session=False)
    send_log_owned = SendLog.kol_id == kol_id
    if thread_ids:
        send_log_owned = or_(send_log_owned, SendLog.thread_id.in_(thread_ids))
    db.query(SendLog).filter(send_log_owned).delete(synchronize_session=False)
    if thread_ids:
        db.query(Message).filter(Message.thread_id.in_(thread_ids)).delete(synchronize_session=False)
        db.query(Thread).filter(Thread.id.in_(thread_ids)).delete(synchronize_session=False)
    # kol_email / project_assessment 虽声明了 ondelete=CASCADE,但 SQLite 开发库
    # 不执行,显式删除让两种库行为一致。
    db.query(KolEmail).filter(KolEmail.kol_id == kol_id).delete(synchronize_session=False)
    db.query(ProjectAssessment).filter(
        ProjectAssessment.kol_id == kol_id
    ).delete(synchronize_session=False)
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


@router.post("/import-email-collection")
async def import_email_collection(
    file: UploadFile = File(...),
    preset: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """导入"邮箱采集结果"格式 Excel（Richup/Pippit/Dola 三种）→ 大数据库 + 选入 kol。

    与 /import-candidate 的区别：这个接口处理 22-23 列的邮箱采集结果格式，
    /import-candidate 处理 28 列的 KOL-Find 候选池格式。

    preset 不传则按 sheet 名/列名自动识别（richup/pippit/dola）。
    含增量补全：已存在的 KOL 会被补全粉丝数/平台等空字段。
    """
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "只支持 .xlsx 文件")

    content = await file.read()
    if not content:
        raise HTTPException(400, "文件为空")

    from scripts.import_email_collection import run_import as run_email_import

    batch = f"upload-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    try:
        stats = run_email_import(content, preset, commit=True, batch=batch, db=db)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return stats


@router.post("/enrich-empty")
def enrich_empty_profiles(db: Session = Depends(get_db)):
    """补全回信建档、但无频道画像的 KOL。

    找出所有 channel_url 为空 且 status 属于回信类（in_conversation / sent）
    的 KOL，丢线程池异步用 name 搜 YouTube + about 页邮箱验证后填空式回写。
    立即返回排队数量，不等待抓取完成。
    """
    from services.kol_enrich import enrich_reply_kol, ENRICHABLE_STATUSES
    from concurrent.futures import ThreadPoolExecutor

    rows = (
        db.query(Kol.id)
        .filter(Kol.status.in_(ENRICHABLE_STATUSES))
        .filter((Kol.channel_url == None) | (Kol.channel_url == ""))  # noqa: E711
        .all()
    )
    kol_ids = [r[0] for r in rows]
    if not kol_ids:
        return {"queued": 0}

    pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="kol-enrich")
    for kid in kol_ids:
        pool.submit(enrich_reply_kol, kid)
    pool.shutdown(wait=False)
    return {"queued": len(kol_ids)}
