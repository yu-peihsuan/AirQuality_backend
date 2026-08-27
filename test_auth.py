"""
test_auth.py — 裝置認證機制測試腳本
執行方式：python test_auth.py

不依賴 pytest，也不會啟動完整的 main.py（避免載入 RAG／爬蟲等重量級模組）。
測試方式是另外組一個最小的 FastAPI app，掛上 api/auth.py、api/admin.py 的
router 與兩個分別以 get_caller_identity／verify_admin 保護的假端點，
驗證認證邏輯本身。

所有實際動作都包在 main() 裡，模組層級不執行任何檢查。因此即使日後導入
pytest、這個檔名被自動探索到，單純 import 也不會有副作用（不會建立暫存
資料庫、不會呼叫 sys.exit 中斷 collection）。
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, ".")

# core.config 在 import 當下讀取環境變數，因此必須先設定
os.environ["JWT_SECRET"] = "test-secret-for-auth-unit-test"
os.environ["ADMIN_TOKEN"] = "test-admin-token"

import jwt
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import db.devices_db as devices_db
import db.reports_db as reports_db
from api.admin import module as admin_router
from api.auth import module as auth_router
from core.auth import CallerIdentity, get_caller_identity, hash_device_id, verify_admin
from core.config import Config, TokenAudience

app = FastAPI()
app.include_router(auth_router)
app.include_router(admin_router)


@app.get("/protected")
def protected(caller: CallerIdentity = Depends(get_caller_identity)):
    return {"device_id": caller.device_id}


@app.post("/admin-only")
def admin_only(_: None = Depends(verify_admin)):
    return {"status": "success"}


_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    """執行全部檢查，回傳失敗數（0 表示全數通過）。"""
    # 測試用的暫存 DB，避免污染 crawler/user_reports.db
    # （兩個模組共用同一個檔案，與正式環境一致）
    tmp_db = os.path.join(tempfile.mkdtemp(), "test_devices.db")
    devices_db.DB_PATH = tmp_db
    reports_db.DB_PATH = tmp_db
    devices_db.init_device_db()
    reports_db.init_db()

    client = TestClient(app)

    RAW_DEVICE_ID = "a1b2c3d4e5f60718"  # 假裝是 Android 的 ANDROID_ID

    # ─── 測試 1：裝置識別碼雜湊 ──────────────────────────────────────────────────
    print("=" * 60)
    print("🧪 測試 1：裝置識別碼雜湊")
    print("=" * 60)

    h1 = hash_device_id(RAW_DEVICE_ID)
    h2 = hash_device_id(RAW_DEVICE_ID)
    check("相同輸入產生相同代稱", h1 == h2)
    check("代稱與原始 ANDROID_ID 不同（不外洩硬體識別碼）", h1 != RAW_DEVICE_ID)
    check("不同裝置產生不同代稱", h1 != hash_device_id("ffffffffffffffff"))
    print()

    # ─── 測試 2：未認證的請求一律被拒 ────────────────────────────────────────────
    print("=" * 60)
    print("🧪 測試 2：未認證的請求")
    print("=" * 60)

    r = client.get("/protected")
    check("沒有 Authorization header → 401", r.status_code == 401, f"實得 {r.status_code}")

    r = client.get("/protected", headers={"Authorization": "Bearer not-a-real-token"})
    check("亂填的 token → 401", r.status_code == 401, f"實得 {r.status_code}")
    print()

    # ─── 測試 3：裝置註冊 ────────────────────────────────────────────────────────
    print("=" * 60)
    print("🧪 測試 3：裝置註冊")
    print("=" * 60)

    r = client.post("/api/auth/device", json={"device_id": RAW_DEVICE_ID})
    check("註冊成功 → 200", r.status_code == 200, f"實得 {r.status_code} {r.text}")
    body = r.json()
    check("回傳 access_token", bool(body.get("access_token")))
    check("回傳 refresh_token", bool(body.get("refresh_token")))
    check("回傳 expires_in", isinstance(body.get("expires_in"), int))

    access_token = body["access_token"]
    refresh_token = body["refresh_token"]

    r = client.post("/api/auth/device", json={})
    check("缺少 device_id → 422", r.status_code == 422, f"實得 {r.status_code}")
    print()

    # ─── 測試 4：帶 access token 存取受保護端點 ──────────────────────────────────
    print("=" * 60)
    print("🧪 測試 4：以 access token 存取受保護端點")
    print("=" * 60)

    r = client.get("/protected", headers={"Authorization": f"Bearer {access_token}"})
    check("帶有效 token → 200", r.status_code == 200, f"實得 {r.status_code}")
    check("token 內的 device_id 是雜湊後的代稱",
          r.status_code == 200 and r.json()["device_id"] == hash_device_id(RAW_DEVICE_ID))
    print()

    # ─── 測試 5：refresh token 不能當 access token 用 ────────────────────────────
    print("=" * 60)
    print("🧪 測試 5：兩種 token 不可互換（audience 隔離）")
    print("=" * 60)

    r = client.get("/protected", headers={"Authorization": f"Bearer {refresh_token}"})
    check("拿 refresh token 存取受保護端點 → 401", r.status_code == 401, f"實得 {r.status_code}")

    r = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {access_token}"})
    check("拿 access token 呼叫 refresh → 401", r.status_code == 401, f"實得 {r.status_code}")
    print()

    # ─── 測試 6：token 續期 ──────────────────────────────────────────────────────
    print("=" * 60)
    print("🧪 測試 6：token 續期")
    print("=" * 60)

    r = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {refresh_token}"})
    check("以 refresh token 續期 → 200", r.status_code == 200, f"實得 {r.status_code} {r.text}")
    if r.status_code == 200:
        new_access = r.json()["access_token"]
        rr = client.get("/protected", headers={"Authorization": f"Bearer {new_access}"})
        check("續期後的 access token 可用", rr.status_code == 200, f"實得 {rr.status_code}")
        check("refresh token 未接近到期時原樣沿用", r.json()["refresh_token"] == refresh_token)

    r = client.post("/api/auth/refresh", headers={"Authorization": "Bearer garbage"})
    check("無效的 refresh token → 401", r.status_code == 401, f"實得 {r.status_code}")
    print()

    # ─── 測試 7：偽造與過期的 token ──────────────────────────────────────────────
    print("=" * 60)
    print("🧪 測試 7：偽造與過期的 token")
    print("=" * 60)

    forged = jwt.encode(
        {
            "device_id": "attacker",
            "iss": Config.jwt_issuer,
            "aud": TokenAudience.ACCESS,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        },
        "wrong-secret",
        algorithm="HS256",
    )
    r = client.get("/protected", headers={"Authorization": f"Bearer {forged}"})
    check("用錯誤密鑰簽的 token → 401", r.status_code == 401, f"實得 {r.status_code}")

    expired = jwt.encode(
        {
            "device_id": hash_device_id(RAW_DEVICE_ID),
            "iss": Config.jwt_issuer,
            "aud": TokenAudience.ACCESS,
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,
        },
        Config.jwt_secret,
        algorithm="HS256",
    )
    r = client.get("/protected", headers={"Authorization": f"Bearer {expired}"})
    check("已過期的 token → 401", r.status_code == 401, f"實得 {r.status_code}")

    wrong_issuer = jwt.encode(
        {
            "device_id": hash_device_id(RAW_DEVICE_ID),
            "iss": "someone-else",
            "aud": TokenAudience.ACCESS,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        },
        Config.jwt_secret,
        algorithm="HS256",
    )
    r = client.get("/protected", headers={"Authorization": f"Bearer {wrong_issuer}"})
    check("issuer 不符的 token → 401", r.status_code == 401, f"實得 {r.status_code}")
    print()

    # ─── 測試 8：裝置封鎖 ────────────────────────────────────────────────────────
    print("=" * 60)
    print("🧪 測試 8：封鎖濫用裝置")
    print("=" * 60)

    DEVICE = hash_device_id(RAW_DEVICE_ID)

    devices_db.revoke_device(DEVICE)

    r = client.get("/protected", headers={"Authorization": f"Bearer {access_token}"})
    check("封鎖後，手上未過期的 access token 即刻失效 → 403",
          r.status_code == 403, f"實得 {r.status_code}")

    r = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {refresh_token}"})
    check("被封鎖的裝置無法續期 → 401", r.status_code == 401, f"實得 {r.status_code}")

    devices_db.restore_device(DEVICE)
    r = client.get("/protected", headers={"Authorization": f"Bearer {access_token}"})
    check("解除封鎖後恢復正常 → 200", r.status_code == 200, f"實得 {r.status_code}")
    print()

    # ─── 測試 9：管理員端點 ──────────────────────────────────────────────────────
    print("=" * 60)
    print("🧪 測試 9：管理員端點")
    print("=" * 60)

    r = client.post("/admin-only")
    check("未帶管理密鑰 → 403", r.status_code == 403, f"實得 {r.status_code}")

    r = client.post("/admin-only", headers={"X-Admin-Token": "wrong"})
    check("錯誤的管理密鑰 → 403", r.status_code == 403, f"實得 {r.status_code}")

    r = client.post("/admin-only", headers={"X-Admin-Token": os.environ["ADMIN_TOKEN"]})
    check("正確的管理密鑰 → 200", r.status_code == 200, f"實得 {r.status_code}")

    r = client.post("/admin-only", headers={"Authorization": f"Bearer {access_token}"})
    check("一般裝置憑證無法存取管理端點 → 403", r.status_code == 403, f"實得 {r.status_code}")
    print()

    # ─── 測試 10：未設定密鑰時 fail closed ───────────────────────────────────────
    print("=" * 60)
    print("🧪 測試 10：未設定密鑰時 fail closed")
    print("=" * 60)

    _saved_jwt, _saved_admin = Config.jwt_secret, Config.admin_token
    try:
        Config.jwt_secret = ""
        r = client.post("/api/auth/device", json={"device_id": RAW_DEVICE_ID})
        check("JWT_SECRET 未設定 → 500（不使用預設密鑰）", r.status_code == 500, f"實得 {r.status_code}")

        Config.jwt_secret = _saved_jwt
        Config.admin_token = ""
        r = client.post("/admin-only", headers={"X-Admin-Token": "anything"})
        check("ADMIN_TOKEN 未設定 → 500（不放行）", r.status_code == 500, f"實得 {r.status_code}")
    finally:
        Config.jwt_secret, Config.admin_token = _saved_jwt, _saved_admin
    print()

    # ─── 測試 11：管理端點的裝置管理 ─────────────────────────────────────────────
    print("=" * 60)
    print("🧪 測試 11：管理端點的裝置管理")
    print("=" * 60)

    ADMIN = {"X-Admin-Token": os.environ["ADMIN_TOKEN"]}

    r = client.get("/api/admin/devices")
    check("未帶管理密鑰無法列出裝置 → 403", r.status_code == 403, f"實得 {r.status_code}")

    r = client.get("/api/admin/devices", headers=ADMIN)
    check("列出裝置 → 200", r.status_code == 200, f"實得 {r.status_code} {r.text}")
    listed = r.json().get("devices", []) if r.status_code == 200 else []
    check("清單包含已註冊的裝置", any(d["device_id"] == DEVICE for d in listed))
    check("清單附上近期回報數（用於找出灌水來源）",
          all("recent_reports" in d for d in listed))

    r = client.post(f"/api/admin/devices/{DEVICE}/revoke", headers=ADMIN)
    check("封鎖裝置 → 200", r.status_code == 200, f"實得 {r.status_code} {r.text}")

    r = client.get("/protected", headers={"Authorization": f"Bearer {access_token}"})
    check("封鎖後該裝置立刻被擋 → 403", r.status_code == 403, f"實得 {r.status_code}")

    r = client.get("/api/admin/devices", headers=ADMIN)
    check("清單顯示封鎖狀態",
          any(d["device_id"] == DEVICE and d["revoked"] for d in r.json()["devices"]))

    r = client.post(f"/api/admin/devices/{DEVICE}/restore", headers=ADMIN)
    check("解除封鎖 → 200", r.status_code == 200, f"實得 {r.status_code}")

    r = client.get("/protected", headers={"Authorization": f"Bearer {access_token}"})
    check("解除後恢復存取 → 200", r.status_code == 200, f"實得 {r.status_code}")

    r = client.post("/api/admin/devices/no-such-device/revoke", headers=ADMIN)
    check("封鎖不存在的裝置 → 404", r.status_code == 404, f"實得 {r.status_code}")

    r = client.post(f"/api/admin/devices/{DEVICE}/revoke")
    check("未帶管理密鑰無法封鎖 → 403", r.status_code == 403, f"實得 {r.status_code}")
    print()

    # ─── 結果 ────────────────────────────────────────────────────────────────────
    print("=" * 60)
    print(f"通過 {_passed} 項，失敗 {_failed} 項")
    print("=" * 60)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
