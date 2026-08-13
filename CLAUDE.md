# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

台灣空氣品質 App 的後端（Python + FastAPI），部署於 Google Cloud Run。
對應的 Android App 在姊妹 repo `AirQualityApp`——兩者的資料契約沒有共用定義檔，
改動 API 欄位時必須同步檢查 App 端的 `AirQualityApi.kt` 與各 `*Response` data class。

> **要修缺陷或做重構，先讀 [`docs/ROADMAP.md`](docs/ROADMAP.md)。**
> 那份文件列出全部 29 個已確認缺陷的根因（含 file:line）、修法、
> 對應的驗證測試與陷阱，設計成不需要前後文就能接手。
> 本文件負責「這個 repo 怎麼運作」，ROADMAP 負責「接下來要做什麼、怎麼做」。

---

## 常用指令

```bash
pip install -r requirements-dev.txt   # 測試相依（不含 chromadb，見下方說明）
pytest -q                             # 全套單元測試，約 2 秒
```

```bash
pytest tests/test_rag_engine.py::test_hybrid_puts_the_matching_aqi_level_first   # 單一測試
pytest -m known_bug -q                # 只列已知缺陷
pytest -m known_bug --runxfail        # 看已知缺陷實際失敗在哪一行
TZ=UTC pytest -q                      # 以正式環境時區執行（CI 會跑這個）
pytest -m network tests/integration/  # 需真實金鑰、會產生費用，預設不跑
pytest --cov --cov-report=term-missing   # 覆蓋率（會讓測試從 1.4s 變 4.5s）
```

`scripts/` 下是會影響真實使用者或產生費用的手動工具，**不是測試**：
`send_test_push.py`（對真實裝置發推播）、`rag_smoke.py`（重建知識庫 + 呼叫 LLM）。
兩者的檔名與函式名都不得以 `test_` 開頭，由 `tests/test_repo_hygiene.py` 強制——
它們原本叫 `test_fcm.py` / `test_rag.py` 放在根目錄，會被 pytest 收集後真的執行。

```bash
uvicorn main:app --reload --port 8000            # 本機開發
docker compose up -d --build                     # 含 ChromaDB 的完整環境
gcloud run deploy airquality-api --source . --region asia-east1   # 部署，網址不變
```

沒有 linter／formatter 設定，也沒有型別檢查。

---

## 架構：一次請求會扇出到哪裡

`main.py`（1,300+ 行、20 個端點）同時是路由層、排程層、外部 API client 與業務邏輯層。
真正需要跨檔案閱讀才能理解的是三條管線：

**1. RAG 建議管線** — `/api/rag_advice` 是全系統扇出最廣的端點，單一請求同步串起九個來源：

```
MOENV AQI ─┬─→ aqi/pm2.5
           └─→ 風速/風向/溫度        （目前對同一縣市呼叫兩次）
CWA 即時天氣 + 天氣預報
MOENV 空品預報（AQF_P_01）
NCDR 火災 feed → 每則警示再抓一次 CAP XML（1+N，無快取）
scraped_news.json + user_reports.db → 事件脈絡
gis.analyze_hotspots（KDE）→ gis.check_downwind（下風扇形）
rag.embedder（embedding）→ rag.rag_engine（LLM 生成）
```

全部是 blocking `requests` 且端點是 sync `def`，因此佔用 FastAPI 的 threadpool。
系統**沒有任何快取層**（`_aqi_records_cache` 只在 exception 時當 fallback）。

**2. 新聞管線** — `crawler/news_scraper.py` 抓 RSS → 關鍵字／台灣地名／即時性規則過濾
（方法一）→ `rag/llm_structurer.py` 逐篇 LLM 結構化並依 `is_confirmed × is_realtime` 過濾
（方法二）→ 寫入 `scraped_news.json`。注意 `news.db` 有寫入與清理但**從未被讀取**，
`/api/news` 讀的是 JSON 檔。

**3. 推播管線** — 五個 APScheduler job 跑在 web process 內（`lifespan`），
各自查 `fcm/token_store.py` 取得目標裝置後呼叫 `fcm/fcm_sender.py`。
去重狀態（`_forecast_pushed` / `_fire_pushed` / `_aqi_alerted`）是**行程內的模組層變數**，
重啟或 scale out 就失效。

---

## 狀態與持久化：最重要的架構限制

四份可變狀態全都放在 Cloud Run 的**暫時性容器磁碟**上，重新部署即歸零：

| 路徑 | 內容 | 存取方式 |
|---|---|---|
| `crawler/user_reports.db` | 民眾回報 | SQLite（`db/reports_db.py`） |
| `crawler/fcm_tokens.json` | 裝置 token + 每日通知設定 | **整檔讀改寫、無鎖** |
| `crawler/scraped_news.json` | 新聞快取 | 整檔讀寫 |
| `rag/chroma_db/` | 向量庫（僅 8 條規則） | ChromaDB |

