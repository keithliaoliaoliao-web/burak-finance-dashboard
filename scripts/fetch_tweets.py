import json
import os
import re
import time
import urllib.request
from datetime import datetime

# 目標推特帳號：Burak Finance
TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

AUTH_TOKEN = os.environ.get("TWITTER_AUTH_TOKEN", "").strip()
CT0 = os.environ.get("TWITTER_CT0", "").strip()

def log(message):
    """即時輸出日誌至 GitHub Actions 控制台"""
    print(message, flush=True)

def robust_parse_date(raw_date):
    """強大相容性日期解析器：轉換為標準 ISO 8601"""
    if not raw_date:
        return None
    
    s = str(raw_date).strip()
    if not s or s.lower() in ("none", "null", "1970-01-01t00:00:00z", "1970-01-01 00:00", "未知時間"):
        return None

    # 1. Twitter 官方標準格式: "Thu Aug 20 16:20:00 +0000 2026"
    try:
        dt = datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass

    # 2. 標準 ISO 格式: "2026-08-20T16:20:00Z"
    if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', s):
        return s[:19] + "Z"

    # 3. 常見日期字串: "2026-08-20 16:20:00" 或 "2026-08-20 16:20"
    try:
        clean = s.replace("/", "-")
        if len(clean) >= 19:
            dt = datetime.strptime(clean[:19], "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        elif len(clean) == 16:
            dt = datetime.strptime(clean, "%Y-%m-%d %H:%M")
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        elif len(clean) == 10:
            dt = datetime.strptime(clean, "%Y-%m-%d")
            return dt.strftime("%Y-%m-%dT00:00:00Z")
    except Exception:
        pass

    # 4. Unix 時間戳
    if s.isdigit():
        try:
            ts = int(s)
            dt = datetime.utcfromtimestamp(ts / 1000.0 if ts > 1e11 else ts)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass

    return None

def recover_tweet_date(item):
    """從所有備援欄位中安全還原日期"""
    candidates = [
        item.get("created_at"),
        item.get("date"),
        item.get("datetime"),
        item.get("timestamp"),
        item.get("time")
    ]
    legacy = item.get("legacy") if isinstance(item.get("legacy"), dict) else {}
    candidates.append(legacy.get("created_at"))

    for c in candidates:
        parsed = robust_parse_date(c)
        if parsed and not parsed.startswith("1970"):
            return parsed

    return "1970-01-01T00:00:00Z"

def clean_invalid_records(tweets):
    """自動剔除無效假 ID 資料"""
    valid = []
    removed = 0
    for tw in tweets:
        t_id = str(tw.get("id", "")).strip()
        if t_id.isdigit() and len(t_id) >= 10:
            valid.append(tw)
        else:
            removed += 1
            
    if removed > 0:
        log(f"🧹 [清理完成] 成功剔除 {removed} 筆無效假 ID 資料！")
    return valid

def load_existing_tweets(filepath):
    """讀取本地現有推文資料庫"""
    if not os.path.exists(filepath):
        log("ℹ️ 本地 tweets.json 不存在，將建立新資料庫。")
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
                t_id = str(item.get("id") or item.get("id_str") or "").strip()
                if not t_id:
                    continue
                item["created_at"] = recover_tweet_date(item)
                cleaned.append(item)
                
            return clean_invalid_records(cleaned)
    except Exception as e:
        log(f"⚠️ 讀取現有推文失敗: {e}")
        return []

def fetch_syndication_stream(screen_name):
    """軌道 1：官方 Syndication 串流（獲取原創主貼文）"""
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
        log("🔑 官方認證憑證 (Cookies) 注入成功。")
    else:
        log("⚠️ 未偵測到完整 Cookies，使用公開訪客模式連線。")

    fetched_tweets = []
    log(f"📡 [軌道 1] 正在連線 Twitter 官方串流抓取 @{screen_name} 最新原創主推文...")

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8")

            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html)
            if not match:
                log("  ⚠️ 未能解析出結構化 JSON 區塊。")
                return []

            data = json.loads(match.group(1))
            entries = data.get("props", {}).get("pageProps", {}).get("timeline", {}).get("entries", [])

            for entry in entries:
                tweet_raw = entry.get("content", {}).get("tweet")
                if not tweet_raw:
                    continue

                tweet_id = str(tweet_raw.get("id_str") or tweet_raw.get("id", "")).strip()
                text = tweet_raw.get("full_text") or tweet_raw.get("text", "")
                
                raw_time = tweet_raw.get("created_at", "")
                created_at = robust_parse_date(raw_time) or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

                fav_count = int(tweet_raw.get("favorite_count", 0) or 0)
                rt_count = int(tweet_raw.get("retweet_count", 0) or 0)
                views_data = tweet_raw.get("views", {})
                views = int(views_data.get("count", 0)) if isinstance(views_data, dict) and str(views_data.get("count", "")).isdigit() else 0

                if tweet_id and text:
                    fetched_tweets.append({
                        "id": tweet_id,
                        "text": text.strip(),
                        "created_at": created_at,
                        "favorite_count": fav_count,
                        "retweet_count": rt_count,
                        "views": views,
                        "url": f"https://twitter.com/{screen_name}/status/{tweet_id}"
                    })

            log(f"  ✨ [軌道 1] 解析出 {len(fetched_tweets)} 則主推文！")

    except Exception as e:
        log(f"  ⚠️ [軌道 1 異常]: {e}")

    return fetched_tweets

