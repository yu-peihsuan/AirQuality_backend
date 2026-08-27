# 必須在所有其他 import 之前載入，確保 rag/llm 模組建立 client 時能讀到 key
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI
from pydantic import BaseModel
import requests
import os
import math
import urllib3
import json
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from rag.llm_structurer import analyze_citizen_report
from fcm.token_store import (
    register_token, get_tokens_by_county, get_all_tokens,
    set_daily_preference, get_due_daily_tokens, mark_daily_sent, get_token_record,
    claim_token,
)
from fcm.fcm_sender import send_multicast
from gis.hotspot_analyzer import analyze_hotspots, check_downwind, get_affected_counties, COUNTY_CENTROIDS
from api.auth import module as auth_router
from core.auth import CallerIdentity, get_caller_identity, verify_admin
from db.devices_db import init_device_db
from db.reports_db import (
    init_db, insert_report,
    get_recent_reports, get_all_reports,
    get_recent_confirmed_by_county,
    get_recent_reports_by_region,
    count_recent_by_device, exists_similar_recent,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── RAG 模組 ────────────────────────────────────────────────────────────────
from rag.embedder import build_knowledge_base
from rag.rag_engine import generate_advice


# ── 新聞爬蟲排程任務 ─────────────────────────────────────────────────────────
def _scraper_job():
    """每 1 小時執行一次：爬取新聞、存入 DB、清除過期資料、更新 JSON。"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⏰ 排程爬蟲啟動...")
    try:
        from crawler.news_scraper import run_scraper, init_db as news_init_db, save_to_db, cleanup_old_news
        import json, os
        results = run_scraper()
        news_init_db()
        save_to_db(results)
        cleanup_old_news()
        output_path = os.path.join(os.path.dirname(__file__), "crawler", "scraped_news.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=4)
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ 排程爬蟲完成，共 {len(results)} 筆")
    except Exception as e:
        print(f"⚠️  排程爬蟲失敗：{e}")


# ── 空品預報推播排程任務 ──────────────────────────────────────────────────────
# 記錄已推播過的 (county, forecastdate)，當天內不重複推播同一縣市
_forecast_pushed: set[tuple[str, str]] = set()
_forecast_pushed_date: str = ""


def _forecast_push_job():
    """每 2 小時執行：若明天空品惡化（AQI ≥ 101）且當天尚未推播，則立即推播。"""
    global _forecast_pushed, _forecast_pushed_date
    today = datetime.now().strftime("%Y-%m-%d")
    # 跨日重置已推播記錄
    if _forecast_pushed_date != today:
        _forecast_pushed = set()
        _forecast_pushed_date = today

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⏰ 空品預報推播排程啟動...")
    try:
        from crawler.forecast_fetcher import fetch_worsening_forecasts
        worsening = fetch_worsening_forecasts()
        if not worsening:
            print("  ✅ 明日無縣市空品惡化預報，無需推播。")
            return
        sent = 0
        for rec in worsening:
            county      = rec["region"]
            forecastdate = rec["published_at"]
            key = (county, forecastdate)
            if key in _forecast_pushed:
                continue  # 當天已推過，跳過
            tokens = get_tokens_by_county(county)
            if not tokens:
                _forecast_pushed.add(key)
                continue
            title = f"空品預報｜{county}"
            body  = rec["summary"] or rec["title"]
            try:
                send_multicast(tokens, title=title, body=body, data={"type": "forecast", "county": county})
                _forecast_pushed.add(key)
                print(f"  📲 推播：{county} | {len(tokens)} 台裝置")
                sent += 1
            except Exception as e:
                print(f"  ⚠️ 推播失敗：{county} | {e}")
        if sent:
            print(f"  ✅ 預報推播完成，共 {sent} 個縣市")
        else:
            print("  ✅ 無新預警需推播（已全部推送過）")
    except Exception as e:
        print(f"⚠️  預報推播排程失敗：{e}")


# ── 火災警示推播 ──────────────────────────────────────────────────────────────
_fire_pushed: set[str] = set()  # 已推播過的 alert ID

def _fire_alert_push_job():
    """每 10 分鐘：有新重大火災警示就推播給受影響縣市。"""
    global _fire_pushed
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⏰ 火災警示推播排程啟動...")
    try:
        from crawler.fire_alert_scraper import fetch_fire_alerts
        alerts = fetch_fire_alerts(hours=2)
        sent = 0
        for alert in alerts:
            aid = alert.get("id", "")
            if not aid or aid in _fire_pushed:
                continue
            county = alert.get("county", "")
            tokens = get_tokens_by_county(county)
            if not tokens:
                _fire_pushed.add(aid)
                continue
            title = f"🔥 火災警示｜{county}"
            body  = alert.get("area_desc", "") or alert.get("description", "重大火災")
            try:
                send_multicast(tokens, title=title, body=body.strip(), data={"type": "fire", "county": county})
                _fire_pushed.add(aid)
                sent += 1
                print(f"  📲 火災推播：{county} | {len(tokens)} 台裝置")
            except Exception as e:
                print(f"  ⚠️ 火災推播失敗：{e}")
        if sent:
            print(f"  ✅ 火災警示推播完成，共 {sent} 筆")
    except Exception as e:
        print(f"⚠️  火災推播排程失敗：{e}")


# ── AQI 超標推播 ──────────────────────────────────────────────────────────────
_aqi_alerted: dict[str, int] = {}  # county -> last pushed AQI band (0=normal,1=sensitive,2=all)

# ── AQI 回應快取（MOENV API 不穩定時的備援）────────────────────────────────────
_aqi_records_cache: list = []       # 最後一次成功取得的全台站點原始資料
_aqi_cache_ts: datetime | None = None  # 快取建立時間

def _aqi_alert_push_job():
    """每 30 分鐘：AQI ≥ 151 推全體；AQI 101-150 推敏感族群。"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⏰ AQI 超標推播排程啟動...")
    try:
        API_KEY = os.getenv("MOENV_API_KEY", "")
        url = (f"https://data.moenv.gov.tw/api/v2/aqx_p_432"
               f"?api_key={API_KEY}&limit=1000&sort=ImportDate desc&format=JSON")
        resp = requests.get(url, timeout=10)
        records = resp.json()
        if not isinstance(records, list):
            records = records.get("records", [])

        # 以縣市分組取最高 AQI
        county_max: dict[str, int] = {}
        for r in records:
            county = r.get("county", "")
            try:
                aqi = int(r.get("aqi", 0))
            except (ValueError, TypeError):
                continue
            if aqi > county_max.get(county, 0):
                county_max[county] = aqi

        for county, aqi in county_max.items():
            prev_band = _aqi_alerted.get(county, 0)
            if aqi >= 151:
                band = 2
                if band <= prev_band:
                    continue
                tokens = get_tokens_by_county(county)
                title = f"🔴 空氣危害｜{county}"
                body  = f"AQI {aqi}，請盡量待室內"
            elif aqi >= 101:
                band = 1
                if band <= prev_band:
                    continue
                from fcm.token_store import get_sensitive_tokens_by_county
                tokens = get_sensitive_tokens_by_county(county)
                title = f"⚠️ 空氣警示｜{county}"
                body  = f"AQI {aqi}，敏感族群注意防護"
            else:
                _aqi_alerted[county] = 0
                continue
            if not tokens:
                continue
            try:
                send_multicast(tokens, title=title, body=body, data={"type": "aqi", "county": county})
                _aqi_alerted[county] = band
                print(f"  📲 AQI 推播：{county} AQI {aqi} | {len(tokens)} 台裝置")
            except Exception as e:
                print(f"  ⚠️ AQI 推播失敗：{county} | {e}")
    except Exception as e:
        print(f"⚠️  AQI 推播排程失敗：{e}")


