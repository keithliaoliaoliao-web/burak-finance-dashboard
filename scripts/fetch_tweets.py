import json
import os
import re
import time
import urllib.request
import urllib.parse
from datetime import datetime
import xml.etree.ElementTree as ET

# 目標推特帳號：Burak Finance
TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

AUTH_TOKEN = os.environ.get("TWITTER_AUTH_TOKEN", "").strip()
CT0 = os.environ.get("TWITTER_CT0", "").strip()

def log(message):
    """即時輸出日誌至 GitHub Actions 控制台"""
    print(message, flush=True)

def parse_to_iso_date(raw_date):
    """將各類日期格式嚴格轉換為標準 ISO 8601，空值回傳 1970 年（沉底）"""
    if not raw_date:
        return "1970-01-01T00:00:00Z"
    
    s = str(raw_date).strip()
    if not s or s.lower() in ("none", "null"):
        return "1970-01-01T00:00:00Z"

    # Twitter 官方格式: "Thu Aug 20 08:30:00 +0000 2026"
    try:
        dt = datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass

    # 標準 ISO 格式: "2026-08-20T08:30:00Z"
    if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', s):
        return s[:19] + "Z"

    # 常見格式: "2026-08-20 08:30:00" 或 "2026/08/20 08:30"
    try:
        clean = s.replace("/", "-")
        if len(clean) >= 19:
            dt = datetime.strptime(clean[:19], "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        elif len(clean) == 16:
            dt = datetime.strptime(clean, "%Y-%m-%d %H:%M")
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass

    # 時間戳記 (Unix Timestamp)
    if s.isdigit():
        try:
            ts = int(s)
            dt = datetime.utcfromtimestamp(ts / 1000.0 if ts > 1e11 else ts)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass

    return "1970-01-01T00:00:00Z"

def load_existing_tweets(filepath):
    """讀取本地現有推文資料庫並進行欄位格式修復"""
    if not os.path.exists(filepath):
        log("ℹ️ 本地 tweets.json 尚不存在，將建立新檔案。")
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, list):
                return []
            
            cleaned = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                t_id = str(item.get("id", "")).strip()
                if not t_id:
                    continue
                
                raw_d = item.get("created_at") or item.get("date") or item.get("datetime") or item.get("timestamp")
                item["created_at"] = parse_to_iso_date(raw_d)
                cleaned.append(item)
            return cleaned
    except Exception as e:
        log(f"⚠️ 讀取現有推文失敗: {e}")
        return []

def fetch_syndication_stream(screen_name):
    """軌道 1：官方 Syndication 串流（附帶防快取時間戳記）"""
    timestamp = int(time.time())
    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{screen_name}?t={timestamp}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": "https://platform.twitter.com/"
    }

    if AUTH_TOKEN and CT0:
        headers["Cookie"] = f"auth_token={AUTH_TOKEN}; ct0={CT0};"
        headers["x-csrf-token"] = CT0

    fetched = []
    log(f"📡 [軌道 1] 正在連線 Twitter 官方串流抓取 @{screen_name} 最新發文...")

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8")

            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html)
            if not match:
                log("⚠️ 未能解析出 JSON 區塊。")
                return []

            data = json.loads(match.group(1))
            entries = data.get("props", {}).get("pageProps", {}).get("timeline", {}).get("entries", [])

            for entry in entries:
                tweet_raw = entry.get("content", {}).get("tweet")
                if not tweet_raw:
                    continue

                tweet_id = str(tweet_raw.get("id_str") or tweet_raw.get("id", "")).strip()
                text = tweet_raw.get("full_text") or tweet_raw.get("text", "")
                created_at = parse_to_iso_date(tweet_raw.get("created_at", ""))
                fav_count = tweet_raw.get("favorite_count", 0)
                rt_count = tweet_raw.get("retweet_count", 0)
                views_data = tweet_raw.get("views", {})
                views = views_data.get("count", 0) if isinstance(views_data, dict) else (tweet_raw.get("views", 0) or 0)

                if tweet_id and text:
                    fetched.append({
                        "id": tweet_id,
                        "text": text.strip(),
                        "created_at": created_at,
                        "favorite_count": int(fav_count),
                        "retweet_count": int(rt_count),
                        "views": int(views) if str(views).isdigit() else 0,
                        "url": f"https://twitter.com/{screen_name}/status/{tweet_id}"
                    })

            log(f"✨ [軌道 1] 順利解析出 {len(fetched)} 則推文！")

    except Exception as e:
        log(f"⚠️ [軌道 1 異常]: {e}")

    return fetched

