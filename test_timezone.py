"""
test_timezone.py — 時間基準一致性測試腳本
執行方式：python test_timezone.py

驗證修正後的核心不變量：**寫入端與查詢端用同一個時間基準**，因此
資料庫的時間窗等於設計值，而且與行程所在時區無關。

修正前系統有三種基準並存（見 core/timeutil 的模組說明）。在開發機
（Asia/Taipei）三者剛好一致所以測不出來，但 Cloud Run 的行程時區是 UTC，
所有時間窗都被放大 8 小時——回報頻率限制 3 分鐘變成 8 小時 3 分。

所有實際動作都包在 main() 裡，模組層級不執行任何檢查，因此即使被
pytest 探索到，單純 import 也不會有副作用。
"""

import os
import re
import sys
import tempfile
from datetime import timedelta

sys.path.insert(0, ".")

import db.reports_db as reports_db
from core.timeutil import (TW, cutoff_iso, now_iso, now_tw, parse_iso,
                           today_str, to_tw)

_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  OK  {name}")
    else:
        _failed += 1
        print(f"  XX  {name}" + (f" -- {detail}" if detail else ""))


def _iso_ago(**kwargs) -> str:
    """N 分鐘／小時前的台灣時間 ISO 字串（帶位移，即新格式）。"""
    return (now_tw() - timedelta(**kwargs)).isoformat()


def _naive_iso_ago(**kwargs) -> str:
    """修正前的舊格式：台灣時間牆上時刻，不帶位移。"""
    return (now_tw() - timedelta(**kwargs)).replace(tzinfo=None).isoformat()


