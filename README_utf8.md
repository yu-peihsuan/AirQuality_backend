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

#### 後端 API 預設運行位置：

- **系統狀態測試 (Root)**
  http://localhost:8000/

- **取得最新空氣品質資料 (AQI Endpoint)**
  http://localhost:8000/api/air_quality
  *(可附帶參數測試特定縣市：`?county=臺北市`)*
