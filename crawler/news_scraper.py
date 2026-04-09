import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import json
import os
import sqlite3

# LLM 語意結構化（OpenRouter OPENAI_API_KEY 需設定）
try:
    from rag.llm_structurer import structure_news_batch
    _LLM_STRUCTURING_ENABLED = True
except ImportError:
    _LLM_STRUCTURING_ENABLED = False
    print("⚠️  rag.llm_structurer 未找到，跳過語意結構化")

# 關鍵字過濾：空氣品質相關災情
KEYWORDS = [
    # 火災相關
    "火災", "火警", "大火", "濃煙", "失火", "工廠大火", "工廠火災", "火燒山",
    # 空氣品質相關
    "空污", "空汙", "異味", "空氣品質", "臭味",
    "PM2.5", "PM10", "細懸浮微粒", "懸浮微粒",
    "霾", "霧霾", "落塵",
    "廢氣", "排放", "空品", "紫爆", "空污警報",
    "臭氧", "二氧化硫", "一氧化碳",
    # 空品警示狀態
    "汙染物累積", "污染物累積",
    "空氣品質欠佳",
    "境外汙染", "境外污染",
    "紅色警戒", "橘色警戒",
]

# 台灣過濾：確保新聞發生在台灣，排除常見的國外災情地區
TAIWAN_COUNTIES = ["台北", "新北", "基隆", "桃園", "新竹", "苗栗", "台中", "彰化", "南投", "雲林", "嘉義", "台南", "高雄", "屏東", "宜蘭", "花蓮", "台東", "澎湖", "金門", "馬祖"]
TAIWAN_KEYWORDS = TAIWAN_COUNTIES + ["台灣", "縣", "市", "區", "鄉", "鎮", "北部", "中部", "南部", "東部", "西部", "離島"]
FOREIGN_KEYWORDS = ["中國", "美國", "日本", "韓國", "加州", "澳洲", "歐洲", "印尼", "印度", "俄羅斯", "烏克蘭", "加薩", "以色列", "國外", "世界", "國際"]

# ── 方法一：規則層 ────────────────────────────────────────────────────────────
# 排除詞：含這些詞的新聞大概率不是即時事件
NON_REALTIME_EXCLUSIONS = [
    "回顧", "紀念", "週年", "歷史", "周年紀念",
    "電影", "戲劇", "小說", "書評", "遊戲",
    "判決", "起訴", "賠償", "審判", "開庭", "法院", "訴訟",
    "演習", "演練", "防災教育", "消防講習", "防火宣導",
    "保險", "理賠", "股票", "ETF", "投資","呼籲", "籲"
]

# 即時性正向提示詞（標題+摘要至少含一個，才視為即時事件）
REALTIME_HINTS = [
    # 時間詞
    "今", "昨", "今日", "昨日", "今天", "昨天",
    "剛才", "剛剛", "凌晨", "上午", "下午", "深夜", "晚間",
    # 火災即時詞
    "發生", "延燒", "竄火", "冒煙", "濃煙", "悶燒", "起火",
    "警消", "消防", "出動", "搶救", "現場", "撲滅", "灌救",
    # 空氣品質即時詞
    "超標", "紫爆", "不良", "預警", "警戒", "管制", "停工",
    "紅害", "橘色", "空品不良", "空品惡化", "居民投訴",
    "排放", "外洩", "飄散", "蔓延",
    "亮紅燈", "亮橘燈", "來襲", "欠佳", "累積", "境外",
    # 通報類
    "民眾", "居民", "通報", "發現", "檢舉",
]

def is_realtime_event(title: str, summary: str = "") -> bool:
    """方法一：規則層 — 判斷新聞是否為即時事件。
    
    流程：
    1. 含排除詞（歷史/判決/演習等）→ False
    2. 不含任何即時性提示詞 → False
    3. 其餘 → True
    """
    text = title + " " + summary
    if any(exc in text for exc in NON_REALTIME_EXCLUSIONS):
        return False
    if not any(hint in text for hint in REALTIME_HINTS):
        return False
    return True

def is_within_last_3_days(published_str):
    """檢查新聞發布時間是否在過去 3 天內"""
    if not published_str:
        return False
    try:
        import email.utils
        # 優先用 email.utils 解析 RFC 2822 格式（RSS 標準格式）
        # e.g. "Tue, 31 Mar 2026 07:00:00 GMT"
        parsed = email.utils.parsedate_to_datetime(published_str)
        pub_dt = parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        now = datetime.utcnow()
        diff = now - pub_dt
        return diff <= timedelta(days=3)
    except Exception:
        pass
    try:
        # 備用：ISO 8601 格式 e.g. "2026-03-31T07:00:00Z"
        cleaned = published_str.replace("Z", "+00:00")
        from datetime import timezone
        parsed = datetime.fromisoformat(cleaned)
        pub_dt = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        now = datetime.utcnow()
        return (now - pub_dt) <= timedelta(days=3)
    except Exception as e:
        print(f"時間解析失敗，略過此筆: {published_str} | {e}")
        return False  # 解析失敗 → 拒絕，避免舊文混入

# 從 CityCountyData.json 動態載入 DISTRICTS
# JSON 結構: [{"CityName": "臺北市", "AreaList": [{"AreaName": "中正區"}, ...]}, ...]
_CITY_ALIAS = {
    "連江": "馬祖",   # 連江縣 = 馬祖
    "釣魚臺": None,   # 排除釣魚台
    "南海島": None,   # 排除南海島
}
_SKIP_CITIES = {"釣魚臺", "南海島"}

_json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CityCountyData.json")
with open(_json_path, encoding="utf-8") as _f:
    _city_data = json.load(_f)

