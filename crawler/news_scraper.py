import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import json
import os
import re

# 關鍵字過濾：空氣品質相關災情
KEYWORDS = ["火災", "火警", "大火", "濃煙","空污", "空汙", "異味", "空氣品質", "失火", "臭味", "工廠大火", "工廠火災", "火燒山"]

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

DISTRICTS = {
    "台北": ["中正", "大同", "中山", "松山", "大安", "萬華", "信義", "士林", "北投", "內湖", "南港", "文山"],
    "新北": ["萬里", "金山", "板橋", "汐止", "深坑", "石碇", "瑞芳", "平溪", "雙溪", "貢寮", "新店", "坪林", "烏來", "永和", "中和", "土城", "三峽", "樹林", "鶯歌", "三重", "新莊", "泰山", "林口", "蘆洲", "五股", "八里", "淡水", "三芝", "石門"],
    "基隆": ["仁愛", "信義", "中正", "中山", "安樂", "暖暖", "七堵"],
    "桃園": ["中壢", "平鎮", "龍潭", "楊梅", "新屋", "觀音", "桃園", "龜山", "八德", "大溪", "復興", "大園", "蘆竹"],
    "新竹": ["東區", "北區", "香山", "竹北", "湖口", "新豐", "新埔", "關西", "芎林", "寶山", "竹東", "五峰", "橫山", "尖石", "北埔", "峨眉"],
    "苗栗": ["竹南", "頭份", "三灣", "南庄", "獅潭", "後龍", "通霄", "苑裡", "苗栗", "造橋", "頭屋", "公館", "大湖", "泰安", "銅鑼", "三義", "西湖", "卓蘭"],
    "台中": ["中區", "東區", "南區", "西區", "北區", "北屯", "西屯", "南屯", "太平", "大里", "霧峰", "烏日", "豐原", "后里", "石岡", "東勢", "和平", "新社", "潭子", "大雅", "神岡", "大肚", "沙鹿", "龍井", "梧棲", "清水", "大甲", "外埔", "大安"],
    "彰化": ["彰化", "芬園", "花壇", "秀水", "鹿港", "福興", "線西", "和美", "伸港", "員林", "社頭", "永靖", "埔心", "溪湖", "大村", "埔鹽", "田中", "北斗", "田尾", "埤頭", "溪州", "竹塘", "二林", "大城", "芳苑", "二水"],
    "南投": ["南投", "中寮", "草屯", "國姓", "埔里", "仁愛", "名間", "集集", "水里", "魚池", "信義", "竹山", "鹿谷"],
    "雲林": ["斗南", "大埤", "虎尾", "土庫", "褒忠", "東勢", "台西", "崙背", "麥寮", "斗六", "林內", "古坑", "莿桐", "西螺", "二崙", "北港", "水林", "口湖", "四湖", "元長"],
    "嘉義": ["東區", "西區", "番路", "梅山", "竹崎", "阿里山", "中埔", "大埔", "水上", "鹿草", "太保", "朴子", "東石", "六腳", "新港", "民雄", "大林", "溪口", "義竹", "布袋"],
    "台南": ["中西", "東區", "南區", "北區", "安平", "安南", "永康", "歸仁", "新化", "左鎮", "玉井", "楠西", "南化", "仁德", "關廟", "龍崎", "官田", "麻豆", "佳里", "西港", "七股", "將軍", "學甲", "北門", "新營", "後壁", "白河", "東山", "六甲", "下營", "柳營", "鹽水", "善化", "大內", "山上", "新市", "安定"],
    "高雄": ["新興", "前金", "苓雅", "鹽埕", "鼓山", "旗津", "前鎮", "三民", "楠梓", "小港", "左營", "仁武", "大社", "岡山", "路竹", "阿蓮", "田寮", "燕巢", "橋頭", "梓官", "彌陀", "永安", "湖內", "鳳山", "大寮", "林園", "鳥松", "大樹", "旗山", "美濃", "六龜", "內門", "杉林", "甲仙", "桃源", "那瑪夏", "茂林", "茄萣"],
    "屏東": ["屏東", "三地門", "霧台", "瑪家", "九如", "里港", "高樹", "鹽埔", "長治", "麟洛", "竹田", "內埔", "萬丹", "潮州", "泰武", "來義", "萬巒", "崁頂", "新埤", "南州", "林邊", "東港", "琉球", "佳冬", "新園", "枋寮", "枋山", "春日", "獅子", "車城", "牡丹", "恆春", "滿州"],
    "宜蘭": ["宜蘭", "頭城", "礁溪", "壯圍", "員山", "羅東", "三星", "大同", "五結", "冬山", "蘇澳", "南澳"],
    "花蓮": ["花蓮", "新城", "秀林", "吉安", "壽豐", "鳳林", "光復", "豐濱", "瑞穗", "萬榮", "玉里", "卓溪", "富里"],
    "台東": ["台東", "綠島", "蘭嶼", "延平", "卑南", "鹿野", "關山", "海端", "池上", "東河", "成功", "長濱", "太麻里", "金峰", "大武", "達仁"],
    "澎湖": ["馬公", "西嶼", "望安", "七美", "白沙", "湖西"],
    "金門": ["金沙", "金湖", "金寧", "金城", "烈嶼", "烏坵"],
    "馬祖": ["南竿", "北竿", "莒光", "東引"]
}

def extract_region(text):
    """從標題或摘要中擷取地區，精確到鄉鎮市區"""
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
