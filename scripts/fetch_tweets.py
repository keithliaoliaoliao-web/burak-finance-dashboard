import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime

# ==========================================
# 參數設定區 (Burak: burak_finance / Serenity: aleabitoreddit)
# ==========================================
TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

AUTH_TOKEN = os.environ.get("TWITTER_AUTH_TOKEN", "").strip()
CT0 = os.environ.get("TWITTER_CT0", "").strip()

# Twitter 官方 Snowflake 紀元起點 (2010-11-04 01:42:54.657 UTC)
TWITTER_EPOCH = 1288834974657

def log(message):
    """即時強制輸出日誌至 GitHub Actions 控制台"""
    print(message, flush=True)

def snowflake_to_iso(tweet_id_str):
    """利用 Twitter Snowflake 演算法由推文 ID 反推精確 UTC 發布時間"""
    try:
        t_id = int(str(tweet_id_str).strip())
        timestamp_ms = (t_id >> 22) + TWITTER_EPOCH
        dt = datetime.utcfromtimestamp(timestamp_ms / 1000.0)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None

def load_existing_tweets(filepath):
    """讀取本地現有推文資料庫並校正時間"""
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
                if not t_id.isdigit() or len(t_id) < 10:
                    continue

                item["id"] = t_id
                item["created_at"] = snowflake_to_iso(t_id) or item.get("created_at") or "1970-01-01T00:00:00Z"
                cleaned.append(item)

            return cleaned
    except Exception as e:
        log(f"⚠️ 讀取現有推文失敗: {e}")
        return []

def fetch_syndication_page(screen_name, max_pages=3):
    """透過 Twitter 官方 Syndication 串流多頁深度抓取最新與歷史推文"""
    base_url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{screen_name}"
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
        log("⚠️ 未偵測到完整 Cookies，使用訪客模式連線。")

    all_fetched = []
    seen_ids = set()

    for page in range(1, max_pages + 1):
        timestamp = int(time.time()) + page
        req_url = f"{base_url}?t={timestamp}&p={page}"
        log(f"📡 正在連線官方串流抓取 @{screen_name} 推文 (批次 {page}/{max_pages})...")

        try:
            req = urllib.request.Request(req_url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as response:
                html = response.read().decode("utf-8")

                match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html)
                if not match:
                    break

                data = json.loads(match.group(1))
                entries = data.get("props", {}).get("pageProps", {}).get("timeline", {}).get("entries", [])
                page_count = 0

                for entry in entries:
                    tweet_raw = entry.get("content", {}).get("tweet")
                    if not tweet_raw:
                        continue

                    tweet_id = str(tweet_raw.get("id_str") or tweet_raw.get("id", "")).strip()
                    if not tweet_id or tweet_id in seen_ids:
                        continue

                    seen_ids.add(tweet_id)
                    text = tweet_raw.get("full_text") or tweet_raw.get("text", "")
                    created_at = snowflake_to_iso(tweet_id) or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

                    fav_count = int(tweet_raw.get("favorite_count", 0) or 0)
                    rt_count = int(tweet_raw.get("retweet_count", 0) or 0)
                    views_data = tweet_raw.get("views", {})
                    views = int(views_data.get("count", 0)) if isinstance(views_data, dict) and str(views_data.get("count", "")).isdigit() else 0

                    if tweet_id and text:
                        all_fetched.append({
                            "id": tweet_id,
                            "text": text.strip(),
                            "created_at": created_at,
                            "favorite_count": fav_count,
                            "retweet_count": rt_count,
                            "views": views,
                            "url": f"https://twitter.com/{screen_name}/status/{tweet_id}"
                        })
                        page_count += 1

                log(f"  ✨ 批次 {page} 解析出 {page_count} 則推文！")
                if page_count == 0:
                    break
                time.sleep(0.5)

        except Exception as e:
            log(f"  ⚠️ 批次 {page} 連線異常: {e}")
            break

    return all_fetched

