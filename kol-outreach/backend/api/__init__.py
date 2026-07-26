"""API 路由聚合"""
from fastapi import APIRouter, Depends

from api import kol, mailbox, threads, webhook, stats, operators, snov, crawler
from api import mailbox_credentials, attachments
from api import auto_replies
from api import feishu
from api.auth import require_dashboard_auth

api_router = APIRouter()

# webhook 由 Snov 服务器直接推送，走独立的 SNOV_WEBHOOK_TOKEN 校验，
# 不能要求 Basic Auth（Snov 不会带账号密码）。
api_router.include_router(webhook.router, prefix="/webhook", tags=["Webhook"])

# 其余看板/运营端点经公网可达，一律要求 Basic Auth；
# 凭据未配置时整体 503（宁可关闭也不公开）。
protected = APIRouter(dependencies=[Depends(require_dashboard_auth)])
protected.include_router(kol.router, prefix="/kols", tags=["KOL"])
protected.include_router(operators.router, prefix="/operators", tags=["运营人员"])
protected.include_router(threads.router, prefix="/threads", tags=["会话"])
protected.include_router(mailbox.router, prefix="/mailbox", tags=["邮箱"])
protected.include_router(stats.router, prefix="/stats", tags=["统计"])
protected.include_router(snov.router, prefix="/snov", tags=["Snov"])
protected.include_router(crawler.router, prefix="/crawler", tags=["采集"])
protected.include_router(mailbox_credentials.router, prefix="/mailbox-credentials", tags=["邮箱凭据"])
protected.include_router(attachments.router, prefix="/attachments", tags=["附件"])
protected.include_router(auto_replies.router, prefix="/auto-replies", tags=["自动回复"])
protected.include_router(feishu.router, prefix="/feishu", tags=["飞书同步"])
api_router.include_router(protected)
