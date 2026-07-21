"""邮箱应用专用密码的对称加密。

用 Fernet（AES128-CBC + HMAC）加密存 DB，主密钥从环境变量 ATTACHMENT_MASTER_KEY 读。
密钥缺失时降级为 base64（不加密），仅适合 dev 起步——会打警告日志。
"""
import base64
import logging

from config import settings

logger = logging.getLogger(__name__)

_fernet = None
_fernet_checked = False


def _get_fernet():
    """惰性初始化 Fernet。密钥未配置时返回 None（降级模式）。"""
    global _fernet, _fernet_checked
    if _fernet_checked:
        return _fernet
    _fernet_checked = True
    if not settings.attachment_master_key_configured:
        logger.warning(
            "ATTACHMENT_MASTER_KEY 未配置，邮箱密码将降级为 base64 明文存储。"
            "生产环境必须配置 Fernet 密钥。生成方法见 .env 注释。"
        )
        return None
    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(settings.ATTACHMENT_MASTER_KEY.encode())
    except Exception as e:
        logger.error("ATTACHMENT_MASTER_KEY 无效，降级为 base64 存储: %s", e)
        _fernet = None
    return _fernet


def encrypt_password(plain: str) -> str:
    """加密明文密码，返回可存 DB 的字符串。

    加密返回 Fernet token（str）；降级模式返回 base64 编码的明文。
    """
    if not plain:
        return ""
    f = _get_fernet()
    if f is None:
        return "b64:" + base64.b64encode(plain.encode()).decode()
    return "f:" + f.encrypt(plain.encode()).decode()


def decrypt_password(token: str) -> str:
    """解密 DB 里的密文，返回明文密码。

    兼容加密和降级两种格式；空串返回空串。
    """
    if not token:
        return ""
    if token.startswith("f:"):
        f = _get_fernet()
        if f is None:
            logger.error("密文是 Fernet 格式但密钥未配置，无法解密")
            return ""
        try:
            return f.decrypt(token[2:].encode()).decode()
        except Exception as e:
            logger.error("Fernet 解密失败: %s", e)
            return ""
    if token.startswith("b64:"):
        try:
            return base64.b64decode(token[4:].encode()).decode()
        except Exception:
            return ""
    # 兼容历史裸数据
    return token


def is_encryption_enabled() -> bool:
    """是否启用了真正的 Fernet 加密（用于状态展示）。"""
    return _get_fernet() is not None
