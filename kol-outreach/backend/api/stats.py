"""统计接口 - 响应时长 / 处理量 / 意向分布"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from db import get_db
from models import CrawlerProduct, Thread, Kol, Message
from services.crawler.config_rules import productTerms

router = APIRouter()
logger = logging.getLogger(__name__)


def _list_all_products(db: Session) -> list[tuple[str, str]]:
    """产品权威清单：内置 productTerms + crawler_product 自定义表。

    返回 ``[(normalized_code, display_label), ...]``，按字典序稳定排序。
    新增内置产品（改 ``config_rules.productTerms``）或用户在前端新建自定义
    产品（写 ``crawler_product`` 表）后，本函数下次调用立即返回新条目，
    总览页的项目下拉列表据此自动扩展，无需改代码。
    """
    seen: dict[str, str] = {}
    # 内置产品：Dreamina / Pippit / Kimi / Dola / ...
    for name in productTerms.keys():
        code = name.casefold()
        seen[code] = name
    # 自定义产品：用户在采集器页面新增的
    rows = db.query(CrawlerProduct).all()
    for row in rows:
        code = row.name_normalized or (row.name or "").casefold()
        if code and code not in seen:
            seen[code] = row.name
    # 按显示名排序，保证下拉顺序稳定
    return sorted(seen.items(), key=lambda kv: kv[1].casefold())


def _match_product_for_campaign(campaign_name: str, products: list[tuple[str, str]]) -> Optional[str]:
    """根据 campaign 名模糊匹配产品：campaign 名包含产品名（不区分大小写）。

    匹配优先按显示名长度倒序，避免短名误吞（如 ``Dola`` 不会抢占 ``Dolatron`` 之类）。
    Snov campaign 名形如 ``DOLA-2026-07-17`` / ``Pippit-2026-07-15`` /
    ``Dreamina-Q3``，使用 contains 比前缀更宽松，能容纳未来的命名风格。
    """
    if not campaign_name:
        return None
    name_lower = campaign_name.lower()
    # 长串优先匹配，避免短产品名误中长产品名
    for code, label in sorted(products, key=lambda kv: len(kv[1]), reverse=True):
        if label.lower() in name_lower:
            return code
    return None


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


def _safe_float(value) -> float:
    """Snov 把百分比返回成带 % 的字符串（"38%"），统一解析成 float。"""
    if value is None:
        return 0.0
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return 0.0


def _resolve_campaign_ids_for_project(project: str, db: Session) -> str:
    """把项目 code 映射到 Snov campaign_id 列表（逗号分隔）。

    匹配规则：campaign 名包含某个产品显示名（不区分大小写）即归属该产品。
    Snov 的 ``/statistics/campaign-analytics`` 接受逗号分隔的 ``campaign_id``，
    因此把该项目下所有 campaign 的 ID 拼起来一次性聚合，比循环调用更高效。
    """
    from services.snov_client import get_snov_client
    products = _list_all_products(db)
    campaigns = get_snov_client().list_campaigns()
    ids: list[str] = []
    for item in campaigns:
        cid = str(item.get("id") or item.get("hash") or "")
        raw_name = item.get("campaign")
        name = raw_name.get("name") if isinstance(raw_name, dict) else raw_name
        if cid and _match_product_for_campaign(name or "", products) == project:
            ids.append(cid)
    return ",".join(ids)


@router.get("/campaign-projects")
def campaign_projects(db: Session = Depends(get_db)):
    """列出可选项目，供前端选择器渲染。

    产品权威清单来自采集配置（``productTerms`` 内置 + ``crawler_product``
    自定义表），与 Snov 解耦。这样无论是否已建过 Snov campaign，所有产品
    都会出现在下拉里；未跑过营销活动的产品，KPI 自然显示为 0。

    返回示例：``[{"code":"all","label":"全部项目"},
                  {"code":"dola","label":"Dola"}, ...]``
    """
    products = _list_all_products(db)
    result = [{"code": "all", "label": "全部项目"}]
    for code, label in products:
        result.append({"code": code, "label": label})
    return result


@router.get("/campaign-rates")
def campaign_rates(
    project: str = Query("all", description="all / dola / pippit / dreamina / ..."),
    db: Session = Depends(get_db),
):
    """打开率 / 回复率 / 合作率，用于总览看板顶部 KPI 卡。

    ``project`` 为 ``all`` 时聚合全账户；否则按产品名模糊匹配 Snov campaign
    （如 ``dola`` 匹配所有 campaign 名包含 "Dola" 的活动）再聚合。

    数据取自 Snov ``campaign-analytics``：
      - 打开率 = email_opens_rate
      - 回复率 = email_replies_rate
      - 合作率 = interested / contacted_by_email（Snov 已用 AI 把回复分类成
        interested / maybe / not_interested，"感兴趣"是合作意向的最佳代理指标）
    """
    open_rate = 0.0
    reply_rate = 0.0
    cooperation_rate = 0.0
    open_count = 0
    reply_count = 0
    interested_count = 0
    contacted = 0

    try:
        from services.snov_client import get_snov_client
        client = get_snov_client()

        campaign_id = None
        if project and project != "all":
            campaign_id = _resolve_campaign_ids_for_project(project, db)
            if not campaign_id:
                # 该项目暂无 campaign：直接返回 0，避免 Snov 把整账户当成"无过滤"。
                return {
                    "project": project,
                    "open_rate": 0.0, "reply_rate": 0.0, "cooperation_rate": 0.0,
                    "open_count": 0, "reply_count": 0,
                    "cooperated_count": 0, "contacted_by_email": 0,
                }

        payload = client.get_campaign_analytics(campaign_id=campaign_id)
        # Snov v2 平铺返回字段，也兼容被 data 包裹的情况。
        metrics = payload.get("data", payload) if isinstance(payload, dict) else {}

        open_rate = _safe_float(metrics.get("email_opens_rate"))
        reply_rate = _safe_float(metrics.get("email_replies_rate"))
        open_count = int(metrics.get("email_opens") or 0)
        reply_count = int(metrics.get("email_replies") or 0)
        contacted = int(metrics.get("contacted_by_email") or 0)
        interested_count = int(metrics.get("interested") or 0)

        # 合作率 = 意向合作人数 / 总联系人数
        cooperation_rate = (
            round(interested_count * 100.0 / contacted, 2) if contacted else 0.0
        )
    except Exception as exc:
        logger.warning("Snov campaign-analytics 调用失败，KPI 将显示 0: %s", exc)

    return {
        "project": project or "all",
        "open_rate": round(open_rate, 2),
        "reply_rate": round(reply_rate, 2),
        "cooperation_rate": round(cooperation_rate, 2),
        "open_count": open_count,
        "reply_count": reply_count,
        "cooperated_count": interested_count,
        "contacted_by_email": contacted,
    }
