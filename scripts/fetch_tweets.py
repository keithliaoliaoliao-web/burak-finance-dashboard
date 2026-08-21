import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime

# ==========================================
# 參數設定區 (Burak Finance 專案)
# ==========================================
TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

# 最大向下探索頁數（遇重複資料庫會自動提前停止，避免被 Twitter 限速）
MAX_PAGES_TO_FETCH = 10

AUTH_TOKEN = os.environ.get("TWITTER_AUTH_TOKEN", "").strip()
CT0 = os.environ.get("TWITTER_CT0", "").strip()

# Twitter 官方公開 Bearer Token 與 Snowflake 起始紀元
BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
TWITTER_EPOCH = 1288834974657

def log(message):
    """即時輸出日誌至 GitHub Actions 控制台"""
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
                if not t_id.isdigit() or len(t_id) < 10:
                    continue

                item["id"] = t_id
                item["created_at"] = snowflake_to_iso(t_id) or item.get("created_at") or "1970-01-01T00:00:00Z"
                cleaned.append(item)

            return cleaned
    except Exception as e:
        log(f"⚠️ 讀取現有推文失敗: {e}")
        return []

def get_user_id_and_initial_tweets(screen_name):
    """取得作者的 Twitter 數字 User ID 與第一批即時推文"""
    timestamp = int(time.time())
    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{screen_name}?t={timestamp}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://platform.twitter.com/"
    }

    if AUTH_TOKEN and CT0:
        headers["Cookie"] = f"auth_token={AUTH_TOKEN}; ct0={CT0};"
        headers["x-csrf-token"] = CT0
        log("🔑 官方認證憑證 (Cookies) 注入成功。")

    user_id = None
    tweets = []

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8")
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', html)
            if match:
                data = json.loads(match.group(1))
                entries = data.get("props", {}).get("pageProps", {}).get("timeline", {}).get("entries", [])

                for entry in entries:
                    tw = entry.get("content", {}).get("tweet")
                    if not tw:
                        continue

                    if not user_id and tw.get("user", {}).get("id_str"):
                        user_id = str(tw["user"]["id_str"]).strip()

                    tweet_id = str(tw.get("id_str") or tw.get("id", "")).strip()
                    text = tw.get("full_text") or tw.get("text", "")
                    created_at = snowflake_to_iso(tweet_id) or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

                    fav_count = int(tw.get("favorite_count", 0) or 0)
                    rt_count = int(tw.get("retweet_count", 0) or 0)
                    views_data = tw.get("views", {})
                    views = int(views_data.get("count", 0)) if isinstance(views_data, dict) and str(views_data.get("count", "")).isdigit() else 0

                    if tweet_id and text:
                        tweets.append({
                            "id": tweet_id,
                            "text": text.strip(),
                            "created_at": created_at,
                            "favorite_count": fav_count,
                            "retweet_count": rt_count,
                            "views": views,
                            "url": f"https://twitter.com/{screen_name}/status/{tweet_id}"
                        })
    except Exception as e:
        log(f"⚠️ 初始化串流抓取異常: {e}")

    return user_id, tweets

