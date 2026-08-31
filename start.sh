#!/bin/sh
# 新聞爬蟲不在這裡另外跑：main.py 的 lifespan 已把 _scraper_job 排成
# 啟動時立即執行（next_run_time=now_tw()），在這裡再跑一次是重複工作——
# 重複抓取各新聞來源、重複寫入同一個資料庫。
#
# 修正時區時另有一個具體理由：news_scraper.py 現在會匯入 core.timeutil，
# 為此補上了 sys.path 根目錄。副作用是原本在單獨執行時會匯入失敗、
# 因而停用的 rag.llm_structurer 變成可以匯入，於是這一份重複執行也會
# 開始呼叫 LLM——等於每次容器啟動都多付一次 LLM 費用。
echo "🚀 啟動 FastAPI 伺服器（新聞爬蟲由 APScheduler 於啟動時立即執行）..."
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
