import json
import os
import re
import urllib.request
import urllib.parse
from datetime import datetime
import xml.etree.ElementTree as ET

TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

# 熱門標的擴展查詢清單，用以擴大搜尋深度與廣度
TICKER_QUERIES = [
    "", "$NVDA", "$TSM", "$AMD", "$AAPL", "$MSFT", "$GOOGL", "$AMZN", "$META",
    "$AVGO", "$MRVL", "$AAOI", "$LITE", "$COHR", "$AXTI", "$SIVE", "$NBIS",
    "$PLTR", "$SMCI", "$CRWD", "$PANW", "$ARM", "$QCOM", "$MU", "$INTC",
    "bullish", "bearish", "target", "earnings", "breakout", "support"
]

def load_existing_tweets(filepath):
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                for key in ["tweets", "data", "statuses", "results"]:
                    if key in data and isinstance(data[key], list):
                        return data[key]
                return list(data.values())
            return []
    except Exception as e:
        print(f"⚠️ 讀取現有推文失敗: {e}")
        return []

def fetch_from_google_rss_query(query_str):
    """向 Google 搜尋索引發送特定關鍵字查詢"""
    encoded_q = urllib.parse.quote(query_str)
    url = f"https://news.google.com/rss/search?q={encoded_q}&hl=en-US&gl=US&ceid=US:en"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    results = []
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read()

        root = ET.fromstring(content)
        for item in root.findall("./channel/item"):
            title = item.findtext("title") or ""
            pub_date = item.findtext("pubDate") or ""

            # 移除 Google 來源後綴
            clean_text = re.sub(r' - [^-]+$', '', title).strip()
            if not clean_text:
                continue

            # 以內容雜湊產生唯一 ID
            tweet_id = str(abs(hash(clean_text)))[:18]

            results.append({
                "id": tweet_id,
                "text": clean_text,
                "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "favorite_count": 0,
                "retweet_count": 0,
                "views": 0,
                "url": f"https://twitter.com/{TARGET_HANDLE}"
            })
    except Exception:
        pass
    return results

def run_deep_fetch(screen_name, target_total=500):
    """多維度廣度搜尋推文"""
    print(f"🔄 開始針對 @{screen_name} 執行深度推文爬取（目標上限: {target_total} 則）...")
    
    collected_map = {}
    
    for sub in TICKER_QUERIES:
        if len(collected_map) >= target_total:
            break
            
        q = f"site:x.com/{screen_name} {sub}".strip()
        batch = fetch_from_google_rss_query(q)
        
        new_in_batch = 0
        for item in batch:
            t_id = item["id"]
            if t_id not in collected_map:
                collected_map[t_id] = item
                new_in_batch += 1
                
        print(f"  🔍 查詢 [{sub or '全部'}]：新增 {new_in_batch} 則（目前累計抓取: {len(collected_map)} 則）")

    return list(collected_map.values())

def save_merged_tweets(filepath, new_tweets):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    existing_tweets = load_existing_tweets(filepath)

    tweets_map = {str(t.get("id", "")).strip(): t for t in existing_tweets if t.get("id")}

    added_count = 0
    for t in new_tweets:
        t_id = str(t.get("id", "")).strip()
        if t_id and t_id not in tweets_map:
            tweets_map[t_id] = t
            added_count += 1

    merged_list = list(tweets_map.values())
    merged_list.sort(key=lambda x: str(x.get("created_at", "") or x.get("date", "")), reverse=True)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(merged_list, f, ensure_ascii=False, indent=2)

    print(f"🎉 資料更新完成！本次新增 {added_count} 則推文，目前推文資料庫總計: {len(merged_list)} 則。")

if __name__ == "__main__":
    target_limit = int(os.environ.get("FETCH_TARGET", 300))
    fetched_tweets = run_deep_fetch(TARGET_HANDLE, target_total=target_limit)
    save_merged_tweets(TWEETS_FILE, fetched_tweets)
