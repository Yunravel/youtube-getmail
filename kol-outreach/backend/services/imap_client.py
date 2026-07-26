"""IMAP 邮箱客户端：经 SOCKS5 代理连 Gmail，抓取邮件和附件。

国内直连 imap.gmail.com 被墙，必须走代理（Clash 等）。通过 monkey-patch
socket.create_connection 让 imaplib.IMAP4_SSL 自动走 SOCKS5，无需子类化。

用法：
    with ImapMailbox(email, password) as mb:
        msgs = mb.fetch_unread(since_days=30)
        for em in msgs:
            for att in em.attachments:
                print(att.filename, att.size, att.content_type)
"""
from __future__ import annotations

import calendar
import email
import email.utils
import imaplib
import logging
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.header import decode_header
from typing import Optional

import socks

from config import settings

logger = logging.getLogger(__name__)


def _decode_header(value: Optional[str]) -> str:
    """解码 RFC 2047 邮件头（如 =?UTF-8?B?...?=）。"""
    if not value:
        return ""
    try:
        return str(email.header.make_header(decode_header(value)))
    except Exception:
        return str(value)


@dataclass
class EmailAttachment:
    """一个真实附件（已 base64 解码的 bytes）。"""
    filename: str
    content_type: str
    size: int
    data: bytes


@dataclass
class FetchedEmail:
    """从 IMAP 拉取并解析好的一封邮件。"""
    uid: str
    message_id: str           # RFC 822 Message-ID 头
    in_reply_to: str
    references: str
    from_email: str           # 已小写归一
    from_name: str
    to_email: str
    subject: str
    date: Optional[datetime]
    attachments: list[EmailAttachment] = field(default_factory=list)
    # 正文（用于把 Snov 未回传的第三方地址回信直接落库为中台消息）。
    body_text: str = ""
    body_html: str = ""