# ── 每日空氣品質摘要推播 ──────────────────────────────────────────────────────

def _fetch_aqi_all_stations() -> list[dict]:
    """抓取即時 AQI，回傳全台測站清單 [{sitename, county, aqi, status, lat, lng}]。"""
    API_KEY = os.getenv("MOENV_API_KEY", "")
    url = (f"https://data.moenv.gov.tw/api/v2/aqx_p_432"
           f"?api_key={API_KEY}&limit=1000&sort=ImportDate desc&format=JSON")
    resp = requests.get(url, timeout=10)
    records = resp.json()
    if not isinstance(records, list):
        records = records.get("records", [])

    stations: list[dict] = []
    for r in records:
        try:
            stations.append({
                "sitename": r.get("sitename", ""),
                "county":   r.get("county", ""),
                "aqi":      int(r.get("aqi", 0)),
                "status":   r.get("status", ""),
                "lat":      float(r.get("latitude")),
                "lng":      float(r.get("longitude")),
            })
        except (ValueError, TypeError):
            continue
    return stations


def _nearest_station(stations: list[dict], lat: float, lng: float) -> dict | None:
    """回傳距離 (lat, lng) 最近的測站；無資料回傳 None。"""
    best, best_d = None, None
    for s in stations:
        d_lat = math.radians(s["lat"] - lat)
        d_lng = math.radians(s["lng"] - lng)
        a = (math.sin(d_lat / 2) ** 2
             + math.cos(math.radians(lat)) * math.cos(math.radians(s["lat"]))
             * math.sin(d_lng / 2) ** 2)
        d = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        if best_d is None or d < best_d:
            best, best_d = s, d
    return best


def _county_best_from(stations: list[dict]) -> dict[str, dict]:
    """由測站清單整理出每縣市 AQI 最高的站（無座標裝置的 fallback）。"""
    county_best: dict[str, dict] = {}
    for s in stations:
        if s["county"] and s["aqi"] > county_best.get(s["county"], {}).get("aqi", -1):
            county_best[s["county"]] = s
    return county_best


def _daily_summary_push_job():
    """每分鐘檢查一次：有裝置設定的每日通知時間到了，就推播當地 AQI 摘要（每裝置每天最多推一次）。"""
    # 使用台灣時間（UTC+8）：使用者於 App 設定的是本地時間，
    # 而 Cloud Run 伺服器預設為 UTC，若用 datetime.now() 會差 8 小時而永遠對不上。
    now = datetime.now(timezone(timedelta(hours=8)))
    today = now.strftime("%Y-%m-%d")
    due = get_due_daily_tokens(now.hour, now.minute, today)
    if not due:
        return

    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] ⏰ 每日摘要推播：{len(due)} 台裝置到時間")
    try:
        stations = _fetch_aqi_all_stations()
    except Exception as e:
        print(f"  ⚠️ 每日摘要取得 AQI 失敗：{e}")
        return
    if not stations:
        print("  ⚠️ 每日摘要：無測站資料，跳過本輪")
        return
    county_best = _county_best_from(stations)

    for t in due:
        # 有座標 → 用「最近測站」（與 App 首頁顯示一致）；無座標 → 縣市最高 AQI 站
        s = None
        lat, lng = t.get("lat"), t.get("lng")
        if lat is not None and lng is not None:
            try:
                s = _nearest_station(stations, float(lat), float(lng))
            except (ValueError, TypeError):
                s = None
        use_nearest = s is not None
        if s is None:
            s = county_best.get(t["county"])
        if not s:
            continue
        title = f"🌤 每日空氣品質摘要｜{t['county']}"
        body = (f"鄰近測站 {s['sitename']}｜AQI {s['aqi']}（{s['status']}）"
                if use_nearest else f"AQI {s['aqi']}（{s['status']}）")
        try:
            send_multicast([t["token"]], title=title, body=body,
                            data={"type": "daily_summary", "county": t["county"]})
            mark_daily_sent(t["token"], today)
            print(f"  📲 每日摘要推播：{t['county']} {s['sitename']} AQI {s['aqi']}")
        except Exception as e:
            print(f"  ⚠️ 每日摘要推播失敗：{t['county']} | {e}")


