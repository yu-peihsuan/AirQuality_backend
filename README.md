# AirQuality Backend

台灣空氣品質即時監測 App 的後端服務——整合政府開放資料、民眾回報與 RAG AI
健康建議，以 **Python + FastAPI** 開發，部署於 Google Cloud Run。

前端（Android）：[yu-peihsuan/AirQualityApp](https://github.com/yu-peihsuan/AirQualityApp)

**正式環境**：https://airquality-api-968727437042.asia-east1.run.app
（`/docs` 為 Swagger UI，可直接互動測試所有端點）

---

## 技術棧

- **框架**：FastAPI + Uvicorn（ASGI）
- **資料庫**：SQLite（民眾回報／裝置／推播狀態／爬蟲新聞）
- **向量檢索**：ChromaDB（embedded，健康規則知識庫）
- **LLM**：OpenRouter — GPT-4o-mini（生成／語意審核）、text-embedding-3-small（向量化）
- **排程**：APScheduler（`BackgroundScheduler`，與 API 同行程）
- **推播**：Firebase Cloud Messaging（`firebase-admin`）
- **空間分析**：NumPy + SciPy（KDE 核密度估計、IDW 反距離加權）
- **認證**：PyJWT（裝置匿名憑證）
- **部署**：Docker → Google Cloud Run（`asia-east1`）

## 主要功能

- **即時空品** — 全台 AQI/PM2.5 查詢；**IDW 空間插值**估計任意座標的空品
- **空品預報** — 環境部 AQF_P_01，明日惡化自動推播
- **民眾回報** — 提交污染事件，經 **LLM 語意審核**判定可信度與分類，含頻率限制與去重
- **RAG AI 健康顧問** — 依個人健康檔案 + 即時情境（空品／天氣／預報／附近事件／下風處）生成建議
- **GIS 熱點分析** — KDE 核密度估計找出污染熱點，並判斷使用者是否位於下風處
- **新聞爬蟲** — Google News / Yahoo / 公視 RSS，LLM 語意過濾空污相關報導
- **火災警示** — NCDR 民生示警平台 CAP 格式解析
- **FCM 推播** — 空品超標、預報惡化、火災警示、每日摘要（依使用者設定時間）
- **裝置匿名憑證** — 無帳號系統，以裝置 JWT 保護寫入與計費端點；管理端可封鎖濫用裝置

---

## 系統架構

```
Android App ──HTTPS/JSON──► Cloud Run (FastAPI 單體容器, min-instances=1)
     ▲                            │
     │                            ├── 同步：auth / aqi / weather / news
     │                            │        report / rag / gis / fcm / admin
     │                            │
     │                            ├── 非同步：APScheduler 排程（爬蟲・推播）
     │                            │
     │                            ├── 資料：SQLite（回報・裝置・新聞）
     │                            │        ChromaDB（健康規則向量，embedded）
     │                            │
     │                            └── 外部：MOENV・CWA・NCDR・RSS
     │                                     OpenRouter・Google Maps
     └────────FCM push────────────────────  Firebase
```

| 層 | 內容 |
|----|------|
| Client | Android App（Kotlin + Compose），憑證由 OkHttp 攔截器自動附加與續期 |
| Network | Cloud Run 受管入口（DNS、TLS、自動擴縮）；無自建 LB／CDN，回應皆為動態 JSON |
| Application | FastAPI 單體容器。API 與排程共用同一行程，故需 `min-instances=1` 保持實例不休眠 |
| Data | SQLite 負責交易型讀寫與時間窗查詢；ChromaDB 負責語意相似度檢索，兩者存取模式不同故分開 |
| External | 環境部、中央氣象署、NCDR、RSS、OpenRouter、Google Maps、Firebase |

外部服務失效時系統**降級而非整體失敗**：天氣或新聞取不到，RAG 建議少一段情境仍可生成；
資料源金鑰未設定時對應功能關閉、服務照常啟動；但 `JWT_SECRET` 未設定採 fail closed，
認證端點一律回 500。

> **時間基準**：全系統統一使用台灣時間（UTC+8，aware），時間戳格式為
> `2026-08-27T12:34:32+08:00`。唯一入口是 `core/timeutil.py`，
> 請勿在其他模組直接呼叫 `datetime.now()`（`test_timezone.py` 會掃描阻擋）。

---

## 快速開始

### 1. 環境變數

複製範本並填入金鑰：

```bash
cp .env.example .env
```

| 變數名稱 | 說明 | 來源 |
|---------|------|------|
| `MOENV_API_KEY` | 環境部開放資料 API Key | [data.moenv.gov.tw](https://data.moenv.gov.tw) |
| `CWA_API_KEY` | 中央氣象署開放資料 API Key | [opendata.cwa.gov.tw](https://opendata.cwa.gov.tw) |
| `OPENROUTER_API_KEY` | OpenRouter LLM API Key | [openrouter.ai](https://openrouter.ai) |
| `MAPS_API_KEY` | Google Maps Geocoding API Key | Google Cloud Console |
| `JWT_SECRET` | 裝置憑證簽章密鑰 | 自行產生隨機字串（見下） |
| `ADMIN_TOKEN` | 管理端點密鑰 | 同上 |

產生隨機密鑰：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

> `.env` 已列入 `.gitignore`，請勿上傳。

### 2. 啟動

**Docker（推薦）**

```bash
docker compose up -d --build
docker compose logs -f backend_api    # 追蹤 log
```

修改程式碼或 `.env` 後重啟：

```bash
docker compose down
docker compose up -d --build
```

**本機開發**

```bash
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

啟動後開 **http://localhost:8000/docs** 即可互動測試。

### 3. 測試

```bash
python test_auth.py        # 41 項：雜湊、註冊、續期、audience 隔離、封鎖、fail closed
python test_timezone.py    # 25 項：時間基準、時間窗邊界、上游格式、naive datetime 掃描
```

兩支均為獨立腳本，不需 pytest。

---

## API 端點

### 認證方式

本 App 沒有帳號系統，改以**裝置匿名註冊**：首次啟動時以裝置識別碼換取一組 JWT，
之後所有請求帶 Bearer token。伺服器不儲存原始 `ANDROID_ID`，收到後先做
HMAC-SHA256 雜湊，資料庫與 token 內都只出現雜湊後的代稱。

| 標記 | 保護方式 | 適用 |
|:---:|----------|------|
| （空白） | 無 | 政府開放資料的查詢端點，本來就對外 |
| 🔑 | `Authorization: Bearer <access_token>` | 會寫入資料、花費 LLM 額度或動到特定裝置設定 |
| 🛡 | `X-Admin-Token: <ADMIN_TOKEN>` | 對全體裝置推播、或大量觸發 LLM |

```bash
# 1. 註冊取得 token（access 1 小時、refresh 30 天）
curl -X POST https://<host>/api/auth/device \
     -H "Content-Type: application/json" \
     -d '{"device_id": "<ANDROID_ID>"}'
# → {"status":"success","access_token":"...","refresh_token":"...","expires_in":3600}

# 2. 呼叫受保護端點
curl -X POST https://<host>/api/report \
     -H "Authorization: Bearer <access_token>" \
     -H "Content-Type: application/json" \
     -d '{"location":"台南市東區","category":"異味","description":"有燒塑膠味"}'
```

Android 端由 `AuthInterceptor` 自動附加憑證、`TokenAuthenticator` 在 401 時自動續期，
畫面層不需要處理 token。

### 基本

| 方法 | 端點 | 認證 | 說明 |
|------|------|:---:|------|
| GET | `/` | | 服務健康檢查 |
| GET | `/docs` | | Swagger UI |

### 認證

| 方法 | 端點 | 認證 | 說明 |
|------|------|:---:|------|
| POST | `/api/auth/device` | | 裝置註冊，取得 access + refresh token |
| POST | `/api/auth/refresh` | | 以 refresh token 換發新的 access token |

### 空氣品質

| 方法 | 端點 | 認證 | 說明 |
|------|------|:---:|------|
| GET | `/api/air_quality` | | 全台 AQI 資料（`?county=台南市` 指定縣市） |
| GET | `/api/air_quality/estimate` | | **IDW 空間插值**估計任意座標的 AQI/PM2.5。參數：`lat`、`lng`、`k`（預設 4）、`power`（預設 2.0） |

> IDW 取最近 k 個測站以距離 `-p` 次方加權。全台 84 測站留一交叉驗證（LOOCV）顯示，
> 較「最近測站法」降低 AQI 誤差 **19.2%**（MAE 7.11 → 5.74）、PM2.5 誤差 **21.5%**。
> 重現：`python analysis/idw_validation.py`

### 天氣與預報

| 方法 | 端點 | 認證 | 說明 |
|------|------|:---:|------|
| GET | `/api/weather` | | 即時天氣（`?county=` 或 `?lat=&lng=`） |
| GET | `/api/forecast` | | 今日空品預報摘要（`?county=` 指定縣市） |
| GET | `/api/forecast/raw` | | AQF_P_01 今日完整原始資料（`?county=`） |

### 新聞與事件

| 方法 | 端點 | 認證 | 說明 |
|------|------|:---:|------|
| GET | `/api/news` | | 爬蟲新聞，24 小時內（`?region=` 指定地區） |
| GET | `/api/fire_alerts` | | 民生示警平台重大火災警示（`?region=`） |

### 民眾回報

| 方法 | 端點 | 認證 | 說明 |
|------|------|:---:|------|
| GET | `/api/user_reports` | | 24 小時內回報（含 `is_confirmed` 可信度欄位，`?region=`） |
| GET | `/api/user_reports/history` | 🔑 | 所有歷史回報 |
| POST | `/api/report` | 🔑 | 提交民眾回報 |

`POST /api/report` 請求格式：

```json
{
  "location": "台南市中西區",
  "category": "fire",
  "description": "附近有濃煙",
  "latitude": 23.0,
  "longitude": 120.2
}
```

**驗證機制**：每筆回報經 LLM 語意審核（`analyze_citizen_report`）判定是否為可信
污染事件（`is_confirmed`）並分類事件類型／嚴重度；每裝置 3 分鐘限 1 筆、6 小時內
相同類型＋內容自動去重；熱點分析需 `min_reports` 筆以上共識才成立警示。
App 端依 `is_confirmed` 顯示【已證實】／【未證實】標籤。

### RAG AI 健康顧問

| 方法 | 端點 | 認證 | 說明 |
|------|------|:---:|------|
| POST | `/api/rag_advice` | 🔑 | 取得個人化空氣品質建議 |
| POST | `/api/rag_advice/experiment` | 🛡 | 消融實驗用（比較各情境欄位的貢獻） |

`POST /api/rag_advice` 請求格式：

```json
{
  "county": "台南市",
  "latitude": 23.0,
  "longitude": 120.2,
  "aqi": 85,
  "pm25": 35.2,
  "user_profile": {
    "age_group": "adult",
    "is_pregnant": false,
    "has_asthma": false,
    "has_cardiovascular": false,
    "has_allergy": false
  }
}
```

建議整合的情境：當前 AQI/PM2.5、即時天氣、降雨機率、附近污染事件
（新聞 + 民眾回報 + 火災警示）、空品預報趨勢、下風處熱點判斷、當前時間
（避免深夜建議外出）。

### GIS 熱點分析

| 方法 | 端點 | 認證 | 說明 |
|------|------|:---:|------|
| GET | `/api/hotspots` | | KDE 污染熱點分析。參數：`min_reports`（預設 2）、`radius_km`（預設 1.5）、`top_n`（預設 10） |

### FCM 推播

| 方法 | 端點 | 認證 | 說明 |
|------|------|:---:|------|
| POST | `/api/fcm/register` | 🔑 | 裝置上傳 FCM Token |
| PUT | `/api/fcm/daily-notification` | 🔑 | 設定每日摘要推播時間 |
| POST | `/api/fcm/daily-notification/test` | 🔑 | 測試每日摘要推播 |
| POST | `/api/fcm/push` | 🛡 | 手動推播（指定縣市或全部） |
| POST | `/api/fcm/test` | 🛡 | 推播測試通知給所有已註冊裝置 |
| POST | `/api/fcm/test_auto` | 🛡 | 手動觸發自動推播邏輯 |

`POST /api/fcm/push` 請求格式：

```json
{
  "county": "台南市",
  "title": "空品警示",
  "body": "今日 AQI 超過 150，請注意防護"
}
```

### 管理

整個 router 以 `dependencies=[Depends(verify_admin)]` 保護，新增端點不必逐一掛驗證。
封鎖立即生效：被封鎖的裝置呼叫受保護端點會拿到 403，也無法再續期。

| 方法 | 端點 | 認證 | 說明 |
|------|------|:---:|------|
| GET | `/api/admin/devices` | 🛡 | 列出已註冊裝置，依近期回報數排序（`?hours=24`） |
| POST | `/api/admin/devices/{device_id}/revoke` | 🛡 | 封鎖裝置 |
| POST | `/api/admin/devices/{device_id}/restore` | 🛡 | 解除封鎖 |

---

## 排程任務

APScheduler `BackgroundScheduler(timezone=TW)`，與 API 共用同一行程，
於 `lifespan` 啟動時註冊。

| 任務 | Job ID | 頻率 | 說明 |
|------|--------|------|------|
| 新聞爬蟲 | `news_scraper` | 每 1 小時 | 爬取 Google News / Yahoo / 公視，LLM 語意過濾，清除過期資料 |
| 空品預報推播 | `forecast_push` | 每 30 分鐘 | 明日 AQI ≥ 101 的縣市自動推播，當日同縣市不重複推 |
| 火災警示推播 | `fire_alert_push` | 每 10 分鐘 | 有新的重大火災警示就推播給受影響縣市 |
| AQI 超標推播 | `aqi_alert_push` | 每 30 分鐘 | AQI ≥ 151 推全體；101–150 推敏感族群 |
| 每日摘要推播 | `daily_summary_push` | 每分鐘檢查 | 到了使用者設定的時間就推當地 AQI 摘要（每裝置每天最多一次） |

> 排程依賴常駐行程，Cloud Run 以 `min-instances=1` 保持實例不休眠。
> 推播去重狀態存於 SQLite（`db/push_state.py`），重新部署不會重複推播。

---

## 資料來源

| 資料 | 來源 | 格式 | 更新 |
|------|------|------|------|
| AQI 即時觀測 | 環境部 `aqx_p_432` | JSON | 每小時 |
| 空品預報 | 環境部 `AQF_P_01` | JSON | 每 30 分鐘 |
| 即時天氣 | 中央氣象署 `O-A0003-001` | JSON | 每小時 |
| 天氣預報 | 中央氣象署 `F-C0032-001`（今明 36 小時） | JSON | 每 6 小時 |
| 重大火災警示 | NCDR 民生示警平台 | CAP / XML | 事件驅動 |
| 新聞 | Google News / Yahoo 新聞 / 公共電視 | RSS | 爬蟲每 1 小時 |
| 地址 → 座標 | Google Maps Geocoding | JSON | 即時 |
| LLM / Embedding | OpenRouter（GPT-4o-mini、text-embedding-3-small） | JSON | 即時 |

台灣官方資料源給的時間**一律是台灣時間**，不論有沒有標 `+08:00`，解析時不再加減時區；
唯一例外是 Google News RSS 的 `pubDate` 標的是 GMT，需要換算。

---

## 專案結構

```
AirQuality_backend/
├── main.py                     FastAPI 進入點：lifespan、排程註冊、主要端點
├── api/
│   ├── auth.py                 /api/auth/*   裝置註冊與 token 續期
│   └── admin.py                /api/admin/*  裝置檢視與封鎖（整個 router 需管理員）
├── core/
│   ├── auth.py                 JWT 簽發／驗證、device_id 雜湊、管理員驗證
│   ├── config.py               環境變數集中讀取、token 有效期與 audience
│   └── timeutil.py             全系統唯一的時間入口（台灣時間 UTC+8）
├── crawler/
│   ├── news_scraper.py         RSS 爬蟲 + LLM 語意過濾（news.db）
│   ├── fire_alert_scraper.py   NCDR 民生示警 CAP 解析
│   ├── forecast_fetcher.py     環境部 AQF_P_01 空品預報
│   ├── weather_fetcher.py      中央氣象署即時天氣與預報
│   ├── CityCountyData.json     縣市鄉鎮對照
│   ├── news.db                 爬蟲新聞（SQLite，不入版控）
│   └── user_reports.db         回報／裝置／FCM token／推播狀態（SQLite，不入版控）
├── db/
│   ├── reports_db.py           民眾回報讀寫、頻率限制、去重
│   ├── devices_db.py           裝置註冊與封鎖狀態
│   ├── push_state.py           推播去重狀態（避免重複推播）
│   └── firestore_reports.py    Firestore 遷移雛形（尚未接上）
├── rag/
│   ├── rag_engine.py           檢索 + 生成主流程
│   ├── embedder.py             ChromaDB 知識庫建立與檢索（embedded）
│   ├── health_rules.py         健康規則知識庫來源
│   └── llm_structurer.py       民眾回報的 LLM 語意審核與結構化
├── gis/
│   ├── hotspot_analyzer.py     KDE 核密度估計、下風處判斷
│   └── interpolation.py        IDW 反距離加權空間插值
├── fcm/
│   ├── fcm_sender.py           Firebase 推播發送
│   └── token_store.py          FCM Token 管理、每日通知時間設定
├── analysis/
│   ├── idw_validation.py       IDW vs 最近測站法 LOOCV 驗證
│   └── rag_ablation.py         RAG 情境欄位消融實驗
├── scripts/
│   ├── check_rag_pipeline.py   RAG 流程健檢
│   └── manual_fcm_push.py      手動推播工具
├── test_auth.py                認證測試（獨立腳本，41 項）
├── test_timezone.py            時間基準測試（獨立腳本，25 項）
├── Dockerfile
├── docker-compose.yml
├── start.sh                    Cloud Run 進入點（讀取注入的 PORT）
├── requirements.txt
└── .env.example
```

---

## 部署（Google Cloud Run）

後端以 Docker 容器部署於 Firebase 專案（`airquality-4d1b6`）的 Cloud Run，
`min-instances=1` 維持常駐，讓 APScheduler 排程不中斷。

```bash
gcloud run deploy airquality-api --source . --region asia-east1
```

網址不變，環境變數已設定於 Cloud Run 服務上，重新部署自動沿用。

注意事項：

- `serviceAccountKey.json` 與 `.env` **不會**上傳（依 `.gitignore` 排除）；
  雲端的 FCM 使用專案預設服務帳戶（`fcm_sender.py` 自動判斷）
- **Cloud Run 磁碟為暫時性：重新部署會清空民眾回報與裝置資料**，展示前避免部署
- `start.sh` 讀取 Cloud Run 注入的 `PORT`，本機仍預設 8000
