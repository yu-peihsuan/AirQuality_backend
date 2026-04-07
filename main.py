from fastapi import FastAPI
from pydantic import BaseModel
import requests
import os
import urllib3
import json
from datetime import datetime
from rag.llm_structurer import structure_news_event

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI()

def normalize_name(name: str) -> str:
    """將「臺」統一轉為「台」，使兩者在比對時視為相同。"""
    return name.replace("臺", "台") if name else name

@app.get("/")
def read_root():
    return {"message": "空氣品質後端伺服器已在 Docker 中成功啟動！"}

@app.get("/api/air_quality")
def get_air_quality(county: str = None):
    # 從環境變數讀取 API Key (.env 內設定)
    API_KEY = os.getenv("MOENV_API_KEY", "REPLACE_WITH_YOUR_API_KEY")
    url = f"https://data.moenv.gov.tw/api/v2/aqx_p_432?api_key={API_KEY}&limit=1000&sort=ImportDate desc&format=JSON"
    
    try:
        response = requests.get(url)
        data = response.json()
        
        # 處理 API: 若格式為直接的陣列(list)，或是包在 dict 裡的 "records"
        records = data if isinstance(data, list) else data.get("records", [])
        
        if county:
            # 篩選特定縣市的測站（台／臺視為相同）
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
    # 從環境變數讀取 CWA API Key (.env 內設定)
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
            
            # 取得經緯度 (如果有提供)
            lat = ""
            lon = ""
            coordinates = r.get("GeoInfo", {}).get("Coordinates", [])
            for coord in list(coordinates):
                # 這裡不確定回傳的 Coordinates 內格式，使用 safe get
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
            # 篩選特定縣市的測站（台／臺視為相同）
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

class ReportRequest(BaseModel):
    location: str
    category: str
    description: str
    latitude: float | None = None
    longitude: float | None = None


@app.post("/api/report")
def submit_report(req: ReportRequest):
    news_item = {
        "source": "民眾回報",
        "region": req.location,
        "title": f"[{req.category}] {req.location}",
        "summary": req.description,
        "url": "",
        "published_at": datetime.now().isoformat(),
        "timestamp": datetime.now().isoformat(),
        "latitude": req.latitude,
        "longitude": req.longitude,
    }

    structured = structure_news_event(news_item)

    reports_path = os.path.join(os.path.dirname(__file__), "crawler", "user_reports.json")
    reports = []
    if os.path.exists(reports_path):
        with open(reports_path, "r", encoding="utf-8") as f:
            reports = json.load(f)
    reports.insert(0, structured)
    with open(reports_path, "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)

    is_confirmed = False
    se = structured.get("structured_event")
    if se:
        is_confirmed = se.get("is_confirmed_pollution_event", False)

    return {
        "status": "success",
        "message": "回報已送出，感謝您的通報。",
        "is_confirmed": is_confirmed,
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
            
        if region:
            filtered_news = []
            norm_region = normalize_name(region)
            for n in news_data:
                news_region = n.get("region", "")
                norm_news_region = normalize_name(news_region)
                # 只顯示有明確標記該地區的新聞
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

