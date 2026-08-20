import json
import os
import re
import time
import urllib.request
import urllib.parse
from datetime import datetime

TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

AUTH_TOKEN = os.environ.get("TWITTER_AUTH_TOKEN", "").strip()
CT0 = os.environ.get("TWITTER_CT0", "").strip()

# Twitter 官方公開 Web Bearer Token
TWITTER_BEARER = "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

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

def get_auth_headers():
    """建立 Twitter 官方 REST API 認證標頭"""
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://x.com/{TARGET_HANDLE}",
        "Authorization": TWITTER_BEARER,
        "Cookie": f"auth_token={AUTH_TOKEN}; ct0={CT0};",
        "x-csrf-token": CT0,
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-client-language": "en"
    }

def parse_tweet_object(tw, screen_name):
    """解析單則推文標準欄位"""
    t_id = str(tw.get("id_str") or tw.get("id", "")).strip()
    text = tw.get("full_text") or tw.get("text", "")
    created_at = tw.get("created_at", "")
    
    fav_count = tw.get("favorite_count", 0)
    rt_count = tw.get("retweet_count", 0)
    views_data = tw.get("views", {})
    views = views_data.get("count", 0) if isinstance(views_data, dict) else (tw.get("views", 0) or 0)

    iso_date = ""
    try:
        dt = datetime.strptime(str(created_at), "%a %b %d %H:%M:%S %z %Y")
        iso_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        iso_date = str(created_at)

    return {
        "id": t_id,
        "text": text.strip(),
        "created_at": iso_date,
        "favorite_count": int(fav_count),
        "retweet_count": int(rt_count),
        "views": int(views) if str(views).isdigit() else 0,
        "url": f"https://twitter.com/{screen_name}/status/{t_id}"
    }

def fetch_timeline_with_pagination(screen_name, max_pages=3):
    """透過官方 REST v1.1 user_timeline 連續翻頁回溯推文"""
    headers = get_auth_headers()
    all_fetched = []
    seen_ids = set()
    max_id = None

    print(f"🚀 開始向 Twitter 官方發起 @{screen_name} 時間軸抓取（預設翻頁: {max_pages} 頁，每頁上限 200 則）...", flush=True)

    for page_idx in range(1, max_pages + 1):
        params = {
            "screen_name": screen_name,
            "count": "200",
            "tweet_mode": "extended",
            "include_rts": "true"
        }
        if max_id:
            params["max_id"] = str(max_id)

        url = f"https://api.twitter.com/1.1/statuses/user_timeline.json?{urllib.parse.urlencode(params)}"
        
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if not isinstance(data, list) or len(data) == 0:
                print(f"  📄 [第 {page_idx} 頁] 未回傳更多推文，結束翻頁。", flush=True)
                break

            page_added = 0
            oldest_id_in_page = None

            for raw_tw in data:
                parsed = parse_tweet_object(raw_tw, screen_name)
                t_id = parsed["id"]
                
                # 記錄本頁最舊的 ID 以便計算下一頁的 max_id
                if t_id.isdigit():
                    t_id_num = int(t_id)
                    if oldest_id_in_page is None or t_id_num < oldest_id_in_page:
                        oldest_id_in_page = t_id_num

                if t_id and parsed["text"] and t_id not in seen_ids:
                    seen_ids.add(t_id)
                    all_fetched.append(parsed)
                    page_added += 1

            print(f"  ✨ [第 {page_idx} 頁] 成功取得 {len(data)} 則原始資料（新增解析 {page_added} 則）！", flush=True)

            # 若沒有抓到新的推文或無法取得更舊的 ID，終止翻頁
            if not oldest_id_in_page or page_added == 0:
                break

            # 下一頁從 (最舊 ID - 1) 開始抓取
            max_id = oldest_id_in_page - 1
            time.sleep(1.5)  # 溫和間隔，避免觸發速率限制

        except Exception as e:
            print(f"  ⚠️ [第 {page_idx} 頁] 請求異常: {e}", flush=True)
            break

    return all_fetched

def fetch_syndication_backup(screen_name):
    """備援管道：官方 Syndication 串流"""
    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{screen_name}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://platform.twitter.com/",
        "Cookie": f"auth_token={AUTH_TOKEN}; ct0={CT0};"
    }

    fetched = []
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as response:
            html = response.read().decode("utf-8")
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html)
            if match:
                data = json.loads(match.group(1))
                entries = data.get("props", {}).get("pageProps", {}).get("timeline", {}).get("entries", [])
                for entry in entries:
                    tw = entry.get("content", {}).get("tweet")
                    if tw:
                        fetched.append(parse_tweet_object(tw, screen_name))
    except Exception:
        pass
    return fetched

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
    if not AUTH_TOKEN or not CT0:
        print("❌ 錯誤：未設定 TWITTER_AUTH_TOKEN 或 TWITTER_CT0 Secrets。", flush=True)
        exit(0)

    # 1. 執行官方 REST API 分頁抓取 (預設連續抓 3 頁，每頁 200 則)
    tweets = fetch_timeline_with_pagination(TARGET_HANDLE, max_pages=3)
    
    # 2. 若 REST API 未回傳，自動切換至備援 Syndication
    if not tweets:
        print("ℹ️ 正在啟用備援通道...", flush=True)
        tweets = fetch_syndication_backup(TARGET_HANDLE)

    save_merged_tweets(TWEETS_FILE, tweets)
    print("✅ 任務全部完成。", flush=True)
