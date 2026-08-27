"""
manual_fcm_push.py — 手動發送 FCM 推播的維運腳本
執行方式：python scripts/manual_fcm_push.py

刻意不叫 test_*.py：這支會對「所有真實裝置」發出推播，若被 pytest
當成測試自動執行，就會直接騷擾到全部使用者。它是維運工具，不是測試。

一般情況請改用受管理密鑰保護的 POST /api/fcm/push 端點。
"""

import os
import sys

# 讓腳本無論從哪個目錄執行都能匯入專案模組
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from fcm.fcm_sender import send_notification, send_multicast
from fcm.token_store import get_all_tokens

def push_to_single_device(token: str):
    print(f"嘗試發送單一推播至 Token: {token[:20]}...")
    success = send_notification(
        token=token,
        title="FCM 測試推播",
        body="這是一條來自後端的單一測試推播訊息",
        data={"type": "test", "message": "hello world"}
    )
    if success:
        print("✅ 單一推播發送成功！")
    else:
        print("❌ 單一推播發送失敗！")

def push_to_all_devices():
    print("嘗試發送批次推播給所有已註冊的裝置...")
    # 假設 token_store.json 有儲存
    tokens = get_all_tokens()
    
    if not tokens:
        print("⚠️ 目前沒有任何已註冊的 FCM Token，請先開啟 App 上傳 Token。")
        return
        
    print(f"找到 {len(tokens)} 個裝置，準備發送...")
    result = send_multicast(
        tokens=tokens,
        title="FCM 測試群發推播",
        body="這是一條來自後端的群發測試推播訊息",
        data={"type": "test", "message": "hello everyone"}
    )
    
    print(f"✅ 群發推播完成！成功: {result['success']}, 失敗: {result['failure']}")

if __name__ == "__main__":
    print("1. 測試發送給所有已註冊裝置 (建議選擇)")
    print("2. 測試發送給指定 Token")
    choice = input("請選擇測試方式 (1 或 2): ")
    
    if choice == "1":
        push_to_all_devices()
    elif choice == "2":
        token = input("請輸入裝置的 FCM Token: ")
        push_to_single_device(token)
    else:
        print("無效的選擇")
