import json
import os
import re
import urllib.request
import urllib.parse
from datetime import datetime

TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

AUTH_TOKEN = os.environ.get("TWITTER_AUTH_TOKEN", "").strip()
CT0 = os.environ.get("TWITTER_CT0", "").strip()

# 常用美股熱門標的清單（用以深度回溯歷史推文）
POPULAR_TICKERS = [
    "$NVDA", "$TSM", "$AMD", "$AAPL", "$MSFT", "$GOOGL", "$AMZN", "$META",
    "$AVGO", "$MRVL", "$AAOI", "$LITE", "$COHR", "$AXTI", "$SIVE", "$NBIS",
    "$PLTR", "$SMCI", "$CRWD", "$PANW", "$ARM", "$QCOM", "$MU", "$INTC"
]

def load_existing_tweets(filepath):
    """讀取本地現有推文資料庫"""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"⚠️ 讀取現有推文失敗: {e}", flush=True)
        return []

def get_auth_headers():
    """建立 Twitter 官方認證請求標頭"""
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://platform.twitter.com/",
        "Cookie": f"auth_token={AUTH_TOKEN}; ct0={CT0};",
        "x-csrf-token": CT0
    }

def fetch_latest_stream(screen_name):
    """核心一：抓取最新首頁推文串流 (約 20 則)"""
    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{screen_name}"
    headers = get_auth_headers()
    fetched = []

    try:
        print(f"📡 [核心 1] 正在抓取 @{screen_name} 最新發布推文...", flush=True)
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8")
            
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html)
            if not match:
                return []

            data = json.loads(match.group(1))
            entries = data.get("props", {}).get("pageProps", {}).get("timeline", {}).get("entries", [])
            
            for entry in entries:
                tweet_raw = entry.get("content", {}).get("tweet")
                if not tweet_raw:
                    continue

                tweet_id = str(tweet_raw.get("id_str") or tweet_raw.get("id", "")).strip()
                text = tweet_raw.get("full_text") or tweet_raw.get("text", "")
                created_at = tweet_raw.get("created_at", "")
                fav_count = tweet_raw.get("favorite_count", 0)
                rt_count = tweet_raw.get("retweet_count", 0)
                views = tweet_raw.get("views", {}).get("count") if isinstance(tweet_raw.get("views"), dict) else 0

                iso_date = ""
                try:
                    dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
                    iso_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    iso_date = str(created_at)

                if tweet_id and text:
                    fetched.append({
                        "id": tweet_id,
                        "text": text,
                        "created_at": iso_date,
                        "favorite_count": int(fav_count),
                        "retweet_count": int(rt_count),
                        "views": int(views) if views else 0,
                        "url": f"https://twitter.com/{screen_name}/status/{tweet_id}"
                    })

            print(f"  ✨ 最新串流順利取得 {len(fetched)} 則推文！", flush=True)

    except Exception as e:
        print(f"  ⚠️ 最新串流請求異常: {e}", flush=True)

    return fetched

def fetch_historical_ticker_tweets(screen_name):
    """核心二：透過個股關鍵字回溯歷史重要推文"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    
    import xml.etree.ElementTree as ET
    historical_fetched = []
    seen_ids = set()

    print(f"🔍 [核心 2] 開始進行個股標的歷史回溯搜尋...", flush=True)
    
    for ticker in POPULAR_TICKERS:
        try:
            q = urllib.parse.quote(f"site:x.com/{screen_name} {ticker}")
            feed_url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
            
            req = urllib.request.Request(feed_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                xml_data = resp.read()

            root = ET.fromstring(xml_data)
            items = root.findall("./channel/item")
            
            for item in items:
                title = item.findtext("title") or ""
                clean_title = re.sub(r' - [^-]+$', '', title).strip()
                if not clean_title or ticker.lower() not in clean_title.lower():
                    continue

                t_id = str(abs(hash(clean_title)))[:18]
                if t_id not in seen_ids:
                    seen_ids.add(t_id)
                    historical_fetched.append({
                        "id": t_id,
                        "text": clean_title,
                        "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "favorite_count": 0,
                        "retweet_count": 0,
                        "views": 0,
                        "url": f"https://twitter.com/{screen_name}"
                    })

        except Exception:
            continue

    print(f"  ✨ 個股回溯共挖掘到 {len(historical_fetched)} 則歷史相關推文！", flush=True)
    return historical_fetched

def save_merged_tweets(filepath, new_tweets):
    """與現有資料庫合併、去重並由新到舊排序儲存"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    existing_tweets = load_existing_tweets(filepath)

    tweets_map = {str(t.get("id", "")).strip(): t for t in existing_tweets if t.get("id")}

    added_count = 0
    for t in new_tweets:
        t_id = str(t.get("id", "")).strip()
        if t_id:
            if t_id not in tweets_map:
                added_count += 1
            # 更新或加入最新資料
            tweets_map[t_id] = t

    merged_list = list(tweets_map.values())
    merged_list.sort(key=lambda x: str(x.get("created_at", "") or x.get("date", "")), reverse=True)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(merged_list, f, ensure_ascii=False, indent=2)

    print(f"📊 [結算報告] 本次新增推文: {added_count} 則 | 目前推文資料庫總數: {len(merged_list)} 則", flush=True)

if __name__ == "__main__":
    print(f"🚀 啟動 @{TARGET_HANDLE} 推文雙核心收集引擎...", flush=True)
    
    # 1. 抓取最新串流
    latest_tweets = fetch_latest_stream(TARGET_HANDLE)
    
    # 2. 挖掘歷史個股推文
    historical_tweets = fetch_historical_ticker_tweets(TARGET_HANDLE)
    
    # 3. 合併儲存
    all_collected = latest_tweets + historical_tweets
    save_merged_tweets(TWEETS_FILE, all_collected)
    print("✅ 任務完成。", flush=True)
