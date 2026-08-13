"""手動推播工具：對真實裝置發送測試通知。

    python scripts/send_test_push.py

⚠️  這會發出**真實推播**到已註冊的裝置，需要有效的 Firebase 憑證。
不是測試，是 operational tooling —— 因此刻意不放在 tests/、
函式也不以 test_ 開頭，避免被 pytest 收集後意外對所有使用者發推播。

自動化的 fcm_sender 單元測試在 tests/test_fcm_sender.py（不發真實推播）。
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from fcm.fcm_sender import send_notification, send_multicast
from fcm.token_store import get_all_tokens


def send_single_push(token: str):
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


def send_broadcast_push():
    print("嘗試發送批次推播給所有已註冊的裝置...")
    tokens = get_all_tokens()

    if not tokens:
        print("⚠️ 目前沒有任何已註冊的 FCM Token，請先開啟 App 上傳 Token。")
        return

    print(f"找到 {len(tokens)} 個裝置，準備發送...")
    confirm = input(f"確定要對 {len(tokens)} 台真實裝置發送推播嗎？(yes/N): ")
    if confirm.strip().lower() != "yes":
        print("已取消。")
        return

    result = send_multicast(
        tokens=tokens,
        title="FCM 測試群發推播",
        body="這是一條來自後端的群發測試推播訊息",
        data={"type": "test", "message": "hello everyone"}
    )

    print(f"✅ 群發推播完成！成功: {result['success']}, 失敗: {result['failure']}")


if __name__ == "__main__":
    print("1. 測試發送給所有已註冊裝置")
    print("2. 測試發送給指定 Token")
    choice = input("請選擇測試方式 (1 或 2): ")

    if choice == "1":
        send_broadcast_push()
    elif choice == "2":
        token = input("請輸入裝置的 FCM Token: ")
        send_single_push(token)
    else:
        print("無效的選擇")