def enrich_recent_metrics(tweets_list, max_count=25):
    """為最新 25 則推文同步真實的按讚數、轉推數與完整內文"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    updated = 0
    check_limit = min(len(tweets_list), max_count)
    log(f"🔄 正在為最新 {check_limit} 則推文同步即時真實互動數據 (Likes/RT/Views)...")

    for tw in tweets_list[:check_limit]:
        t_id = str(tw.get("id", "")).strip()
        if not t_id.isdigit() or len(t_id) < 10:
            continue

        api_url = f"https://api.fxtwitter.com/status/{t_id}"
        try:
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    t_data = data.get("tweet", {})
                    if t_data:
                        likes = int(t_data.get("likes", 0) or t_data.get("favorite_count", 0) or 0)
                        retweets = int(t_data.get("retweets", 0) or t_data.get("retweet_count", 0) or 0)
                        views = int(t_data.get("views", 0) or 0)

                        tw["favorite_count"] = likes
                        tw["retweet_count"] = retweets
                        tw["views"] = views

                        if t_data.get("text") and len(t_data["text"]) > len(tw.get("text", "")):
                            tw["text"] = t_data["text"]

                        updated += 1
            time.sleep(0.15)
        except Exception:
            continue

    log(f"✨ 成功同步 {updated} 則推文的即時官方互動數據！")
    return tweets_list

def save_merged_tweets(filepath, incoming_tweets):
    """合併推文、去重、即時數據補齊並依真實發布時間降序儲存"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    existing_tweets = load_existing_tweets(filepath)

    tweets_map = {str(t.get("id", "")).strip(): t for t in existing_tweets if t.get("id")}

    added_count = 0
    updated_count = 0

    for t in incoming_tweets:
        t_id = str(t.get("id", "")).strip()
        if not t_id or not t_id.isdigit() or len(t_id) < 10:
            continue

        if t_id not in tweets_map:
            tweets_map[t_id] = t
            added_count += 1
            log(f"  ➕ 全新收錄推文 [{t_id}] ({t['created_at']}): {t['text'][:35]}...")
        else:
            old = tweets_map[t_id]
            if t.get("favorite_count", 0) >= old.get("favorite_count", 0):
                tweets_map[t_id]["favorite_count"] = t["favorite_count"]
            if t.get("retweet_count", 0) >= old.get("retweet_count", 0):
                tweets_map[t_id]["retweet_count"] = t["retweet_count"]
            if t.get("views", 0) >= old.get("views", 0):
                tweets_map[t_id]["views"] = t["views"]
            if len(t.get("text", "")) > len(old.get("text", "")):
                tweets_map[t_id]["text"] = t["text"]
            updated_count += 1

    merged_list = list(tweets_map.values())

    # 嚴格依 Snowflake 精確時間由新到舊排序
    merged_list.sort(
        key=lambda x: str(x.get("created_at") or snowflake_to_iso(x.get("id")) or "1970-01-01T00:00:00Z"),
        reverse=True
    )

    # 同步前 25 則最新推文的互動數據
    merged_list = enrich_recent_metrics(merged_list, max_count=25)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(merged_list, f, ensure_ascii=False, indent=2)

    log(
        f"📊 [結算報告] 本次抓取: {len(incoming_tweets)} 則 | "
        f"全新新增: {added_count} 則 | "
        f"數據更新: {updated_count} 則 | "
        f"目前推文資料庫總數: {len(merged_list)} 則"
    )

    if merged_list:
        latest = merged_list[0]
        log(f"🔝 資料庫最新第 1 筆推文: {latest.get('created_at')} (ID: {latest.get('id')}) | ❤️ {latest.get('favorite_count', 0)}  🔁 {latest.get('retweet_count', 0)}  👁️ {latest.get('views', 0)}")

if __name__ == "__main__":
    log(f"🚀 開始執行 @{TARGET_HANDLE} 官方認證深度推文擷取任務...")
    tweets = fetch_syndication_page(TARGET_HANDLE, max_pages=3)
    save_merged_tweets(TWEETS_FILE, tweets)
    log("✅ 任務全部完成。")