def fetch_fxtwitter_timeline(screen_name):
    """軌道 2：FxTwitter 全動態串流（捕獲最新回覆串、即時發文與真實互動數）"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }

    fetched_tweets = []
    log(f"🔍 [軌道 2] 正在連線全動態 API 檢索 @{screen_name} 最新發文與回覆串...")

    # 檢索端點清單
    endpoints = [
        f"https://api.fxtwitter.com/{screen_name}",
        f"https://api.fxtwitter.com/{screen_name}/latest"
    ]

    for ep in endpoints:
        try:
            req = urllib.request.Request(ep, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    raw_list = []
                    if isinstance(data, dict):
                        if isinstance(data.get("tweets"), list):
                            raw_list = data["tweets"]
                        elif isinstance(data.get("tweet"), dict):
                            raw_list = [data["tweet"]]

                    for item in raw_list:
                        if not isinstance(item, dict):
                            continue
                        t_id = str(item.get("id") or item.get("id_str") or "").strip()
                        text = item.get("text") or item.get("full_text") or ""
                        
                        raw_time = item.get("created_at") or item.get("created_timestamp")
                        created_at = robust_parse_date(raw_time) or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

                        likes = int(item.get("likes", 0) or item.get("favorite_count", 0) or 0)
                        retweets = int(item.get("retweets", 0) or item.get("retweet_count", 0) or 0)
                        views = int(item.get("views", 0) or 0)

                        if t_id and text and t_id.isdigit() and len(t_id) >= 10:
                            fetched_tweets.append({
                                "id": t_id,
                                "text": text.strip(),
                                "created_at": created_at,
                                "favorite_count": likes,
                                "retweet_count": retweets,
                                "views": views,
                                "url": f"https://twitter.com/{screen_name}/status/{t_id}"
                            })
        except Exception as e:
            log(f"  ⚠️ [軌道 2 端點 {ep} 異常]: {e}")

    log(f"  ✨ [軌道 2] 成功解析出 {len(fetched_tweets)} 則全動態真實推文！")
    return fetched_tweets

def save_merged_tweets(filepath, new_tweets):
    """將雙軌新推文與現有資料庫合併去重，由新到舊嚴格排序儲存"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    existing_tweets = load_existing_tweets(filepath)

    tweets_map = {str(t.get("id", "")).strip(): t for t in existing_tweets if t.get("id")}

    added_count = 0
    updated_count = 0

    for t in new_tweets:
        t_id = str(t.get("id", "")).strip()
        if not t_id or not t_id.isdigit() or len(t_id) < 10:
            continue

        if t_id not in tweets_map:
            tweets_map[t_id] = t
            added_count += 1
            log(f"  ➕ 新增推文 [{t_id}] ({t['created_at']}): {t['text'][:35]}...")
        else:
            # 既有推文同步最新點讚數與轉推數
            old = tweets_map[t_id]
            if t.get("favorite_count", 0) >= old.get("favorite_count", 0):
                tweets_map[t_id]["favorite_count"] = t["favorite_count"]
            if t.get("retweet_count", 0) >= old.get("retweet_count", 0):
                tweets_map[t_id]["retweet_count"] = t["retweet_count"]
            if t.get("views", 0) >= old.get("views", 0):
                tweets_map[t_id]["views"] = t["views"]
            updated_count += 1

    merged_list = list(tweets_map.values())

    # 嚴格按 ISO 8601 時間由新到舊排序
    merged_list.sort(
        key=lambda x: str(x.get("created_at") or recover_tweet_date(x)), 
        reverse=True
    )

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(merged_list, f, ensure_ascii=False, indent=2)

    log(
        f"📊 [結算報告] 本次抓取: {len(new_tweets)} 則 | "
        f"全新新增: {added_count} 則 | "
        f"數據更新: {updated_count} 則 | "
        f"目前推文資料庫總數: {len(merged_list)} 則"
    )

    if merged_list:
        latest = merged_list[0]
        log(f"🔝 最新第 1 筆推文: {latest.get('created_at')} (ID: {latest.get('id')}) | ❤️ {latest.get('favorite_count', 0)}  🔁 {latest.get('retweet_count', 0)}  👁️ {latest.get('views', 0)}")

if __name__ == "__main__":
    log(f"🚀 開始執行 @{TARGET_HANDLE} 雙軌推文擷取任務...")
    
    # 1. 抓取原創主推文
    tweets_syndication = fetch_syndication_stream(TARGET_HANDLE)
    
    # 2. 抓取全動態即時推文與回覆串
    tweets_dynamic = fetch_fxtwitter_timeline(TARGET_HANDLE)
    
    # 3. 雙軌聚合儲存
    all_tweets = tweets_syndication + tweets_dynamic
    save_merged_tweets(TWEETS_FILE, all_tweets)
    log("✅ 任務全部完成。")
