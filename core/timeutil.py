"""全系統唯一的時間基準：台灣時間（UTC+8，aware）。

為什麼需要這個模組
------------------
修正前，同一套程式碼裡有三種時間基準並存：

    main.py submit_report      UTC+8 牆上時間，去掉 tzinfo
    db/reports_db.py 的 cutoff  datetime.now()（行程所在時區）
    排程與 token_store          aware UTC+8

開發機是 Asia/Taipei，前兩者剛好一致，所以本機測起來完全正常；
但 Cloud Run 的行程時區是 UTC，於是資料庫裡的時間戳永遠比 cutoff
早 8 小時，所有時間窗都被放大：

    回報頻率限制  3 分鐘  → 8 小時 3 分（使用者被鎖將近一整天）
    相同內容去重  6 小時  → 14 小時
    近期回報查詢  24 小時 → 32 小時（熱點分析與 RAG 吃到過期事件）

為什麼選 UTC+8 而不是 UTC
-------------------------
慣例上會存 UTC、只在輸出時轉當地時間。這裡刻意選 UTC+8，理由有二：

1. 資料庫裡既有的時間戳已經是 UTC+8 牆上時間。改存 UTC 會讓新舊資料
   相差 8 小時，得先做資料遷移；維持 UTC+8 則不必動既有資料。
2. 服務對象全在台灣，排程（每日摘要推播）本來就以台灣時間定義，
   換算成 UTC 反而讓排程邏輯更難讀。

真正的缺陷是「基準不一致」，不是「基準不是 UTC」。

輸出格式
--------
一律輸出帶 `+08:00` 位移的 ISO 字串，例如：

    2026-08-27T12:34:32.227786+08:00

修正前寫入的是不帶位移的字串。加上位移有兩個好處：時間戳自我描述、
不再需要靠上下文猜測時區；而且 App 的 MapScreen 用
`SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssZ")` 解析，格式中的 `Z`
要求必須有位移——不帶位移的舊格式它其實一直解析失敗。

字串比較相容性：SQLite 的 cutoff 比較是字串比較，而 ISO 格式的
年月日時分秒在前、位移在後，因此新格式與既有的無位移字串仍可正確比大小
（只有在微秒完全相同時才會有差異，可忽略）。
"""

from datetime import datetime, timedelta, timezone

# 台灣時間（UTC+8）。台灣自 1980 年起不再實施日光節約時間，固定位移即可。
TW = timezone(timedelta(hours=8))


def now_tw() -> datetime:
    """現在的台灣時間（aware）。"""
    return datetime.now(TW)


def now_iso() -> str:
    """現在的台灣時間 ISO 字串，帶 +08:00 位移。寫入資料庫一律用這個。"""
    return now_tw().isoformat()


def cutoff_iso(*, hours: int = 0, minutes: int = 0) -> str:
    """N 小時／分鐘前的台灣時間 ISO 字串，供資料庫的時間窗查詢使用。"""
    return (now_tw() - timedelta(hours=hours, minutes=minutes)).isoformat()


def today_str() -> str:
    """今天的日期（台灣時間），格式 YYYY-MM-DD。

    用於「今天是否已推播」這類判斷。用行程時區會讓 Cloud Run（UTC）
    在台灣時間每天 08:00 之前都還停留在前一天。
    """
    return now_tw().strftime("%Y-%m-%d")


def log_ts() -> str:
    """log 用的時間字串（台灣時間），格式 YYYY-MM-DD HH:MM:SS。"""
    return now_tw().strftime("%Y-%m-%d %H:%M:%S")


def to_tw(dt: datetime) -> datetime:
    """把任意 datetime 正規化成 aware 台灣時間。

    naive 的輸入一律視為台灣時間——本系統寫出的舊資料就是這個語意。
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TW)
    return dt.astimezone(TW)


def parse_iso(value: str) -> datetime | None:
    """解析 ISO 時間字串成 aware 台灣時間；無法解析回傳 None。

    同時吃得下帶位移（新格式）與不帶位移（修正前的舊資料）兩種字串，
    因此資料庫裡新舊混雜時仍可正確比較。
    """
    if not value:
        return None
    try:
        return to_tw(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, TypeError):
        return None
