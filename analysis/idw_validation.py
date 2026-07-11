# analysis/idw_validation.py
# IDW 空間插值 vs 最近測站法：留一交叉驗證（Leave-One-Out Cross-Validation）
#
# 方法：輪流把每個真實測站當作「假想使用者位置」，以其餘測站估計該點數值，
#       與該站實際觀測值比對，計算 MAE / RMSE。
#
# 執行：在 AirQuality_backend 目錄下 `python analysis/idw_validation.py`

import math
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from gis.interpolation import idw_estimate, _valid_stations

API_URL = "https://airquality-api-968727437042.asia-east1.run.app/api/air_quality"


def _nearest_estimate(user_lat, user_lng, records, field):
    """基線方法：最近測站法（目前 App 的做法）。"""
    est = idw_estimate(user_lat, user_lng, records, field=field, k=1, power=2.0)
    return est["value"] if est else None


def loocv(records: list[dict], field: str, methods: dict) -> dict:
    """對每個測站做留一驗證，回傳各方法的 MAE / RMSE / N。"""
    stations = _valid_stations(records, field)
    errors: dict[str, list[float]] = {name: [] for name in methods}

    for i, target in enumerate(stations):
        others_raw = [r for r in records
                      if r.get("sitename") != target["sitename"]]
        for name, fn in methods.items():
            pred = fn(target["lat"], target["lng"], others_raw, field)
            if pred is not None:
                errors[name].append(pred - target["value"])

    result = {}
    for name, errs in errors.items():
        n = len(errs)
        mae = sum(abs(e) for e in errs) / n
        rmse = math.sqrt(sum(e * e for e in errs) / n)
        result[name] = {"mae": mae, "rmse": rmse, "n": n}
    return result


def main():
    print("下載測站即時資料...")
    resp = requests.get(API_URL, timeout=60)
    records = resp.json().get("records", [])
    print(f"共 {len(records)} 筆測站記錄\n")

    methods = {
        "最近測站法(基線)": lambda la, ln, rs, f: _nearest_estimate(la, ln, rs, f),
        "IDW k=3 p=2":     lambda la, ln, rs, f: (idw_estimate(la, ln, rs, f, k=3, power=2.0) or {}).get("value"),
        "IDW k=4 p=2":     lambda la, ln, rs, f: (idw_estimate(la, ln, rs, f, k=4, power=2.0) or {}).get("value"),
        "IDW k=5 p=2":     lambda la, ln, rs, f: (idw_estimate(la, ln, rs, f, k=5, power=2.0) or {}).get("value"),
        "IDW k=4 p=1":     lambda la, ln, rs, f: (idw_estimate(la, ln, rs, f, k=4, power=1.0) or {}).get("value"),
    }

    for field, label in [("aqi", "AQI"), ("pm2.5", "PM2.5")]:
        print(f"=== {label} 留一交叉驗證 ===")
        print(f"{'方法':<18} {'MAE':>8} {'RMSE':>8} {'N':>5}")
        result = loocv(records, field, methods)
        baseline_mae = result["最近測站法(基線)"]["mae"]
        for name, m in result.items():
            improve = (1 - m["mae"] / baseline_mae) * 100 if baseline_mae else 0
            note = f"（比基線改善 {improve:+.1f}%）" if name != "最近測站法(基線)" else ""
            print(f"{name:<18} {m['mae']:>8.2f} {m['rmse']:>8.2f} {m['n']:>5} {note}")
        print()


if __name__ == "__main__":
    main()
