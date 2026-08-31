# AirQuality App — Backend

台灣空氣品質即時監測 App 的後端服務，使用 **Python + FastAPI** 開發，整合多個開放資料平台與 AI 建議系統。

---

## 系統架構

```
FastAPI (main.py)
├── 空氣品質 API        ← 環境部 MOENV AQI 資料
├── 新聞爬蟲            ← Google News / Yahoo / 公視 RSS（每 6 小時排程）
├── 民眾回報系統        ← SQLite 資料庫 + LLM 語意分析
├── 民生示警火災        ← NCDR CAP XML 解析
├── 空品預報            ← 環境部 AQF_P_01（每 30 分鐘更新）
├── 天氣資訊            ← 中央氣象署 O-A0003-001 / F-C0032-001
├── RAG AI 健康顧問     ← ChromaDB + GPT-4o-mini（OpenRouter）
├── GIS 熱點分析        ← KDE 核密度估計 + 風向下風處判斷
├── IDW 空間插值        ← 個人定位點空品估計（gis/interpolation.py）
└── FCM 推播通知        ← Firebase Cloud Messaging
```

**正式環境**：已部署於 Google Cloud Run（`asia-east1`，always-on）
**服務網址**：https://airquality-api-968727437042.asia-east1.run.app （`/docs` 為 Swagger UI）

---

## 環境變數設定

請複製 `.env.example` 並填入金鑰：

```bash
cp .env.example .env
```

