#!/bin/sh
echo "🕷️  背景執行新聞爬蟲..."
python crawler/news_scraper.py &

echo "🚀 啟動 FastAPI 伺服器..."
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
