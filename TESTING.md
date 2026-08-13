# 測試指南

本專案的單元測試以「**先建立回歸網、再重構**」為目的而寫。
測試套件同時扮演兩個角色：驗證現有行為，以及**以可執行的形式記錄已知缺陷**。

```bash
pip install -r requirements-dev.txt
pytest -q
```

目前：**360 個測試（333 通過 / 27 已知缺陷），約 2 秒跑完。**

---

## 設計原則

### 1. 快，才會有人跑

整套 2 秒內完成，因此可以在每次存檔後跑。達成方式：

- **不打網路**：`conftest.py` 的 autouse fixture 把 `requests` 的
  `get/post/put/delete/request/head` 與 `Session.request` 全部換成會 assert 失敗的替身。
  測試若不小心打真實 API 會立刻紅燈，而不是變慢或間歇性失敗。
- **不呼叫 LLM**：`rag_engine` 與 `llm_structurer` 的 OpenAI client 以替身注入。
- **不裝 chromadb**：見下方「為什麼 requirements-dev.txt 不引用 requirements.txt」。

### 2. 測試之間不共用狀態

`reports_db` 與 `token_store` 兩個 fixture 會把 DB 路徑與 token 檔導向 `tmp_path`，
每個測試拿到全新的空白狀態。**不要**寫依賴執行順序的測試。

### 3. 時間必須是確定的

任何讀真實時鐘的邏輯都要嘛注入固定輸入、要嘛用 `tz` fixture 明確指定時區。

反例（本專案實際踩過）：`test_due_list_uses_catch_up_semantics` 原本透過
`set_daily_preference(hour=23, ...)` 建立狀態，但該函式會讀真實時鐘決定是否
抑制當天推播——於是測試在 23:00 之後就會紅。修正方式是用 `_seed_daily()`
直接寫入確定的狀態，只驗 `get_due_daily_tokens` 的比對邏輯。

**判準**：如果一個測試的結果會隨「現在幾點」改變，它就是錯的。

### 4. 環境變數在 collection 之前設定

`rag/embedder.py` 與 `rag/llm_structurer.py` 在 module scope 就建立 OpenAI client，
所以假金鑰不能用 fixture 設定——`conftest.py` 在 import 期就以
`os.environ.setdefault` 填入。這也順便確保測試不會誤用開發者本機 `.env` 的真金鑰。

---

## 已知缺陷的表達方式

**已確認但尚未修復的缺陷，一律寫成 `xfail(strict=True)` 並標記 `known_bug`，
而不是寫在註解、TODO 或 issue 裡。**

```python
@pytest.mark.known_bug
@pytest.mark.xfail(
    strict=True,
    reason="submit_report 寫入 UTC+8 牆上時間，count_recent_by_device 用 "
           "datetime.now()（UTC）計算 cutoff，導致 3 分鐘的頻率限制在 "
           "Cloud Run 上變成 8 小時 3 分。",
)
def test_rate_limit_window_is_correct_under_utc_timezone(reports_db, tz):
    ...
```

### 為什麼是 strict=True

`strict=True` 表示：**缺陷修好後測試會 XPASS，而 XPASS 會讓整個測試套件失敗。**

這是刻意的。它強迫修完的人回到測試檔移除標記，因此：

- 這份清單不會腐爛——修好的項目無法留在清單上
- 缺陷的「期望行為」以可執行的形式存在，而不是散落在文件裡的描述
- 新人可以直接跑 `pytest -m known_bug --runxfail` 看每個缺陷實際壞在哪一行

### 修好缺陷後的流程

1. 移除 `@pytest.mark.known_bug` 與 `@pytest.mark.xfail(...)` 兩個裝飾器
2. 確認測試以正面斷言的形式通過
3. 若 reason 裡描述的根因與實際修法不同，順手更新相關測試的註解

### 什麼時候該新增 known_bug 標記

只在**已確認是真缺陷、但這次不修**時使用。不要拿它來標記：
「還沒想清楚的行為」、「上游 API 的問題」、「效能不夠好但正確」。
那些應該是 issue，不是 xfail。

---

## 目前的 27 個已知缺陷

```bash
pytest -m known_bug -q          # 列出清單
pytest -m known_bug --runxfail  # 看實際失敗位置
```

| 分類 | 缺陷 | 檔案 |
|---|---|---|
| 時區 | 寫入用 UTC+8、查詢用行程時區，所有時間窗在 Cloud Run 上放大 8 小時（限流 3 分→8 小時、去重 6→14 小時、查詢 24→32 小時） | `test_timezone_contract.py` |
| 推播 | 空品預報推播拿「空品區」名稱去查縣市 token，永遠對不上，該管線從未送出任何一則 | `test_forecast_fetcher.py` |
| 推播 | 「臺／台」未在寫入時正規化，臺北／臺中／臺南／臺東的擴散推播查無裝置 | `test_token_store.py` |
| 推播 | token 更新時 county 與 conditions 被空字串覆寫（App 的 `onNewToken` 會這樣呼叫） | `test_token_store.py` |
| 推播 | 沒有移除 token 的介面，FCM 回報失效的 token 永久累積 | `test_token_store.py` |
| 併發 | token 檔為無鎖的整檔讀改寫，併發註冊會 lost update | `test_token_store.py` |
| 地區判讀 | 「新市區」經 `rstrip` 縮成「新」，含「新聞」「最新」「創新」的標題全被誤判為台南市 | `test_region_extraction.py` |
| 地區判讀 | 縣治與縣同名時蓋掉真正的鄉鎮，輸出「南投縣南投」 | `test_region_extraction.py` |
| 地區判讀 | 「竹北市」因子字串比對命中「北市」而誤判為台北市 | `test_region_extraction.py` |
| 熱點分析 | 回報點共線時 `gaussian_kde` 拋 `LinAlgError`，被吞掉後靜默回傳空清單 | `test_hotspots.py` |
| 健康規則 | AQI 151–200 的等級名稱在 `health_rules`（「不健康」）與 `_aqi_to_status`（「對所有族群不健康」）之間不一致 | `test_health_rules.py` |
| 健康規則 | AQI > 500 查無規則，最危險時反而失去等級資訊；負值無防禦 | `test_health_rules.py` |
| 效能 | `/api/rag_advice` 對同一縣市重複查詢 MOENV | `test_rag_advice_endpoint.py` |
| 一致性 | 熱點分析未帶入本次請求的風況，一律以「無風」計算，卻又用真實風速做下風處判斷 | `test_rag_advice_endpoint.py` |

