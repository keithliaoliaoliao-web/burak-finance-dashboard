import json
import os
import re
import time
import urllib.request
import urllib.parse
from datetime import datetime
import xml.etree.ElementTree as ET

# ==========================================
# 參數設定區 (Burak Finance 專案)
# ==========================================
TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

AUTH_TOKEN = os.environ.get("TWITTER_AUTH_TOKEN", "").strip()
CT0 = os.environ.get("TWITTER_CT0", "").strip()

def log(message):
    """即時輸出日誌至 GitHub Actions 控制台"""
    print(message, flush=True)

def snowflake_to_iso(tweet_id_str):
    """利用 Twitter Snowflake 演算法直接由推文 ID 反推精確 UTC 發布時間"""
    try:
        t_id = int(str(tweet_id_str).strip())
        # Twitter Snowflake epoch: 1288834974657 (2010-11-04 01:42:54.657 UTC)
        timestamp_ms = (t_id >> 22) + 1288834974657
        dt = datetime.utcfromtimestamp(timestamp_ms / 1000.0)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None

def parse_date_robust(raw_date, tweet_id=None):
    """結合字串解析與 Snowflake 雙重校驗，產出最精準的 ISO 8601 時間"""
    if tweet_id and str(tweet_id).isdigit() and len(str(tweet_id)) >= 12:
        sn_date = snowflake_to_iso(tweet_id)
        if sn_date:
            return sn_date

    if not raw_date:
        return "1970-01-01T00:00:00Z"

    s = str(raw_date).strip()
    if not s or s.lower() in ("none", "null", "未知時間"):
        return "1970-01-01T00:00:00Z"

    # Twitter 官方格式: "Thu Aug 20 17:08:52 +0000 2026"
    try:
        dt = datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass

    # ISO 格式
    if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', s):
        return s[:19] + "Z"

    return "1970-01-01T00:00:00Z"

def load_existing_tweets(filepath):
    """讀取本地現有推文資料庫，並由 Snowflake 校正所有推文的真實時間"""
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
                
                # 自動校準為精確發布時間
                item["id"] = t_id
                item["created_at"] = parse_date_robust(item.get("created_at"), tweet_id=t_id)
                cleaned.append(item)
                
            return cleaned
    except Exception as e:
        log(f"⚠️ 讀取現有推文失敗: {e}")
        return []

def fetch_syndication_stream(screen_name):
    """軌道 1：官方 Syndication 串流（獲取最新發文）"""
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
        log("⚠️ 未偵測到完整 Cookies，使用訪客模式連線。")

    fetched_tweets = []
    log(f"📡 [軌道 1] 正在連線 Twitter 官方串流抓取 @{screen_name} 最新推文...")

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8")

            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html)
            if match:
                data = json.loads(match.group(1))
                entries = data.get("props", {}).get("pageProps", {}).get("timeline", {}).get("entries", [])

                for entry in entries:
                    tweet_raw = entry.get("content", {}).get("tweet")
                    if not tweet_raw:
                        continue

                    tweet_id = str(tweet_raw.get("id_str") or tweet_raw.get("id", "")).strip()
                    text = tweet_raw.get("full_text") or tweet_raw.get("text", "")
                    created_at = parse_date_robust(tweet_raw.get("created_at", ""), tweet_id=tweet_id)

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

                log(f"  ✨ [軌道 1] 官方串流解析出 {len(fetched_tweets)} 則推文！")

    except Exception as e:
        log(f"  ⚠️ [軌道 1 異常]: {e}")

    return fetched_tweets

def fetch_history_backfill(screen_name, existing_tweets, target_fetch=30):
    """軌道 2：歷史區間深度回溯（擴充更多歷史推文）"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    
    log(f"🔍 [軌道 2] 啟動歷史深度回溯模組，向下挖掘 @{screen_name} 更多過往貼文...")
    backfilled = []
    
    # 透過搜尋索引回溯歷史推文
    query = f"site:x.com/{screen_name}/status"
    feed_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-US&gl=US&ceid=US:en"

    try:
        req = urllib.request.Request(feed_url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            xml_data = resp.read()

        root = ET.fromstring(xml_data)
        items = root.findall("./channel/item")

        for item in items:
            link = item.findtext("link") or ""
            title = item.findtext("title") or ""
            
            # 從連結中提取真實 Status ID
            m = re.search(r'status/(\d+)', link) or re.search(r'status/(\d+)', title)
            if not m:
                continue
                
            t_id = m.group(1).strip()
            clean_text = re.sub(r' - [^-]+$', '', title).strip()
            
            if t_id and clean_text and len(clean_text) > 5:
                real_date = snowflake_to_iso(t_id) or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                backfilled.append({
                    "id": t_id,
                    "text": clean_text,
                    "created_at": real_date,
                    "favorite_count": 0,
                    "retweet_count": 0,
                    "views": 0,
                    "url": f"https://twitter.com/{screen_name}/status/{t_id}"
                })
                
        log(f"  ✨ [軌道 2] 歷史回溯探索到 {len(backfilled)} 則具備真實 ID 的貼文！")

    except Exception as e:
        log(f"  ⚠️ [軌道 2 探索異常]: {e}")

    return backfilled

def enrich_recent_metrics(tweets_list, max_count=20):
    """【即時數據同步】為最新 20 則推文同步真實的按讚數、轉推數與瀏覽量"""
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

                        # 補全完整內文
                        if t_data.get("text") and len(t_data["text"]) > len(tw.get("text", "")):
                            tw["text"] = t_data["text"]

                        updated += 1
            time.sleep(0.15)
        except Exception:
            continue

    log(f"✨ 成功同步 {updated} 則推文的即時官方互動數據！")
    return tweets_list

def save_merged_tweets(filepath, new_tweets):
    """將雙軌新推文與現有資料庫合併去重，並依 Snowflake 真實時間降序儲存"""
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
            log(f"  ➕ 全新收錄推文 [{t_id}] ({t['created_at']}): {t['text'][:35]}...")
        else:
            # 既有推文更新指標
            old = tweets_map[t_id]
            if t.get("favorite_count", 0) >= old.get("favorite_count", 0):
                tweets_map[t_id]["favorite_count"] = t["favorite_count"]
            if t.get("retweet_count", 0) >= old.get("retweet_count", 0):
                tweets_map[t_id]["retweet_count"] = t["retweet_count"]
            if t.get("views", 0) >= old.get("views", 0):
                tweets_map[t_id]["views"] = t["views"]
            updated_count += 1

    merged_list = list(tweets_map.values())

    # 嚴格按 Snowflake 精確 UTC 時間由新到舊排序
    merged_list.sort(
        key=lambda x: str(x.get("created_at") or snowflake_to_iso(x.get("id")) or "1970-01-01T00:00:00Z"), 
        reverse=True
    )

    # 針對前 20 則推文同步真實按讚與瀏覽數
    merged_list = enrich_recent_metrics(merged_list, max_count=20)

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
    log(f"🚀 開始執行 @{TARGET_HANDLE} 官方深度雙軌推文擷取任務...")
    
    # 1. 抓取最新發文
    tweets_stream = fetch_syndication_stream(TARGET_HANDLE)
    
    # 2. 深度歷史回溯（依真實 ID 擴充）
    existing = load_existing_tweets(TWEETS_FILE)
    tweets_history = fetch_history_backfill(TARGET_HANDLE, existing)
    
    # 3. 雙軌聚合儲存
    all_incoming = tweets_stream + tweets_history
    save_merged_tweets(TWEETS_FILE, all_incoming)
    log("✅ 任務全部完成。")
