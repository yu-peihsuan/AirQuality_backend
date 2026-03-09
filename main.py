from fastapi import FastAPI
import requests
import os
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI()

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
            # 篩選特定縣市的測站
            records = [r for r in records if r.get("county") == county]
            
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
            # 篩選特定縣市的測站
            records = [r for r in records if r.get("county") == county]
            
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
