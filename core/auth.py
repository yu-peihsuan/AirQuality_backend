"""裝置匿名憑證（JWT）與管理員驗證。

架構參考老師提供的 dl-app-api（`src/core/auth.py`）：
  - HS256 JWT，access／refresh 雙 token
  - 兩種 token 的 aud 不同，refresh token 無法當作 access token 使用
  - 端點以 `Depends(get_caller_identity)` 保護，取得的身分同時是資料歸屬來源

與參考專案的差異：為什麼不做 signup / login
--------------------------------------------
參考專案是社群 App，使用者本來就要註冊帳號，因此以 email + bcrypt 密碼
換取 JWT。本專案是公用空氣品質資訊服務，使用者開啟 App 只是想看 AQI，
強制註冊會直接流失使用者。

因此把「email + 密碼」換成「裝置匿名註冊」：App 首次啟動時以裝置識別碼
換取一組 JWT，之後所有請求帶 Bearer token。安全強度等同參考專案的
signup（任何人都能註冊一個新身分），但關鍵差別在於——註冊完成後
device_id 由伺服器簽在 token 裡，客戶端無法再逐次請求自行竄改。
回報頻率限制原本讀取 request body 的 device_id（改個字串即可繞過），
改讀 token 內的身分後才真正鎖得住。

已知限制：攻擊者仍可重複呼叫註冊端點取得大量新身分，藉此繞過以裝置為
單位的頻率限制。要擋住這一層需要裝置證明（如 Firebase App Check /
Play Integrity），列為後續工作。

隱私處理
--------
Android 端送來的是 ANDROID_ID（硬體相關識別碼）。伺服器不直接儲存或
簽發原始值，而是先以 JWT_SECRET 作為 pepper 取 HMAC-SHA256 後截斷，
得到與硬體無關的代稱。資料庫與 token 內都只出現這個代稱。

回應格式的例外
--------------
本專案其他端點失敗時一律回 HTTP 200 加 {"status": "error"}。認證是刻意的
例外，直接回 401／403／500：呼叫端要靠狀態碼決定「重新註冊」或「放棄」，
Cloud Run 的錯誤率監控也需要分辨被拒絕與成功，包在 200 裡就失去意義了。
"""

import hashlib
import hmac
import logging
import time

import jwt
from fastapi import Header, HTTPException, status
from pydantic import BaseModel

from core.config import Config, Limit, TokenAudience
from db.devices_db import is_revoked

logger = logging.getLogger(__name__)


class CallerIdentity(BaseModel):
    """通過驗證的呼叫端身分。device_id 為雜湊後的裝置代稱。"""

    device_id: str


# ── 密鑰取用 ─────────────────────────────────────────────────────────────────

def _secret() -> str:
    """取得 JWT 密鑰；未設定時拒絕服務（fail closed，不退回預設值）。"""
    if not Config.jwt_secret:
        logger.error("JWT_SECRET 未設定：認證端點無法運作，請於環境變數補上")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server auth is not configured",
        )
    return Config.jwt_secret


def hash_device_id(raw_device_id: str) -> str:
    """將 Android 端送來的原始裝置識別碼轉為與硬體無關的代稱。

    以 JWT_SECRET 為 pepper 取 HMAC-SHA256 並截斷至 32 字元。
    注意：JWT_SECRET 一旦更換，所有裝置代稱都會改變，等同全體重新註冊
    （既有回報的頻率限制紀錄失去對應，但視窗僅 3 分鐘，影響可忽略）。
    """
    digest = hmac.new(
        _secret().encode("utf-8"),
        raw_device_id.strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:32]


# ── 簽發 ─────────────────────────────────────────────────────────────────────

