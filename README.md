# AirQuality App — Backend

本專案使用 **Python + FastAPI**，並以 **Docker Compose** 統一管理後端伺服器（以及未來擴充的 ChromaDB 向量資料庫等服務）。

## 專案啟動方式

### 1 建立 `.env` 檔案（第一次啟動必做，之後不需重複執行）

請依照專案內提供的 `.env.example` 範本，新增並建立自己的 `.env` 檔案：

```bash
cp .env.example .env
```

`.env` 內包含所需的 `MOENV_API_KEY`（台灣環境部開放資料平台 API 金鑰）。為了確保成功取得 AQI 資料，請務必更換為您自己申請的 API Key，或暫時使用預設範例金鑰。

> ⚠️ `.env` 檔案內含機密資訊，已被加入 `.gitignore` 中不會被上傳到 GitHub，請每位開發者自行建立。

---

### 2 使用 Docker Compose 啟動整套服務

在專案目錄下執行以下指令開啟服務：

```bash
docker compose up -d --build
```

### 3 若有修改程式碼或環境變數

如果您有進一步修改 `main.py` 或是 `.env` 中的金鑰內容，為確保留新的設定檔套用，請執行以下指令重新啟動容器：

```bash
docker compose restart
```

---

### 4 查看後端 Log & 測試 API 狀態

#### 追蹤後端運行 Log

```bash
docker compose logs -f backend_api
```

---

### 5 API 端點總覽

後端啟動後可透過以下網址存取。所有 POST 端點可在 **Swagger UI** 互動測試：
**http://localhost:8000/docs**

#### 基本

| 方法 | 網址 | 說明 |
|------|------|------|
| GET | `http://localhost:8000/` | 確認後端是否正常運行 |
| GET | `http://localhost:8000/docs` | Swagger UI，可測試所有 API |

#### 空氣品質 / 氣象

| 方法 | 網址 | 說明 |
|------|------|------|
| GET | `http://localhost:8000/api/air_quality` | 全台 AQI 資料 |
| GET | `http://localhost:8000/api/air_quality?county=台北市` | 指定縣市 AQI |
| GET | `http://localhost:8000/api/weather` | 全台氣象資料 |
| GET | `http://localhost:8000/api/weather?county=台北市` | 指定縣市氣象 |

#### 新聞 / 民眾回報

| 方法 | 網址 | 說明 |
|------|------|------|
| GET | `http://localhost:8000/api/news` | 全部爬蟲新聞 |
| GET | `http://localhost:8000/api/news?region=台北市` | 指定地區新聞 |
| GET | `http://localhost:8000/api/user_reports` | 24 小時內民眾回報 |
| GET | `http://localhost:8000/api/user_reports/history` | 所有歷史回報 |
| POST | `http://localhost:8000/api/report` | 提交民眾回報 |

#### RAG 個人化建議

| 方法 | 網址 | 說明 |
|------|------|------|
| POST | `http://localhost:8000/api/rag_advice` | 取得個人化空氣品質建議 |

#### GIS 熱點分析

| 方法 | 網址 | 說明 |
|------|------|------|
| GET | `http://localhost:8000/api/hotspots` | 熱點分析結果 |
| GET | `http://localhost:8000/api/hotspots?min_reports=2&radius_km=1.5&top_n=10` | 自訂參數 |

> **RAG 建議回應新增欄位**（需傳入 `latitude`/`longitude`）：
> - `is_downwind: bool` — 使用者是否在污染熱點下風處
> - `downwind_sources: list` — 上風側污染熱點清單（含 `distance_km`、`bearing_to_user`）
> - 事件描述（`event_context`）現在同時整合新聞爬蟲與民眾回報兩個來源

#### FCM 推播

| 方法 | 網址 | 說明 |
|------|------|------|
| GET | `http://localhost:8000/api/fcm/test` | 推播測試通知給所有裝置 |
| POST | `http://localhost:8000/api/fcm/register` | 裝置註冊 FCM Token |
| POST | `http://localhost:8000/api/fcm/push` | 手動推播（指定縣市或全部） |

---

### 6 執行爬蟲程式

```bash
docker compose exec backend_api python crawler/news_scraper.py
```

