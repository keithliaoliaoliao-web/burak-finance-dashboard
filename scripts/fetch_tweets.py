import os
import json
import re
import urllib.request
import urllib.error
from datetime import datetime

TARGET_HANDLE = "burak_finance"
TARGET_USER_ID = "371876593"
OUTPUT_FILE = "data/tweets.json"
TWITTER_EPOCH = 1288834974657

# 由環境變數注入登入憑證 (可由 GitHub Secrets 提供)
TWITTER_AUTH_TOKEN = os.environ.get("TWITTER_AUTH_TOKEN", "").strip()
TWITTER_CT0 = os.environ.get("TWITTER_CT0", "").strip()

def snowflake_to_timestamp(tweet_id_str):
    try:
        t_id = int(str(tweet_id_str).strip())
        return (t_id >> 22) + TWITTER_EPOCH
    except Exception:
        return 0

def build_headers():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://twitter.com/{TARGET_HANDLE}",
    }
    # 若存在登入 Cookie 則注入，徹底繞過 429
    if TWITTER_AUTH_TOKEN:
        cookie_parts = [f"auth_token={TWITTER_AUTH_TOKEN}"]
        if TWITTER_CT0:
            cookie_parts.append(f"ct0={TWITTER_CT0}")
            headers["x-csrf-token"] = TWITTER_CT0
        headers["Cookie"] = "; ".join(cookie_parts)
    return headers

def safe_fetch_json(url):
    headers = build_headers()
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            if res.status != 200:
                return None
            raw = res.read().decode("utf-8")
            if len(raw) < 10 or not raw.strip().startswith(("{", "[")):
                return None
            return json.loads(raw)
    except Exception as e:
        print(f"⚠️ 請求失敗 ({url}): {e}")
        return None

def extract_tweet_id(item):
    for k in ["id", "id_str", "tweet_id", "tweetId", "rest_id"]:
        if k in item and item[k]:
            return str(item[k]).strip()
    return ""

def load_existing_tweets(filepath):
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def fetch_syndication_stream():
    """抓取 Twitter 官方 Syndication 串流"""
    print(f"📡 正在抓取 @{TARGET_HANDLE} 最新推文串流...", flush=True)
    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{TARGET_HANDLE}"
    headers = build_headers()
    req = urllib.request.Request(url, headers=headers)
    
    tweets = []
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            html = res.read().decode("utf-8")
            # 尋找內嵌的 __NEXT_DATA__ JSON 區塊
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html)
            if match:
                data = json.loads(match.group(1))
                timeline_entries = data.get("props", {}).get("pageProps", {}).get("timeline", {}).get("entries", [])
                for entry in timeline_entries:
                    content = entry.get("content", {}).get("tweet", {})
                    if content:
                        t_id = extract_tweet_id(content)
                        text = content.get("text", "")
                        created_at = content.get("created_at", "")
                        if t_id and text:
                            tweets.append({
                                "id": t_id,
                                "text": text,
                                "created_at": created_at,
                                "favorite_count": content.get("favorite_count", 0),
                                "retweet_count": content.get("retweet_count", 0),
                                "url": f"https://twitter.com/{TARGET_HANDLE}/status/{t_id}"
                            })
    except Exception as e:
        print(f"⚠️ Syndication 抓取錯誤: {e}", flush=True)
    return tweets

def merge_and_compare_sources(existing_tweets, new_tweets):
    """歷史資料融合去重，確保推文依 Snowflake UTC 時間嚴格由新到舊排序"""
    tweet_dict = {}
    
    # 載入既有本地資料
    for t in existing_tweets:
        t_id = extract_tweet_id(t)
        if t_id:
            tweet_dict[t_id] = t

    # 融合新抓取資料
    added_count = 0
    for t in new_tweets:
        t_id = extract_tweet_id(t)
        if t_id:
            if t_id not in tweet_dict:
                added_count += 1
            tweet_dict[t_id] = t

    merged = list(tweet_dict.values())
    merged.sort(key=lambda x: snowflake_to_timestamp(extract_tweet_id(x)), reverse=True)
    
    print(f"✅ 資料融合完成：既有 {len(existing_tweets)} 則，新增 {added_count} 則，當前資料庫共 {len(merged)} 則。")
    return merged

if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    existing = load_existing_tweets(OUTPUT_FILE)
    incoming = fetch_syndication_stream()
    
    # 防清空熔斷機制：若連線失敗抓取數為 0，嚴禁寫入空檔
    if not incoming and existing:
        print("⚠️ 本次連線未能取得新貼文，觸發熔斷防清空機制，保留既有資料庫。")
    else:
        final_tweets = merge_and_compare_sources(existing, incoming)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(final_tweets, f, ensure_ascii=False, indent=2)
        print(f"💾 推文已成功寫入 {OUTPUT_FILE}")
