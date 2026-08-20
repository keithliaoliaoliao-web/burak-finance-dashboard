import json
import os
import re
import urllib.request
import urllib.parse
from datetime import datetime
import email.utils

TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

# Twitter 官方公開 Web 客戶端 Bearer Token
TWITTER_WEB_BEARER = "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

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

def fetch_via_twitter_guest_api(screen_name):
    """第一層：透過 Twitter 官方訪客 Token 取得用戶推文"""
    try:
        print("🔑 正在向 Twitter 官方申請訪客授權金鑰 (Guest Token)...")
        guest_req = urllib.request.Request(
            "https://api.twitter.com/1.1/guest/activate.json",
            headers={
                "Authorization": TWITTER_WEB_BEARER,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Referer": "https://twitter.com/"
            },
            data=b""
        )
        
        with urllib.request.urlopen(guest_req, timeout=10) as resp:
            guest_data = json.loads(resp.read().decode("utf-8"))
            guest_token = guest_data.get("guest_token")

        if not guest_token:
            print("⚠️ 未能取得 Guest Token，切換至備援管道。")
            return []

        print(f"✅ 成功取得 Guest Token，正在讀取 @{screen_name} 推文...")
        
        # 透過 Twitter Syndication 搭配 Guest Token 進行認證請求
        timeline_url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{screen_name}"
        timeline_req = urllib.request.Request(
            timeline_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "x-guest-token": guest_token,
                "Authorization": TWITTER_WEB_BEARER,
                "Referer": f"https://twitter.com/{screen_name}"
            }
        )

        with urllib.request.urlopen(timeline_req, timeout=15) as resp:
            html = resp.read().decode("utf-8")
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html)
            if not match:
                return []

            data = json.loads(match.group(1))
            entries = data.get("props", {}).get("pageProps", {}).get("timeline", {}).get("entries", [])
            
            fetched = []
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
                    fetched.append({
                        "id": tweet_id,
                        "text": text,
                        "created_at": iso_date,
                        "favorite_count": fav_count,
                        "retweet_count": rt_count,
                        "views": int(views) if views else 0,
                        "url": f"https://twitter.com/{screen_name}/status/{tweet_id}"
                    })

            if fetched:
                print(f"✨ 透過官方 Guest API 成功擷取 {len(fetched)} 則推文！")
                return fetched

    except Exception as e:
        print(f"⚠️ 官方 Guest API 請求異常: {e}")

    return []

def fetch_via_public_rss_mirrors(screen_name):
    """第二層：透過活躍的社群 RSS 鏡像節點取得推文"""
    mirrors = [
        f"https://nitter.d420.de/{screen_name}/rss",
        f"https://nitter.privacydev.net/{screen_name}/rss",
        f"https://xcancel.com/{screen_name}/rss",
        f"https://nitter.net/{screen_name}/rss"
    ]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*"
    }

    import xml.etree.ElementTree as ET

    for url in mirrors:
        try:
            print(f"📡 嘗試連接備援鏡像: {url}")
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    continue
                content = resp.read()

            root = ET.fromstring(content)
            items = root.findall("./channel/item")
            if not items:
                continue

            fetched = []
            for item in items:
                title = item.findtext("title") or ""
                desc = item.findtext("description") or ""
                link = item.findtext("link") or ""
                guid = item.findtext("guid") or ""
                pub_date = item.findtext("pubDate") or ""

                text_content = desc or title
                text_clean = re.sub(r'<[^>]+>', '', text_content).strip()

                full_url = link or guid
                tweet_id = ""
                id_match = re.search(r"status/(\d+)", full_url)
                if id_match:
                    tweet_id = id_match.group(1)
                elif guid:
                    tweet_id = guid.split("/")[-1].replace("#m", "").strip()

                iso_date = ""
                try:
                    parsed_tuple = email.utils.parsedate_tz(pub_date)
                    if parsed_tuple:
                        ts = email.utils.mktime_tz(parsed_tuple)
                        iso_date = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    iso_date = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

                if tweet_id and text_clean:
                    fetched.append({
                        "id": tweet_id,
                        "text": text_clean,
                        "created_at": iso_date,
                        "favorite_count": 0,
                        "retweet_count": 0,
                        "views": 0,
                        "url": f"https://twitter.com/{screen_name}/status/{tweet_id}"
                    })

            if fetched:
                print(f"✨ 透過鏡像節點取得 {len(fetched)} 則推文！")
                return fetched

        except Exception as e:
            print(f"  ⚠️ 鏡像 [{url}] 無法連線: {e}")

    return []

def save_merged_tweets(filepath, new_tweets):
    """第三層：比對去重並更新本地資料庫"""
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
    
    # 依序執行雙層擷取機制
    tweets = fetch_via_twitter_guest_api(TARGET_HANDLE)
    if not tweets:
        tweets = fetch_via_public_rss_mirrors(TARGET_HANDLE)
        
    save_merged_tweets(TWEETS_FILE, tweets)
