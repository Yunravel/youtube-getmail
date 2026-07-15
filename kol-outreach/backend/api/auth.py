"""看板 API 的轻量访问控制。"""
import hmac

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from config import settings

security = HTTPBasic()


def require_dashboard_auth(
    credentials: HTTPBasicCredentials = Depends(security),
) -> None:
    """保护运营数据；Snov webhook 使用独立 token，不经过此校验。"""
    if not settings.dashboard_auth_is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard authentication is not configured",
        )

    valid_user = hmac.compare_digest(credentials.username, settings.DASHBOARD_USERNAME)
    valid_password = hmac.compare_digest(credentials.password, settings.DASHBOARD_PASSWORD)
    if not (valid_user and valid_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid dashboard credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
