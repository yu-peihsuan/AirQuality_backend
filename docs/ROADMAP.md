# 缺陷修復計畫

這份文件的目的是讓**沒有前後文的新 session** 能直接接手開發。
只要讀完 [`CLAUDE.md`](../CLAUDE.md)（架構與陷阱）與本文件（做什麼、怎麼驗），
就足以獨立完成任何一個項目，不需要回溯先前的對話。

前置狀態：測試網已建立（384 測試 / 29 個 strict xfail / CI 雙時區 / 覆蓋率棘輪）。
細節見 [`TESTING.md`](../TESTING.md)。

---

## 開始之前：這個 repo 的工作規則

### 1. 缺陷清單就是測試本身

29 個已確認缺陷寫成 `xfail(strict=True)` + `known_bug` marker。

```bash
pytest -m known_bug -q          # 列出全部
pytest -m known_bug --runxfail  # 看每個實際失敗在哪一行、為什麼
```

**`strict=True` 表示修好後測試會 XPASS，而 XPASS 會讓整個測試套件失敗。**
這是刻意的：它強迫你回到測試檔移除標記。

### 2. 每個項目的標準流程

```bash
git checkout main && git pull && git checkout -b fix/<主題>

pytest -m known_bug --runxfail -k <相關關鍵字>   # 1. 先看缺陷實際壞在哪
                                                  # 2. 改生產程式碼
                                                  # 3. 移除該測試的 known_bug 與 xfail 兩個裝飾器
TZ=UTC pytest -q && TZ=Asia/Taipei pytest -q      # 4. 兩個時區都要綠
pytest --cov                                      # 5. 覆蓋率有升就調高門檻
```

**移除標記時**：把 `@pytest.mark.known_bug` 與 `@pytest.mark.xfail(...)` 兩行一起刪掉，
測試本身通常不用改（它本來就是以「正確行為」撰寫的）。
若 `reason` 描述的根因與你實際的修法不同，順手更新測試檔的註解。

### 3. 覆蓋率棘輪

`.github/workflows/tests.yml` 的 `--cov-fail-under=42` **只准往上調**。
補完一批測試後跑 `pytest --cov` 看實際值，把門檻推到新的實際值（略減 1 避免抖動）。

### 4. 不要為了衝覆蓋率去補 `main.py` 的測試

`main.py` 佔全部未覆蓋敘述的一半，但它預定要被拆層（見項目 7）。
拆完之後邏輯會變成可注入的純函式，覆蓋率自然上升。現在對著它寫測試是白工。

### 5. 跨 repo 影響

