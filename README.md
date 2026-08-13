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

| 方法 | 端點 | 說明 |
|------|------|------|
| POST | `/api/fcm/register` | 裝置上傳 FCM Token |
| POST | `/api/fcm/push` | 手動推播（指定縣市或全部） |
| GET | `/api/fcm/test` | 推播測試通知給所有已註冊裝置 |

POST `/api/fcm/push` 請求格式：
```json
{
  "county": "台南市",
  "title": "空品警示",
  "body": "今日 AQI 超過 150，請注意防護"
}
```

---

## 排程任務（APScheduler）

| 任務 | 頻率 | 說明 |
|------|------|------|
| 新聞爬蟲 | 每 1 小時 | 抓取 Google News / Yahoo / 公視，LLM 語意過濾，清除過期資料 |
| 空品預報推播 | 每 2 小時 | 明日 AQI ≥ 101 的縣市自動推播，當日同縣市不重複推 |
| 每日空品摘要 | 依用戶設定時間 | 推播使用者訂閱的每日 AQI 摘要通知 |

> 排程依賴常駐程序，Cloud Run 以 `min-instances=1` 保持實例不休眠。

---

## 測試

```bash
pip install -r requirements-dev.txt
pytest -q
```

單元測試不需要 chromadb、不打任何外部 API、不呼叫 LLM、不發送推播，
整套約 2 秒跑完（`tests/conftest.py` 會攔截所有 `requests` 對外呼叫，
並在 chromadb 缺席時注入替身）。

### 時區

正式環境（Cloud Run）的 TZ 是 **UTC**，開發機通常是 **Asia/Taipei**，
許多時間相關的缺陷只在前者浮現。CI 會在兩個時區各跑一次完整測試：

```bash
TZ=UTC pytest -q
TZ=Asia/Taipei pytest -q
```

### 已知缺陷的表達方式

已確認但尚未修復的缺陷，一律寫成 `xfail(strict=True)` 並標記 `known_bug`：

```bash
pytest -m known_bug -q          # 列出所有已知缺陷
pytest -m known_bug --runxfail  # 看它們實際失敗在哪一行
```

`strict=True` 的意義是：**缺陷修好後測試會 XPASS，而 XPASS 會讓整個測試套件失敗**，
強迫回來移除標記。因此這批標記就是待修清單本身，不會隨時間腐爛。

目前共 27 個，涵蓋：

| 範圍 | 缺陷 |
|------|------|
| 時區 | 寫入用 UTC+8、查詢用行程時間，所有時間窗在 Cloud Run 上放大 8 小時 |
| 推播 | 空品預報推播用空品區名稱查縣市 token，從未送出任何一則 |
| 推播 | 「臺／台」未正規化，四個縣市的擴散推播查無裝置 |
| 推播 | token 更新時縣市與健康狀況被空字串覆寫；失效 token 無法移除 |
| 推播 | token 檔為無鎖的整檔讀寫，併發註冊會 lost update |
| 地區判讀 | 「新市區」被縮成「新」，任何含「新」的標題都誤判為台南市 |
| 地區判讀 | 縣治與縣同名時蓋掉真正的鄉鎮（「南投縣南投」） |
| 地區判讀 | 「竹北市」因子字串比對命中「北市」而誤判為台北市 |
| 熱點分析 | 回報點共線時 KDE 退化，例外被吞掉後靜默回傳空清單 |
| 健康規則 | AQI 151–200 的等級名稱與官方用語不一致；AQI > 500 無對應規則 |
| 效能 | `/api/rag_advice` 對同一縣市重複查詢 MOENV；熱點分析未帶入風況 |

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
