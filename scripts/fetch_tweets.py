import json
import os
import re
import urllib.request
import urllib.parse
from datetime import datetime
import xml.etree.ElementTree as ET

TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

AUTH_TOKEN = os.environ.get("TWITTER_AUTH_TOKEN", "").strip()
CT0 = os.environ.get("TWITTER_CT0", "").strip()

# 常用美股熱門標的清單（用以全面擴展搜尋歷史推文）
POPULAR_TICKERS = [
    "$NVDA", "$TSM", "$AMD", "$AAPL", "$MSFT", "$GOOGL", "$AMZN", "$META",
    "$AVGO", "$MRVL", "$AAOI", "$LITE", "$COHR", "$AXTI", "$SIVE", "$NBIS",
    "$PLTR", "$SMCI", "$CRWD", "$PANW", "$ARM", "$QCOM", "$MU", "$INTC",
    "$COIN", "$MSTR", "$HOOD", "$APP", "$BABA", "$PDD", "$NIO", "$TSLA"
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
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://twitter.com/",
        "authorization": "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA",
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session"
    }
    if AUTH_TOKEN and CT0:
        headers["Cookie"] = f"auth_token={AUTH_TOKEN}; ct0={CT0};"
        headers["x-csrf-token"] = CT0
    return headers

def extract_tweets_recursively(data, screen_name):
    """全能遞迴解析器：從 Twitter 任何 JSON 結構中自動挖掘所有推文"""
    extracted = []
    seen = set()

    def recurse(node):
        if isinstance(node, dict):
            # 檢查是否為推文節點
            legacy = node.get("legacy") if isinstance(node.get("legacy"), dict) else node
            text = legacy.get("full_text") or legacy.get("text") or node.get("full_text") or node.get("text")
            t_id = legacy.get("id_str") or legacy.get("id") or node.get("id_str") or node.get("id")
            created_at = legacy.get("created_at") or node.get("created_at")

            if text and t_id and created_at and isinstance(text, str):
                t_id_str = str(t_id).strip()
                if t_id_str not in seen and len(text.strip()) > 0:
                    seen.add(t_id_str)
                    
                    fav_count = legacy.get("favorite_count", 0) or node.get("favorite_count", 0) or 0
                    rt_count = legacy.get("retweet_count", 0) or node.get("retweet_count", 0) or 0
                    views_data = node.get("views") or legacy.get("views") or {}
                    views = views_data.get("count", 0) if isinstance(views_data, dict) else (node.get("view_count", 0) or 0)

                    iso_date = ""
                    try:
                        dt = datetime.strptime(str(created_at), "%a %b %d %H:%M:%S %z %Y")
                        iso_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    except Exception:
                        iso_date = str(created_at)

                    extracted.append({
                        "id": t_id_str,
                        "text": text.strip(),
                        "created_at": iso_date,
                        "favorite_count": int(fav_count),
                        "retweet_count": int(rt_count),
                        "views": int(views),
                        "url": f"https://twitter.com/{screen_name}/status/{t_id_str}"
                    })

            for val in node.values():
                recurse(val)
        elif isinstance(node, list):
            for item in node:
                recurse(item)

    recurse(data)
    return extracted

def fetch_syndication_stream(screen_name):
    """管道一：抓取官方首頁即時串流"""
    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{screen_name}"
    headers = get_auth_headers()
    headers["Referer"] = "https://platform.twitter.com/"

    print(f"📡 [管道 1] 正在讀取 @{screen_name} 首頁即時推文串流...", flush=True)
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8")
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html)
            if match:
                data = json.loads(match.group(1))
                tweets = extract_tweets_recursively(data, screen_name)
                print(f"  ✨ [管道 1 成功] 取得 {len(tweets)} 則最新推文！", flush=True)
                return tweets
    except Exception as e:
        print(f"  ⚠️ [管道 1 異常]: {e}", flush=True)
    return []

def fetch_adaptive_search_stream(screen_name, query_text=""):
    """管道二：官方認證 Adaptive Search 深度搜尋串流"""
    query = f"from:{screen_name} {query_text}".strip()
    encoded_q = urllib.parse.quote(query)
    url = f"https://api.twitter.com/2/search/adaptive.json?q={encoded_q}&count=50&tweet_mode=extended"
    headers = get_auth_headers()

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as response:
            data = json.loads(response.read().decode("utf-8"))
            return extract_tweets_recursively(data, screen_name)
    except Exception:
        return []

def fetch_historical_ticker_rss(screen_name):
    """管道三：多標的歷史 RSS 廣度補充"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    rss_fetched = []
    seen = set()

    for ticker in POPULAR_TICKERS:
        try:
            q = urllib.parse.quote(f"site:x.com/{screen_name} {ticker}")
            feed_url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
            
            req = urllib.request.Request(feed_url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                xml_data = resp.read()

            root = ET.fromstring(xml_data)
            for item in root.findall("./channel/item"):
                title = item.findtext("title") or ""
                clean_title = re.sub(r' - [^-]+$', '', title).strip()
                if not clean_title or ticker.lower() not in clean_title.lower():
                    continue

                t_id = str(abs(hash(clean_title)))[:18]
                if t_id not in seen:
                    seen.add(t_id)
                    rss_fetched.append({
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

    return rss_fetched

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
            # 覆蓋更新最新數據
            tweets_map[t_id] = t

    merged_list = list(tweets_map.values())
    merged_list.sort(key=lambda x: str(x.get("created_at", "") or x.get("date", "")), reverse=True)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(merged_list, f, ensure_ascii=False, indent=2)

    print(f"📊 [結算報告] 本次新增推文: {added_count} 則 | 目前推文資料庫總數: {len(merged_list)} 則", flush=True)

if __name__ == "__main__":
    print(f"🚀 啟動 @{TARGET_HANDLE} 深度推文收集引擎...", flush=True)
    all_collected = []

    # 1. 抓取首頁最新即時串流
    stream_tweets = fetch_syndication_stream(TARGET_HANDLE)
    all_collected.extend(stream_tweets)

    # 2. 官方認證搜尋串流
    print(f"🔍 [管道 2] 正在向 Twitter 官方發送深度時間軸搜尋...", flush=True)
    base_search_tweets = fetch_adaptive_search_stream(TARGET_HANDLE, "")
    print(f"  ✨ [管道 2 成功] 搜尋取得 {len(base_search_tweets)} 則推文！", flush=True)
    all_collected.extend(base_search_tweets)

    # 3. 針對美股重點標的發起多維度檢索
    print(f"🔍 [管道 3] 正在針對熱門標的進行深入挖掘...", flush=True)
    ticker_tweets_count = 0
    for tk in POPULAR_TICKERS[:12]:
        tk_tweets = fetch_adaptive_search_stream(TARGET_HANDLE, tk)
        ticker_tweets_count += len(tk_tweets)
        all_collected.extend(tk_tweets)
    print(f"  ✨ [管道 3 成功] 個股檢索共挖掘出 {ticker_tweets_count} 則相關推文！", flush=True)

    # 4. RSS 廣度補充
    rss_tweets = fetch_historical_ticker_rss(TARGET_HANDLE)
    all_collected.extend(rss_tweets)

    # 5. 合併寫入
    save_merged_tweets(TWEETS_FILE, all_collected)
    print("✅ 任務全部完成。", flush=True)