# ── Lifespan：啟動時初始化 DB 與知識庫 ───────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. 初始化民眾回報 DB 與已註冊裝置表
    try:
        init_db()
        init_device_db()
    except Exception as e:
        print(f"⚠️  DB 初始化失敗：{e}")

    # 2. 初始化 RAG 知識庫
    print("🚀 FastAPI 啟動中，正在初始化健康規則知識庫...")
    try:
        build_knowledge_base(force_rebuild=False)
    except Exception as e:
        print(f"⚠️  知識庫初始化失敗（服務仍可使用，但 RAG 功能受限）：{e}")

    # 3. 啟動新聞爬蟲排程（每 1 小時，啟動時立刻執行一次）
    from datetime import datetime as _dt
    scheduler = BackgroundScheduler()
    scheduler.add_job(_scraper_job, "interval", hours=1, id="news_scraper",
                      next_run_time=_dt.now())
    scheduler.add_job(_forecast_push_job,    "interval", minutes=30, id="forecast_push",   next_run_time=_dt.now())
    scheduler.add_job(_fire_alert_push_job,  "interval", minutes=10, id="fire_alert_push", next_run_time=_dt.now())
    scheduler.add_job(_aqi_alert_push_job,   "interval", minutes=30, id="aqi_alert_push",  next_run_time=_dt.now())
    # 每日摘要用 cron 對齊每分鐘第 0 秒檢查（interval 會落在啟動時刻的秒數上，
    # 造成最多近一分鐘的延遲）；時間比對本身在 job 內以台灣時間（UTC+8）計算
    scheduler.add_job(_daily_summary_push_job, "cron", second=0, id="daily_summary_push")
    scheduler.start()
    print("⏰ 新聞爬蟲排程已啟動（每 1 小時執行一次，啟動時立即執行）")

    yield

    scheduler.shutdown(wait=False)
    print("🛑 FastAPI 關閉中...")


app = FastAPI(lifespan=lifespan)
app.include_router(auth_router)


def normalize_name(name: str) -> str:
    """將「臺」統一轉為「台」，使兩者在比對時視為相同。"""
    return name.replace("臺", "台") if name else name


def _extract_county_from_location(text: str) -> str | None:
    """從地址文字中提取縣市名稱。"""
    norm = normalize_name(text or "")
    for county in COUNTY_CENTROIDS:
        if normalize_name(county) in norm:
            return county
    return None


def _extract_wind_from_records(records: list) -> dict:
    """從站點清單計算全台平均風速風向。"""
    speeds, directions = [], []
    for r in records:
        try:
            speeds.append(float(r.get("windspeed", 0) or 0))
            directions.append(float(r.get("winddirection", 0) or 0))
        except (ValueError, TypeError):
            pass
    if speeds:
        return {
            "wind_speed": round(sum(speeds) / len(speeds), 1),
            "wind_direction": round(sum(directions) / len(directions), 1),
        }
    return {"wind_speed": 0.0, "wind_direction": 0.0}


def _fetch_wind_national_from_aqi() -> dict:
    """從環境部 AQI 資料取得全台平均風速風向（供熱點分析用）。"""
    global _aqi_records_cache, _aqi_cache_ts
    API_KEY = os.getenv("MOENV_API_KEY", "")
    url = (
        f"https://data.moenv.gov.tw/api/v2/aqx_p_432"
        f"?api_key={API_KEY}&limit=1000&sort=ImportDate desc&format=JSON"
    )
    try:
        resp = requests.get(url, timeout=10)
        records = resp.json()
        if not isinstance(records, list):
            records = records.get("records", [])
        if records:
            _aqi_records_cache = records
            _aqi_cache_ts = datetime.now()
        return _extract_wind_from_records(records)
    except Exception as e:
        print(f"AQI 全台風速查詢失敗：{e}")
        if _aqi_records_cache:
            print("  → 使用 AQI 快取資料計算風速")
            return _extract_wind_from_records(_aqi_records_cache)
    return {"wind_speed": 0.0, "wind_direction": 0.0}


def _geocode_address(address: str) -> tuple | None:
    """Google Maps Geocoding API：地址文字 → (lat, lng)，取得失敗回傳 None。"""
    api_key = os.getenv("MAPS_API_KEY", "")
    if not api_key:
        return None
    try:
        encoded = requests.utils.quote(address)
        url = (
            f"https://maps.googleapis.com/maps/api/geocode/json"
            f"?address={encoded}&key={api_key}&language=zh-TW"
        )
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("status") == "OK":
            loc = data["results"][0]["geometry"]["location"]
            return (loc["lat"], loc["lng"])
    except Exception as e:
        print(f"Geocoding 失敗：{e}")
    return None


# ── 輔助函式：取得縣市的 AQI 與氣象資料 ────────────────────────────────────────

def _best_record_for_county(records: list, county: str) -> dict:
    """從站點清單中挑出縣市最高 AQI 測站資料。"""
    norm = normalize_name(county)
    county_records = [r for r in records if normalize_name(r.get("county", "")) == norm]
    if not county_records:
        return {}

    def safe_aqi(r):
        try:
            return int(r.get("aqi", 0))
        except (ValueError, TypeError):
            return 0

    best = max(county_records, key=safe_aqi)
    return {
        "aqi": safe_aqi(best),
        "pm25": float(best.get("pm2.5", 0) or 0),
        "sitename": best.get("sitename", ""),
        "wind_speed": float(best.get("windspeed", 0) or 0),
        "wind_direction": float(best.get("winddirection", 0) or 0),
        "temperature": float(best.get("temperature", 0) or 0),
    }


def _fetch_aqi_for_county(county: str) -> dict:
    """內部呼叫：取得縣市最佳代表測站 AQI 與風速風向"""
    global _aqi_records_cache, _aqi_cache_ts
    API_KEY = os.getenv("MOENV_API_KEY", "")
    url = (
        f"https://data.moenv.gov.tw/api/v2/aqx_p_432"
        f"?api_key={API_KEY}&limit=1000&sort=ImportDate desc&format=JSON"
    )
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        records = data if isinstance(data, list) else data.get("records", [])
        if records:
            _aqi_records_cache = records
            _aqi_cache_ts = datetime.now()
        return _best_record_for_county(records, county)
    except Exception as e:
        print(f"AQI 查詢失敗：{e}")
        if _aqi_records_cache:
            print(f"  → 使用 AQI 快取資料（{county}）")
            return _best_record_for_county(_aqi_records_cache, county)
        return {}


_TYPE_ZH = {
    "fire": "火災/濃煙", "chemical": "化學異味", "dust": "揚塵",
    "odor": "異味", "vehicle": "車輛廢氣", "factory": "工廠排放",
    "general_air_quality": "空氣品質不良",
}


