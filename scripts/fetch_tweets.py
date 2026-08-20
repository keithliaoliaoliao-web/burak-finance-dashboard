import json
import os
import re
import urllib.request
import urllib.parse
from datetime import datetime
import xml.etree.ElementTree as ET

TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

def load_existing_tweets(filepath):
    """讀取現有推文資料庫"""
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

def fetch_via_clean_syndication(screen_name):
    """通道一：Twitter 官方純淨 Syndication 端點 (不帶 Token，模擬標準嵌入組件)"""
    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{screen_name}?showReplies=true"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://platform.twitter.com/",
        "Sec-Fetch-Dest": "iframe",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site"
    }

    try:
        print(f"📡 [通道 1] 正在連線 Twitter 官方 Syndication 串流...")
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8")

            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html)
            if not match:
                print("  ℹ️ Syndication 回應未包含 JSON 結構，嘗試下一個通道...")
                return []

            data = json.loads(match.group(1))
            entries = data.get("props", {}).get("pageProps", {}).get("timeline", {}).get("entries", [])
            
            fetched_tweets = []
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
                    iso_date = created_at

                if tweet_id and text:
                    fetched_tweets.append({
                        "id": tweet_id,
                        "text": text,
                        "created_at": iso_date,
                        "favorite_count": fav_count,
                        "retweet_count": rt_count,
                        "views": int(views) if views else 0,
                        "url": f"https://twitter.com/{screen_name}/status/{tweet_id}"
                    })

            if fetched_tweets:
                print(f"  ✨ 成功從 Syndication 擷取 {len(fetched_tweets)} 則推文！")
                return fetched_tweets

    except Exception as e:
        print(f"  ⚠️ [通道 1] 連線失敗: {e}")

    return []

def fetch_via_sotwe_api(screen_name):
    """通道二：Sotwe 公開社交資料 API"""
    url = f"https://api.sotwe.com/v3/user/{screen_name}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": f"https://www.sotwe.com/{screen_name}",
        "Accept": "application/json"
    }

    try:
        print(f"📡 [通道 2] 正在連線 Sotwe API 端點...")
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))

            posts = data.get("data", []) or data.get("posts", [])
            if not posts and isinstance(data, dict):
                posts = data.get("user", {}).get("posts", [])

            fetched_tweets = []
            for post in posts:
                tweet_id = str(post.get("id") or post.get("id_str") or "").strip()
                text = post.get("text") or post.get("content") or ""
                created_at = post.get("createdAt") or post.get("created_at") or ""
                fav_count = post.get("likeCount", 0) or post.get("favorite_count", 0)
                rt_count = post.get("retweetCount", 0) or post.get("retweet_count", 0)

                iso_date = ""
                if isinstance(created_at, (int, float)):
                    dt = datetime.utcfromtimestamp(created_at / 1000.0 if created_at > 1e11 else created_at)
                    iso_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                else:
                    iso_date = str(created_at)

                if tweet_id and text:
                    fetched_tweets.append({
                        "id": tweet_id,
                        "text": text,
                        "created_at": iso_date,
                        "favorite_count": fav_count,
                        "retweet_count": rt_count,
                        "views": 0,
                        "url": f"https://twitter.com/{screen_name}/status/{tweet_id}"
                    })

            if fetched_tweets:
                print(f"  ✨ 成功從 Sotwe API 擷取 {len(fetched_tweets)} 則推文！")
                return fetched_tweets

    except Exception as e:
        print(f"  ⚠️ [通道 2] 連線失敗: {e}")

    return []

def fetch_via_google_feed(screen_name):
    """通道三：Google 搜尋索引 RSS 備援"""
    query = urllib.parse.quote(f"site:x.com/{screen_name} OR site:twitter.com/{screen_name}")
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    try:
        print(f"📡 [通道 3] 正在透過 Google 索引備援擷取推文...")
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as response:
            content = response.read()

        root = ET.fromstring(content)
        items = root.findall("./channel/item")
        
        fetched_tweets = []
        for item in items:
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            pub_date = item.findtext("pubDate") or ""
            
            # 從標題移除來源後綴
            clean_title = re.sub(r' - [^-]+$', '', title).strip()
            
            tweet_id = str(abs(hash(clean_title)))[:18]
            
            if clean_title:
                fetched_tweets.append({
                    "id": tweet_id,
                    "text": clean_title,
                    "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "favorite_count": 0,
                    "retweet_count": 0,
                    "views": 0,
                    "url": f"https://twitter.com/{screen_name}"
                })

        if fetched_tweets:
            print(f"  ✨ 成功從 Google 索引擷取 {len(fetched_tweets)} 則公開推文內容！")
            return fetched_tweets

    except Exception as e:
        print(f"  ⚠️ [通道 3] 連線失敗: {e}")

    return []

def save_merged_tweets(filepath, new_tweets):
    """將新抓取的推文與現有資料庫合併去重"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    existing_tweets = load_existing_tweets(filepath)

    tweets_map = {}
    for t in existing_tweets:
        t_id = str(t.get("id", "")).strip()
        if t_id:
            tweets_map[t_id] = t

    added_count = 0
    for t in new_tweets:
        t_id = str(t.get("id", "")).strip()
        if t_id:
            if t_id not in tweets_map:
                added_count += 1
            tweets_map[t_id] = t

    merged_list = list(tweets_map.values())
    merged_list.sort(key=lambda x: str(x.get("created_at", "") or x.get("date", "")), reverse=True)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(merged_list, f, ensure_ascii=False, indent=2)

    print(f"🎉 資料更新完成！本次新增 {added_count} 則推文，目前推文資料庫總計: {len(merged_list)} 則。")

if __name__ == "__main__":
    print(f"🔄 開始抓取 @{TARGET_HANDLE} 的最新推文...")
    
    # 依序嘗試三個通道
    results = fetch_via_clean_syndication(TARGET_HANDLE)
    if not results:
        results = fetch_via_sotwe_api(TARGET_HANDLE)
    if not results:
        results = fetch_via_google_feed(TARGET_HANDLE)
        
    save_merged_tweets(TWEETS_FILE, results)
