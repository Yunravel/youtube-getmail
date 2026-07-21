"""API 路由聚合"""
from fastapi import APIRouter

from api import kol, mailbox, threads, webhook, stats, operators, snov, crawler
from api import mailbox_credentials, attachments

api_router = APIRouter()
# 看板公开访问；webhook 仍由 Snov 专用 token 校验。
api_router.include_router(kol.router, prefix="/kols", tags=["KOL"])
api_router.include_router(operators.router, prefix="/operators", tags=["运营人员"])
api_router.include_router(threads.router, prefix="/threads", tags=["会话"])
api_router.include_router(mailbox.router, prefix="/mailbox", tags=["邮箱"])
api_router.include_router(webhook.router, prefix="/webhook", tags=["Webhook"])
api_router.include_router(stats.router, prefix="/stats", tags=["统计"])
api_router.include_router(snov.router, prefix="/snov", tags=["Snov"])
api_router.include_router(crawler.router, prefix="/crawler", tags=["采集"])
api_router.include_router(mailbox_credentials.router, prefix="/mailbox-credentials", tags=["邮箱凭据"])
api_router.include_router(attachments.router, prefix="/attachments", tags=["附件"])
