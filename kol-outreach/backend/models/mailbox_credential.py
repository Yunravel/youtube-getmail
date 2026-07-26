"""IMAP 邮箱凭据表 - 存发信邮箱的应用专用密码，用于直连 Gmail 抓附件。

密码字段加密存储（services/crypto.py 用 Fernet），永不通过 API 明文返回。
"""
from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from db import Base


class MailboxCredential(Base):
    __tablename__ = "mailbox_credential"

    id = Column(Integer, primary_key=True, index=True)
    # 发信邮箱地址（如 ducakanita@gmail.com），唯一
    email = Column(String(200), nullable=False, unique=True, index=True)
    # Fernet 加密后的应用专用密码（密文）。缺失密钥时降级 base64。
    encrypted_password = Column(Text, nullable=False)
    # 邮箱服务商类型：gmail / google_workspace / custom
    provider = Column(String(30), nullable=False, default="gmail")
    imap_host = Column(String(100), nullable=False, default="imap.gmail.com")
    imap_port = Column(Integer, nullable=False, default=993)
    smtp_host = Column(String(100), nullable=False, default="smtp.gmail.com")
    smtp_port = Column(Integer, nullable=False, default=465)
    smtp_use_ssl = Column(Boolean, nullable=False, default=True)
    smtp_verified_at = Column(DateTime, nullable=True)
    smtp_last_error = Column(Text, nullable=True)
    # Snov 里的发信邮箱 id（import-from-snov 时回填，便于追溯）
    snov_id = Column(Integer, nullable=True)
    # 运行时状态（最近一次同步的结果，便于前端展示和排障）
    last_synced_at = Column(DateTime, nullable=True)
    last_sync_status = Column(String(20), nullable=True)  # success / partial / failed
    last_error = Column(Text, nullable=True)
    # 是否参与同步。关闭的邮箱不会被轮询/手动同步抓取。
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