def _fetch_user_report_events(county: str) -> str:
    """從 DB 取得近 24 小時、同縣市的確認回報事件描述"""
    try:
        recent = get_recent_confirmed_by_county(normalize_name(county), hours=24)
        if not recent:
            return "無"

        events = []
        seen: set[tuple[str, str]] = set()
        for r in recent:
            ev_type = _TYPE_ZH.get(r.get("event_type", ""), r.get("category", "污染"))
            severity = r.get("severity", "")
            desc = r.get("summary", "")[:30]
            # 同類型＋同內容的回報只取一筆，避免重複事件灌爆事件脈絡
            key = (ev_type, desc)
            if key in seen:
                continue
            seen.add(key)
            label = f"民眾回報{ev_type}" + (f"（{severity}）" if severity else "") + f"：{desc}"
            events.append(label)

        return "、".join(events[:2]) if events else "無"
    except Exception as e:
        print(f"用戶回報事件查詢失敗：{e}")
        return "無"


def _fetch_recent_events_for_region(region: str) -> str:
    """從 scraped_news.json 與民生示警火災警示撈取近期同地區事件描述"""
    norm = normalize_name(region)
    events = []

    # ── 1. 民生示警即時重大火災警示 ──────────────────────────────────────────
    try:
        from crawler.fire_alert_scraper import fetch_fire_alerts
        fire_alerts = fetch_fire_alerts(hours=24)
        for alert in fire_alerts:
            alert_county = normalize_name(alert.get("county", ""))
            area_desc    = normalize_name(alert.get("area_desc", ""))
            if norm in alert_county or norm in area_desc:
                desc = alert.get("description", "火災")
                location = alert.get("area_desc", "")
                label = f"重大火災警示：{desc}"
                if location:
                    label += f"（{location}）"
                events.append(label)
    except Exception as e:
        print(f"民生示警火災查詢失敗：{e}")

    # ── 2. 新聞爬蟲事件 ───────────────────────────────────────────────────────
    try:
        file_path = os.path.join(
            os.path.dirname(__file__), "crawler", "scraped_news.json"
        )
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                all_news = json.load(f)

            region_news = [
                n for n in all_news
                if norm in normalize_name(n.get("region", ""))
            ]

            confirmed_events = []
            for n in region_news:
                se = n.get("structured_event")
                if se and se.get("is_confirmed_pollution_event"):
                    event_type = se.get("event_type", "unknown")
                    severity   = se.get("severity", "unknown")
                    title      = n.get("title", "")
                    confirmed_events.append(f"{title}（{event_type}，{severity}）")

            if confirmed_events:
                events.extend(confirmed_events[:2])
            else:
                fire_news = [
                    n["title"] for n in region_news
                    if any(k in n.get("title", "") for k in ["火災", "濃煙", "火警", "大火", "異味"])
                ]
                if fire_news:
                    events.append(f"附近有火災/濃煙通報：{fire_news[0]}")
    except Exception as e:
        print(f"新聞事件查詢失敗：{e}")

    # 去除完全相同的事件描述後再組合
    events = list(dict.fromkeys(events))
    return "、".join(events[:3]) if events else "無"


# ── Pydantic 模型 ────────────────────────────────────────────────────────────

class UserProfile(BaseModel):
    age_group: str = "adult"      # adult / child / elderly
    is_pregnant: bool = False
    has_asthma: bool = False
    has_cardiovascular: bool = False
    has_allergy: bool = False
    other_notes: str | None = None


class RagAdviceRequest(BaseModel):
    county: str
    latitude: float | None = None
    longitude: float | None = None
    aqi: int | None = None
    pm25: float | None = None
    user_profile: UserProfile = UserProfile()


# ── API Endpoints ────────────────────────────────────────────────────────────

@app.get("/")
def read_root():
    return {"message": "空氣品質後端伺服器已在 Docker 中成功啟動！"}


@app.get("/api/air_quality")
def get_air_quality(county: str = None):
    global _aqi_records_cache, _aqi_cache_ts
    API_KEY = os.getenv("MOENV_API_KEY", "REPLACE_WITH_YOUR_API_KEY")
    url = f"https://data.moenv.gov.tw/api/v2/aqx_p_432?api_key={API_KEY}&limit=1000&sort=ImportDate desc&format=JSON"

    try:
        response = requests.get(url, timeout=15)
        data = response.json()
        records = data if isinstance(data, list) else data.get("records", [])

        if records:
            _aqi_records_cache = records
            _aqi_cache_ts = datetime.now()

        if county:
            norm_county = normalize_name(county)
            records = [r for r in records if normalize_name(r.get("county", "")) == norm_county]

        return {
            "status": "success",
            "county": county,
            "message": "成功取得環境部資料",
            "records": records
        }
    except Exception as e:
        print(f"MOENV API 連線失敗：{e}")
        if _aqi_records_cache:
            records = _aqi_records_cache
            if county:
                norm_county = normalize_name(county)
                records = [r for r in records if normalize_name(r.get("county", "")) == norm_county]
            age_min = int((datetime.now() - _aqi_cache_ts).total_seconds() / 60) if _aqi_cache_ts else 0
            print(f"  → 回傳 {age_min} 分鐘前的快取資料（{len(records)} 筆）")
            return {
                "status": "cached",
                "county": county,
                "message": f"環境部 API 暫時無法連線，顯示 {age_min} 分鐘前的資料",
                "records": records
            }
        return {
            "status": "error",
            "county": county,
            "message": f"連線錯誤: {str(e)}",
            "records": []
        }


@app.get("/api/air_quality/estimate")
def get_air_quality_estimate(lat: float, lng: float, k: int = 4, power: float = 2.0):
    """
    以 IDW 反距離加權空間插值，估計使用者定位點的 AQI 與 PM2.5。
    取代單一最近測站法，提升個人定位點的數據精度。
    """
    from gis.interpolation import idw_estimate
    base = get_air_quality(None)
    records = base.get("records", [])
    if not records:
        return {"status": "error", "message": "無測站資料", "estimate": None, "stations": []}

    aqi_est  = idw_estimate(lat, lng, records, field="aqi",   k=k, power=power)
    pm25_est = idw_estimate(lat, lng, records, field="pm2.5", k=k, power=power)
    if not aqi_est:
        return {"status": "error", "message": "有效測站不足", "estimate": None, "stations": []}

    return {
        "status": base.get("status", "success"),
        "estimate": {
            "aqi":    aqi_est["value"],
            "pm25":   pm25_est["value"] if pm25_est else None,
            "method": aqi_est["method"],
            "k":      k,
        },
        "stations": aqi_est["stations"],
    }