DISTRICTS = {}
for _city in _city_data:
    _city_name = _city["name"].replace("臺", "台")  # 統一用「台」
    _short = _city_name.rstrip("市縣")               # 去掉後綴，e.g. "台北"
    if _short in _SKIP_CITIES:
        continue
    _short = _CITY_ALIAS.get(_short, _short)         # 套用別名
    if _short is None:
        continue
    _districts = [area["name"].rstrip("區鄉鎮市") for area in _city["districts"]]
    if _short not in DISTRICTS:
        DISTRICTS[_short] = _districts
    else:
        DISTRICTS[_short].extend(d for d in _districts if d not in DISTRICTS[_short])

# 特定地標／園區 → 直接對應縣市（優先比對，避免漏抓）
LOCATION_ALIASES = {
    "竹科":   "新竹市",   # 新竹科學工業園區
    "中科":   "台中市",   # 中部科學工業園區
    "南科":   "台南市",   # 南部科學工業園區
    "工業區": None,       # 太廣，不單獨判定
}

def extract_region(text):
    """從標題或摘要中擷取地區，精確到鄉鎮市區"""
    # 0. 優先比對地標別名
    for alias, region_name in LOCATION_ALIASES.items():
        if alias in text and region_name:
            return region_name

    found_county = None
    found_district = None

    for county, districts in DISTRICTS.items():
        if county in text:
            found_county = county
            for district in districts:
                if district in text:
                    found_district = district
                    break
            if found_district:
                break
                
    if not found_county:
        # 嘗試只找鄉鎮市區 (排除容易混淆的單字)
        exclude_districts = ["東區", "西區", "南區", "北區", "中區", "仁愛", "信義", "和平", "成功", "大同", "中山", "中正", "光復", "復興", "建國"]
        for county, districts in DISTRICTS.items():
            for district in districts:
                if district in exclude_districts:
                    continue
                if district in text:
                    found_county = county
                    found_district = district
                    break
            if found_county:
                break

    if found_county:
        # 補齊縣市後綴
        c_suffix = "市" if found_county in ["基隆", "台北", "新北", "桃園", "台中", "嘉義", "台南", "高雄"] else "縣"
        if found_county == "新竹":
            c_suffix = "市" if found_district in ["東區", "北區", "香山", None] else "縣"
        elif found_county == "嘉義":
            c_suffix = "市" if found_district in ["東區", "西區", None] else "縣"
            
        c_name = found_county + c_suffix
        
        if found_district:
            # 去文章找 district 後面接著的字是不是行政區字尾
            idx = text.find(found_district)
            next_char_idx = idx + len(found_district)
            d_name = found_district
            if next_char_idx < len(text) and text[next_char_idx] in ["區", "鄉", "鎮", "市"]:
                d_name += text[next_char_idx]
            else:
                # 附加上預設的行政區後綴
                if c_suffix == "市":
                    d_name += "區"
                
            return f"{c_name}{d_name}"
        else:
            return c_name
            
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
            if not any(tw_city in title or tw_city in summary for tw_city in TAIWAN_KEYWORDS):
                 continue

            # 方法一：規則層內容判斷
            if not is_realtime_event(title, summary):
                print(f"  ⏭ 規則過濾移除（公視）：{title[:40]}")
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
                if not any(tw_city in title or tw_city in summary for tw_city in TAIWAN_KEYWORDS):
                     continue

                # 方法一：規則層內容判斷
                if not is_realtime_event(title, summary):
                    print(f"  ⏭ 規則過濾移除（Yahoo）：{title[:40]}")
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

            # 方法一：規則層內容判斷（Google RSS 無 summary，僅用 title 判斷）
            if not is_realtime_event(title):
                print(f"  ⏭ 規則過濾移除（Google）：{title[:40]}")
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

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news.db")

def init_db():
    """建立資料庫與資料表（若不存在）"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT,
            region      TEXT,
            title       TEXT,
            summary     TEXT,
            url         TEXT UNIQUE,
            published_at TEXT,
            timestamp   TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_to_db(news_list):
    """將新聞清單寫入 SQLite，url 重複則略過（INSERT OR IGNORE）"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    inserted = 0
    for news in news_list:
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO news
                    (source, region, title, summary, url, published_at, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                news.get("source", ""),
                news.get("region", ""),
                news.get("title", ""),
                news.get("summary", ""),
                news.get("url", ""),
                news.get("published_at", ""),
                news.get("timestamp", ""),
            ))
            if cursor.rowcount > 0:
                inserted += 1
        except Exception as e:
            print(f"寫入 DB 失敗: {e}")
    conn.commit()
    conn.close()
    print(f"💾 已將 {inserted} 筆新資料寫入資料庫: {DB_PATH}")
    return inserted

def run_scraper(enable_llm_structuring: bool = True):
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
             print(f"   摘要: {news['summary'][:50]}...")
         print(f"   連結: {news['url']}\n")

    # LLM 語意結構化（若模組存在且未被禁用）
    if enable_llm_structuring and _LLM_STRUCTURING_ENABLED and all_news:
        print("--------------------------------------------------")
        all_news = structure_news_batch(all_news)
        print("--------------------------------------------------")
    
    return all_news

if __name__ == "__main__":
    # 確保儲存目錄存在
    os.makedirs(os.path.dirname(os.path.abspath(__file__)), exist_ok=True)
    
    results = run_scraper()
    
    # 寫入 SQLite 資料庫
    init_db()
    save_to_db(results)
    
    # 同時也存成 JSON 方便檢視
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scraped_news.json')
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
        
    print(f"📄 JSON 備份已儲存至: {output_path}")
