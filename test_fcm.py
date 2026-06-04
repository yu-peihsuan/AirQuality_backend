import json
from fcm.fcm_sender import send_notification, send_multicast
from fcm.token_store import get_all_tokens

def test_single_push(token: str):
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

def test_multicast_push():
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
        test_multicast_push()
    elif choice == "2":
        token = input("請輸入裝置的 FCM Token: ")
        test_single_push(token)
    else:
        print("無效的選擇")