推論出來的幾個約束：

- **排程在 web process 內** → 部署必須維持 `min-instances=1`；一旦 scale out，
  N 個實例會各跑一份排程並持有各自的 token 檔。
- **`token_store` 的 read-modify-write 無鎖** → sync endpoint 跑在 threadpool 上，
  併發註冊會 lost update；`json.dump` 寫到一半時讀取會拿到截斷的檔案。
- **`db/firestore_reports.py` 已寫好但沒接上**（`main.py` import 的是 `reports_db`）。
  要解持久化與多實例問題時，這是現成的起點。

---

## 跨模組慣例與陷阱

**縣市字串有兩種寫法**：MOENV 與 CWA 回傳「臺」，`gis.COUNTY_CENTROIDS`
與 `crawler.DISTRICTS` 用「台」。`main.normalize_name()` 負責轉換，
但**沒有在所有邊界上套用**——尤其 `fcm/token_store.py` 的 county 是 App 直接
上傳的原始字串，未經正規化。任何以縣市為 key 的查找都要先確認兩側基準一致。

**時間有三種基準並存**：`main.submit_report` 寫入「UTC+8 牆上時間去 tzinfo」，
`db/reports_db.py` 的 cutoff 用 `datetime.now()`（行程時區），
排程與 `token_store` 用 aware UTC+8。開發機是 Asia/Taipei 所以前兩者剛好一致，
Cloud Run 是 UTC 就會差 8 小時。**新增任何時間比較前，先確認兩側的基準**。

**所有錯誤都回 HTTP 200**：20 個端點沒有一個 `raise HTTPException`，
失敗一律回 `{"status": "error", "message": str(e)}`。改動端點時若要導入正確狀態碼，
必須同步改 App 端——它目前只看 body 的 `status` 欄位。

**日誌只有 `print()`**，沒有 severity 或結構化欄位。

**18 處函式內 import**（如 `from crawler.forecast_fetcher import ...` 散在各 endpoint 內）。
這會讓啟動期錯誤延後到第一次請求才爆，也讓 monkeypatch 的目標分成兩種：

- `main` 頂層 import 的（`analyze_hotspots`、`check_downwind`、`generate_advice`）
  → patch `main.<name>`
- 函式內才 import 的（`fetch_weather_for_county`、`fetch_latest_forecast`、
  `fetch_fire_alerts`、`get_tokens_near`）→ 必須 patch **來源模組**的屬性，
  因為 `main` 在呼叫當下才綁定

`tests/test_rag_advice_endpoint.py` 的 `wiring` fixture 是這兩種寫法的現成範例。

---

## 測試慣例

詳見 `TESTING.md`。三件在改動程式碼前必須知道的事：

1. **已知缺陷寫成 `xfail(strict=True)` + `known_bug` marker**，不是註解也不是 issue。
   修好缺陷後該測試會 XPASS，而 XPASS **會讓整個測試套件失敗** ——
   這是刻意的：請一併移除 `known_bug` 與 `xfail` 標記，並把測試改寫成正面斷言。
   目前有 27 個，等同待修清單。

2. **測試不得打真實網路**。`tests/conftest.py` 有 autouse fixture 攔截所有 `requests`
   對外呼叫並直接 assert 失敗。確實需要網路的測試請掛 `@pytest.mark.network`。

3. **覆蓋率的 `--cov-fail-under` 是棘輪，只准往上調**。目前 42%（實際 43%）。
   生產覆蓋率偏低幾乎全來自 `main.py`（800 敘述、19%）。
   **不要為了衝數字去補 `main.py` 的測試**——它即將被拆層，
   拆完之後邏輯變成可注入的純函式，覆蓋率會自然上升。

4. **測試不依賴 chromadb**。`requirements-dev.txt` 刻意不引用 `requirements.txt`——
   chromadb 會帶進 onnxruntime／grpcio 等重量級傳遞相依，安裝要數分鐘，
   但知識庫只有 8 條規則。`conftest.py` 在 chromadb 缺席時注入替身。
   驗證真實向量檢索請另建 `@pytest.mark.network` 的整合測試。

---

## 文件漂移（動到相關程式碼時順手修）

- README 的排程表寫「空品預報推播 每 2 小時」，實際是 30 分鐘（`main.py` 的 `minutes=30`）；
  火災警示（10 分）與 AQI 超標（30 分）兩個 job 完全沒被列出。
- `README_utf8.md` 是 README 的舊版殘留（54 行 vs 317 行，且帶 BOM），內容已過期。
- `requirements.txt` 未鎖版本，且 `firebase-admin` 出現兩次。
