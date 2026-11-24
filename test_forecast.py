"""
test_forecast.py — 空品預報（AQF_P_01）挑選邏輯測試
執行方式：python test_forecast.py

2026-11-10 起 AQF_P_01 改版：新增 timeperiod（全日／上午／下午），預報由 3 天
改為 4 天，第 1 天有全日、上午、下午三筆。API 回傳順序不固定，每個空品區
必須明確挑出「最新發布、明日、全日」那一筆，推播文案寫的才是真的明日預報。
"""

import sys

sys.path.insert(0, ".")

from crawler.forecast_fetcher import _pick_forecast

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


def rec(area, pub, date, aqi, timeperiod=None):
    r = {"area": area, "publishtime": pub, "forecastdate": date, "aqi": str(aqi)}
    if timeperiod is not None:
        r["timeperiod"] = timeperiod
    return r


def main():
    today = "2026-11-10"

    print("\n[改版前格式：無 timeperiod，3 天，順序打亂]")
    old = [rec("北部", "2026-11-10 16:30", d, a)
           for d, a in [("2026-11-13", 1), ("2026-11-11", 2), ("2026-11-12", 3)]]
    got = _pick_forecast(old, today)
    check("挑到明日", [r["aqi"] for r in got] == ["2"], str(got))

    print("\n[改版後 10:30 發布：第 1 天＝今天，含上午／下午]")
    new = [rec("北部", "2026-11-10 10:30", d, a, tp) for d, tp, a in [
        ("2026-11-13", "全日", 1), ("2026-11-10", "上午", 2), ("2026-11-11", "全日", 3),
        ("2026-11-10", "全日", 4), ("2026-11-12", "全日", 5), ("2026-11-10", "下午", 6),
    ]]
    got = _pick_forecast(new, today)
    check("挑到明日全日，不是今天或半日", [r["aqi"] for r in got] == ["3"], str(got))

    print("\n[改版後 16:30 發布：第 1 天＝明日，含上午／下午]")
    new = [rec("中部", "2026-11-10 16:30", "2026-11-11", a, tp)
           for tp, a in [("下午", 1), ("上午", 2), ("全日", 3)]]
    new.append(rec("中部", "2026-11-10 16:30", "2026-11-14", 4, "全日"))
    got = _pick_forecast(new, today)
    check("明日有三筆時挑全日", [r["aqi"] for r in got] == ["3"], str(got))

    print("\n[同一天有兩次發布]")
    mixed = [rec("竹苗", "2026-11-10 10:30", "2026-11-11", 1, "全日"),
             rec("竹苗", "2026-11-10 16:30", "2026-11-11", 2, "全日")]
    got = _pick_forecast(mixed, today)
    check("只用最新那次發布", [r["aqi"] for r in got] == ["2"], str(got))

    print("\n[明日缺資料]")
    got = _pick_forecast([rec("花東", "2026-11-10 10:30", "2026-11-12", 7, "全日"),
                          rec("花東", "2026-11-10 10:30", "2026-11-10", 8, "全日")], today)
    check("退而取今天以後最近一天", [r["aqi"] for r in got] == ["8"], str(got))

    print(f"\n通過 {_passed}，失敗 {_failed}")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