| 變數名稱 | 說明 | 來源 |
|---------|------|------|
| `MOENV_API_KEY` | 環境部開放資料 API Key | [data.moenv.gov.tw](https://data.moenv.gov.tw) |
| `OPENROUTER_API_KEY` | OpenRouter LLM API Key | [openrouter.ai](https://openrouter.ai) |
| `MAPS_API_KEY` | Google Maps Geocoding API Key | Google Cloud Console |
| `CWA_API_KEY` | 中央氣象署開放資料 API Key | [opendata.cwa.gov.tw](https://opendata.cwa.gov.tw) |
| `JWT_SECRET` | 裝置憑證簽章密鑰（見 [API 認證](#api-認證)） | 自行產生隨機字串 |
| `ADMIN_TOKEN` | 管理端點密鑰 | 自行產生隨機字串 |

> `.env` 已加入 `.gitignore`，請勿上傳至 GitHub。

---

## 啟動方式

### Docker（推薦）

```bash
docker compose up -d --build
```

重啟（修改程式碼或 `.env` 後）：

```bash
docker compose down
docker compose up -d --build
```

查看 Log：

```bash
docker compose logs -f backend_api
```

### 本機開發（不使用 Docker）

```bash
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

---

## 雲端部署（Google Cloud Run）

後端以 Docker 容器部署於 Firebase 專案（`airquality-4d1b6`）的 Cloud Run，
`min-instances=1` 維持常駐，APScheduler 排程不中斷。

程式修改後重新部署（一行指令，網址不變）：

```bash
gcloud run deploy airquality-api --source . --region asia-east1
```

注意事項：
- 環境變數（各 API 金鑰）已設定於 Cloud Run 服務上，重新部署自動沿用
- `serviceAccountKey.json` 與 `.env` **不會**上傳（依 .gitignore 排除）；
  雲端的 FCM 使用專案預設服務帳戶（`fcm_sender.py` 自動判斷）
- Cloud Run 磁碟為暫時性：**重新部署會清空民眾回報 SQLite 資料**，展示前避免部署
- `start.sh` 會讀取 Cloud Run 注入的 `PORT` 環境變數，本機仍預設 8000

---

## API 端點總覽

後端啟動後，可用 **Swagger UI** 互動測試所有端點：
**http://localhost:8000/docs**

### 基本

| 方法 | 端點 | 說明 |
|------|------|------|
| GET | `/` | 確認後端是否正常運行 |
| GET | `/docs` | Swagger UI |

---

### 空氣品質

| 方法 | 端點 | 說明 |
|------|------|------|
| GET | `/api/air_quality` | 全台 AQI 資料 |
| GET | `/api/air_quality?county=台南市` | 指定縣市 AQI |
| GET | `/api/air_quality/estimate?lat=22.73&lng=120.28` | **IDW 空間插值**：估計任意座標的 AQI/PM2.5（k 個鄰近測站反距離加權，預設 k=4, p=2） |

> IDW 以全台 84 測站留一交叉驗證（LOOCV），較「最近測站法」降低
> AQI 估計誤差 **19.2%**（MAE 7.11→5.74）、PM2.5 誤差 **21.5%**。
> 驗證腳本：`python analysis/idw_validation.py`

---

### 新聞 / 事件

| 方法 | 端點 | 說明 |
|------|------|------|
| GET | `/api/news` | 全部爬蟲新聞（24 小時內） |
| GET | `/api/news?region=台南市` | 指定地區新聞 |
| GET | `/api/fire_alerts` | 民生示警平台重大火災警示（24 小時內） |
| GET | `/api/fire_alerts?region=台南市` | 指定地區火災警示 |

---

### 空品預報

| 方法 | 端點 | 說明 |
|------|------|------|
| GET | `/api/forecast` | 今日空品預報摘要（通知中心用） |
| GET | `/api/forecast?county=台南市` | 指定縣市預報摘要 |
| GET | `/api/forecast/raw` | AQF_P_01 今日完整原始資料 |
| GET | `/api/forecast/raw?county=台南市` | 指定縣市完整預報 |

---

### 民眾回報

| 方法 | 端點 | 說明 |
|------|------|------|
| GET | `/api/user_reports` | 24 小時內回報（含 `is_confirmed` 可信度欄位） |
| GET | `/api/user_reports?region=台南市` | 指定地區回報 |
| GET | `/api/user_reports/history` | 所有歷史回報 |
| POST | `/api/report` | 提交民眾回報 |

**回報驗證機制**：每筆回報經 LLM 語意審核（`analyze_citizen_report`）判定
是否為可信污染事件（`is_confirmed`）並分類事件類型/嚴重度；相同類型＋內容
自動去重；熱點分析需 `min_reports` 筆以上共識才成立警示。App 端依
`is_confirmed` 顯示【已證實】/【未證實】標籤。

POST `/api/report` 請求格式：
```json
{
  "location": "台南市中西區",
  "category": "fire",
  "description": "附近有濃煙",
  "latitude": 23.0,
  "longitude": 120.2
}
```

---

### RAG AI 健康顧問

| 方法 | 端點 | 說明 |
|------|------|------|
| POST | `/api/rag_advice` | 取得個人化空氣品質建議 |

POST `/api/rag_advice` 請求格式：
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

建議整合資訊：
- 當前 AQI / PM2.5
- 即時天氣（溫度、降雨、天氣描述）
- 天氣預報（降雨機率）
- 附近污染事件（新聞 + 民眾回報 + 火災警示）
- 空品預報趨勢
- 下風處熱點判斷
- 當前時間（避免深夜建議外出）

---

### GIS 熱點分析

| 方法 | 端點 | 說明 |
|------|------|------|
| GET | `/api/hotspots` | 污染熱點分析（預設參數） |
| GET | `/api/hotspots?min_reports=2&radius_km=1.5&top_n=10` | 自訂參數 |

---

### FCM 推播通知

| 方法 | 端點 | 說明 | 保護 |
|------|------|------|------|
| POST | `/api/fcm/register` | 裝置上傳 FCM Token | 🔑 |
| POST | `/api/fcm/push` | 手動推播（指定縣市或全部） | 🛡 |
| POST | `/api/fcm/test` | 推播測試通知給所有已註冊裝置 | 🛡 |

POST `/api/fcm/push` 請求格式：
```json
{
  "county": "台南市",
  "title": "空品警示",
  "body": "今日 AQI 超過 150，請注意防護"
}
```

---

## API 認證

### 分層

| 層級 | 保護方式 | 端點 |
|------|----------|------|
| 公開 | 無 | 空品、天氣、預報、新聞、火災警示、熱點、24 小時內民眾回報 |
| 🔑 裝置憑證 | `Authorization: Bearer <access_token>` | `/api/report`、`/api/rag_advice`、`/api/user_reports/history`、`/api/fcm/register`、`/api/fcm/daily-notification`(+`/test`) |
| 🛡 管理員 | `X-Admin-Token: <ADMIN_TOKEN>` | `/api/fcm/push`、`/api/fcm/test`、`/api/fcm/test_auto`、`/api/rag_advice/experiment`、`/api/admin/*` |

公開的是環境部與氣象署的開放資料，本來就對外；需要憑證的是會寫入資料、
花費 LLM 額度或動到特定裝置設定的端點；管理層是會對全體裝置發送推播、
或大量觸發 LLM 的操作。

### 裝置匿名憑證

本 App 沒有帳號系統——空氣品質是公用資訊，強制註冊會流失使用者。因此改為
**裝置匿名註冊**：App 首次啟動時以裝置識別碼換取一組 JWT，之後所有請求帶
Bearer token。

| 方法 | 端點 | 說明 |
|------|------|------|
| POST | `/api/auth/device` | 裝置註冊，取得 access + refresh token |
| POST | `/api/auth/refresh` | 以 refresh token 換發新的 access token |

```bash
# 註冊
curl -X POST https://<host>/api/auth/device      -H "Content-Type: application/json"      -d '{"device_id": "<ANDROID_ID>"}'
# → {"status":"success","access_token":"...","refresh_token":"...","expires_in":3600}

# 呼叫受保護端點
curl -X POST https://<host>/api/report      -H "Authorization: Bearer <access_token>"      -H "Content-Type: application/json"      -d '{"location":"台南市東區","category":"異味","description":"有燒塑膠味"}'
```

- access token 1 小時、refresh token 30 天
- 兩者的 `aud` 不同，refresh token 無法當 access token 使用
- Android 端由 `AuthInterceptor` 自動附加憑證、`TokenAuthenticator` 在 401 時
  自動續期，畫面層不需要處理 token

**為什麼 device_id 不由客戶端提供給業務端點**：回報頻率限制原本讀取 request
body 裡的 `device_id`，呼叫端改個字串就能繞過。改為從 token 取出後，識別碼由
伺服器簽發，客戶端無法逐次請求偽造。

**隱私**：伺服器不儲存原始 ANDROID_ID，收到後先以 `JWT_SECRET` 為 pepper 取
HMAC-SHA256 並截斷，資料庫與 token 內都只出現這個與硬體無關的代稱。


### 管理端點：裝置檢視與封鎖

| 方法 | 端點 | 說明 |
|------|------|------|
| GET | `/api/admin/devices?hours=24` | 列出已註冊裝置，依近期回報數由多到少排序 |
| POST | `/api/admin/devices/{device_id}/revoke` | 封鎖裝置 |
| POST | `/api/admin/devices/{device_id}/restore` | 解除封鎖 |

```bash
export ADMIN_TOKEN="..." API="https://<host>"

# 找出灌水來源：recent_reports 最高的排最前面
curl "$API/api/admin/devices" -H "X-Admin-Token: $ADMIN_TOKEN"

# 封鎖
curl -X POST "$API/api/admin/devices/<device_id>/revoke" -H "X-Admin-Token: $ADMIN_TOKEN"
```

封鎖**立即生效**：受保護端點每次都會檢查封鎖狀態，被封鎖的裝置拿到 403，
也無法再續期。封鎖狀態查詢失敗時刻意 fail open（放行）——token 簽章本身有效，
不應讓一個罕用的管理功能變成全服務的故障點。

封鎖擋的是「這個身分」而非那台實體裝置：對方重新註冊會取得新的代稱。這是匿名
憑證的固有限制，真正的裝置級封鎖同樣需要 Firebase App Check。

`device_id` 是雜湊後的代稱，無法反推回實際裝置，但足以對應同一台裝置的行為。

### 環境變數

| 變數 | 用途 | 未設定時 |
|------|------|----------|
| `JWT_SECRET` | JWT 簽章密鑰 | 認證端點一律回 500（fail closed，不使用預設值） |
| `ADMIN_TOKEN` | 管理端點共用密鑰 | 管理端點一律拒絕 |

產生方式：`python -c "import secrets; print(secrets.token_urlsafe(48))"`

### 已知限制

攻擊者仍可重複呼叫 `/api/auth/device` 取得大量新身分，藉此繞過以裝置為單位的
頻率限制。要擋住這一層需要裝置證明（Firebase App Check / Play Integrity），
列為後續工作。

### 測試

```bash
python test_auth.py
```

不需 pytest，涵蓋雜湊、註冊、續期、audience 隔離、偽造／過期 token、
裝置封鎖與解除、管理端點與 fail closed 共 41 項檢查。

---

## 時間基準

全系統統一使用**台灣時間（UTC+8，aware）**，由 [`core/timeutil.py`](core/timeutil.py) 提供唯一入口。

| 函式 | 用途 |
|------|------|
| `now_tw()` | 現在的台灣時間（aware datetime） |
| `now_iso()` | 寫入資料庫的時間戳，帶 `+08:00` 位移 |
| `cutoff_iso(hours=, minutes=)` | 時間窗查詢的 cutoff |
| `today_str()` | 今天日期（判斷「今天是否已推播」用） |
| `parse_iso()` / `to_tw()` | 解析／正規化，naive 輸入一律視為台灣時間 |
| `log_ts()` | log 用的時間字串 |

**不要在其他模組直接呼叫 `datetime.now()` 或 `datetime.utcnow()`。**
`test_timezone.py` 的測試 6 會掃描原始碼阻擋這件事。

### 為什麼要統一

修正前有三種基準並存：`submit_report` 寫入用 UTC+8 牆上時間、
`db/reports_db.py` 的 cutoff 用 `datetime.now()`（行程時區）、排程用 aware UTC+8。

開發機是 Asia/Taipei，前兩者剛好一致所以本機測不出問題；
但 **Cloud Run 的行程時區是 UTC**，資料庫時間戳永遠比 cutoff 早 8 小時，
所有時間窗被放大：

| 設計值 | 修正前線上實際 |
|--------|----------------|
| 回報頻率限制 3 分鐘 | 8 小時 3 分 |
| 相同內容去重 6 小時 | 14 小時 |
| 近期回報查詢 24 小時 | 32 小時 |

### 時間戳格式

```
2026-08-27T12:34:32.227786+08:00
```

修正前不帶位移。加上位移後時間戳可自我描述，且 App 的 `MapScreen`
以 `SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssZ")` 解析——格式中的 `Z`
要求必須有位移，舊格式它其實一直解析失敗。

既有的無位移資料仍可正確比較：ISO 格式年月日時分秒在前、位移在後，
字串比較的結果不受影響。

### 上游資料來源的時間

台灣官方資料源給的就是台灣時間，**不論有沒有寫 `+08:00`**。無標記者由
`to_tw()` / `parse_iso()` 視為台灣時間，不會被當成 UTC 再加 8 小時。

| 來源 | 欄位 | 實際格式 | 處理 |
|------|------|----------|------|
| 環境部 `AQF_P_01` | `publishtime` | `2026-08-27 10:30` | 無標記 → 台灣時間 |
| 環境部 `aqx_p_432` | `publishtime` | `2026/08/27 16:00:00` | 僅顯示，不解析 |
| 中央氣象署 `F-C0032-001` | `startTime` | `2026-08-27 12:00:00` | 無標記 → 台灣時間 |
| NCDR 火災警示 | `updated` | `2026-08-20T08:56:48+08:00` | 有標記 → 直接採用 |
| **Google News RSS** | `pubDate` | `Thu, 27 Aug 2026 07:39:16 GMT` | **真的是 GMT → 換算** |

最後一列是唯一的例外：Google News 不是台灣資料源，它標 GMT 就是 GMT
（07:39 GMT = 15:39 台灣），必須換算而不能當成台灣時間。
`test_timezone.py` 的測試 6 用實際觀測到的字串把這五種格式都釘住了。

### 測試

```bash
python test_timezone.py
```

23 項檢查，涵蓋基準位移、三個時間窗的邊界、新舊格式相容、
以及阻擋 naive `datetime.now()` 重新出現的原始碼掃描。

---

## 排程任務（APScheduler）

| 任務 | 頻率 | 說明 |
|------|------|------|
| 新聞爬蟲 | 每 1 小時 | 抓取 Google News / Yahoo / 公視，LLM 語意過濾，清除過期資料 |
| 空品預報推播 | 每 2 小時 | 明日 AQI ≥ 101 的縣市自動推播，當日同縣市不重複推 |
| 每日空品摘要 | 依用戶設定時間 | 推播使用者訂閱的每日 AQI 摘要通知 |

> 排程依賴常駐程序，Cloud Run 以 `min-instances=1` 保持實例不休眠。

---

## 分析工具（analysis/）

| 腳本 | 用途 |
|------|------|
| `idw_validation.py` | IDW vs 最近測站法留一交叉驗證（MAE/RMSE，比較 k、power 參數組合） |

---

## 主要資料來源

| 資料 | 來源 |
|------|------|
| AQI 即時資料 | 環境部 `aqx_p_432` |
| 空品預報 | 環境部 `AQF_P_01`（每 30 分鐘） |
| 即時天氣 | 中央氣象署 `O-A0003-001` |
| 天氣預報 | 中央氣象署 `F-C0032-001`（今明 36 小時） |
| 重大火災警示 | NCDR 民生示警平台 CAP 格式 |
| 新聞 | Google News / Yahoo 新聞 / 公共電視 RSS |
