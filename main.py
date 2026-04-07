from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel
import requests
import os
import urllib3
import json

# 載入 .env 環境變數（本機開發用，Docker 透過 docker-compose 傳入）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # production 環境不一定有 python-dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── RAG 模組 ────────────────────────────────────────────────────────────────
from rag.embedder import build_knowledge_base
from rag.rag_engine import generate_advice


# ── Lifespan：啟動時建立知識庫 ────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 FastAPI 啟動中，正在初始化健康規則知識庫...")
    try:
        build_knowledge_base(force_rebuild=False)
    except Exception as e:
        print(f"⚠️  知識庫初始化失敗（服務仍可使用，但 RAG 功能受限）：{e}")
    yield
    print("🛑 FastAPI 關閉中...")


app = FastAPI(lifespan=lifespan)


def normalize_name(name: str) -> str:
    """將「臺」統一轉為「台」，使兩者在比對時視為相同。"""
    return name.replace("臺", "台") if name else name


# ── 輔助函式：取得縣市的 AQI 與氣象資料 ────────────────────────────────────────

def _fetch_aqi_for_county(county: str) -> dict:
    """內部呼叫：取得縣市最佳代表測站 AQI"""
    API_KEY = os.getenv("MOENV_API_KEY", "")
    url = (
        f"https://data.moenv.gov.tw/api/v2/aqx_p_432"
        f"?api_key={API_KEY}&limit=1000&sort=ImportDate desc&format=JSON"
    )
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        records = data if isinstance(data, list) else data.get("records", [])

        norm = normalize_name(county)
        county_records = [
            r for r in records
            if normalize_name(r.get("county", "")) == norm
        ]

        if not county_records:
            return {}

        # 取 AQI 最高的測站（最具代表性）
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
        }
    except Exception as e:
        print(f"AQI 查詢失敗：{e}")
        return {}


def _fetch_wind_for_county(county: str) -> dict:
    """內部呼叫：取得縣市氣象站平均風速風向"""
    API_KEY = os.getenv("CWA_API_KEY", "")
    url = (
        f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001"
        f"?Authorization={API_KEY}&format=JSON"
    )
    try:
        resp = requests.get(url, verify=False, timeout=10)
        data = resp.json()
        stations = data.get("records", {}).get("Station", [])

        norm = normalize_name(county)
        county_stations = [
            s for s in stations
            if normalize_name(s.get("GeoInfo", {}).get("CountyName", "")) == norm
        ]

        if not county_stations:
            return {"wind_speed": 0.0, "wind_direction": 0.0}

        speeds, directions = [], []
        for s in county_stations:
            we = s.get("WeatherElement", {})
            try:
                speeds.append(float(we.get("WindSpeed", 0) or 0))
                directions.append(float(we.get("WindDirection", 0) or 0))
            except (ValueError, TypeError):
                pass

        return {
            "wind_speed": round(sum(speeds) / len(speeds), 1) if speeds else 0.0,
            "wind_direction": round(sum(directions) / len(directions), 1) if directions else 0.0,
        }
    except Exception as e:
        print(f"氣象查詢失敗：{e}")
        return {"wind_speed": 0.0, "wind_direction": 0.0}


def _fetch_recent_events_for_region(region: str) -> str:
    """從 scraped_news.json 撈取近期同地區結構化污染事件描述"""
    try:
        file_path = os.path.join(
            os.path.dirname(__file__), "crawler", "scraped_news.json"
        )
        if not os.path.exists(file_path):
            return "無"

        with open(file_path, "r", encoding="utf-8") as f:
            all_news = json.load(f)

        norm = normalize_name(region)
        region_news = [
            n for n in all_news
            if norm in normalize_name(n.get("region", ""))
        ]

        # 只取有確認污染事件的新聞（帶有 structured_event 欄位）
        confirmed_events = []
        for n in region_news:
            se = n.get("structured_event")
            if se and se.get("is_confirmed_pollution_event"):
                event_type = se.get("event_type", "unknown")
                severity = se.get("severity", "unknown")
                title = n.get("title", "")
                confirmed_events.append(f"{title}（{event_type}，{severity}）")

        if not confirmed_events:
            # 若無結構化事件，改用標題關鍵字
            fire_news = [
                n["title"] for n in region_news
                if any(k in n.get("title", "") for k in ["火災", "濃煙", "火警", "大火", "異味"])
            ]
            if fire_news:
                return f"附近有火災/濃煙通報：{fire_news[0]}"
            return "無"

        return "、".join(confirmed_events[:2])  # 最多回傳 2 筆

    except Exception as e:
        print(f"事件查詢失敗：{e}")
        return "無"


# ── Pydantic 模型 ────────────────────────────────────────────────────────────

class UserProfile(BaseModel):
    age_group: str = "adult"      # adult / child / elderly
    is_pregnant: bool = False
    has_asthma: bool = False
    has_cardiovascular: bool = False
    has_allergy: bool = False


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
    API_KEY = os.getenv("MOENV_API_KEY", "REPLACE_WITH_YOUR_API_KEY")
    url = f"https://data.moenv.gov.tw/api/v2/aqx_p_432?api_key={API_KEY}&limit=1000&sort=ImportDate desc&format=JSON"

    try:
        response = requests.get(url)
        data = response.json()
        records = data if isinstance(data, list) else data.get("records", [])

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
        return {
            "status": "error",
            "county": county,
            "message": f"連線錯誤: {str(e)}",
            "records": []
        }


@app.get("/api/weather")
def get_weather(county: str = None):
    API_KEY = os.getenv("CWA_API_KEY", "REPLACE_WITH_YOUR_CWA_API_KEY")
    url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001?Authorization={API_KEY}&format=JSON"

    try:
        response = requests.get(url, verify=False)
        data = response.json()

        raw_records = []
        if "records" in data and "Station" in data["records"]:
            raw_records = data["records"]["Station"]

        records = []
        for r in raw_records:
            county_name = r.get("GeoInfo", {}).get("CountyName", "")
            lat, lon = "", ""
            coordinates = r.get("GeoInfo", {}).get("Coordinates", [])
            for coord in list(coordinates):
                if isinstance(coord, dict):
                    lat = coord.get("StationLatitude", lat)
                    lon = coord.get("StationLongitude", lon)

            records.append({
                "sitename": r.get("StationName", ""),
                "county": county_name,
                "latitude": lat,
                "longitude": lon,
                "WindSpeed": r.get("WeatherElement", {}).get("WindSpeed", "0"),
                "WindDirection": r.get("WeatherElement", {}).get("WindDirection", "0")
            })

        if county:
            norm_county = normalize_name(county)
            records = [r for r in records if normalize_name(r.get("county", "")) == norm_county]

        return {
            "status": "success",
            "county": county,
            "message": "成功取得氣象署資料",
            "records": records
        }
    except Exception as e:
        return {
            "status": "error",
            "county": county,
            "message": f"連線錯誤: {str(e)}",
            "records": []
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
def get_rag_advice(req: RagAdviceRequest):
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

        # 2. 取得氣象資料
        wind_data = _fetch_wind_for_county(county)
        wind_speed = wind_data.get("wind_speed", 0.0)
        wind_direction = wind_data.get("wind_direction", 0.0)

        # 3. 取得近期污染事件（從已結構化的新聞）
        event_desc = _fetch_recent_events_for_region(county)

        # 4. 呼叫 RAG 引擎生成建議
        result = generate_advice(
            county=county,
            aqi=aqi,
            pm25=pm25,
            wind_speed=wind_speed,
            wind_direction=wind_direction,
            user_profile=req.user_profile.model_dump(),
            event_description=event_desc,
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