@app.get("/api/user_reports")
def get_user_reports(region: str = None):
    """回傳 24 小時內的民眾回報；若指定 region 則只回傳該地區。"""
    try:
        if region:
            records = get_recent_reports_by_region(region, hours=24)
        else:
            records = get_recent_reports(hours=24)
        return {"status": "success", "records": records}
    except Exception as e:
        return {"status": "error", "message": str(e), "records": []}


@app.get("/api/user_reports/history")
def get_user_reports_history(caller: CallerIdentity = Depends(get_caller_identity)):
    """回傳所有歷史民眾回報。"""
    try:
        return {"status": "success", "records": get_all_reports()}
    except Exception as e:
        return {"status": "error", "message": str(e), "records": []}


class ReportRequest(BaseModel):
    location: str
    category: str
    description: str
    latitude: float | None = None
    longitude: float | None = None
    # device_id 不再由客戶端提供：改自 access token 取得（見 submit_report），
    # 否則呼叫端只要換一個字串就能繞過下方的回報頻率限制。


def _cross_verify_report(county: str, event_type: str) -> list[str]:
    """
    多源交叉驗證：將民眾回報與客觀資料源自動比對，回傳佐證來源清單。
    比對來源：官方火災警示（NCDR）、新聞事件、測站數據異常。
    """
    sources: list[str] = []
    norm = normalize_name(county) if county else ""
    if not norm:
        return sources

    # 1. 官方火災警示（火災類回報）
    if event_type in ("fire", "火災煙霧"):
        try:
            from crawler.fire_alert_scraper import fetch_fire_alerts
            for alert in fetch_fire_alerts(hours=24):
                if (norm in normalize_name(alert.get("county", ""))
                        or norm in normalize_name(alert.get("area_desc", ""))):
                    sources.append("官方火災警示")
                    break
        except Exception as e:
            print(f"交叉驗證-火災警示查詢失敗：{e}")

    # 2. 新聞事件（同區域的已確認污染事件或火災相關報導）
    try:
        news_path = os.path.join(os.path.dirname(__file__), "crawler", "scraped_news.json")
        if os.path.exists(news_path):
            with open(news_path, "r", encoding="utf-8") as f:
                all_news = json.load(f)
            for n in all_news:
                if norm not in normalize_name(n.get("region", "")):
                    continue
                se_n = n.get("structured_event") or {}
                if se_n.get("is_confirmed_pollution_event") or any(
                        k in n.get("title", "") for k in ("火災", "濃煙", "火警", "大火", "異味")):
                    sources.append("新聞報導")
                    break
    except Exception as e:
        print(f"交叉驗證-新聞查詢失敗：{e}")

    # 3. 測站數據異常（PM2.5 達敏感族群不健康等級，或 AQI 破百）
    try:
        data = _fetch_aqi_for_county(county)
        if (data.get("pm25") or 0) >= 35.5 or (data.get("aqi") or 0) >= 101:
            sources.append("測站數據異常")
    except Exception as e:
        print(f"交叉驗證-測站查詢失敗：{e}")

    return sources


@app.post("/api/report")
def submit_report(req: ReportRequest, caller: CallerIdentity = Depends(get_caller_identity)):
    now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat()

    # ── 回報頻率限制（防灌水／防重複）────────────────────────────────────
    if count_recent_by_device(caller.device_id, minutes=3) >= 1:
        return {
            "status": "rate_limited",
            "message": "回報太頻繁，請於 3 分鐘後再試。",
            "is_confirmed": False,
        }
    if exists_similar_recent(req.location, req.category, req.description, hours=6):
        return {
            "status": "duplicate",
            "message": "相同內容的回報已存在，感謝您的通報。",
            "is_confirmed": False,
        }

    # 座標為 null 時，嘗試用 Google Maps API 將地址文字轉換為座標
    lat = req.latitude
    lng = req.longitude
    if lat is None or lng is None:
        coords = _geocode_address(req.location)
        if coords:
            lat, lng = coords

    report_item = {
        "source": "民眾回報",
        "region": req.location,
        "category": req.category,
        "title": f"[{req.category}] {req.location}",
        "summary": req.description,
        "url": "",
        "published_at": now,
        "timestamp": now,
        "latitude": lat,
        "longitude": lng,
    }

    # 使用民眾回報專用語意分析
    structured = analyze_citizen_report(report_item)
    se = structured.get("structured_event")
    is_confirmed = se.get("is_confirmed_pollution_event", False) if se else False

    # 多源交叉驗證：與官方警示／新聞／測站數據自動比對
    county_v = _extract_county_from_location(req.location) or ""
    event_type_v = (se.get("event_type") or req.category) if se else req.category
    verify_sources = _cross_verify_report(county_v, event_type_v)

    # 所有回報（確認＆未確認）都存入 SQLite，is_confirmed 欄位區分
    structured["device_id"] = caller.device_id
    structured["verify_sources"] = verify_sources
    insert_report(structured)

    # 確認為污染事件時，自動推播給受影響縣市的裝置
    if is_confirmed and lat is not None and lng is not None:
        try:
            county = _extract_county_from_location(req.location) or ""
            aqi_wind = _fetch_aqi_for_county(county) if county else _fetch_wind_national_from_aqi()
            wind_speed = aqi_wind.get("wind_speed", 0.0)
            wind_direction = aqi_wind.get("wind_direction", 0.0)

            affected = get_affected_counties(lat, lng, wind_speed, wind_direction)
            event_type = (se.get("event_type") or "general_air_quality") if se else "general_air_quality"
            type_label = _TYPE_ZH.get(event_type, "污染事件")

            tokens = list({
                t for c in affected
                for t in get_tokens_by_county(normalize_name(c))
            })
            if tokens:
                send_multicast(
                    tokens,
                    title=f"[民報] {type_label}",
                    body=req.description[:50] if req.description else type_label,
                    data={"type": event_type},
                )
                print(f"✅ FCM 推播：{type_label} @ {req.location}，推送 {len(tokens)} 台裝置")

            # 附近 5km 裝置也推播（精準通知）
            from fcm.token_store import get_tokens_near
            nearby_tokens = [t for t in get_tokens_near(lat, lng, radius_km=5.0) if t not in tokens]
            if nearby_tokens:
                send_multicast(
                    nearby_tokens,
                    title=f"[民眾回報] 📍 附近{type_label}",
                    body=req.description[:50] if req.description else type_label,
                    data={"type": event_type},
                )
                print(f"✅ 附近裝置推播：{len(nearby_tokens)} 台")
        except Exception as e:
            print(f"⚠️ FCM 推播失敗：{e}")

    return {
        "status": "success",
        "message": "回報已送出，感謝您的通報。",
        "is_confirmed": is_confirmed,
        "verify_sources": verify_sources,
        "structured_event": se,
    }


