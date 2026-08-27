"""集中讀取認證相關的環境變數與參數。

參考老師提供的 dl-app-api（`src/core/config.py`）：所有 env 讀取集中在單一
類別，而不是散在各模組裡呼叫 os.getenv()，之後要調整時不必翻遍程式碼。

與參考專案的差異
----------------
參考專案用 `os.environ["jwtSecret"]`，缺少變數時在 import 階段直接崩潰。
本專案的資料來源金鑰（MOENV_API_KEY 等）沿用寬鬆讀取（缺少時對應功能降級，
服務仍可啟動），但 JWT_SECRET 屬於安全設定，採 fail closed：缺少時服務照常
啟動並提供公開資料，但任何需要認證的端點一律拒絕（見 core/auth.py）。
這樣設定疏漏會立刻以 500 暴露出來，而不是靜默地用預設密鑰簽發 token。
"""

import os


class Config:
    """認證設定。值在 import 時讀取一次（Cloud Run 的環境變數不會中途改變）。"""

    # JWT 簽章密鑰。未設定時所有認證端點回 500（fail closed，不使用預設值）。
    jwt_secret: str = os.getenv("JWT_SECRET", "")

    # 管理員端點（推播測試、RAG 實驗）的共用密鑰。未設定時一律拒絕。
    admin_token: str = os.getenv("ADMIN_TOKEN", "")

    # JWT 的 issuer，寫進 token 的 iss 欄位並於解碼時驗證
    jwt_issuer: str = "airquality-api"


class Limit:
    """Token 有效期限（秒）。

    參考專案是 access 15 分鐘 / refresh 7 天，屬於「有帳號可重新登入」的前提。
    本 App 沒有登入畫面，token 過期時使用者無從手動補救，只能靠背景續期，
    因此把 access 拉長到 1 小時（減少續期次數）、refresh 拉長到 30 天
    （偶爾開啟 App 的使用者也不會失效）。
    """

    ACCESS_TOKEN_EXPIRY = 60 * 60             # 1 小時
    REFRESH_TOKEN_EXPIRY = 60 * 60 * 24 * 30  # 30 天


class TokenAudience:
    """access 與 refresh token 的 aud 值。

    兩者刻意不同，解碼時各自驗證 audience，
    因此 refresh token 無法被拿來當 access token 直接存取受保護端點。
    """

    ACCESS = "device-access"
    REFRESH = "device-refresh"
