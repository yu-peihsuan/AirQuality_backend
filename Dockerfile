# 使用官方的 Python 3.10 輕量版映像檔作為基底
FROM python:3.10-slim

# 設定容器內的工作目錄為 /app
WORKDIR /app

# 將本機的 requirements.txt 複製到容器內
COPY requirements.txt .

# 安裝 Python 套件 (不快取以節省空間)
RUN pip install --no-cache-dir -r requirements.txt

# 將當前目錄的所有程式碼檔案複製到容器的 /app 目錄下
COPY . .

# 宣告容器會使用的 Port
EXPOSE 8000

# 給啟動腳本執行權限
RUN chmod +x start.sh

# 容器啟動時：先跑爬蟲，再啟動 FastAPI
CMD ["sh", "start.sh"]