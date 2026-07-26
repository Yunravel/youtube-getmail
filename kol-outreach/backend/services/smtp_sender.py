"""SMTP transport with explicit pre-send vs ambiguous-delivery failures."""
from __future__ import annotations

import html
import smtplib
import socket
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import socks

from config import settings
from services.crypto import decrypt_password


class PreSendError(RuntimeError):
    pass


class AmbiguousDeliveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class SmtpCredentialSnapshot:
    """:func:`send_message` 所需凭据字段的纯数据快照。

    SMTP 发送阶段不得持有 DB session（DATABASE_DEVELOPMENT.md §8），而 ORM
    凭据对象在 commit（expire_on_commit=True）后属性全部过期、session close 后
    变 detached，届时 :func:`_connect` 读任何属性都会抛 DetachedInstanceError。
    调用方应在关闭 session 前用 :meth:`from_credential` 拍快照，把快照传给
    :func:`send_message`。本模块按属性鸭子类型取值，:func:`test_smtp_connection`
    的调用方仍可直接传 live ORM 对象，两者属性同名。
    """

    email: str
    encrypted_password: str
    smtp_host: str
    smtp_port: int
    smtp_use_ssl: bool

    @classmethod
    def from_credential(cls, credential) -> "SmtpCredentialSnapshot":
        return cls(
            email=credential.email,
            encrypted_password=credential.encrypted_password,
            smtp_host=credential.smtp_host,
            smtp_port=credential.smtp_port,
            smtp_use_ssl=credential.smtp_use_ssl,
        )


class _ProxySocketMixin:
    def _get_socket(self, host, port, timeout):
        if not settings.SMTP_PROXY_ENABLED:
            return super()._get_socket(host, port, timeout)
        raw = socks.create_connection(
            (host, port), timeout=timeout,
            proxy_type=socks.SOCKS5,
            proxy_addr=settings.SMTP_PROXY_HOST,
            proxy_port=settings.SMTP_PROXY_PORT,
        )
        if isinstance(self, smtplib.SMTP_SSL):
            return self.context.wrap_socket(raw, server_hostname=host)
        return raw


class ProxySMTP_SSL(_ProxySocketMixin, smtplib.SMTP_SSL):
    pass


class ProxySMTP(_ProxySocketMixin, smtplib.SMTP):
    pass


def _connect(credential, timeout: int = 30):
    password = decrypt_password(credential.encrypted_password)
    if not password:
        raise PreSendError("邮箱没有可用密码")
    try:
        if credential.smtp_use_ssl:
            client = ProxySMTP_SSL(
                credential.smtp_host, credential.smtp_port, timeout=timeout,
                context=ssl.create_default_context(),
            )
        else:
            client = ProxySMTP(credential.smtp_host, credential.smtp_port, timeout=timeout)
            client.ehlo()
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        client.login(credential.email, password)
        return client
    except (smtplib.SMTPException, OSError, socket.error) as exc:
        raise PreSendError(str(exc)) from exc


def test_smtp_connection(credential) -> None:
    client = _connect(credential)
    try:
        client.noop()
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def build_reply_message(*, from_email: str, to_email: str, subject: str, body: str,
                        in_reply_to: str, references: str = "") -> EmailMessage:
    message = EmailMessage()
    message["From"] = from_email
    message["To"] = to_email
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=from_email.split("@")[-1])
    message["In-Reply-To"] = f"<{in_reply_to.strip('<>')}>"
    refs = [token for token in (references or "").split() if token]
    canonical = f"<{in_reply_to.strip('<>')}>"
    if canonical not in refs:
        refs.append(canonical)
    message["References"] = " ".join(refs)
    message.set_content(body)
    safe_html = "<div>" + html.escape(body).replace("\n", "<br>\n") + "</div>"
    message.add_alternative(safe_html, subtype="html")
    return message


def send_message(credential, message: EmailMessage) -> None:
    client = _connect(credential)
    try:
        refused = client.send_message(message)
        if refused:
            raise AmbiguousDeliveryError(f"recipient refused: {refused}")
    except (smtplib.SMTPServerDisconnected, TimeoutError, OSError) as exc:
        # DATA may already have been accepted; retrying could duplicate delivery.
        raise AmbiguousDeliveryError(str(exc)) from exc
    except smtplib.SMTPException as exc:
        raise AmbiguousDeliveryError(str(exc)) from exc
    finally:
        try:
            client.quit()
        except Exception:
            client.close()