---

## 時區

正式環境（Cloud Run）的 TZ 是 **UTC**，開發機通常是 **Asia/Taipei**。
本專案有相當比例的缺陷只在前者浮現，因此：

```bash
TZ=UTC pytest -q
TZ=Asia/Taipei pytest -q
```

CI（`.github/workflows/tests.yml`）會在這兩個時區各跑一次完整測試。
**兩邊都綠，才代表時間邏輯真的與行程時區無關。**

寫新測試時，凡是涉及時間比較的，請用 `tz` fixture 明確指定，
不要依賴執行環境的預設值：

```python
def test_something(reports_db, tz):
    tz("UTC")          # 測試結束後自動還原
    ...
```

---

## 為什麼 `requirements-dev.txt` 不引用 `requirements.txt`

一般慣例是 `-r requirements.txt`，本專案刻意不這樣做：

`chromadb` 會帶進 onnxruntime、grpcio、opentelemetry 等傳遞相依，安裝需要數分鐘
（實測 uv 在本機超過 20 分鐘未完成）。但 RAG 知識庫**只有 8 條規則**，
單元測試完全用不到向量資料庫——`conftest.py` 的 `_install_chromadb_stub()`
會在 chromadb 缺席時注入極簡替身。

代價是 `requirements-dev.txt` 需要手動與 `requirements.txt` 的直接相依保持同步。
這個取捨換到的是「2 秒的測試」與「不用等 5 分鐘的 CI」。

> 附帶一提：這個 stub 能成立本身就說明 chromadb 對本專案是過度設計。
> 8 個向量用 numpy 算 cosine 即可，可省掉一個重量級依賴、一個持久化目錄，
> 以及每次 `/api/rag_advice` 都要打一次的 embedding API 往返。

需要驗證真實向量檢索或真實外部 API 時，請另建掛 `@pytest.mark.network`
的整合測試，並在該 CI 工作流程安裝完整的 `requirements.txt`。

---

## 撰寫新測試

### 可用的 fixtures（`tests/conftest.py`）

| Fixture | 用途 |
|---|---|
| `reports_db` | 導向 `tmp_path` 並完成建表的 `db.reports_db` 模組 |
| `token_store` | 導向 `tmp_path` 的 `fcm.token_store` 模組 |
| `tz` | 切換行程時區，測試結束自動還原 |
| `_block_outbound_http` | autouse，攔截所有對外 HTTP |

### monkeypatch 的目標取決於 import 位置

`main.py` 有 18 處函式內 import，因此 patch 目標分兩種：

```python
# main 頂層 import 的 → patch main 的屬性
monkeypatch.setattr(main, "analyze_hotspots", _stub)
monkeypatch.setattr(main, "generate_advice", _stub)

# 函式內才 import 的 → 必須 patch 來源模組
import crawler.weather_fetcher as weather_mod
monkeypatch.setattr(weather_mod, "fetch_weather_for_county", _stub)
```

`tests/test_rag_advice_endpoint.py` 的 `wiring` fixture 是完整範例。

### 測試命名

用「敘述期望行為」的句子，而不是「測試某函式」：

```
✓ test_calm_wind_uses_radius_not_direction
✓ test_device_id_is_never_exposed_to_clients
✗ test_check_downwind_2
✗ test_insert_report
```

失敗訊息會直接顯示測試名稱，好名字讓 CI 紅燈自己解釋自己。

### 邊界值優先

本專案大量邏輯是門檻判斷（AQI 50/100/150/200/300、風速 1.0 m/s、
時間窗 3 分/6 小時/24 小時、半徑 5/50 km）。這些邊界值兩側都要有測試——
`test_forecast_fetcher.py` 與 `test_health_rules.py` 的參數化寫法可直接參考。

---

## 目前未涵蓋的範圍

寫測試時值得知道哪裡還是空白：

- **`fcm/fcm_sender.py`**：完全沒有測試（需要 mock `firebase_admin`）
- **`db/firestore_reports.py`**：沒有測試（該模組目前也未被接上）
- **爬蟲的網路層**：`fetch_pts_news` / `fetch_yahoo_news` / `fetch_google_news`
  只測了過濾邏輯，RSS 解析與 HTML 清理未覆蓋
- **`crawler/weather_fetcher.py`**：CWA 回應格式解析（`_get_field` 要應付
  dict 與 list 兩種結構、`_get_rain` 要試五個可能欄位名）未覆蓋
- **`main.py` 的排程 job**：五個 `_*_push_job` 沒有測試
- **端對端**：沒有 `TestClient` 層級的 HTTP 測試（`/api/rag_advice` 是以
  直接呼叫函式的方式測編排邏輯，未經過 FastAPI 的序列化與驗證）
