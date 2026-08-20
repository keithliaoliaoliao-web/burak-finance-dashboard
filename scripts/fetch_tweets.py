import json
import os
import re
import urllib.request
import urllib.parse
from datetime import datetime

TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

def load_existing_tweets(filepath):
    """讀取本地現存的推文資料庫"""
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

def fetch_via_allorigins_proxy(screen_name):
    """方法一：透過 AllOrigins 雲端中繼代理繞過機房 IP 限制"""
    target_url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{screen_name}"
    proxy_url = f"https://api.allorigins.win/raw?url={urllib.parse.quote(target_url)}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    try:
        print(f"📡 正在透過雲端中繼代理連線 Syndication 端點...")
        req = urllib.request.Request(proxy_url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as response:
            html = response.read().decode("utf-8")
            
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html)
            if not match:
                print("  ℹ️ 未在代理回應中找到結構化 JSON，嘗試備援管道...")
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
                print(f"✨ 成功透過中繼代理取得 {len(fetched_tweets)} 則推文！")
                return fetched_tweets

    except Exception as e:
        print(f"  ⚠️ 代理連線失敗: {e}")

    return []

def fetch_via_jina_reader(screen_name):
    """方法二：透過 Jina AI 雲端無頭瀏覽器代理擷取動態內容"""
    url = f"https://r.jina.ai/https://x.com/{screen_name}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        print(f"📡 正在透過 Jina 雲端瀏覽器渲染 X 頁面...")
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=25) as response:
            content = response.read().decode("utf-8")

        # 解析 Markdown 區塊中的推文連結與內容
        tweet_blocks = re.findall(r'https://x\.com/' + screen_name + r'/status/(\d+)', content)
        unique_ids = list(dict.fromkeys(tweet_blocks))

        fetched_tweets = []
        lines = content.split('\n')
        
        for t_id in unique_ids:
            # 尋找該 ID 關聯的段落文字
            related_text = ""
            for idx, line in enumerate(lines):
                if t_id in line:
                    # 向上擷取上下文
                    chunk = lines[max(0, idx-8):min(len(lines), idx+4)]
                    filtered = [l.strip() for l in chunk if l.strip() and not l.startswith('http') and not l.startswith('[')]
                    if filtered:
                        related_text = " ".join(filtered)
                    break
            
            if not related_text:
                related_text = f"來自 @{screen_name} 的即時推文 (ID: {t_id})"

            fetched_tweets.append({
                "id": t_id,
                "text": related_text,
                "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "favorite_count": 0,
                "retweet_count": 0,
                "views": 0,
                "url": f"https://twitter.com/{screen_name}/status/{t_id}"
            })

        if fetched_tweets:
            print(f"✨ 成功透過 Jina 渲染器取得 {len(fetched_tweets)} 則推文！")
            return fetched_tweets

    except Exception as e:
        print(f"  ⚠️ Jina 渲染器連線失敗: {e}")

    return []

def save_merged_tweets(filepath, new_tweets):
    """與本地現有資料庫合併去重並儲存"""
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
            # 覆蓋更新最新資料
            tweets_map[t_id] = t

    merged_list = list(tweets_map.values())
    merged_list.sort(key=lambda x: str(x.get("created_at", "") or x.get("date", "")), reverse=True)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(merged_list, f, ensure_ascii=False, indent=2)

    print(f"🎉 資料更新完成！本次新增 {added_count} 則推文，目前推文資料庫總計: {len(merged_list)} 則。")

if __name__ == "__main__":
    print(f"🔄 開始雲端抓取 @{TARGET_HANDLE} 的最新推文...")
    # 優先嘗試中繼代理，若無結果則自動切換至無頭瀏覽器代理
    recent_tweets = fetch_via_allorigins_proxy(TARGET_HANDLE)
    if not recent_tweets:
        recent_tweets = fetch_via_jina_reader(TARGET_HANDLE)
        
    save_merged_tweets(TWEETS_FILE, recent_tweets)
