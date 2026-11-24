# AirQuality Backend｜空氣品質即時監測後端

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
│   ├── token_store.py          # FCM token 與每日通知設定（本機 JSON）
│   └── token_store_firestore.py # 同介面的 Firestore 版，Cloud Run 上自動啟用
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
