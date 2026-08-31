# db/push_state.py
# 推播去重狀態的持久化儲存

"""排程推播的「這則已經送過了」記錄。

為什麼需要這個模組
------------------
三條推播管線原本都把去重狀態放在模組層的全域變數裡：

    main.py  _forecast_pushed  set[(county, forecastdate)]
    main.py  _fire_pushed      set[alert_id]
    main.py  _aqi_alerted      dict[county, band]

而這三個 job 都是 `next_run_time=now_tw()`，行程一啟動就立刻執行一次。
Cloud Run 冷啟動、水平擴縮、重新部署都會建立新行程，於是：

    行程重啟 → 全域變數清空 → 排程立刻執行 → 同一則再推一次

預報是每日發布的，所以只要當天空品差，那天每次重啟都會重推給同一批裝置。
火災警示與 AQI 超標同理。放進 SQLite 之後，狀態跟著資料檔走，
重啟不再遺失。

為什麼用 key-value 而不是各開一張表
-----------------------------------
三種狀態的形狀不同（集合、集合、對映），但共通需求只有「依 key 查值、
寫值、清掉過期的」。開三張表要寫三套幾乎一樣的存取函式；單一 key-value
表加上命名前綴（forecast: / fire: / aqi_band:）就夠了，之後多一條管線
也不必動結構。
"""

import os
import sqlite3

from core.timeutil import cutoff_iso, now_iso

# 與民眾回報共用同一個 DB 檔，省得多管理一份
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "crawler", "user_reports.db")

# 保留天數。預報／火災的 key 帶日期或警示 ID，不清會無限成長。
_RETENTION_DAYS = 7


def _get_conn() -> sqlite3.Connection:
    # timeout：這些寫入來自 APScheduler 的背景執行緒，可能與 API 請求
    # 同時寫入同一個 DB 檔。預設 5 秒不夠時 sqlite 會直接丟
    # "database is locked"，這裡放寬到 10 秒。
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_push_state_db():
    """建立資料表並清掉過期記錄（冪等，可重複呼叫）。"""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS push_state (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT
            )
        """)
        conn.commit()
    purged = purge_old(days=_RETENTION_DAYS)
    print(f"✅ push_state DB 初始化完成（清除 {purged} 筆過期記錄）")


def get_value(key: str) -> str | None:
    """讀取一筆狀態；沒有這個 key 回傳 None。"""
    with _get_conn() as conn:
        row = conn.execute("SELECT value FROM push_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_value(key: str, value: str = "1"):
    """寫入或覆蓋一筆狀態。"""
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO push_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, value, now_iso()),
        )
        conn.commit()


def was_pushed(key: str) -> bool:
    """這則是否已經推播過。"""
    return get_value(key) is not None


def mark_pushed(key: str):
    """標記這則已經推播過。"""
    set_value(key, "1")


def get_int(key: str, default: int = 0) -> int:
    """讀取一筆數值狀態（AQI 警示等級用）；讀不到或不是數字回傳 default。"""
    raw = get_value(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def set_int(key: str, value: int):
    """寫入一筆數值狀態。"""
    set_value(key, str(value))


def purge_old(days: int = _RETENTION_DAYS) -> int:
    """清掉超過 days 天沒更新的記錄，回傳清掉的筆數。

    AQI 警示等級也會一併清掉——超過一週沒更新代表那個縣市早就回到正常，
    等級歸零本來就是對的。
    """
    cutoff = cutoff_iso(hours=days * 24)
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM push_state WHERE updated_at < ?", (cutoff,))
        conn.commit()
        return cur.rowcount or 0