class ImapMailbox:
    """单个邮箱的 IMAP 操作封装。支持上下文管理器自动连接/登出。"""

    def __init__(
        self,
        email_addr: str,
        password: str,
        imap_host: str = "imap.gmail.com",
        imap_port: int = 993,
    ):
        self.email_addr = email_addr
        self.password = password
        self.imap_host = imap_host
        self.imap_port = imap_port
        self._imap: Optional[imaplib.IMAP4_SSL] = None
        self._orig_create_connection: Optional[callable] = None

    # ===== 上下文管理 =====
    def __enter__(self) -> "ImapMailbox":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # ===== 代理 socket 注入 =====
    def _install_proxy(self) -> None:
        """monkey-patch socket.create_connection 让 imaplib 走 SOCKS5。"""
        proxy_host = settings.IMAP_PROXY_HOST
        proxy_port = settings.IMAP_PROXY_PORT

        def patched(address, *args, **kwargs):
            s = socks.socksocket()
            s.set_proxy(socks.SOCKS5, proxy_host, proxy_port)
            s.settimeout(kwargs.get("timeout") or 30)
            s.connect(address)
            return s

        self._orig_create_connection = socket.create_connection
        socket.create_connection = patched

    def _restore_socket(self) -> None:
        if self._orig_create_connection is not None:
            socket.create_connection = self._orig_create_connection
            self._orig_create_connection = None

    # ===== 连接 =====
    def connect(self) -> None:
        """建立代理 socket + TLS + IMAP 登录。失败抛异常带邮箱名。

        代理链路（Clash 等）偶发 ``SSL EOF`` / ``socket error`` 断连，属于瞬时
        故障：这里按 IMAP_CONNECT_RETRIES 做指数退避重试，避免整个邮箱本轮
        同步直接失败（日志中多个 Gmail 邮箱因此丢过附件抓取）。
        """
        attempts = max(1, settings.IMAP_CONNECT_RETRIES)
        delay = max(0.5, settings.IMAP_CONNECT_RETRY_DELAY_SECONDS)
        last_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            if settings.IMAP_PROXY_ENABLED:
                self._install_proxy()
            try:
                self._imap = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
                self._imap.login(self.email_addr, self.password)
                return
            except Exception as e:
                last_error = e
                self._imap = None
                if attempt < attempts:
                    wait = delay * attempt
                    logger.warning(
                        "IMAP 连接失败（第 %d/%d 次） [%s]: %s；%.0f 秒后重试",
                        attempt, attempts, self.email_addr, e, wait,
                    )
                    time.sleep(wait)
            finally:
                # 只在建立连接阶段 patch；连接已建好后恢复，避免影响其他网络代码
                self._restore_socket()
        raise RuntimeError(
            f"IMAP 连接/登录失败 [{self.email_addr}]: {last_error}"
        ) from last_error

    def close(self) -> None:
        if self._imap:
            try:
                if self._imap.state in ("SELECTED", "AUTH"):
                    self._imap.logout()
            except Exception:
                pass
            self._imap = None

    # ===== 搜索 + 抓取 =====
    def fetch_unread(
        self,
        since_days: Optional[int] = None,
        only_unread: bool = True,
        limit: int = 200,
    ) -> list[FetchedEmail]:
        """拉取邮件并解析。

        only_unread=True：只抓未读（轮询模式）。
        only_unread=False + since_days：抓指定天数内所有邮件（手动模式）。
        limit：最多抓多少封，防一次性拉太多。
        """
        if not self._imap:
            raise RuntimeError("IMAP 未连接，先 connect() 或用 with 语句")

        self._imap.select("INBOX", readonly=True)

        # 构造搜索条件
        criteria = []
        if only_unread:
            criteria.append("UNSEEN")
        if since_days is not None and since_days > 0:
            since_date = (datetime.utcnow() - timedelta(days=since_days)).strftime("%d-%b-%Y")
            criteria.append(f'SINCE {since_date}')
        crit_str = " ".join(criteria) if criteria else "ALL"

        typ, data = self._imap.uid("search", None, crit_str)
        if typ != "OK":
            raise RuntimeError(f"IMAP search 失败 [{self.email_addr}]: {typ}")
        uids = data[0].split() if data[0] else []
        # 最新的在前（uid 升序，反转）
        uids = uids[::-1][:limit]

        results: list[FetchedEmail] = []
        for uid in uids:
            try:
                em = self._fetch_one(uid)
                if em:
                    results.append(em)
            except Exception as e:
                logger.warning("抓取邮件失败 uid=%s [%s]: %s", uid, self.email_addr, e)
        return results

    def _fetch_one(self, uid: bytes) -> Optional[FetchedEmail]:
        """抓取并解析单封邮件（按 uid，PEEK 不动已读状态）。"""
        typ, msg_data = self._imap.uid("fetch", uid, "(BODY.PEEK[])")
        if typ != "OK" or not msg_data or not msg_data[0]:
            return None
        # msg_data[0] 形如 (b'<uid> (BODY[] {size}', b'<raw bytes>')
        raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else msg_data[0]
        msg = email.message_from_bytes(raw)

        from_name, from_email = self._parse_address(msg.get("From", ""))
        to_email = self._parse_address(msg.get("To", ""))[1]
        subject = _decode_header(msg.get("Subject", ""))
        message_id = (msg.get("Message-ID") or "").strip().strip("<>")
        in_reply_to = (msg.get("In-Reply-To") or "").strip().strip("<>")
        references = (msg.get("References") or "").strip()
        date = self._parse_date(msg.get("Date"))

        attachments = self._extract_attachments(msg)
        body_text, body_html = self._extract_body(msg)

        return FetchedEmail(
            uid=uid.decode(),
            message_id=message_id,
            in_reply_to=in_reply_to,
            references=references,
            from_email=from_email.lower(),
            from_name=from_name,
            to_email=to_email.lower(),
            subject=subject,
            date=date,
            attachments=attachments,
            body_text=body_text,
            body_html=body_html,
        )

    # ===== 标记已读 =====
    def mark_read(self, uid: str) -> None:
        """把指定 uid 的邮件标记为已读（避免重复抓取）。

        需要先 select 邮箱（非 readonly）。本方法内部重新 select 为可写。
        """
        if not self._imap:
            return
        try:
            self._imap.select("INBOX")  # 可写模式
            self._imap.uid("store", uid, "+FLAGS", "\\Seen")
        except Exception as e:
            logger.warning("标记已读失败 uid=%s [%s]: %s", uid, self.email_addr, e)

    # ===== 解析辅助 =====
    @staticmethod
    def _parse_address(raw: str) -> tuple[str, str]:
        """解析 'Name <email@x.com>' → (name, email)。"""
        if not raw:
            return "", ""
        name, addr = email.utils.parseaddr(raw)
        return _decode_header(name), addr

    @staticmethod
    def _parse_date(raw: Optional[str]) -> Optional[datetime]:
        """解析 Date 头为 naive UTC；解析失败返回 None（调用方按缺失处理）。

        Date 头没带时区信息时按 UTC 解释：``mktime_tz`` 对无时区的 10 元组
        会用机器本地时区换算，UTC+8 的 Windows 工作站会偏 8 小时。
        """
        if not raw:
            return None
        try:
            tup = email.utils.parsedate_tz(raw)
            if not tup:
                return None
            if tup[9] is None:
                return datetime.utcfromtimestamp(calendar.timegm(tup[:9]))
            return datetime.utcfromtimestamp(email.utils.mktime_tz(tup))
        except Exception:
            return None

    @staticmethod
    def _extract_body(msg) -> tuple[str, str]:
        """提取正文，返回 ``(text, html)``。跳过附件部分，优先 text/plain。"""
        text_parts: list[str] = []
        html_parts: list[str] = []
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            ctype = part.get_content_type()
            if ctype not in ("text/plain", "text/html"):
                continue
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                continue
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                content = payload.decode(charset, errors="replace")
            except LookupError:
                content = payload.decode("utf-8", errors="replace")
            if ctype == "text/plain":
                text_parts.append(content)
            else:
                html_parts.append(content)
        return "\n".join(text_parts).strip(), "\n".join(html_parts).strip()

    @staticmethod
    def _extract_attachments(msg) -> list[EmailAttachment]:
        """遍历 MIME parts，提取 Content-Disposition: attachment 的部分。"""
        out: list[EmailAttachment] = []
        for part in msg.walk():
            if part.get_content_disposition() != "attachment":
                continue
            filename = _decode_header(part.get_filename()) or "attachment"
            try:
                payload = part.get_payload(decode=True) or b""
            except Exception:
                continue
            out.append(EmailAttachment(
                filename=filename,
                content_type=part.get_content_type() or "application/octet-stream",
                size=len(payload),
                data=payload,
            ))
        return out
