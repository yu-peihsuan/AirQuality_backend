"""管理端點：已註冊裝置的檢視與封鎖。

整個 router 以 `dependencies=[Depends(verify_admin)]` 保護，新增端點時不必
逐一記得掛上驗證——這是 main.py 目前逐一標註的做法容易漏掉的地方。

參考專案 dl-app-api 沒有管理員角色，這部分是本專案自行補上的。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from core.auth import verify_admin
from db.devices_db import list_devices, restore_device, revoke_device
from db.reports_db import count_recent_by_device

logger = logging.getLogger(__name__)

module = APIRouter(
    prefix="/api/admin",
    tags=["Admin"],
    dependencies=[Depends(verify_admin)],
)


@module.get("/devices")
def get_devices(hours: int = 24):
    """列出已註冊裝置，回報數多的排前面，方便找出灌水來源。

    device_id 是雜湊後的代稱（見 core/auth.py），無法反推回實際裝置，
    但足以在此處對應同一台裝置的行為。
    """
    devices = list_devices()
    for d in devices:
        d["recent_reports"] = count_recent_by_device(d["device_id"], minutes=hours * 60)

    devices.sort(key=lambda d: d["recent_reports"], reverse=True)
    return {
        "status": "success",
        "hours": hours,
        "count": len(devices),
        "devices": devices,
    }


@module.post("/devices/{device_id}/revoke")
def post_revoke_device(device_id: str):
    """封鎖裝置。

    立即生效：受保護端點每次都會檢查封鎖狀態，被封鎖的裝置會拿到 403，
    也無法再用 refresh token 續期。該裝置若重新註冊會取得新的代稱，
    因此封鎖擋的是「這個身分」，不是永久擋住那台實體裝置。
    """
    if not revoke_device(device_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Device not found: {device_id}",
        )
    logger.warning(f"管理員封鎖裝置：{device_id}")
    return {"status": "success", "device_id": device_id, "revoked": True}


@module.post("/devices/{device_id}/restore")
def post_restore_device(device_id: str):
    """解除封鎖（誤封時使用）。"""
    if not restore_device(device_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Device not found: {device_id}",
        )
    logger.info(f"管理員解除封鎖：{device_id}")
    return {"status": "success", "device_id": device_id, "revoked": False}
