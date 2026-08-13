"""fcm/fcm_sender.py — FCM 推播發送。

此模組原本沒有任何自動化測試，只有一支互動式手動腳本
（原 test_fcm.py，現為 scripts/send_test_push.py），
而該腳本會對所有已註冊裝置發出真實推播。

這裡把 firebase_admin 整層換成替身，驗證我們自己的邏輯：
短路、型別轉換、例外吞噬與回傳計數。不發送任何真實推播。
"""

import pytest

import fcm.fcm_sender as sender


class _FakeMulticastResponse:
    def __init__(self, success_count, failure_count, responses=None):
        self.success_count = success_count
        self.failure_count = failure_count
        self.responses = responses or []


class _FakeMessaging:
    """記錄呼叫內容的 firebase_admin.messaging 替身。"""

    def __init__(self, *, send_error=None, multicast_error=None,
                 success_count=1, failure_count=0):
        self.sent = []
        self.multicast = []
        self.send_error = send_error
        self.multicast_error = multicast_error
        self.success_count = success_count
        self.failure_count = failure_count

    # 這些「訊息物件」只是把參數原樣留存，方便測試斷言
    def Notification(self, title=None, body=None):
        return {"title": title, "body": body}

    def Message(self, notification=None, data=None, token=None):
        return {"notification": notification, "data": data, "token": token}

    def MulticastMessage(self, notification=None, data=None, tokens=None):
        return {"notification": notification, "data": data, "tokens": tokens}

    def send(self, message):
        if self.send_error:
            raise self.send_error
        self.sent.append(message)
        return "projects/test/messages/1"

    def send_each_for_multicast(self, message):
        if self.multicast_error:
            raise self.multicast_error
        self.multicast.append(message)
        return _FakeMulticastResponse(self.success_count, self.failure_count)


@pytest.fixture
def messaging(monkeypatch):
    """注入 messaging 替身，並跳過 Firebase 初始化。"""
    fake = _FakeMessaging()
    monkeypatch.setattr(sender, "messaging", fake)
    monkeypatch.setattr(sender, "_init_firebase", lambda: None)
    return fake


# ── send_notification ────────────────────────────────────────────────────────

def test_send_notification_returns_true_on_success(messaging):
    assert sender.send_notification("tok-1", "標題", "內文") is True
    assert len(messaging.sent) == 1


def test_send_notification_passes_title_body_and_token(messaging):
    sender.send_notification("tok-1", "空品警示", "AQI 已達 160")
    msg = messaging.sent[0]
    assert msg["token"] == "tok-1"
    assert msg["notification"] == {"title": "空品警示", "body": "AQI 已達 160"}


def test_send_notification_returns_false_instead_of_raising(messaging, monkeypatch):
    """推播失敗不得讓呼叫端的排程整個掛掉。"""
    monkeypatch.setattr(sender, "messaging",
                        _FakeMessaging(send_error=RuntimeError("FCM 掛了")))
    assert sender.send_notification("tok-1", "標題", "內文") is False


def test_send_notification_coerces_data_values_to_strings(messaging):
    """FCM 的 data payload 只接受字串值，非字串必須先轉換。"""
    sender.send_notification("tok-1", "標題", "內文",
                             data={"type": "aqi", "aqi": 160, "urgent": True})
    data = messaging.sent[0]["data"]
    assert data == {"type": "aqi", "aqi": "160", "urgent": "True"}
    assert all(isinstance(v, str) for v in data.values())


def test_send_notification_handles_missing_data(messaging):
    sender.send_notification("tok-1", "標題", "內文")
    assert messaging.sent[0]["data"] == {}


# ── send_multicast ───────────────────────────────────────────────────────────

def test_send_multicast_short_circuits_on_empty_token_list(messaging):
    """沒有目標裝置時不該碰 Firebase，避免無謂的初始化與網路往返。"""
    assert sender.send_multicast([], "標題", "內文") == {"success": 0, "failure": 0}
    assert messaging.multicast == []


def test_send_multicast_returns_counts_from_the_response(monkeypatch):
    fake = _FakeMessaging(success_count=7, failure_count=3)
    monkeypatch.setattr(sender, "messaging", fake)
    monkeypatch.setattr(sender, "_init_firebase", lambda: None)

    result = sender.send_multicast(["t1", "t2"], "標題", "內文")
    assert result == {"success": 7, "failure": 3}