def generate_tokens(device_id: str, gen_refresh: bool = True) -> dict:
    """為指定裝置代稱簽發 access token（可選 refresh token）。

    時間刻意使用 UTC epoch 秒數（time.time()），不走 core/timeutil 的台灣時間。
    JWT 規格明定 iat／exp 是 UTC epoch，PyJWT 驗證時也以 UTC epoch 比對；
    換成台灣時間會讓效期整整算錯 8 小時。這是全系統唯一不使用台灣時間的地方。
    """
    secret = _secret()
    now = int(time.time())

    payload = {
        "device_id": device_id,
        "iss": Config.jwt_issuer,
        "iat": now,
    }

    tokens = {
        "access_token": jwt.encode(
            payload | {
                "aud": TokenAudience.ACCESS,
                "exp": now + Limit.ACCESS_TOKEN_EXPIRY,
            },
            secret,
            algorithm="HS256",
        ),
        "expires_in": Limit.ACCESS_TOKEN_EXPIRY,
    }

    if gen_refresh:
        tokens["refresh_token"] = jwt.encode(
            payload | {
                "aud": TokenAudience.REFRESH,
                "exp": now + Limit.REFRESH_TOKEN_EXPIRY,
            },
            secret,
            algorithm="HS256",
        )

    return tokens


# ── 解碼 ─────────────────────────────────────────────────────────────────────

def _decode(token: str, audience: str) -> dict:
    """解碼並驗證 JWT；任何失敗都回 401，不向呼叫端透露失敗原因。"""
    try:
        return jwt.decode(
            token,
            _secret(),
            algorithms=["HS256"],
            issuer=Config.jwt_issuer,
            audience=audience,
        )
    except jwt.ExpiredSignatureError:
        logger.info("Token 已過期")
    except jwt.InvalidAudienceError:
        # 例如拿 refresh token 去存取受保護端點
        logger.warning("Token audience 不符")
    except jwt.InvalidTokenError as e:
        logger.warning(f"Token 無效：{e}")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def decode_access_token(token: str) -> dict:
    return _decode(token, TokenAudience.ACCESS)


def decode_refresh_token(token: str) -> dict:
    return _decode(token, TokenAudience.REFRESH)


# ── FastAPI 相依注入 ─────────────────────────────────────────────────────────

async def get_caller_identity(
    authorization: str = Header(None, description="Bearer <access_token>"),
) -> CallerIdentity:
    """保護一般端點：驗證 access token 並取出裝置身分。

    用法與參考專案相同，在端點簽章加一個參數即可：
        def submit_report(caller: CallerIdentity = Depends(get_caller_identity), ...)
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_access_token(token)

    device_id = payload.get("device_id")
    if not device_id:
        logger.warning("Token 缺少 device_id 欄位")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 封鎖檢查。放在這裡（而非只在續期時檢查）封鎖才會即刻生效，
    # 否則被封鎖的裝置還能用手上未過期的 access token 繼續操作最多一小時。
    #
    # 刻意 fail open：查詢失敗時放行。token 簽章本身是有效的，若因為資料庫
    # 暫時讀不到就擋下所有請求，等於把一個罕用的管理功能變成全服務的故障點。
    try:
        if is_revoked(device_id):
            logger.warning(f"已封鎖的裝置嘗試存取：{device_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Device revoked",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"封鎖狀態查詢失敗，暫予放行：{e}")

    return CallerIdentity(device_id=device_id)


async def verify_admin(
    x_admin_token: str = Header(None, description="管理員密鑰"),
) -> None:
    """保護管理端點（手動推播、推播測試、RAG 實驗）。

    這些端點會對全體已註冊裝置發送通知、或觸發要付費的 LLM 呼叫，
    不屬於一般使用者功能，因此不使用裝置憑證，改用獨立的共用密鑰。
    參考專案沒有管理員角色，這部分是本專案自行補上的。
    """
    if not Config.admin_token:
        logger.error("ADMIN_TOKEN 未設定：管理端點一律拒絕")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server auth is not configured",
        )

    # 固定時間比對，避免以回應時間逐字元猜測密鑰
    if not x_admin_token or not hmac.compare_digest(x_admin_token, Config.admin_token):
        logger.warning("管理端點驗證失敗")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )
