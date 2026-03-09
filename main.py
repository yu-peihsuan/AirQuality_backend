from fastapi import FastAPI
import requests
import os

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