# crawler/fire_alert_scraper.py
# 從民生示警公開資料平台（NCDR）抓取消防署即時重大火災警示

import requests
import feedparser
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET

FIRE_FEED_URL = "https://alerts.ncdr.nat.gov.tw/RssAtomFeed.ashx?AlertType=1087"
_CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/91.0.4472.124 Safari/537.36"
    )
}

_TAIWAN_COUNTIES = [
    "台北市", "新北市", "基隆市", "桃園市", "新竹市", "新竹縣",
    "苗栗縣", "台中市", "彰化縣", "南投縣", "雲林縣", "嘉義市",
    "嘉義縣", "台南市", "高雄市", "屏東縣", "宜蘭縣", "花蓮縣",
    "台東縣", "澎湖縣", "金門縣", "連江縣",
]


def _extract_county(area_desc: str) -> str:
    """從 areaDesc（如「台南市中西區青年路」）取出縣市名稱。"""
    normalized = area_desc.replace("臺", "台")
    for county in _TAIWAN_COUNTIES:
        if county in normalized:
            return county
    return ""


def _parse_circle(circle_str: str) -> tuple[float, float] | None:
    """解析 CAP circle 欄位，回傳 (lat, lng)。格式：'lat,lng radius'"""
    try:
        coord_part = circle_str.strip().split(" ")[0]
        a, b = coord_part.split(",")
        a, b = float(a), float(b)
        # 台灣座標範圍：lat 21.5–25.5、lng 118.0–122.5
        if 21.5 <= a <= 25.5 and 118.0 <= b <= 122.5:
            return a, b
        # 嘗試 lng,lat 順序
        if 21.5 <= b <= 25.5 and 118.0 <= a <= 122.5:
            return b, a
        return None
    except Exception:
        return None


def _fetch_cap_detail(cap_url: str) -> dict:
    """從個別 CAP 檔案取得地點、座標、嚴重程度。"""
    result = {
        "area_desc": "", "county": "",
        "latitude": None, "longitude": None,
        "severity": "", "description": "",
    }
    try:
        resp = requests.get(cap_url, headers=_HEADERS, timeout=8)
        root = ET.fromstring(resp.content)
        ns = {"cap": _CAP_NS}

        area_desc = root.findtext("cap:info/cap:area/cap:areaDesc", namespaces=ns) or ""
        circle    = root.findtext("cap:info/cap:area/cap:circle",   namespaces=ns) or ""
        severity  = root.findtext("cap:info/cap:severity",          namespaces=ns) or ""
        desc      = root.findtext("cap:info/cap:description",       namespaces=ns) or ""

        coords = _parse_circle(circle)
        result.update({
            "area_desc":   area_desc,
            "county":      _extract_county(area_desc),
            "latitude":    coords[0] if coords else None,
            "longitude":   coords[1] if coords else None,
            "severity":    severity,
            "description": desc,
        })
    except Exception as e:
        print(f"CAP 解析失敗 ({cap_url})：{e}")
    return result


def fetch_fire_alerts(hours: int = 24) -> list[dict]:
    """
    抓取民生示警平台近 N 小時的有效重大火災警示。

    只回傳 msgType=Alert（排除 Cancel 已結案）。

    回傳格式：
    [
      {
        "id":          "NFA_Fire_...",
        "area_desc":   "台南市中西區青年路",
        "county":      "台南市",
        "latitude":    22.992,
        "longitude":   120.206,
        "description": "火災-一般(集合)住宅",
        "severity":    "Moderate",
        "updated":     "2026-04-22T17:21:59+08:00",
        "cap_url":     "https://...",
        "source":      "NCDR_NFA",
        "event_type":  "fire",
      },
      ...
    ]
    """
    results = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    try:
        resp = requests.get(FIRE_FEED_URL, headers=_HEADERS, timeout=10)
        feed = feedparser.parse(resp.content)
    except Exception as e:
        print(f"民生示警火災 feed 抓取失敗：{e}")
        return []

    for entry in feed.entries:
        if entry.get("cap_msgtype", "") == "Cancel":
            continue

        updated_str = entry.get("updated", "")
        try:
            updated = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
            if updated < cutoff:
                continue
        except Exception:
            continue

        cap_url = entry.get("link", "")
        detail = _fetch_cap_detail(cap_url) if cap_url else {}

        results.append({
            "id":          entry.get("id", ""),
            "area_desc":   detail.get("area_desc", ""),
            "county":      detail.get("county", ""),
            "latitude":    detail.get("latitude"),
            "longitude":   detail.get("longitude"),
            "description": detail.get("description", entry.get("summary", "火災")),
            "severity":    detail.get("severity", ""),
            "updated":     updated_str,
            "cap_url":     cap_url,
            "source":      "NCDR_NFA",
            "event_type":  "fire",
        })

    return results