@app.get("/api/news")
def get_news(region: str = None):
    try:
        file_path = os.path.join(os.path.dirname(__file__), "crawler", "scraped_news.json")
        if not os.path.exists(file_path):
            return {
                "status": "error",
                "region": region,
                "message": "找不到新聞資料檔案",
                "records": []
            }

        with open(file_path, "r", encoding="utf-8") as f:
            news_data = json.load(f)

        # 只保留 48 小時內的新聞
        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        def is_fresh(n):
            ts = n.get("timestamp") or n.get("published_at", "")
            if not ts:
                return False
            try:
                import email.utils
                parsed = email.utils.parsedate_to_datetime(ts)
                return parsed.astimezone(timezone.utc) >= cutoff
            except Exception:
                pass
            try:
                parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                return parsed.astimezone(timezone.utc) >= cutoff
            except Exception:
                return False
        news_data = [n for n in news_data if is_fresh(n)]

        if region:
            filtered_news = []
            norm_region = normalize_name(region)
            for n in news_data:
                news_region = n.get("region", "")
                norm_news_region = normalize_name(news_region)
                if (norm_region in norm_news_region) or (norm_news_region in norm_region):
                    filtered_news.append(n)
            news_data = filtered_news

        return {
            "status": "success",
            "region": region,
            "message": "成功取得新聞資料",
            "records": news_data
        }
    except Exception as e:
        return {
            "status": "error",
            "region": region,
            "message": f"讀取新聞失敗: {str(e)}",
            "records": []
        }


@app.post("/api/rag_advice")
def get_rag_advice(req: RagAdviceRequest, caller: CallerIdentity = Depends(get_caller_identity)):
    """
    RAG 個人化空氣品質建議端點。
    根據用戶位置、健康檔案，結合當前 AQI/氣象資料與近期污染事件，
    透過 RAG + GPT 生成個人化建議。
    """
    try:
        county = req.county

        # 1. 取得當地 AQI 資料（若前端已傳入則直接使用，不重複呼叫 API）
        if req.aqi is not None:
            aqi = req.aqi
            pm25 = req.pm25 if req.pm25 is not None else 0.0
        else:
            aqi_data = _fetch_aqi_for_county(county)
            aqi = aqi_data.get("aqi", 0)
            pm25 = aqi_data.get("pm25", 0.0)

        # 2. 從 AQI 資料取得風速風向與溫度
        aqi_wind = _fetch_aqi_for_county(county)
        wind_speed = aqi_wind.get("wind_speed", 0.0)
        wind_direction = aqi_wind.get("wind_direction", 0.0)
        temperature = aqi_wind.get("temperature", 0.0)

        # 3. 取得即時天氣（降雨、天氣描述）
        from crawler.weather_fetcher import fetch_weather_for_county, fetch_weather_forecast_for_county
        weather_data     = fetch_weather_for_county(county, lat=req.latitude, lng=req.longitude)
        weather_desc     = weather_data.get("description", "")
        is_raining       = weather_data.get("is_raining", False)
        weather_forecast = fetch_weather_forecast_for_county(county)
        # 氣象署有溫度時優先使用，補足 AQI 溫度資料
        if weather_data.get("temp") is not None:
            temperature = weather_data["temp"]

        # 3. 取得近期污染事件（新聞 + 民眾回報，合併）
        news_event_desc   = _fetch_recent_events_for_region(county)
        report_event_desc = _fetch_user_report_events(county)
        all_events = [e for e in [news_event_desc, report_event_desc] if e and e != "無"]
        event_desc = "、".join(all_events) if all_events else "無"

        # 4. 下風處判斷（須有使用者 GPS 座標且有風）
        is_downwind = False
        downwind_sources: list[dict] = []

        if req.latitude is not None and req.longitude is not None and wind_speed >= 0.5:
            hotspots = analyze_hotspots(min_reports=2, cluster_radius_km=1.5, top_n=10)
            if hotspots:
                downwind_sources = check_downwind(
                    user_lat=req.latitude,
                    user_lng=req.longitude,
                    wind_direction_deg=wind_direction,
                    hotspots=hotspots,
                    wind_speed=wind_speed,
                )
                if downwind_sources:
                    is_downwind = True
                    src = downwind_sources[0]
                    _TYPE_ZH = {
                        "fire": "火災/濃煙", "chemical": "化學異味", "dust": "揚塵",
                        "odor": "異味", "vehicle": "車輛廢氣", "factory": "工廠排放",
                        "general_air_quality": "空氣品質不良",
                    }
                    type_label = _TYPE_ZH.get(src.get("dominant_type", ""), "污染源")
                    dw_desc = (
                        f"您目前位於{type_label}污染熱點的下風處"
                        f"（距離約 {src['distance_km']} km，"
                        f"強度 {int(src['intensity'] * 100)}%）"
                    )
                    event_desc = f"{event_desc}；{dw_desc}" if event_desc != "無" else dw_desc

        # 5. 取得明日空品預報
        forecast_aqi, forecast_status = 0, ""
        try:
            from crawler.forecast_fetcher import fetch_latest_forecast, _parse_aqi
            fc_records = fetch_latest_forecast(county)
            if fc_records:
                fc = fc_records[0]
                forecast_aqi    = _parse_aqi(fc.get("aqi"))
                forecast_status = fc.get("status", "")
        except Exception:
            pass

        # 6. 呼叫 RAG 引擎生成建議
        result = generate_advice(
            county=county,
            aqi=aqi,
            pm25=pm25,
            wind_speed=wind_speed,
            wind_direction=wind_direction,
            user_profile=req.user_profile.model_dump(),
            event_description=event_desc,
            is_downwind=is_downwind,
            forecast_aqi=forecast_aqi,
            forecast_status=forecast_status,
            temperature=temperature,
            weather_desc=weather_desc,
            is_raining=is_raining,
            weather_forecast=weather_forecast,
        )

        return {
            "status": "success",
            "county": county,
            "aqi": aqi,
            "pm25": pm25,
            "wind_speed": wind_speed,
            "wind_direction": wind_direction,
            "aqi_level": result["aqi_level"],
            "advice": result["advice"],
            "event_context": event_desc,
            "is_downwind": is_downwind,
            "downwind_sources": downwind_sources[:3],
            "retrieved_rules": result["retrieved_rules"],
            "rag_error": result.get("error"),
        }

    except Exception as e:
        return {
            "status": "error",
            "county": req.county,
            "message": f"RAG 建議生成失敗: {str(e)}",
            "advice": None,
        }


