"""裝置認證端點。

對應參考專案 dl-app-api 的 `src/api/auth.py`：
    signup       → POST /api/auth/device   （裝置匿名註冊，不需帳號密碼）
    login        → 無                       （沒有帳號，故無登入流程）
    refresh_token → POST /api/auth/refresh

以 APIRouter 分檔，未來其他端點也能逐步從 main.py 搬出來。
"""

import logging
import time

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from core.auth import decode_refresh_token, generate_tokens, hash_device_id
from core.config import Limit
from db.devices_db import is_active, upsert_device

logger = logging.getLogger(__name__)

module = APIRouter(prefix="/api/auth", tags=["Auth"])


class DeviceRegisterRequest(BaseModel):
    # Android 端傳 ANDROID_ID；伺服器雜湊後才使用，不會原樣儲存
    device_id: str = Field(..., min_length=1, max_length=256)


@module.post("/device")
def register_device(req: DeviceRegisterRequest):
    """裝置匿名註冊：以裝置識別碼換取一組 JWT。

    等同參考專案的 signup，差別在於不需要 email 與密碼。任何呼叫端都能
    註冊（與 signup 相同的信任模型），價值在於註冊後 device_id 由伺服器
    簽在 token 內，客戶端無法再逐次請求偽造身分。
    """
    device_id = hash_device_id(req.device_id)
    upsert_device(device_id)

    logger.info(f"裝置註冊：{device_id}")
    return {"status": "success", **generate_tokens(device_id)}


@module.post("/refresh")
def refresh_token(authorization: str = Header(..., description="Bearer <refresh_token>")):
    """以 refresh token 換取新的 access token。

    沿用參考專案的做法：refresh token 尚未接近到期時原樣沿用，
    只有剩餘效期不足 7 天才一併換發，避免每次續期都產生新的長效憑證。
    """
    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_refresh_token(token)

    device_id = payload.get("device_id", "")
    if not device_id or not is_active(device_id):
        # 裝置不存在（例如伺服器重啟後資料表清空）或已被封鎖。
        # App 端收到 401 後會重新走一次註冊流程。
        logger.info(f"refresh 失敗，裝置無效：{device_id}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    upsert_device(device_id)  # 更新 last_seen

    near_expiry = payload["exp"] - int(time.time()) < 60 * 60 * 24 * 7
    tokens = generate_tokens(device_id, gen_refresh=near_expiry)
    if not near_expiry:
        tokens["refresh_token"] = token

    return {"status": "success", **tokens}
