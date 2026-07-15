"""统计接口 - 响应时长 / 处理量 / 意向分布"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from db import get_db
from models import Thread, Kol, Message

router = APIRouter()


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    """总览数据 - Dashboard 用"""
    total_kols = db.query(func.count(Kol.id)).scalar() or 0
    total_threads = db.query(func.count(Thread.id)).scalar() or 0
    hot_threads = db.query(func.count(Thread.id)).filter(Thread.status == "hot").scalar() or 0
    open_threads = db.query(func.count(Thread.id)).filter(Thread.status == "open").scalar() or 0

    return {
        "total_kols": total_kols,
        "total_threads": total_threads,
        "hot_threads": hot_threads,
        "open_threads": open_threads,
    }


@router.get("/intent-distribution")
def intent_distribution(db: Session = Depends(get_db)):
    """意向分布(饼图)"""
    rows = db.query(
        Thread.last_intent, func.count(Thread.id)
    ).filter(
        Thread.last_intent.isnot(None)
    ).group_by(Thread.last_intent).all()
    return [{"intent": k, "count": v} for k, v in rows]