def test_send_multicast_passes_every_token(messaging):
    tokens = [f"tok-{i}" for i in range(5)]
    sender.send_multicast(tokens, "標題", "內文")
    assert messaging.multicast[0]["tokens"] == tokens


def test_send_multicast_coerces_data_values_to_strings(messaging):
    sender.send_multicast(["t1"], "標題", "內文", data={"type": "fire", "count": 3})
    assert messaging.multicast[0]["data"] == {"type": "fire", "count": "3"}


def test_send_multicast_counts_all_tokens_as_failed_on_exception(monkeypatch):
    """整批送出失敗時，failure 數必須等於 token 數，讓呼叫端知道全滅。"""
    fake = _FakeMessaging(multicast_error=RuntimeError("上游 503"))
    monkeypatch.setattr(sender, "messaging", fake)
    monkeypatch.setattr(sender, "_init_firebase", lambda: None)

    result = sender.send_multicast(["t1", "t2", "t3"], "標題", "內文")
    assert result == {"success": 0, "failure": 3}


def test_send_multicast_never_raises(monkeypatch):
    """排程 job 依賴這個保證：推播失敗不得中斷整輪排程。"""
    fake = _FakeMessaging(multicast_error=ValueError("憑證無效"))
    monkeypatch.setattr(sender, "messaging", fake)
    monkeypatch.setattr(sender, "_init_firebase", lambda: None)

    result = sender.send_multicast(["t1"], "標題", "內文")
    assert result["failure"] == 1


def test_send_multicast_result_shape_is_stable(messaging):
    """/api/fcm/push 直接把這個 dict 展開進 API 回應。"""
    assert set(sender.send_multicast(["t1"], "標題", "內文")) == {"success", "failure"}


# ── 已知缺陷 ─────────────────────────────────────────────────────────────────

@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="FCM 的 send_each_for_multicast 單批上限為 500 則，超過會直接拋錯。"
           "send_multicast 沒有分批，一旦註冊裝置超過 500 台，"
           "所有推播會全數失敗（且只會在 log 印一行就被吞掉）。",
)
def test_send_multicast_batches_tokens_within_the_fcm_limit(messaging):
    """超過 500 台裝置時必須自動分批，而不是整批失敗。"""
    tokens = [f"tok-{i}" for i in range(1200)]
    sender.send_multicast(tokens, "標題", "內文")

    assert messaging.multicast, "應該至少送出一批"
    for batch in messaging.multicast:
        assert len(batch["tokens"]) <= 500, "單批 token 數超過 FCM 上限"
    sent = [t for batch in messaging.multicast for t in batch["tokens"]]
    assert sorted(sent) == sorted(tokens), "分批後不得漏送或重送"


@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="send_each_for_multicast 的 response.responses 逐則記錄了失敗原因"
           "（UNREGISTERED / INVALID_ARGUMENT 代表 token 已失效），"
           "但目前只取 success_count/failure_count 就把細節丟掉，"
           "因此無從得知該清除哪些 token。"
           "與 test_token_store.py::test_store_exposes_a_way_to_remove_stale_tokens 相對應。",
)
def test_send_multicast_reports_which_tokens_are_invalid(monkeypatch):
    """回傳值需能指出哪些 token 已失效，才有辦法清理 token store。"""
    class _Resp:
        def __init__(self, success, code=None):
            self.success = success
            self.exception = None if success else type("E", (), {"code": code})()

    fake = _FakeMessaging(success_count=1, failure_count=1)

    def _multicast(message):
        return _FakeMulticastResponse(
            1, 1, responses=[_Resp(True), _Resp(False, "UNREGISTERED")]
        )

    fake.send_each_for_multicast = _multicast
    monkeypatch.setattr(sender, "messaging", fake)
    monkeypatch.setattr(sender, "_init_firebase", lambda: None)

    result = sender.send_multicast(["good-token", "dead-token"], "標題", "內文")
    assert "invalid_tokens" in result
    assert result["invalid_tokens"] == ["dead-token"]
