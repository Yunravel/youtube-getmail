"""邮箱凭据管理 API（IMAP 附件同步用）。

密码永不通过 API 明文返回——列表只返回 has_password 布尔。
支持从 Snov 批量导入发信邮箱地址（密码留空，用户手动填）。
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db
from models import MailboxCredential
from services.crypto import encrypt_password, is_encryption_enabled

router = APIRouter()


class CredentialOut(BaseModel):
    id: int
    email: str
    provider: str
    imap_host: str
    imap_port: int
    snov_id: Optional[int] = None
    has_password: bool
    last_synced_at: Optional[datetime] = None
    last_sync_status: Optional[str] = None
    last_error: Optional[str] = None
    enabled: bool

    class Config:
        from_attributes = True


class CredentialCreate(BaseModel):
    email: str
    password: str
    provider: str = "gmail"
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    enabled: bool = True


class CredentialUpdate(BaseModel):
    password: Optional[str] = None       # 不传表示不改密码
    provider: Optional[str] = None
    imap_host: Optional[str] = None
    imap_port: Optional[int] = None
    enabled: Optional[bool] = None


def _to_out(cred: MailboxCredential) -> CredentialOut:
    return CredentialOut(
        id=cred.id,
        email=cred.email,
        provider=cred.provider,
        imap_host=cred.imap_host,
        imap_port=cred.imap_port,
        snov_id=cred.snov_id,
        has_password=bool(cred.encrypted_password),
        last_synced_at=cred.last_synced_at,
        last_sync_status=cred.last_sync_status,
        last_error=cred.last_error,
        enabled=cred.enabled,
    )


@router.get("", response_model=list[CredentialOut])
def list_credentials(db: Session = Depends(get_db)):
    """列出所有邮箱凭据（不返回密码）。"""
    creds = db.query(MailboxCredential).order_by(MailboxCredential.email).all()
    return [_to_out(c) for c in creds]


@router.get("/status")
def credentials_status():
    """凭据系统状态（加密是否启用等，前端展示用）。"""
    return {
        "encryption_enabled": is_encryption_enabled(),
    }


@router.post("", response_model=CredentialOut)
def create_credential(body: CredentialCreate, db: Session = Depends(get_db)):
    """新增邮箱凭据。明文密码经 Fernet 加密后存库。"""
    email = body.email.strip().lower()
    if db.query(MailboxCredential).filter(MailboxCredential.email == email).first():
        raise HTTPException(400, "邮箱已存在")
    cred = MailboxCredential(
        email=email,
        encrypted_password=encrypt_password(body.password),
        provider=body.provider,
        imap_host=body.imap_host,
        imap_port=body.imap_port,
        enabled=body.enabled,
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    return _to_out(cred)


@router.put("/{cred_id}", response_model=CredentialOut)
def update_credential(cred_id: int, body: CredentialUpdate, db: Session = Depends(get_db)):
    """更新凭据。password 留空表示不改。"""
    cred = db.get(MailboxCredential, cred_id)
    if not cred:
        raise HTTPException(404, "凭据不存在")
    if body.password is not None and body.password.strip():
        cred.encrypted_password = encrypt_password(body.password)
    if body.provider is not None:
        cred.provider = body.provider
    if body.imap_host is not None:
        cred.imap_host = body.imap_host
    if body.imap_port is not None:
        cred.imap_port = body.imap_port
    if body.enabled is not None:
        cred.enabled = body.enabled
    db.commit()
    db.refresh(cred)
    return _to_out(cred)


@router.delete("/{cred_id}")
def delete_credential(cred_id: int, db: Session = Depends(get_db)):
    cred = db.get(MailboxCredential, cred_id)
    if not cred:
        raise HTTPException(404, "凭据不存在")
    db.delete(cred)
    db.commit()
    return {"deleted": cred_id}


@router.post("/import-from-provider")
def import_from_provider(db: Session = Depends(get_db)):
    """从发信平台批量导入已配置的发信邮箱地址（只 gmail-smtp 类型，密码留空待填）。

    平台托管的邮箱（凭据不在用户手里）自动跳过。
    返回 {imported, skipped, skipped_no_imap}。
    """
    from services.snov_client import get_snov_client

    try:
        client = get_snov_client()
        result = client._request("GET", "/v2/sender-accounts/emails")
    except Exception as e:
        raise HTTPException(502, f"调发信平台失败: {e}")

    accounts = result.get("data", []) if isinstance(result, dict) else (result or [])
    imported = 0
    skipped_existing = 0
    skipped_no_imap = 0

    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        # 只导入用户自己掌控的 gmail-smtp 类型；平台托管的跳过
        if acc.get("provider") != "gmail-smtp":
            skipped_no_imap += 1
            continue
        email_addr = (acc.get("email_from") or "").strip().lower()
        if not email_addr or "@" not in email_addr:
            continue
        if db.query(MailboxCredential).filter(MailboxCredential.email == email_addr).first():
            skipped_existing += 1
            continue
        cred = MailboxCredential(
            email=email_addr,
            encrypted_password="",   # 密码留空，用户手动填
            provider="gmail",
            imap_host="imap.gmail.com",
            imap_port=993,
            snov_id=acc.get("id"),
            enabled=True,
        )
        db.add(cred)
        imported += 1

    db.commit()
    return {
        "imported": imported,
        "skipped_existing": skipped_existing,
        "skipped_no_imap": skipped_no_imap,
        "note": "导入的邮箱密码为空，请到邮箱配置页逐个填写应用专用密码。",
    }
