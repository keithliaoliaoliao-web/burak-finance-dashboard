import json
import os
import re
import urllib.request
from datetime import datetime

TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

def load_existing_tweets(filepath):
    """讀取本地現有推文資料庫"""
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

def fetch_tweets_from_syndication(screen_name):
    """透過 Twitter Syndication 端點擷取用戶推文資料"""
    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{screen_name}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"
    }

    req = urllib.request.Request(url, headers=headers)
    fetched_tweets = []

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8")
            
            # 擷取頁面嵌入的 __NEXT_DATA__ JSON
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html)
            if not match:
                print("⚠️ 未找到 Syndication JSON 結構，跳過本次解析。")
                return []

            data = json.loads(match.group(1))
            entries = data.get("props", {}).get("pageProps", {}).get("timeline", {}).get("entries", [])

            for entry in entries:
                tweet_raw = entry.get("content", {}).get("tweet")
                if not tweet_raw:
                    continue

                tweet_id = str(tweet_raw.get("id_str") or tweet_raw.get("id", ""))
                text = tweet_raw.get("full_text") or tweet_raw.get("text", "")
                created_at = tweet_raw.get("created_at", "")
                
                # 統計指標
                favorite_count = tweet_raw.get("favorite_count", 0)
                retweet_count = tweet_raw.get("retweet_count", 0)
                views = tweet_raw.get("views", {}).get("count") if isinstance(tweet_raw.get("views"), dict) else 0

                # 轉換為標準 ISO 時間
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
                        "favorite_count": favorite_count,
                        "retweet_count": retweet_count,
                        "views": int(views) if views else 0,
                        "url": f"https://twitter.com/{screen_name}/status/{tweet_id}"
                    })

            print(f"📥 從 Syndication 成功抓取 {len(fetched_tweets)} 則最新推文！")
    except Exception as e:
        print(f"⚠️ 抓取推文時發生錯誤: {e}")

    return fetched_tweets

def save_merged_tweets(filepath, new_tweets):
    """將抓取的推文與本地資料庫比對去重後儲存"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    existing_tweets = load_existing_tweets(filepath)

    # 建立 ID 字典避免重複
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
            # 更新或加入最新狀態
            tweets_map[t_id] = t

    merged_list = list(tweets_map.values())

    # 依時間新至舊排序
    merged_list.sort(key=lambda x: str(x.get("created_at", "") or x.get("date", "")), reverse=True)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(merged_list, f, ensure_ascii=False, indent=2)

    print(f"🎉 資料更新完成！本次新增 {added_count} 則推文，目前推文資料庫總計: {len(merged_list)} 則。")

if __name__ == "__main__":
    print(f"🔄 開始抓取 @{TARGET_HANDLE} 的最新推文...")
    recent_tweets = fetch_tweets_from_syndication(TARGET_HANDLE)
    save_merged_tweets(TWEETS_FILE, recent_tweets)
