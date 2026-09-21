# AirQuality Backend｜空氣品質即時監測後端

台灣空氣品質即時監測 App 的後端服務，整合政府開放資料、民眾回報與 RAG AI 健康建議。

前端（Android）：[yu-peihsuan/AirQualityApp](https://github.com/yu-peihsuan/AirQualityApp)
正式環境：https://airquality-api-968727437042.asia-east1.run.app （`/docs` 為 Swagger UI）

## 技術棧

- **框架**：FastAPI + Uvicorn（Python）
- **資料庫**：SQLite（民眾回報、裝置、推播狀態、爬蟲新聞）
- **向量資料庫**：ChromaDB（embedded，RAG 健康規則知識庫）
- **LLM**：OpenRouter API（gpt-4o-mini、text-embedding-3-small）
- **排程**：APScheduler（BackgroundScheduler）
- **推播通知**：Firebase Cloud Messaging
- **空間分析**：NumPy + SciPy（KDE 核密度估計、IDW 反距離加權）
- **認證**：PyJWT（裝置匿名憑證，無帳號系統）
- **外部 API**：環境部空品觀測與預報、中央氣象署天氣、NCDR 民生示警、Google Maps、RSS 新聞
- **部署**：Docker → Google Cloud Run（asia-east1）

## 主要功能

- **即時空品**：全台 AQI/PM2.5 查詢，資料來自環境部測站
- **空間插值**：IDW 反距離加權估計任意座標的空品（84 測站 LOOCV 驗證，較最近測站法降低 AQI 誤差 19.2%）
- **空品預報**：環境部 AQF_P_01 預報資料，明日惡化自動推播
- **民眾回報**：使用者提交污染事件，經 LLM 語意審核判定可信度與分類
- **RAG 健康顧問**：依個人健康檔案與即時情境（空品、天氣、預報、附近事件、下風處）生成建議
- **GIS 熱點分析**：KDE 核密度估計找出污染熱點，並判斷使用者是否位於下風處
- **新聞爬蟲**：Google News / Yahoo / 公視 RSS，LLM 語意過濾空污相關報導
- **火災警示**：NCDR 民生示警平台 CAP 格式解析
- **推播通知**：空品超標、預報惡化、火災警示、每日摘要（依使用者設定時間）
- **裝置匿名憑證**：以裝置 JWT 保護寫入與計費端點，管理端可封鎖濫用裝置

## 本地開發

```bash
# 1. Clone repo
git clone https://github.com/yu-peihsuan/AirQuality_backend.git
cd AirQuality_backend

# 2. 設定環境變數
cp .env.example .env
# 編輯 .env 填入真實 key

# 3a. 用 Docker 啟動（推薦）
docker compose up -d --build
docker compose logs -f backend_api

# 3b. 或直接跑本機
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

後端 API 文件：`http://localhost:8000/docs`

需要的環境變數：

| 變數 | 說明 |
|------|------|
| `MOENV_API_KEY` | 環境部開放資料 API Key |
| `CWA_API_KEY` | 中央氣象署開放資料 API Key |
| `OPENROUTER_API_KEY` | OpenRouter LLM API Key |
| `MAPS_API_KEY` | Google Maps Geocoding API Key |
| `JWT_SECRET` | 裝置憑證簽章密鑰 |
| `ADMIN_TOKEN` | 管理端點密鑰 |

## 測試

```bash
python test_auth.py        # 認證：雜湊、註冊、續期、封鎖，共 41 項
python test_timezone.py    # 時間基準：時間窗、上游格式，共 25 項
```

兩支皆為獨立腳本，不需要 pytest、不需要網路。

## API 端點

標示「需裝置憑證」的端點須帶 `Authorization: Bearer <access_token>`，「需管理員」須帶 `X-Admin-Token`，其餘公開。

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | /  | 健康檢查 |
| POST | /api/auth/device | 裝置註冊，取得 access + refresh token |
| POST | /api/auth/refresh | 換發新的 access token |
| GET | /api/air_quality | 查詢 AQI 資料（可帶 `county`） |
| GET | /api/air_quality/estimate | IDW 插值估計座標空品（`lat`、`lng`） |
| GET | /api/weather | 查詢即時天氣（`county` 或 `lat`+`lng`） |
| GET | /api/forecast | 今日空品預報摘要（可帶 `county`） |
| GET | /api/forecast/raw | 今日空品預報完整原始資料 |
| GET | /api/news | 查詢 24 小時內爬蟲新聞（可帶 `region`） |
| GET | /api/fire_alerts | 查詢重大火災警示（可帶 `region`） |
| GET | /api/user_reports | 查詢 24 小時內民眾回報（可帶 `region`） |
| GET | /api/user_reports/history | 查詢所有歷史回報（需裝置憑證） |
| POST | /api/report | 提交民眾回報（需裝置憑證） |
| POST | /api/rag_advice | 取得個人化健康建議（需裝置憑證；`lang` 可選 zh／en） |
| POST | /api/rag_advice/experiment | RAG 消融實驗（需管理員） |
| GET | /api/hotspots | 污染熱點分析（`min_reports`、`radius_km`、`top_n`） |
| POST | /api/fcm/register | 上傳 FCM token（需裝置憑證） |
| PUT | /api/fcm/daily-notification | 設定每日摘要推播時間（需裝置憑證） |
| POST | /api/fcm/daily-notification/test | 測試每日摘要推播（需裝置憑證） |
| POST | /api/fcm/push | 手動推播（需管理員） |
| POST | /api/fcm/test | 推播測試通知（需管理員） |
| POST | /api/fcm/test_auto | 手動觸發自動推播邏輯（需管理員） |
| GET | /api/admin/devices | 列出已註冊裝置（需管理員） |
| POST | /api/admin/devices/{id}/revoke | 封鎖裝置（需管理員） |
| POST | /api/admin/devices/{id}/restore | 解除封鎖（需管理員） |

## 回報驗證說明

民眾回報屬於自願性地理資訊（VGI），需要過濾雜訊才能當作警示依據：

1. 頻率限制：每裝置 3 分鐘限 1 筆
2. 內容去重：6 小時內相同類型＋相同內容視為重複
3. LLM 語意審核：判定是否為可信污染事件（`is_confirmed`），並分類事件類型與嚴重度
4. 多源佐證：與新聞、火災警示交叉比對
5. 熱點門檻：需 `min_reports` 筆以上共識才成立警示

App 端依 `is_confirmed` 顯示【已證實】/【未證實】標籤。

## 排程任務

| 任務 | 頻率 | 說明 |
|------|------|------|
| 新聞爬蟲 | 每 1 小時 | 爬取 RSS、LLM 語意過濾、清除過期資料 |
| 空品預報推播 | 每 30 分鐘 | 明日 AQI ≥ 101 的縣市自動推播 |
| 火災警示推播 | 每 10 分鐘 | 有新的重大火災警示就推給受影響縣市 |
| AQI 超標推播 | 每 30 分鐘 | AQI ≥ 151 推全體，101–150 推敏感族群 |
| 每日摘要推播 | 每分鐘檢查 | 到了使用者設定的時間就推當地 AQI 摘要 |

排程與 API 同一行程，Cloud Run 以 `min-instances=1` 保持實例不休眠。

## 資料來源

| 資料 | 來源 |
|------|------|
| AQI 即時觀測 | 環境部空氣品質監測站（aqx_p_432） |
| 空品預報 | 環境部空氣品質預報（AQF_P_01） |
| 即時天氣 | 中央氣象署自動氣象站（O-A0003-001） |
| 天氣預報 | 中央氣象署 36 小時天氣預報（F-C0032-001） |
| 重大火災警示 | NCDR 民生示警公開資料平台（CAP） |
| 新聞 | Google News、Yahoo 新聞、公共電視（RSS） |
| 地址轉座標 | Google Maps Geocoding API |
| LLM / Embedding | OpenRouter（gpt-4o-mini、text-embedding-3-small） |

台灣官方資料源的時間一律視為台灣時間（UTC+8），全系統時間由 `core/timeutil.py` 統一處理。

## 專案結構

```
AirQuality_backend/
├── main.py                     # FastAPI app 入口（router 組裝、排程註冊、主要端點）
├── api/                        # Routers
│   ├── auth.py                 # 裝置註冊與 token 續期
│   └── admin.py                # 裝置檢視與封鎖
├── core/
│   ├── auth.py                 # JWT 簽發／驗證、device_id 雜湊
│   ├── config.py               # 環境變數集中讀取
│   └── timeutil.py             # 全系統時間基準（UTC+8）
├── crawler/                    # 外部資料抓取
│   ├── news_scraper.py         # RSS 新聞爬蟲 & LLM 語意過濾
│   ├── fire_alert_scraper.py   # NCDR 民生示警 CAP 解析
│   ├── forecast_fetcher.py     # 環境部空品預報
│   ├── weather_fetcher.py      # 中央氣象署天氣
│   └── CityCountyData.json     # 縣市鄉鎮對照表
├── db/                         # SQLite 資料存取
│   ├── reports_db.py           # 民眾回報、頻率限制、去重
│   ├── devices_db.py           # 裝置註冊與封鎖狀態
│   ├── push_state.py           # 推播去重狀態
│   └── firestore_reports.py    # Firestore 遷移雛形（未啟用）
├── rag/                        # RAG 健康建議
│   ├── rag_engine.py           # 檢索 + 生成主流程
│   ├── embedder.py             # ChromaDB 知識庫建立與檢索
│   ├── health_rules.py         # 健康規則知識庫
│   └── llm_structurer.py       # 回報的 LLM 語意審核與結構化
├── gis/                        # 空間分析
│   ├── hotspot_analyzer.py     # KDE 熱點分析、下風處判斷
│   └── interpolation.py        # IDW 空間插值
├── fcm/                        # 推播
│   ├── fcm_sender.py           # Firebase 推播發送
│   └── token_store.py          # FCM token 與每日通知設定
├── analysis/                   # 驗證與實驗腳本
│   ├── idw_validation.py       # IDW vs 最近測站法 LOOCV 驗證
│   └── rag_ablation.py         # RAG 情境欄位消融實驗
├── scripts/                    # 維運工具
├── test_auth.py                # 認證測試
├── test_timezone.py            # 時間基準測試
├── Dockerfile
├── docker-compose.yml
├── start.sh
├── requirements.txt
└── .env.example
```

## 部署

```bash
gcloud run deploy airquality-api --source . --region asia-east1
```

環境變數已設定於 Cloud Run 服務上，重新部署自動沿用，網址不變。
注意 **Cloud Run 磁碟為暫時性，重新部署會清空民眾回報與裝置資料**，展示前避免部署。
