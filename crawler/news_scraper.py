import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import json
import os
import re

# 關鍵字過濾：空氣品質相關災情
KEYWORDS = ["火災", "火警", "大火", "濃煙","空污", "空汙", "異味", "空氣品質", "環保局", "失火", "臭味", "工廠大火", "工廠火災", "火燒山"]

# 台灣過濾：確保新聞發生在台灣，排除常見的國外災情地區
TAIWAN_COUNTIES = ["台北", "新北", "基隆", "桃園", "新竹", "苗栗", "台中", "彰化", "南投", "雲林", "嘉義", "台南", "高雄", "屏東", "宜蘭", "花蓮", "台東", "澎湖", "金門", "馬祖"]
TAIWAN_KEYWORDS = TAIWAN_COUNTIES + ["台灣", "縣", "市", "區", "鄉", "鎮"]
FOREIGN_KEYWORDS = ["中國", "美國", "日本", "韓國", "加州", "澳洲", "歐洲", "印尼", "印度", "俄羅斯", "烏克蘭", "加薩", "以色列", "國外", "世界", "國際"]

def is_within_last_3_days(published_str):
    """檢查新聞發布時間是否在過去 3 天內"""
    if not published_str:
        return False
    try:
        # RSS feed parser 通常會回傳 time.struct_time 或標準格式字串
        # 這裡由於 feedparser 處理過，通常 published 會帶時區 (例: "Wed, 11 Mar 2026 04:58:55 +0800" 或是 "2026-03-11T04:58:55Z")
        # 直接使用 feedparser 解析出來的時間 (避免字串格式不一致)
        # 此處簡易實作，如果是 datetime 解析：
        pub_time = feedparser._parse_date(published_str)
        if pub_time:
            # pub_time 是 time.struct_time
            pub_dt = datetime(*pub_time[:6])
            
            # 使用 UTC 目前時間，因為 feedparser._parse_date 預設轉 UTC
            now = datetime.utcnow()
            diff = now - pub_dt
            return diff <= timedelta(days=3)
        return True # 如果解析失敗，保守起見保留
    except Exception as e:
        print(f"時間解析失敗: {published_str}, {e}")
        return True

def extract_region(text):
    """從標題或摘要中擷取地區"""
    for county in TAIWAN_COUNTIES:
        if county in text:
            return county
    return "台灣 (未指明特定縣市)"

def fetch_pts_news():
    """爬取公共電視新聞的 RSS"""
    # 公視新聞的 RSS 網址 (社會版面，較常有災情)
    rss_url = "https://news.pts.org.tw/xml/newsfeed.xml"
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔍 開始檢查公視 RSS 最新新聞...")
    feed = feedparser.parse(rss_url)
    
    extracted_data = []

    for entry in feed.entries:
        title = entry.title
        summary = entry.summary if 'summary' in entry else ""
        link = entry.link
        published = entry.published if 'published' in entry else ""
        
        # 檢查標題或摘要是否包含我們的關鍵字
        if any(keyword in title or keyword in summary for keyword in KEYWORDS):
            
            if any(foreign in title for foreign in FOREIGN_KEYWORDS):
                continue
            if not any(tw_city in title for tw_city in TAIWAN_KEYWORDS):
                 continue
                 
            if not is_within_last_3_days(published):
                continue
                
            region = extract_region(title + " " + summary)
                
            document = {
                "source": "公共電視",
                "region": region,
                "title": title,
                "summary": summary,
                "url": link,
                "published_at": published,
                "timestamp": datetime.now().isoformat()
            }
            extracted_data.append(document)
            
    return extracted_data