Android App 在姊妹 repo [`AirQualityApp`](https://github.com/yu-peihsuan/AirQualityApp)。
兩者的資料契約**沒有共用定義檔**。動到 API 欄位或 HTTP 狀態碼時，
必須同步檢查 App 端的 `AirQualityApi.kt` 與各 `*Response` data class。
本文件會在需要動 App 的項目標註 **[需同步改 App]**。

---

## 項目總覽

| # | 項目 | 清掉的 xfail | 風險 | 需改 App |
|---|---|---:|---|---|
| 1 | [安全：dockerignore 與推播端點認證](#1-安全dockerignore-與推播端點認證) | 0 | 低 | 否 |
| 2 | [時區統一為 aware UTC](#2-時區統一為-aware-utc) | 5 | 中 | 否 |
| 3 | [推播查找修正](#3-推播查找修正) | 6 | 中 | 是 |
| 4 | [地區判讀重寫](#4-地區判讀重寫) | 6 | 中 | 否 |
| 5 | [KDE 共線防護與等級名稱統一](#5-kde-共線防護與等級名稱統一) | 4 | 低 | 否 |
| 6 | [相依鎖版本](#6-相依鎖版本) | 0 | 低 | 否 |
| 7 | [架構重構（大工程）](#7-架構重構大工程) | 2 | 高 | 是 |

建議依序進行。1、2、5、6 彼此獨立；3 建議在 2 之後（都會動到 `token_store`）；
7 是長期工程，建議 1–6 全部完成後再開始。

---

## 1. 安全：dockerignore 與推播端點認證

**風險等級：這是唯一「現在就有人可以濫用」的項目，建議最先做。**

### 問題

`main.py` 沒有任何 middleware、`Depends` 或認證。以下端點全裸露在
README 公開的正式網址上：

| 端點 | 位置 | 後果 |
|---|---|---|
| `GET /api/fcm/test` | [`main.py:1283`](../main.py) | **任何人開一個網址就能推播給全部裝置**。是 GET，爬蟲、`<img src>`、聊天室連結預覽都可能誤觸發 |
| `GET /api/fcm/test_auto` | [`main.py:1308`](../main.py) | 同上，且帶真實 AQI 數據 |
| `POST /api/fcm/push` | [`main.py:1270`](../main.py) | 任意標題／內文推播全台裝置，可被用來散播假警報 |
| `POST /api/rag_advice/experiment` | [`main.py:1050`](../main.py) | 直通 LLM，等於把 OpenRouter 額度開放給全世界 |

另外 `.dockerignore` **不存在**，而 `Dockerfile` 用 `COPY . .`。
本機建置時會把 `.env`、`serviceAccountKey.json`、`chroma_db/`、`.git`
一起烤進 image layer——**拿到 image 就拿到金鑰**。

### 修法

1. **建立 `.dockerignore`**（最優先，5 分鐘）：至少排除
   `.env*`、`serviceAccountKey.json`、`*firebase-adminsdk*.json`、`.git/`、
   `tests/`、`scripts/`、`analysis/`、`htmlcov/`、`.venv/`、`__pycache__/`、`rag/chroma_db/`
   > 註：`.gitignore` 原本有一行 `.dockerignore` 把它排除在版控外，已於測試 PR 中移除。

2. **測試／管理端點移到 `/internal/`** 並以 Cloud Run IAM invoker + OIDC 保護
   （Cloud Scheduler 呼叫時會自動帶 token）。或至少加一個共享密鑰 header。

3. **`GET /api/fcm/test` 與 `test_auto` 改成 POST** ——
   GET 會被預覽器與爬蟲意外觸發，這是最容易中的一種。

4. **寫入類端點**（`/api/report`、`/api/fcm/register`）建議接 **Firebase App Check**。
   專案已有 Firebase，App 端只需幾行。**[需同步改 App]**

### 驗證

沒有對應的 xfail（安全性不適合用 xfail 表達）。請新增測試：

```python
# tests/test_endpoint_auth.py
def test_push_endpoints_require_authentication(client):
    for method, path in [("post", "/api/fcm/push"), ("post", "/internal/fcm/test")]:
        assert getattr(client, method)(path).status_code in (401, 403)
```

需要 `fastapi.testclient.TestClient`（目前套件裡還沒有端對端測試，這會是第一個）。

---

## 2. 時區統一為 aware UTC

### 問題

系統有**三種時間基準並存**：

| 位置 | 基準 |
|---|---|
| [`main.py:754`](../main.py)（`submit_report` 寫入） | UTC+8 牆上時間，去掉 tzinfo |
| [`db/reports_db.py`](../db/reports_db.py) 的 6 處 cutoff | `datetime.now()`（行程時區） |
| 排程與 `token_store` | aware UTC+8 |

開發機是 Asia/Taipei，前兩者剛好一致；**Cloud Run 是 UTC，於是差 8 小時**。
資料庫裡的時間戳永遠比 cutoff 基準早 8 小時，所有時間窗被放大：

- 裝置回報頻率限制：3 分鐘 → **8 小時 3 分**（使用者被鎖一整天）
- 相同內容去重：6 小時 → 14 小時
- 近期回報查詢：24 小時 → 32 小時（熱點分析與 RAG 事件脈絡都吃到過期事件）

### 修法

**全系統統一用 aware UTC 存取，只在輸出給使用者時轉 UTC+8。**

1. `submit_report` 改寫 `datetime.now(timezone.utc).isoformat()`
2. `db/reports_db.py` 6 處 cutoff 改 `datetime.now(timezone.utc)`
3. 排程 job 內的「使用者設定時間」比對**維持 UTC+8**——
   使用者設的是台灣本地時間，這部分本來就是對的，不要一起改掉
4. 既有資料的遷移：Cloud Run 磁碟是暫時性的，實務上重新部署就沒有舊資料；
   若要保留請寫一次性的 `+8h` 修正腳本放 `scripts/`

### 驗證

```bash
pytest tests/test_timezone_contract.py -q
```

修好後這 5 個會 XPASS，請移除標記：

- `test_rate_limit_window_is_correct_under_utc_timezone`
- `test_dedup_window_is_correct_under_utc_timezone`
- `test_recent_reports_window_is_correct_under_utc_timezone`
- `test_confirmed_reports_window_is_correct_under_utc_timezone`
- `test_query_results_are_identical_across_server_timezones`

該檔案裡的 `_stored_timestamp()` helper 重現了目前的寫入格式，
改完寫入端後**必須同步更新它**，否則測試驗的是舊格式。

### 陷阱

- `test_daily_preference_uses_taiwan_time_regardless_of_server_timezone` 目前是**通過**的，
  它保護「使用者設定時間以 UTC+8 判斷」這個正確行為。不要把它一起改成 UTC。
- SQLite 存的是字串，ISO 8601 的字典序等於時間序——**但前提是格式一致**。
  aware datetime 的 `isoformat()` 會帶 `+00:00` 後綴，與舊資料的 naive 格式
  字典序比較會出錯。建議統一存 `Z` 結尾或維持 naive UTC，二選一並在整個 repo 一致。

---

## 3. 推播查找修正

這一項是 4 個獨立但相關的缺陷，建議一個 PR 一起處理。

### 3a. 空品預報推播從未送出任何一則

[`main.py:83`](../main.py) 拿 `rec["region"]` 當縣市去查 token，
但 [`forecast_fetcher.py:194`](../crawler/forecast_fetcher.py) 的
`location = county or area` 在 `county=None` 時會填入**空品區**名稱
（「北部空品區」），而裝置註冊的是縣市（「臺北市」）。永遠對不上，
`tokens` 為空 → 直接標記已推播並跳過。log 印「無新預警需推播」，看起來完全正常。

**修法**：把 `_COUNTY_TO_AREA` 反轉成 `area → [counties]`，
對每個空品區展開成該區所有縣市再查 token。

**驗證**：`test_forecast_fetcher.py::test_worsening_forecast_region_is_a_county_not_an_air_quality_zone`

### 3b. 「臺／台」未正規化

`COUNTY_CENTROIDS` 用「台」，但 token 的 county 是 App 直接轉傳 MOENV 的原始字串（「臺南市」）。
[`main.py:820`](../main.py) 的 `get_tokens_by_county(normalize_name(c))` **正規化錯了方向**——
把已經是「台」的再正規化一次，卻沒動 token store 那側。
臺北／臺中／臺南／臺東四縣市的擴散推播全數落空。

**修法**：正規化必須在**寫入時**做——`register_token()`
（[`fcm/token_store.py:32`](../fcm/token_store.py)）存進去前先 `normalize_name(county)`。
讀取端才有一致的 key。`normalize_name` 目前在 `main.py`，建議抽到共用模組。

**驗證**：`test_token_store.py::test_county_lookup_is_normalized_between_tai_variants`

### 3c. token 更新時清空縣市與健康狀況 **[需同步改 App]**

App 的 `MyFirebaseMessagingService.onNewToken` 以 `county=""` 呼叫註冊，
而 `register_token` **無條件覆寫**欄位。結果新 token 以空縣市建檔，
收不到任何縣市推播，直到下次 GPS 定位。使用者的敏感族群設定也一併遺失。

**修法**：後端對空值採 partial update（`if county: t["county"] = county`）。
App 端建議改為沿用上次已知的縣市，而不是傳空字串。

**驗證**：
- `test_token_store.py::test_registering_with_blank_county_preserves_the_known_county`
- `test_token_store.py::test_registering_with_blank_conditions_preserves_health_profile`

### 3d. 失效 token 無法清理 + 超過 500 台整批失敗

`send_multicast`（[`fcm/fcm_sender.py:43`](../fcm/fcm_sender.py)）只取
`success_count`/`failure_count`，丟掉 `response.responses` 裡逐則的失敗原因。
`UNREGISTERED` / `INVALID_ARGUMENT` 代表 token 已失效，但拿不到就無從清理，
於是死 token 永久累積，每次推播都算一次 failure。

另外 FCM 的 `send_each_for_multicast` **單批上限 500 則**，
超過直接拋錯 → 一旦註冊裝置破 500 台，**所有推播全數失敗**且只在 log 印一行。

**修法**：
1. `send_multicast` 分批（每批 ≤ 500）
2. 解析 `response.responses[i].exception`，回傳 `invalid_tokens` 清單
3. `token_store` 新增 `remove_token()`，呼叫端據此清理

**驗證**：
- `test_fcm_sender.py::test_send_multicast_batches_tokens_within_the_fcm_limit`
- `test_fcm_sender.py::test_send_multicast_reports_which_tokens_are_invalid`
- `test_token_store.py::test_store_exposes_a_way_to_remove_stale_tokens`

---

## 4. 地區判讀重寫

`extract_region()`（[`crawler/news_scraper.py:173`](../crawler/news_scraper.py)）
是全 repo 分支最密的純函式，五條路徑互相影響。它的輸出決定
`/api/news?region=` 的過濾結果與 RAG 的事件脈絡——錯了會把外縣市的火災掛到使用者頭上。

### 三個缺陷共用同一組根因

| 缺陷 | 根因 |
|---|---|
| 含「新聞」「最新」「創新」的標題 → `台南市新區` | [第 127 行](../crawler/news_scraper.py) `rstrip("區鄉鎮市")` 把「新市區」削成單字「新」，而它不在 `_AMBIGUOUS_DISTRICTS`（[第 167 行](../crawler/news_scraper.py)）白名單內 |
| 「南投縣埔里鎮」→ `南投縣南投` | `_find_district` 先命中與縣同名的行政區就 `break`，蓋掉真正的鄉鎮。南投／花蓮／宜蘭／彰化／屏東全中 |
| 「竹北市」→ `台北市` | `_COUNTY_ABBREVS`（[第 143 行](../crawler/news_scraper.py)）以子字串比對，且「北市」排在最前面 |

### 修法

建議整段重寫而非補丁，原則：

1. **不要用 `rstrip` 削後綴**。`rstrip("區鄉鎮市")` 會把結尾所有屬於該字集的字都吃掉
   （「新市區」→「新」、「左鎮區」→「左」）。改用只移除最後一個字元的方式，
   並保留原始全名供比對。
2. **最長匹配優先**，且比對後檢查詞界，而非單純 `in`。
3. **縣市縮寫**改以長度排序比對，或加詞界檢查，避免「竹北市」命中「北市」。
4. **行政區反推**時，與縣同名者應**降低優先度**而非優先命中。
5. 比對結果不應取決於 `CityCountyData.json` 的排列順序
   （目前「苗栗縣頭份市」解析正確只是因為「頭份」剛好排在「苗栗」前面，
   見 `test_district_matching_currently_depends_on_source_data_ordering`）。

### 驗證

```bash
pytest tests/test_region_extraction.py -q
```

23 個既有測試是**回歸網**，重寫時必須全數維持通過。
另外 3 組 xfail（共 10 個參數化案例）應轉綠：

- `test_common_words_are_not_mistaken_for_tainan_districts`（3 案例）
- `test_county_seat_does_not_shadow_the_real_district`（5 案例）
- `test_abbreviation_matching_does_not_match_across_word_boundaries`（2 案例）

**同時要更新兩個描述現況的測試**（它們斷言的是缺陷本身，不是期望行為）：
- `test_rstrip_produces_single_character_districts`
- `test_district_matching_currently_depends_on_source_data_ordering`

---

## 5. KDE 共線防護與等級名稱統一

### 5a. 回報點共線時熱點分析靜默失效

[`gis/hotspot_analyzer.py:242`](../gis/hotspot_analyzer.py) 的
`gaussian_kde` 在資料點共線時共變異數矩陣退化，拋 `LinAlgError`，
被 `except` 吞掉後 `return []`。

沿道路、河道或煙流軸線分布的回報是**很常見的真實情境**——
明明有共識回報卻分析不出熱點。
[第 237 行](../gis/hotspot_analyzer.py) 現有的 `np.std < 1e-6` 防護
只擋得住「完全重合」，擋不住「共線」。

**修法**：偵測退化情形（例如檢查共變異數矩陣的條件數或行列式），
退化時走簡化路徑——以回報點的重心與擴散半徑直接產生單一熱點，
而不是回傳空清單。

**驗證**：`test_hotspots.py::test_collinear_reports_still_produce_a_hotspot`

### 5b. AQI 等級名稱三處不一致

AQI 151–200 在 [`health_rules.py:317`](../rag/health_rules.py) 是「不健康」，
但 `rag_engine._aqi_to_status` 與 `forecast_fetcher._aqi_to_status` 都是
「對所有族群不健康」（環境部官方用語）。同一個 AQI 在 App 的建議卡片
與預報通知會顯示兩種等級名稱。

**修法**：統一採官方用語，並把重複的 `_aqi_to_status`
（`rag_engine` 與 `forecast_fetcher` 各一份）合併為單一實作。
`test_rag_engine.py::test_aqi_to_status_matches_forecast_module` 已在保護這個一致性。

### 5c. AQI 邊界防禦

`get_rule_by_aqi`（[`health_rules.py:707`](../rag/health_rules.py)）
對 > 500 與負值回傳 `None`，呼叫端未檢查就會讓 `aqi_level` 變成「未知」——
**在最危險的情境下反而失去等級資訊**。

**修法**：> 500 夾到 `aqi_hazardous`，負值夾到 `aqi_good`。

**驗證**：
- `test_health_rules.py::test_rule_level_names_match_the_official_status_names`
- `test_health_rules.py::test_extreme_aqi_still_resolves_to_the_hazardous_rule`
- `test_health_rules.py::test_negative_aqi_is_clamped_to_the_good_rule`

---

## 6. 相依鎖版本

[`requirements.txt`](../requirements.txt) 完全沒鎖版本，且 `firebase-admin` 出現兩次。
`chromadb` 或 `openai` 一個 major bump 就會讓下次 `gcloud run deploy` 靜默壞掉，
而且**無法重現**先前的建置。

**修法**：`pip freeze` 鎖版本，或改用 `uv` / `pip-tools` 管理。移除重複行。

**注意**：`requirements-dev.txt` 刻意不引用 `requirements.txt`（理由見 `TESTING.md`），
鎖版本時兩份都要處理，並在 `TESTING.md` 註明需手動同步直接相依。

---

## 7. 架構重構（大工程）

前 6 項都是點狀修復。這一項是結構性的，建議 1–6 完成後再開始，並拆成多個 PR。

### 7a. 狀態持久化

四份可變狀態全在 Cloud Run 的**暫時性磁碟**上，重新部署即歸零：
`user_reports.db`、`fcm_tokens.json`（含所有裝置 token 與通知設定）、
`scraped_news.json`、`chroma_db/`。

README 目前的處理方式是「展示前避免部署」——這不是解法。

**起點**：[`db/firestore_reports.py`](../db/firestore_reports.py) **已經寫好但沒接上**
（`main.py` import 的是 `reports_db`）。把它接起來，token store 照做。
Firestore 一次解決持久化、多實例一致性、併發寫入三件事。

順帶解決：`token_store` 目前是**無鎖的整檔讀改寫**
（`test_token_store.py::test_concurrent_registration_does_not_lose_updates`），
`json.dump` 寫到一半時讀取會拿到截斷的檔案。

### 7b. 排程搬出 web process

五個 APScheduler job 跑在 web process 內，因此部署必須維持 `min-instances=1`；
一旦 scale out，N 個實例會各跑一份排程 → 推播重複 N 次。

**修法**：Cloud Scheduler → 帶 OIDC 打 `/internal/jobs/{news,forecast,fire,aqi,daily}`。
這一步同時解掉「重複推播」「必須 min-instances=1」「API 無法 scale-to-zero」。

### 7c. 快取層

系統**沒有任何快取**。`/api/rag_advice` 單一請求同步串起九個外部來源，
其中 `fetch_fire_alerts` 是 1+N（每則警示再抓一次 CAP XML，各 8s timeout）。

**修法**：TTL 快取（AQI 10 分、火災 5 分、CWA 10 分、預報 30 分）。
單實例先用 `cachetools.TTLCache`，多實例改 Firestore／Memorystore。
預期可砍掉約 90% 的外部呼叫。

順帶解決 `test_rag_advice_endpoint.py` 的兩個 xfail：
- `test_aqi_upstream_is_queried_at_most_once_per_request`（[`main.py:928`、`933`](../main.py) 重複呼叫）
- `test_hotspot_analysis_uses_the_same_wind_as_downwind_check`（[`main.py:959`](../main.py) 未帶風速）

### 7d. `main.py` 拆層

1,344 行、20 個端點，混雜路由、排程、外部 API client 與業務邏輯。
建議拆成 `routers/`（純 HTTP）、`services/`（業務）、`clients/`（統一 retry + timeout + 快取）、
`jobs/`（排程）。同時把 18 處函式內 import 移到模組頂層。

**這一步完成後覆蓋率會大幅上升**——業務邏輯變成可注入的純函式。

### 7e. 錯誤處理與日誌 **[需同步改 App]**

20 個端點沒有一個 `raise HTTPException`，失敗一律回 HTTP 200 +
`{"status": "error"}`。後果：Cloud Run 的錯誤率監控永遠 0%，
且 `str(e)` 直接把內部例外訊息回傳給客戶端。

日誌只有 `print()`，沒有 severity 或結構化欄位，無法設 alert。

**修法**：4xx/5xx 用 `HTTPException`，全域 exception handler 統一包裝；
`print` 換成 `logging` + `google-cloud-logging`。
**App 端目前只看 body 的 `status` 欄位，改動前必須同步。**

### 7f. 移除 ChromaDB

知識庫只有 **8 條規則**。為 8 份文件跑一個持久化向量資料庫、
每次查詢還打一次 embedding API，是明顯的過度設計
（`tests/conftest.py` 的 stub 能成立本身就是證據）。

**修法**：build 階段把 8 個向量算好存成 JSON，開機載入記憶體，用 numpy 算 cosine。
省掉一個重量級依賴、一個持久化目錄，以及每次 `/api/rag_advice` 的一次網路往返。

---

## 附錄：文件地圖

| 文件 | 內容 |
|---|---|
| [`CLAUDE.md`](../CLAUDE.md) | 架構、跨模組陷阱、常用指令。**新 session 先讀這份** |
| [`TESTING.md`](../TESTING.md) | 測試設計原則、29 個缺陷清單、覆蓋率解讀、撰寫新測試的規範 |
| `docs/ROADMAP.md` | 本文件：修什麼、怎麼修、怎麼驗 |
| [`README.md`](../README.md) | API 端點、部署、資料來源 |