# ── RAG 消融實驗 Endpoint（研究用）────────────────────────────────────────────

class RagExperimentRequest(BaseModel):
    """消融實驗用：所有輸入固定給定，不抓即時天氣/事件，隔離檢索策略變因。"""
    aqi: int
    pm25: float = 0.0
    user_profile: UserProfile = UserProfile()
    event_description: str = "無"
    retrieval_mode: str = "hybrid"   # none / semantic / rule / hybrid


@app.post("/api/rag_advice/experiment")
def rag_advice_experiment(req: RagExperimentRequest, _: None = Depends(verify_admin)):
    """
    檢索策略消融實驗（Ablation Study）端點。
    以受控輸入直接呼叫 RAG 引擎，比較 none/semantic/rule/hybrid 四種檢索策略。
    """
    try:
        result = generate_advice(
            county="實驗情境",
            aqi=req.aqi,
            pm25=req.pm25,
            wind_speed=0.0,
            wind_direction=0.0,
            user_profile=req.user_profile.model_dump(),
            event_description=req.event_description,
            is_downwind=False,
            forecast_aqi=0,
            forecast_status="",
            temperature=25.0,
            weather_desc="晴",
            is_raining=False,
            weather_forecast="無資料",
            retrieval_mode=req.retrieval_mode,
        )
        return {
            "status": "success",
            "retrieval_mode": req.retrieval_mode,
            "aqi": req.aqi,
            "aqi_level": result["aqi_level"],
            "advice": result["advice"],
            "retrieved_rules": result["retrieved_rules"],
            "rag_error": result.get("error"),
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "advice": None}


# ── GIS 熱點分析 Endpoint ─────────────────────────────────────────────────────

@app.get("/api/hotspots")
def get_hotspots(min_reports: int = 2, radius_km: float = 1.5, top_n: int = 10):
    """
    分析民眾回報的空間熱點，結合當前風況判斷影響範圍。
    無風時自動擴大警戒半徑，並標記擴散條件差。
    """
    try:
        wind = _fetch_wind_national_from_aqi()
        hotspots = analyze_hotspots(
            min_reports=min_reports,
            cluster_radius_km=radius_km,
            top_n=top_n,
            wind_speed=wind["wind_speed"],
            wind_direction=wind["wind_direction"],
        )
        return {
            "status": "success",
            "count": len(hotspots),
            "hotspots": hotspots,
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "hotspots": []}


# ── 民生示警火災警示 Endpoint ──────────────────────────────────────────────────

@app.get("/api/fire_alerts")
def get_fire_alerts(region: str = None):
    """回傳民生示警平台近 24 小時的有效重大火災警示，格式與 /api/news 相容。"""
    try:
        from crawler.fire_alert_scraper import fetch_fire_alerts
        alerts = fetch_fire_alerts(hours=24)

        if region:
            norm = normalize_name(region)
            alerts = [
                a for a in alerts
                if norm in normalize_name(a.get("county", ""))
                or norm in normalize_name(a.get("area_desc", ""))
            ]

        records = [
            {
                "source":       "消防署火災警示",
                "region":       a.get("county", ""),
                "title":        a.get("description", "重大火災"),
                "summary":      a.get("area_desc", ""),
                "url":          a.get("cap_url", ""),
                "published_at": a.get("updated", ""),
                "timestamp":    a.get("updated", ""),
                "latitude":     a.get("latitude"),
                "longitude":    a.get("longitude"),
            }
            for a in alerts
        ]

        return {"status": "success", "region": region, "message": "", "records": records}
    except Exception as e:
        return {"status": "error", "region": region, "message": str(e), "records": []}


@app.get("/api/forecast")
def get_forecast(county: str = None):
    """回傳今日空品預報摘要，通知中心用。"""
    try:
        from crawler.forecast_fetcher import fetch_today_forecasts
        records = fetch_today_forecasts(county)
        return {"status": "success", "region": county, "message": "", "records": records}
    except Exception as e:
        return {"status": "error", "region": county, "message": str(e), "records": []}


@app.get("/api/weather")
def get_weather(county: str = None, lat: float = None, lng: float = None):
    """回傳即時天氣，需提供 lat/lng 以找最近測站。"""
    try:
        from crawler.weather_fetcher import fetch_weather_for_county
        data = fetch_weather_for_county(county or "", lat=lat, lng=lng)
        return {
            "status":      "success",
            "county":      county,
            "is_raining":  data.get("is_raining", False),
            "weather":     data.get("weather", ""),
            "temp":        data.get("temp"),
            "description": data.get("description", ""),
        }
    except Exception as e:
        return {"status": "error", "county": county, "message": str(e), "is_raining": False}


@app.get("/api/forecast/raw")
def get_forecast_raw(county: str = None):
    """回傳 AQF_P_01 今日完整原始資料，供查閱所有欄位內容。"""
    try:
        from crawler.forecast_fetcher import fetch_latest_forecast
        records = fetch_latest_forecast(county)
        return {"status": "success", "region": county, "count": len(records), "records": records}
    except Exception as e:
        return {"status": "error", "region": county, "message": str(e), "records": []}



