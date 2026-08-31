# gis/hotspot_analyzer.py
# 基於民眾回報座標，使用 KDE 計算空氣污染熱點

import math
import numpy as np
from scipy.stats import gaussian_kde
from core.timeutil import now_tw
from db.reports_db import get_confirmed_reports

CALM_WIND_THRESHOLD = 1.0  # 低於此風速（m/s）視為無風，擴散條件差

# 依大氣穩定度（時段）動態調整下風扇形參數
# 白天（不穩定）：對流旺盛 → 角度大、距離中
# 夜間（穩定）  ：逆溫易發 → 角度窄、距離遠
_DISPERSION_PARAMS = {
    "day":   {"angle_deg": 45.0, "max_km": 50.0},
    "night": {"angle_deg": 15.0, "max_km": 100.0},
}


def _get_dispersion_params() -> dict:
    """根據台灣當地時間（UTC+8）回傳對應的擴散參數。"""
    from datetime import timezone, timedelta
    tw_hour = now_tw().hour
    return _DISPERSION_PARAMS["day"] if 6 <= tw_hour < 18 else _DISPERSION_PARAMS["night"]

# 台灣各縣市重心座標（lat, lng）
COUNTY_CENTROIDS: dict[str, tuple[float, float]] = {
    "台北市":  (25.0478, 121.5319),
    "新北市":  (24.9936, 121.4572),
    "桃園市":  (24.9936, 121.3010),
    "台中市":  (24.1477, 120.6736),
    "台南市":  (23.0000, 120.2270),
    "高雄市":  (22.6273, 120.3014),
    "基隆市":  (25.1276, 121.7392),
    "新竹市":  (24.8138, 120.9675),
    "新竹縣":  (24.6390, 121.1368),
    "苗栗縣":  (24.4803, 120.9162),
    "彰化縣":  (24.0518, 120.5161),
    "南投縣":  (23.8383, 120.9876),
    "雲林縣":  (23.7101, 120.4313),
    "嘉義市":  (23.4801, 120.4491),
    "嘉義縣":  (23.4518, 120.2554),
    "屏東縣":  (22.5519, 120.5487),
    "宜蘭縣":  (24.6941, 121.7378),
    "花蓮縣":  (23.9872, 121.6016),
    "台東縣":  (22.7972, 121.0713),
    "澎湖縣":  (23.5711, 119.5793),
}


