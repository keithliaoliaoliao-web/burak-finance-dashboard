import json
import os
import re
import urllib.request
from datetime import datetime

TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

AUTH_TOKEN = os.environ.get("TWITTER_AUTH_TOKEN", "").strip()
CT0 = os.environ.get("TWITTER_CT0", "").strip()

def load_existing_tweets(filepath):
    """讀取本地現有的推文資料庫"""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"⚠️ 讀取現有推文失敗: {e}", flush=True)
        return []

def fetch_tweets_syndication(screen_name):
    """透過 Twitter 官方 Syndication 串流抓取最新推文"""
    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{screen_name}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://platform.twitter.com/"
    }

    # 若有設定 Cookie Secrets 則注入驗證標頭
    if AUTH_TOKEN and CT0:
        headers["Cookie"] = f"auth_token={AUTH_TOKEN}; ct0={CT0};"
        headers["x-csrf-token"] = CT0

    fetched_tweets = []
    print(f"📡 正在連線 Twitter 官方串流端點抓取 @{screen_name} 最新推文...", flush=True)

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8")

            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html)
            if not match:
                print("⚠️ 未能從頁面中解析出結構化 JSON 資料區塊。", flush=True)
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
                views_data = tweet_raw.get("views", {})
                views = views_data.get("count", 0) if isinstance(views_data, dict) else (tweet_raw.get("views", 0) or 0)

                iso_date = ""
                try:
                    dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
                    iso_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    iso_date = str(created_at)

                if tweet_id and text:
                    fetched_tweets.append({
                        "id": tweet_id,
                        "text": text.strip(),
                        "created_at": iso_date,
                        "favorite_count": int(fav_count),
                        "retweet_count": int(rt_count),
                        "views": int(views) if str(views).isdigit() else 0,
                        "url": f"https://twitter.com/{screen_name}/status/{tweet_id}"
                    })

            print(f"✨ 順利從官方串流解析出 {len(fetched_tweets)} 則最新推文！", flush=True)

    except Exception as e:
        print(f"⚠️ 串流連線發生異常: {e}", flush=True)

    return fetched_tweets

def save_merged_tweets(filepath, new_tweets):
    """將新推文與現有資料庫合併去重並儲存"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    existing_tweets = load_existing_tweets(filepath)

    tweets_map = {str(t.get("id", "")).strip(): t for t in existing_tweets if t.get("id")}

    added_count = 0
    for t in new_tweets:
        t_id = str(t.get("id", "")).strip()
        if t_id:
            if t_id not in tweets_map:
                added_count += 1
            # 更新為最新數據
            tweets_map[t_id] = t

    merged_list = list(tweets_map.values())
    merged_list.sort(key=lambda x: str(x.get("created_at", "") or x.get("date", "")), reverse=True)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(merged_list, f, ensure_ascii=False, indent=2)

    print(f"📊 [結算報告] 本次新增推文: {added_count} 則 | 目前推文資料庫總數: {len(merged_list)} 則", flush=True)

if __name__ == "__main__":
    print(f"🚀 開始執行 @{TARGET_HANDLE} 推文擷取流程...", flush=True)
    tweets = fetch_tweets_syndication(TARGET_HANDLE)
    save_merged_tweets(TWEETS_FILE, tweets)
    print("✅ 任務執行完成。", flush=True)