def fetch_history_via_graphql(user_id, screen_name, existing_ids_set, max_pages=10):
    """透過 Twitter 官方 GraphQL 游標分頁深度挖掘（具備 Early-Stopping 遇重複早停機制）"""
    if not user_id or not AUTH_TOKEN or not CT0:
        log("⚠️ 未具備完整認證 Cookie 或 User ID，跳過 GraphQL 歷史回填。")
        return []

    headers = {
        "Authorization": f"Bearer {BEARER_TOKEN}",
        "Cookie": f"auth_token={AUTH_TOKEN}; ct0={CT0};",
        "x-csrf-token": CT0,
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Content-Type": "application/json"
    }

    query_ids = ["V7H0Ap3_Hh2FyS75OCDO3Q", "E3opETHurVaQhXMcGvZnpg", "Tg82Ez_40SwGTScOioip6Q", "Q6aDgCRGQW5Pn49WBeaxMw"]
    features = {
        "responsive_web_graphql_timeline_navigation_enabled": True,
        "unified_cards_ad_metadata_container_dynamic_card_content_query_enabled": True,
        "responsive_web_enhance_cards_enabled": False
    }

    all_history = []
    cursor = None
    seen_ids = set()

    log(f"🚀 [歷史探索啟動] 開始透過 GraphQL 分頁檢索 @{screen_name} (上限 {max_pages} 頁，遇既有資料自動提早結束)...")

    for page in range(1, max_pages + 1):
        variables = {
            "userId": user_id,
            "count": 20,
            "includePromotedContent": False,
            "withQuickPromoteEligibilityTweetFields": True,
            "withVoice": True,
            "withV2Timeline": True
        }
        if cursor:
            variables["cursor"] = cursor

        success = False
        page_tweets = []
        next_cursor = None

        for qid in query_ids:
            url = f"https://x.com/i/api/graphql/{qid}/UserTweets?variables={urllib.parse.quote(json.dumps(variables))}&features={urllib.parse.quote(json.dumps(features))}"
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=12) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        instructions = data.get("data", {}).get("user", {}).get("result", {}).get("timeline_v2", {}).get("timeline", {}).get("instructions", [])
                        if not instructions:
                            instructions = data.get("data", {}).get("user", {}).get("result", {}).get("timeline", {}).get("timeline", {}).get("instructions", [])

                        for inst in instructions:
                            for entry in inst.get("entries", []):
                                entry_id = entry.get("entryId", "")
                                if "cursor-bottom" in entry_id:
                                    next_cursor = entry.get("content", {}).get("value")

                                tweet_result = entry.get("content", {}).get("itemContent", {}).get("tweet_results", {}).get("result", {})
                                if not tweet_result:
                                    continue

                                legacy = tweet_result.get("legacy") or tweet_result.get("tweet", {}).get("legacy", {})
                                if not legacy:
                                    continue

                                t_id = str(legacy.get("id_str") or tweet_result.get("rest_id") or "").strip()
                                text = legacy.get("full_text") or legacy.get("text", "")

                                if t_id and text and t_id not in seen_ids:
                                    seen_ids.add(t_id)
                                    created_at = snowflake_to_iso(t_id) or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                                    fav_count = int(legacy.get("favorite_count", 0) or 0)
                                    rt_count = int(legacy.get("retweet_count", 0) or 0)
                                    views = int(tweet_result.get("views", {}).get("count", 0) or 0)

                                    page_tweets.append({
                                        "id": t_id,
                                        "text": text.strip(),
                                        "created_at": created_at,
                                        "favorite_count": fav_count,
                                        "retweet_count": rt_count,
                                        "views": views,
                                        "url": f"https://twitter.com/{screen_name}/status/{t_id}"
                                    })

                        success = True
                        break
            except Exception:
                continue

        if not success or not page_tweets:
            log(f"  ℹ️ 已到達歷史推文邊界或未取得新資料，於第 {page} 頁結束挖掘。")
            break

        all_history.extend(page_tweets)
        
        # 檢查本頁是否有全新推文（不在資料庫中）
        new_in_page = [t for t in page_tweets if t["id"] not in existing_ids_set]
        log(f"  📜 [第 {page}/{max_pages} 頁] 解析出 {len(page_tweets)} 則推文 (其中全新: {len(new_in_page)} 則)")

        # 【Early-Stopping 智慧保護】
        # 若不是第 1 頁，且整頁推文全都是資料庫已收錄的舊推文，表示已接軌歷史庫，自動提早結束！
        if page > 1 and len(new_in_page) == 0:
            log("  🛑 [智慧早停] 本頁所有推文皆已存在於本地資料庫中，已成功接軌歷史紀錄，停止向下請求以節省資源！")
            break

        if not next_cursor or next_cursor == cursor:
            break

        cursor = next_cursor
        time.sleep(1.0)  # 防限速保護間隔

    log(f"✨ 歷史探索完成，累計取得 {len(all_history)} 則推文！")
    return all_history

def enrich_recent_metrics(tweets_list, target_count=35):
    """為最新 35 則推文同步官方即時互動數據"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    updated = 0
    check_limit = min(len(tweets_list), target_count)
    log(f"🔄 正在為最新 {check_limit} 則推文同步即時互動數據 (Likes/RT/Views)...")

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
            time.sleep(0.1)
        except Exception:
            continue

    log(f"✨ 成功同步 {updated} 則推文的即時官方互動指標！")
    return tweets_list

def save_merged_tweets(filepath, incoming_tweets):
    """將抓取到的推文與現有資料庫合併去重，並由新到舊排序儲存"""
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

    # 依 Snowflake 精確時間降序排序（最新在最前）
    merged_list.sort(
        key=lambda x: str(x.get("created_at") or snowflake_to_iso(x.get("id")) or "1970-01-01T00:00:00Z"),
        reverse=True
    )

    # 針對前 35 則最新推文同步即時數據
    merged_list = enrich_recent_metrics(merged_list, target_count=35)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(merged_list, f, ensure_ascii=False, indent=2)

    file_size_kb = os.path.getsize(filepath) / 1024.0

    log(
        f"📊 [結算報告] 本次探索: {len(incoming_tweets)} 則 | "
        f"全新歷史新增: {added_count} 則 | "
        f"數據更新: {updated_count} 則 | "
        f"🎉 目前推文資料庫總數: {len(merged_list)} 則 (檔案大小: {file_size_kb:.1f} KB)"
    )

    if merged_list:
        latest = merged_list[0]
        oldest = merged_list[-1]
        log(f"🔝 最新推文: {latest.get('created_at')} (ID: {latest.get('id')})")
        log(f"🔚 最舊推文: {oldest.get('created_at')} (ID: {oldest.get('id')})")

if __name__ == "__main__":
    log(f"🚀 開始執行 @{TARGET_HANDLE} 自適應推文擷取與同步任務...")

    # 1. 讀取現有資料庫 ID 集合
    existing_tweets = load_existing_tweets(TWEETS_FILE)
    existing_ids = {str(t.get("id", "")).strip() for t in existing_tweets if t.get("id")}

    # 2. 取得 User ID 與最新串流推文
    user_id, latest_tweets = get_user_id_and_initial_tweets(TARGET_HANDLE)
    log(f"🆔 成功取得 @{TARGET_HANDLE} 之 Twitter User ID: {user_id}")

    # 3. 啟動 GraphQL 深度挖掘（支援遇重複早停機制）
    history_tweets = fetch_history_via_graphql(user_id, TARGET_HANDLE, existing_ids, max_pages=MAX_PAGES_TO_FETCH)

    # 4. 合併並寫入資料庫
    all_incoming = latest_tweets + history_tweets
    save_merged_tweets(TWEETS_FILE, all_incoming)
    log("✅ 任務全部完成。")