def _haversine(lat1, lon1, lat2, lon2) -> float:
    """計算兩點之間的距離（公里）。"""
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """計算從點1到點2的方位角（0=北，90=東，順時針）。"""
    d_lon = math.radians(lon2 - lon1)
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    x = math.sin(d_lon) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(d_lon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def get_affected_counties(
    event_lat: float,
    event_lng: float,
    wind_speed: float,
    wind_direction: float,
    calm_radius_km: float = 50.0,
) -> list[str]:
    """
    根據污染事件位置與風況，回傳受影響的縣市名稱清單。
    - 有風：依時段動態決定扇形角度與最遠距離
    - 無風：事件周邊 calm_radius_km 內的縣市
    """
    affected = []
    if wind_speed < CALM_WIND_THRESHOLD:
        for county, (lat, lng) in COUNTY_CENTROIDS.items():
            if _haversine(event_lat, event_lng, lat, lng) <= calm_radius_km:
                affected.append(county)
    else:
        params = _get_dispersion_params()
        angle_tolerance = params["angle_deg"]
        max_km = params["max_km"]
        downwind_dir = (wind_direction + 180) % 360
        for county, (lat, lng) in COUNTY_CENTROIDS.items():
            dist = _haversine(event_lat, event_lng, lat, lng)
            if dist > max_km:
                continue
            brg = _bearing(event_lat, event_lng, lat, lng)
            angle_diff = abs(((brg - downwind_dir + 180) % 360) - 180)
            if angle_diff <= angle_tolerance:
                affected.append(county)
    return affected


def check_downwind(
    user_lat: float,
    user_lng: float,
    wind_direction_deg: float,
    hotspots: list[dict],
    wind_speed: float = 0.0,
) -> list[dict]:
    """
    判斷使用者是否位於污染熱點的下風處。

    風從 wind_direction_deg 方向吹來，污染物往 (wind_direction_deg + 180) % 360 方向擴散。
    若熱點到使用者的方位角落在下風方向 ±angle_tolerance_deg 內，且距離 <= max_distance_km，
    則視為使用者在該熱點的下風處。

    回傳：下風熱點清單，每筆附加 distance_km（與使用者距離）、bearing_to_user（方位角）欄位。
    """
    if wind_direction_deg is None:
        return []

    results = []

    if wind_speed < CALM_WIND_THRESHOLD:
        # 無風：50km 內的熱點全部視為有影響，無方向性
        for hs in hotspots:
            dist = _haversine(user_lat, user_lng, hs["lat"], hs["lng"])
            if dist <= 50.0:
                results.append({
                    **hs,
                    "distance_km": round(dist, 2),
                    "bearing_to_user": 0.0,
                })
    else:
        params = _get_dispersion_params()
        angle_tolerance = params["angle_deg"]
        max_km = params["max_km"]
        downwind_target = (wind_direction_deg + 180) % 360
        for hs in hotspots:
            dist = _haversine(user_lat, user_lng, hs["lat"], hs["lng"])
            if dist > max_km:
                continue
            bearing = _bearing(hs["lat"], hs["lng"], user_lat, user_lng)
            angle_diff = abs(((bearing - downwind_target + 180) % 360) - 180)
            if angle_diff <= angle_tolerance:
                results.append({
                    **hs,
                    "distance_km": round(dist, 2),
                    "bearing_to_user": round(bearing, 1),
                })

    results.sort(key=lambda x: x["distance_km"])
    return results


def _load_reports() -> list[dict]:
    """從 DB 讀取已確認回報，只取有座標的紀錄。"""
    records = get_confirmed_reports()
    return [
        r for r in records
        if r.get("latitude") is not None and r.get("longitude") is not None
    ]


def _get_dominant_type(reports: list[dict]) -> str:
    """找出一組回報中最多的事件類別。"""
    from collections import Counter
    types = []
    for r in reports:
        se = r.get("structured_event")
        if se and se.get("event_type"):
            types.append(se["event_type"])
        else:
            types.append(r.get("category", "unknown"))
    if not types:
        return "unknown"
    return Counter(types).most_common(1)[0][0]


def analyze_hotspots(
    min_reports: int = 2,
    cluster_radius_km: float = 1.5,
    top_n: int = 10,
    wind_speed: float = 0.0,
    wind_direction: float = 0.0,
) -> list[dict]:
    """
    使用 KDE 分析民眾回報熱點。

    流程：
    1. 讀取有座標的民眾回報
    2. 若資料點 < min_reports，直接回傳空清單
    3. 以 gaussian_kde 計算密度
    4. 找出密度最高的網格點作為熱點中心
    5. 合併距離 < cluster_radius_km 的熱點
    6. 回傳前 top_n 個熱點

    回傳格式：
    [
      {
        "lat": 25.03,
        "lng": 121.56,
        "count": 5,
        "intensity": 0.87,   # 0~1 正規化密度
        "radius_km": 1.5,
        "dominant_type": "fire"
      },
      ...
    ]
    """
    is_calm = wind_speed < CALM_WIND_THRESHOLD
    # 無風時擴大影響半徑（污染物無法擴散，在周遭累積）
    effective_radius = cluster_radius_km * 2.0 if is_calm else cluster_radius_km

    reports = _load_reports()
    if len(reports) < min_reports:
        return []

    lats = np.array([r["latitude"]  for r in reports])
    lngs = np.array([r["longitude"] for r in reports])

    def _make_hotspot(lat, lng, count, intensity, nearby):
        return {
            "lat": lat,
            "lng": lng,
            "count": count,
            "intensity": intensity,
            "radius_km": effective_radius,
            "dominant_type": _get_dominant_type(nearby),
            "is_calm_wind": is_calm,
            "wind_speed": round(wind_speed, 1),
            "wind_direction": round(wind_direction, 1),
        }

    # 若所有點座標完全相同或幾乎相同（變異數極低），直接回傳單一熱點
    if np.std(lats) < 1e-6 and np.std(lngs) < 1e-6:
        return [_make_hotspot(float(np.mean(lats)), float(np.mean(lngs)), len(reports), 1.0, reports)]

    # KDE 估計密度（bandwidth 用 scott 法自動選）
    try:
        kde = gaussian_kde(np.vstack([lats, lngs]))
    except Exception:
        return []

    # 建立評估網格（以資料範圍為邊界，解析度 50x50）
    lat_min, lat_max = lats.min() - 0.05, lats.max() + 0.05
    lng_min, lng_max = lngs.min() - 0.05, lngs.max() + 0.05
    grid_lat = np.linspace(lat_min, lat_max, 50)
    grid_lng = np.linspace(lng_min, lng_max, 50)
    gx, gy = np.meshgrid(grid_lat, grid_lng)
    grid_points = np.vstack([gx.ravel(), gy.ravel()])

    density = kde(grid_points).reshape(gx.shape)

    # 正規化密度到 0~1
    d_min, d_max = density.min(), density.max()
    if d_max == d_min:
        return []
    density_norm = (density - d_min) / (d_max - d_min)

    # 找出密度 > 0.5 的候選網格點，依密度排序
    threshold = 0.5
    candidates = []
    rows, cols = np.where(density_norm > threshold)
    for r, c in zip(rows, cols):
        candidates.append({
            "lat": float(grid_lat[c]),
            "lng": float(grid_lng[r]),
            "intensity": float(density_norm[r, c])
        })
    candidates.sort(key=lambda x: x["intensity"], reverse=True)

    # 合併鄰近候選點為單一熱點
    hotspots = []
    used = set()
    for i, cand in enumerate(candidates):
        if i in used:
            continue
        cluster_indices = [i]
        for j, other in enumerate(candidates):
            if j != i and j not in used:
                if _haversine(cand["lat"], cand["lng"], other["lat"], other["lng"]) <= effective_radius:
                    cluster_indices.append(j)
        used.update(cluster_indices)

        center_lat = float(np.mean([candidates[k]["lat"] for k in cluster_indices]))
        center_lng = float(np.mean([candidates[k]["lng"] for k in cluster_indices]))

        # 找出這個熱點範圍內的真實回報
        nearby = [
            rep for rep in reports
            if _haversine(center_lat, center_lng, rep["latitude"], rep["longitude"]) <= effective_radius
        ]

        if len(nearby) < min_reports:
            continue

        hotspots.append(_make_hotspot(
            center_lat, center_lng,
            len(nearby), round(candidates[i]["intensity"], 3), nearby
        ))

        if len(hotspots) >= top_n:
            break

    return hotspots
