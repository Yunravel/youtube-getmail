"""Operational endpoints for reliable Feishu synchronization."""
from fastapi import APIRouter

from services.feishu_push import (
    audit_sheet,
    enqueue_reconciliation,
    process_due_tasks,
    retry_conflicts,
    synchronization_status,
)

router = APIRouter()


@router.get("/status")
def status():
    return synchronization_status()


@router.get("/audit")
def audit():
    return audit_sheet()


@router.post("/reconcile")
def reconcile():
    return {"queued": enqueue_reconciliation()}


@router.post("/process")
def process():
    return process_due_tasks()


@router.post("/retry-conflicts")
def retry_duplicate_conflicts():
    return {"queued": retry_conflicts()}