def main() -> int:
    tmp_db = os.path.join(tempfile.mkdtemp(), "test_tz.db")
    reports_db.DB_PATH = tmp_db
    reports_db.init_db()

    # ─── 測試 1：時間基準本身 ────────────────────────────────────────────
    print("=" * 60)
    print("測試 1：時間基準是 UTC+8 且與行程時區無關")
    print("=" * 60)

    check("now_tw() 的 UTC 位移是 +8 小時",
          now_tw().utcoffset() == timedelta(hours=8),
          f"實得 {now_tw().utcoffset()}")
    check("now_iso() 帶 +08:00 位移（App 的 MapScreen 需要）",
          now_iso().endswith("+08:00"), f"實得 {now_iso()[-6:]!r}")
    check("cutoff_iso() 同樣帶位移", cutoff_iso(hours=1).endswith("+08:00"))
    check("today_str() 與 now_tw() 的日期一致",
          today_str() == now_tw().strftime("%Y-%m-%d"))
    print()

    # ─── 測試 2：回報頻率限制的時間窗（3 分鐘）─────────────────────────
    print("=" * 60)
    print("測試 2：回報頻率限制窗口 = 3 分鐘（不是 8 小時 3 分）")
    print("=" * 60)

    dev = "tz-test-device"
    reports_db.insert_report({"region": "A", "category": "x", "summary": "now",
                              "device_id": dev, "timestamp": now_iso()})
    check("剛送出的回報算在 3 分鐘窗內",
          reports_db.count_recent_by_device(dev, minutes=3) == 1,
          f"實得 {reports_db.count_recent_by_device(dev, minutes=3)}")

    reports_db.insert_report({"region": "A", "category": "x", "summary": "10min",
                              "device_id": dev, "timestamp": _iso_ago(minutes=10)})
    # 這一項就是修正前會失敗的地方：查詢基準若差 8 小時，
    # 10 分鐘前的回報看起來仍在窗內，使用者會被鎖將近一整天。
    check("10 分鐘前的回報不算在 3 分鐘窗內",
          reports_db.count_recent_by_device(dev, minutes=3) == 1,
          f"實得 {reports_db.count_recent_by_device(dev, minutes=3)}，應為 1")

    reports_db.insert_report({"region": "A", "category": "x", "summary": "2min",
                              "device_id": dev, "timestamp": _iso_ago(minutes=2)})
    check("2 分鐘前的回報算在 3 分鐘窗內",
          reports_db.count_recent_by_device(dev, minutes=3) == 2,
          f"實得 {reports_db.count_recent_by_device(dev, minutes=3)}，應為 2")
    print()

    # ─── 測試 3：近期回報查詢的時間窗（24 小時）───────────────────────
    print("=" * 60)
    print("測試 3：近期回報窗口 = 24 小時（不是 32 小時）")
    print("=" * 60)

    reports_db.DB_PATH = os.path.join(tempfile.mkdtemp(), "test_tz2.db")
    reports_db.init_db()

    reports_db.insert_report({"region": "B", "category": "x", "summary": "23h",
                              "timestamp": _iso_ago(hours=23)})
    reports_db.insert_report({"region": "B", "category": "x", "summary": "25h",
                              "timestamp": _iso_ago(hours=25)})
    recent = reports_db.get_recent_reports(hours=24)
    summaries = {r["summary"] for r in recent}
    check("23 小時前的回報在窗內", "23h" in summaries)
    check("25 小時前的回報不在窗內（修正前會被算進來）", "25h" not in summaries,
          f"實得 {summaries}")
    print()

    # ─── 測試 4：與修正前寫入的舊資料相容 ───────────────────────────────
    print("=" * 60)
    print("測試 4：舊格式（不帶位移）的既有資料仍能正確比較")
    print("=" * 60)

    reports_db.DB_PATH = os.path.join(tempfile.mkdtemp(), "test_tz3.db")
    reports_db.init_db()

    old_dev = "legacy-device"
    reports_db.insert_report({"region": "C", "category": "x", "summary": "legacy-2min",
                              "device_id": old_dev,
                              "timestamp": _naive_iso_ago(minutes=2)})
    check("舊格式、2 分鐘前 → 算在窗內",
          reports_db.count_recent_by_device(old_dev, minutes=3) == 1,
          f"實得 {reports_db.count_recent_by_device(old_dev, minutes=3)}")

    reports_db.insert_report({"region": "C", "category": "x", "summary": "legacy-10min",
                              "device_id": old_dev,
                              "timestamp": _naive_iso_ago(minutes=10)})
    check("舊格式、10 分鐘前 → 不算在窗內",
          reports_db.count_recent_by_device(old_dev, minutes=3) == 1,
          f"實得 {reports_db.count_recent_by_device(old_dev, minutes=3)}")
    print()

    # ─── 測試 5：parse_iso 兩種格式都吃得下 ─────────────────────────────
    print("=" * 60)
    print("測試 5：新舊兩種時間戳格式都能解析")
    print("=" * 60)

    aware = parse_iso("2026-08-27T12:34:32.227786+08:00")
    naive = parse_iso("2026-08-27T12:34:32.227786")
    check("帶位移的新格式可解析", aware is not None)
    check("不帶位移的舊格式可解析", naive is not None)
    check("舊格式被視為台灣時間（而非行程時區）",
          aware is not None and naive is not None and aware == naive,
          f"{aware} vs {naive}")
    check("無法解析時回傳 None", parse_iso("not-a-time") is None)
    check("空字串回傳 None", parse_iso("") is None)

    from datetime import datetime, timezone
    utc_noon = datetime(2026, 8, 27, 4, 0, 0, tzinfo=timezone.utc)
    check("to_tw() 把 UTC 正確換算成台灣時間（04:00Z → 12:00）",
          to_tw(utc_noon).hour == 12, f"實得 {to_tw(utc_noon).hour}")
    print()

    # ─── 測試 6：上游資料來源的實際時間格式 ─────────────────────────────
    print("=" * 60)
    print("測試 6：上游資料來源的時間格式")
    print("=" * 60)

    import email.utils

    # 台灣官方資料源：時間就是台灣時間，不論有沒有寫 +08:00。
    # 無標記者必須被視為台灣時間，不能當成 UTC 再加 8 小時。
    moenv = parse_iso("2026-08-27 10:30")            # 環境部 AQF_P_01 publishtime
    check("環境部 publishtime（無標記）視為台灣時間 10:30",
          moenv is not None and moenv.hour == 10 and moenv.utcoffset() == timedelta(hours=8),
          f"實得 {moenv}")

    from datetime import datetime as _dt
    cwa = to_tw(_dt.fromisoformat("2026-08-27 12:00:00"))   # 氣象署 F-C0032-001
    check("氣象署 startTime（無標記）視為台灣時間 12:00",
          cwa.hour == 12 and cwa.utcoffset() == timedelta(hours=8), f"實得 {cwa}")

    ncdr = parse_iso("2026-08-20T08:56:48+08:00")    # NCDR 火災警示 updated
    check("NCDR 火災警示（帶 +08:00）維持 08:56 不被偏移",
          ncdr is not None and ncdr.hour == 8, f"實得 {ncdr}")

    # 非台灣來源：Google News RSS 標的 GMT 就真的是 GMT，必須換算。
    gnews = to_tw(email.utils.parsedate_to_datetime("Thu, 27 Aug 2026 07:39:16 GMT"))
    check("Google News 的 GMT 正確換算成台灣時間 15:39（+8 小時）",
          gnews.hour == 15 and gnews.minute == 39, f"實得 {gnews}")

    # 空品預報的過濾是拿 today_str() 去比對 publishtime 開頭的台灣日期字串
    fake_publishtime = now_tw().strftime("%Y-%m-%d") + " 10:30"
    check("空品預報的日期過濾對得上今天的 publishtime",
          fake_publishtime.startswith(today_str()),
          f"{fake_publishtime!r} vs {today_str()!r}")
    print()

    # ─── 測試 7：回歸防護 ───────────────────────────────────────────────
    print("=" * 60)
    print("測試 7：回歸防護（原始碼掃描）")
    print("=" * 60)

    offenders = []
    skip_dirs = {"__pycache__", "scripts", "tests", ".git", "venv", ".venv"}
    # 涵蓋所有會取得「現在時間」的來源。time.time() 不在其中：
    # core/auth.py 的 JWT iat／exp 依規格必須是 UTC epoch（見該處說明）。
    pattern = re.compile(
        # 攔截所有 datetime.now( 形式，包含 datetime.now()、
        # datetime.now(timezone.utc)、datetime.now(tz=...)。
        # 只寫 datetime.now(\s*) 會漏掉帶參數的寫法。
        r"datetime\.now\("
        r"|datetime\.utcnow\("
        r"|date\.today\(\)"
        r"|datetime\.today\(\)"
        r"|time\.localtime\("
        r"|datetime\.fromtimestamp\("      # 不帶 tz 時取行程時區
    )
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fn in files:
            if not fn.endswith(".py") or fn.startswith("test_"):
                continue
            path = os.path.join(root, fn)
            if os.path.abspath(path) == os.path.abspath("core/timeutil.py"):
                continue
            with open(path, encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    if line.lstrip().startswith("#"):
                        continue
                    if pattern.search(line):
                        offenders.append(f"{path}:{i}")

    check("沒有任何模組自行取得現在時間（一律經由 core/timeutil）",
          not offenders, f"發現 {offenders}")
    print()

    print("=" * 60)
    print(f"通過 {_passed} 項，失敗 {_failed} 項")
    print("=" * 60)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