# ── FCM 推播 Endpoints ────────────────────────────────────────────────────────

class TokenRegisterRequest(BaseModel):
    token:      str
    county:     str   = ""
    lat:        float = None
    lng:        float = None
    conditions: str   = ""  # 健康狀況，逗號分隔，如「氣喘,心血管疾病」


@app.post("/api/fcm/register")
def register_fcm_token(req: TokenRegisterRequest, caller: CallerIdentity = Depends(get_caller_identity)):
    """App 啟動時上傳裝置 FCM Token、縣市、座標、健康狀況。"""
    if not claim_token(req.token, caller.device_id):
        return {"status": "error", "message": "此 Token 已由其他裝置註冊"}
    try:
        register_token(req.token, req.county, req.lat, req.lng, req.conditions,
                       device_id=caller.device_id)
        return {"status": "success", "message": "Token 已註冊"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


class DailyNotificationRequest(BaseModel):
    token:  str
    enabled: bool
    hour:   int | None = None    # 0-23，enabled=True 時必填
    minute: int | None = None    # 0-59，enabled=True 時必填


@app.put("/api/fcm/daily-notification")
def set_daily_notification(req: DailyNotificationRequest, caller: CallerIdentity = Depends(get_caller_identity)):
    """設定或取消裝置的每日空氣品質摘要通知時間。"""
    if not claim_token(req.token, caller.device_id):
        return {"status": "error", "message": "無權變更其他裝置的通知設定"}
    try:
        set_daily_preference(req.token, req.enabled, req.hour, req.minute,
                             device_id=caller.device_id)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


class DailyNotificationTestRequest(BaseModel):
    token: str


@app.post("/api/fcm/daily-notification/test")
def test_daily_notification(req: DailyNotificationTestRequest, caller: CallerIdentity = Depends(get_caller_identity)):
    """測試用：立即推播一次每日摘要，不等排程時間、不佔用當天的正式額度。
    與正式排程同邏輯：有座標用最近測站（與 App 首頁一致），無座標退縣市最高 AQI 站。"""
    if not claim_token(req.token, caller.device_id):
        return {"status": "error", "message": "無權對其他裝置發送測試推播"}
    rec = get_token_record(req.token)
    county = (rec or {}).get("county") or ""
    if not county:
        return {"status": "error", "message": "找不到這個裝置的縣市，請確認 App 已定位並上傳過 Token"}
    try:
        stations = _fetch_aqi_all_stations()
        s = None
        lat, lng = rec.get("lat"), rec.get("lng")
        if lat is not None and lng is not None:
            try:
                s = _nearest_station(stations, float(lat), float(lng))
            except (ValueError, TypeError):
                s = None
        use_nearest = s is not None
        if s is None:
            s = _county_best_from(stations).get(county)
        if not s:
            return {"status": "error", "message": f"目前查不到 {county} 的即時 AQI 資料"}
        title = f"🌤 每日空氣品質摘要｜{county}"
        body = (f"鄰近測站 {s['sitename']}｜AQI {s['aqi']}（{s['status']}）"
                if use_nearest else f"AQI {s['aqi']}（{s['status']}）")
        send_multicast([req.token], title=title, body=body,
                        data={"type": "daily_summary_test", "county": county})
        return {"status": "success", "county": county, "aqi": s["aqi"]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


class PushRequest(BaseModel):
    county: str | None = None   # 指定縣市；None 表示推播全部裝置
    title: str
    body: str


@app.post("/api/fcm/push")
def push_notification(req: PushRequest, _: None = Depends(verify_admin)):
    """手動觸發推播（測試用 / 後台管理用）。"""
    try:
        tokens = get_tokens_by_county(req.county) if req.county else get_all_tokens()
        if not tokens:
            return {"status": "success", "message": "沒有符合條件的裝置", "sent": 0}
        result = send_multicast(tokens, req.title, req.body)
        return {"status": "success", **result}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/fcm/test")
def test_push(_: None = Depends(verify_admin)):
    """測試推播：發送一則假警報給所有已註冊裝置。"""
    try:
        tokens = get_all_tokens()
        if not tokens:
            return {
                "status": "error",
                "message": "尚無已註冊的裝置，請先開啟 App 讓 Token 自動上傳"
            }
        result = send_multicast(
            tokens,
            title="⚠️ 空氣品質警報（測試）",
            body="台北市 AQI 已超過 150，建議減少戶外活動並配戴口罩。",
            data={"type": "test"}
        )
        return {
            "status": "success",
            "message": f"推播已發送給 {len(tokens)} 台裝置",
            **result
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/fcm/test_auto")
def test_auto_push(_: None = Depends(verify_admin)):
    """測試自動推播：抓取真實 AQI 資料，對所有有 token 的縣市各推一則實際數據通知。"""
    try:
        API_KEY = os.getenv("MOENV_API_KEY", "")
        url = (f"https://data.moenv.gov.tw/api/v2/aqx_p_432"
               f"?api_key={API_KEY}&limit=1000&sort=ImportDate desc&format=JSON")
        resp = requests.get(url, timeout=10)
        records = resp.json()
        if not isinstance(records, list):
            records = records.get("records", [])

        county_max: dict[str, int] = {}
        for r in records:
            county = r.get("county", "")
            try:
                aqi = int(r.get("aqi", 0))
            except (ValueError, TypeError):
                continue
            if aqi > county_max.get(county, 0):
                county_max[county] = aqi

        sent_list = []
        for county, aqi in county_max.items():
            tokens = get_tokens_by_county(county)
            if not tokens:
                continue
            title = f"{'🔴' if aqi >= 151 else '🟡'} 空氣品質｜{county}"
            body  = f"目前 AQI {aqi}（自動推播測試）"
            send_multicast(tokens, title=title, body=body, data={"type": "aqi_test", "county": county})
            sent_list.append({"county": county, "aqi": aqi, "devices": len(tokens)})

        if not sent_list:
            return {"status": "error", "message": "沒有任何縣市有已註冊的裝置 token"}
        return {"status": "success", "pushed": sent_list}
    except Exception as e:
        return {"status": "error", "message": str(e)}