def fetch_live_recent_search(screen_name):
    """軌道 2：即時搜尋捕獲（包含最新回覆串與短發文）"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    
    query = f"site:x.com/{screen_name}"
    feed_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-US&gl=US&ceid=US:en"
    fetched = []

    log(f"🔍 [軌道 2] 正在透過即時全網搜尋捕獲 @{screen_name} 最新動態...")
    try:
        req = urllib.request.Request(feed_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            xml_data = resp.read()

        root = ET.fromstring(xml_data)
        items = root.findall("./channel/item")

        for item in items:
            title = item.findtext("title") or ""
            pub_date = item.findtext("pubDate") or ""
            clean_text = re.sub(r' - [^-]+$', '', title).strip()
            
            if not clean_text or len(clean_text) < 5:
                continue

            t_id = str(abs(hash(clean_text)))[:18]
            iso_date = parse_to_iso_date(pub_date)
            if iso_date == "1970-01-01T00:00:00Z":
                iso_date = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

            fetched.append({
                "id": t_id,
                "text": clean_text,
                "created_at": iso_date,
                "favorite_count": 0,
                "retweet_count": 0,
                "views": 0,
                "url": f"https://twitter.com/{screen_name}"
            })

        log(f"✨ [軌道 2] 搜尋取得 {len(fetched)} 則即時動態推文！")

    except Exception as e:
        log(f"⚠️ [軌道 2 異常]: {e}")

    return fetched

def save_merged_tweets(filepath, new_tweets):
    """將新推文與現有資料庫合併去重，並嚴格按真實日期排序儲存"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    existing_tweets = load_existing_tweets(filepath)

    tweets_map = {str(t.get("id", "")).strip(): t for t in existing_tweets if t.get("id")}

    added_count = 0
    updated_count = 0

    for t in new_tweets:
        t_id = str(t.get("id", "")).strip()
        if not t_id:
            continue

        if t_id not in tweets_map:
            tweets_map[t_id] = t
            added_count += 1
            log(f"  ➕ 新增推文 [{t_id}] ({t['created_at']}): {t['text'][:35]}...")
        else:
            if t.get("favorite_count", 0) > 0 or t.get("retweet_count", 0) > 0:
                tweets_map[t_id] = t
            updated_count += 1

    merged_list = list(tweets_map.values())

    # 嚴格按 ISO 8601 日期由新到舊排序
    merged_list.sort(key=lambda x: str(x.get("created_at", "1970-01-01T00:00:00Z")), reverse=True)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(merged_list, f, ensure_ascii=False, indent=2)

    log(
        f"📊 [結算報告] 本次抓取: {len(new_tweets)} 則 | "
        f"全新新增: {added_count} 則 | "
        f"既有推文更新: {updated_count} 則 | "
        f"目前推文資料庫總數: {len(merged_list)} 則"
    )

    if merged_list:
        latest = merged_list[0]
        log(f"🔝 資料庫最新第一筆推文日期: {latest.get('created_at')} (ID: {latest.get('id')})")

if __name__ == "__main__":
    log(f"🚀 開始執行 @{TARGET_HANDLE} 推文擷取流程...")
    
    stream_tweets = fetch_syndication_stream(TARGET_HANDLE)
    search_tweets = fetch_live_recent_search(TARGET_HANDLE)
    
    all_incoming = stream_tweets + search_tweets
    save_merged_tweets(TWEETS_FILE, all_incoming)
    log("✅ 推文擷取任務執行完畢。")