def fetch_yahoo_news():
    """爬取 Yahoo 新聞的 RSS"""
    # Yahoo 社會新聞 RSS
    rss_url = "https://tw.news.yahoo.com/rss/society"
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔍 開始檢查 Yahoo RSS 最新新聞...")
    try:
        # 有些 RSS 伺服器會阻擋預設的 User-Agent，我們模擬瀏覽器
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(rss_url, headers=headers)
        feed = feedparser.parse(response.content)
        
        extracted_data = []

        for entry in feed.entries:
            title = entry.title
            summary = entry.summary if 'summary' in entry else ""
            link = entry.link
            published = entry.published if 'published' in entry else ""
            
            if any(keyword in title or keyword in summary for keyword in KEYWORDS):
                if any(foreign in title for foreign in FOREIGN_KEYWORDS):
                    continue
                if not any(tw_city in title for tw_city in TAIWAN_KEYWORDS):
                     continue
                     
                if not is_within_last_3_days(published):
                    continue
                
                # Yahoo RSS summary 常含有 html 標籤，用 BeautifulSoup 清理
                clean_summary = BeautifulSoup(summary, "html.parser").text if summary else ""
                
                region = extract_region(title + " " + clean_summary)
                
                document = {
                    "source": "Yahoo 新聞",
                    "region": region,
                    "title": title,
                    "summary": clean_summary.strip(),
                    "url": link,
                    "published_at": published, # Yahoo format e.g., "Wed, 11 Mar 2026..."
                    "timestamp": datetime.now().isoformat()
                }
                extracted_data.append(document)
                
        return extracted_data
    except Exception as e:
        print(f"獲取 Yahoo RSS 失敗: {e}")
        return []

def fetch_google_news():
    """使用 Google News RSS 搜尋關鍵字"""
    # 組合 Google 搜尋語法 (利用 OR 連接關鍵字，並限制搜尋台灣新聞)
    query = "+OR+".join(KEYWORDS)
    rss_url = f"https://news.google.com/rss/search?q={query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔍 開始使用 Google News 關鍵字搜尋...")
    try:
        response = requests.get(rss_url)
        feed = feedparser.parse(response.content)
        
        extracted_data = []
        for entry in feed.entries:
            title = entry.title
            link = entry.link
            published = entry.published if 'published' in entry else ""
            
            # 使用我們自定義的 TAIWAN_KEYWORDS 與 FOREIGN_KEYWORDS 二次過濾
            # 1. 確保標題中「不包含」國外地名
            if any(foreign in title for foreign in FOREIGN_KEYWORDS):
                continue
                
            # 2. (選擇性) 確保標題中「包含」台灣地名，這樣更嚴格。如果你覺得太嚴格可以註解掉下面這兩行。
            if not any(tw_city in title for tw_city in TAIWAN_KEYWORDS):
                 continue
            
            # Google RSS 通常把新聞來源放在 title 最後的 " - 來源名稱"
            source = "Google News"
            if " - " in title:
                parts = title.rsplit(" - ", 1)
                title = parts[0]
                source = parts[1]
                
            if not is_within_last_3_days(published):
                continue
                
            region = extract_region(title)  
            
            document = {
                "source": source,
                "region": region,
                "title": title,
                "summary": "",  # Google rss 的 summary 是 html 的連結區塊，用處不大，先留空
                "url": link,
                "published_at": published, # e.g., "Mon, 09 Mar 2026 12:00:00 GMT"
                "timestamp": datetime.now().isoformat()
            }
            extracted_data.append(document)
            
        return extracted_data
    except Exception as e:
        print(f"獲取 Google News 失敗: {e}")
        return []

def run_scraper():
    """執行爬蟲並印出結果"""
    all_news = []
    
    # 執行各個來源的爬蟲
    google_news = fetch_google_news()
    all_news.extend(google_news)
    pts_news = fetch_pts_news()
    all_news.extend(pts_news)
    
    yahoo_news = fetch_yahoo_news()
    all_news.extend(yahoo_news)
    
    print("--------------------------------------------------")
    print(f"✅ 共擷取到 {len(all_news)} 篇可能相關的災情或空汙新聞。")
    print("--------------------------------------------------")
    
    for i, news in enumerate(all_news, 1):
         print(f"{i}. [{news['region']}] {news['title']}")
         print(f"   時間: {news['published_at']}")
         print(f"   來源: {news['source']}")
         if news['summary']:
             # 印出前 50 個字的摘要
             print(f"   摘要: {news['summary'][:50]}...")
         print(f"   連結: {news['url']}\n")
    
    return all_news

if __name__ == "__main__":
    # 確保儲存目錄存在
    os.makedirs(os.path.dirname(os.path.abspath(__file__)), exist_ok=True)
    
    results = run_scraper()
    
    # 將爬取結果存成 JSON 檔案進行檢視與測試
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scraped_news.json')
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
        
    print(f"💾 已將詳細結果儲存至: {output_path}")
