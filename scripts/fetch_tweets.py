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

# Twitter 官方公開 Web 授權 Bearer Token
TWITTER_BEARER = "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

def load_existing_tweets(filepath):
    """讀取現有推文資料庫"""
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
    """建立 Twitter 官方 GraphQL 認證 Headers"""
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

def resolve_user_id(screen_name):
    """取得用戶的唯一數字 ID (rest_id)"""
    # 方式 1：從公開 Syndication 端點快速解析
    try:
        url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{screen_name}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8")
            m = re.search(r'"userId"\s*:\s*"(\d+)"', html) or re.search(r'"id_str"\s*:\s*"(\d+)"', html)
            if m:
                u_id = m.group(1)
                print(f"🔍 成功解析 @{screen_name} 的 User ID: {u_id}", flush=True)
                return u_id
    except Exception:
        pass

    # 方式 2：透過 GraphQL UserByScreenName 查詢
    try:
        variables = json.dumps({"screen_name": screen_name, "withSafetyModeUserFields": True})
        features = json.dumps({"hidden_profile_likes_enabled": True, "responsive_web_graphql_exclude_directive_enabled": True})
        query_url = f"https://x.com/i/api/graphql/NdnUFFeSem-ABYWss0Ja3w/UserByScreenName?variables={urllib.parse.quote(variables)}&features={urllib.parse.quote(features)}"
        
        req = urllib.request.Request(query_url, headers=get_auth_headers())
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            u_id = data.get("data", {}).get("user", {}).get("result", {}).get("rest_id")
            if u_id:
                print(f"🔍 成功透過 GraphQL 取得 User ID: {u_id}", flush=True)
                return str(u_id)
    except Exception as e:
        print(f"⚠️ User ID 解析異常: {e}", flush=True)

    return None

def parse_graphql_timeline(data, screen_name):
    """解析 Twitter GraphQL 回傳的結構化推文與下一頁游標"""
    tweets = []
    next_cursor = None
    seen_ids = set()

    instructions = (
        data.get("data", {}).get("user", {}).get("result", {}).get("timeline_v2", {}).get("timeline", {}).get("instructions", []) or
        data.get("data", {}).get("user", {}).get("result", {}).get("timeline", {}).get("timeline", {}).get("instructions", []) or
        []
    )

    for inst in instructions:
        entries = inst.get("entries", [])
        if not entries and "entry" in inst:
            entries = [inst["entry"]]

        for entry in entries:
            entry_id = entry.get("entryId", "")
            
            # 擷取下一頁的分頁游標 (Cursor)
            if "cursor-bottom" in entry_id or "cursor-showMore" in entry_id:
                content = entry.get("content", {})
                next_cursor = content.get("value") or content.get("cursorType")
                continue

            # 擷取推文實體
            item_content = entry.get("content", {}).get("itemContent", {})
            tweet_result = item_content.get("tweet_results", {}).get("result", {})
            
            # 支援轉發 (Retweet) 或一般推文
            if "tweet" in tweet_result:
                tweet_result = tweet_result["tweet"]

            legacy = tweet_result.get("legacy", {})
            t_id = str(tweet_result.get("rest_id") or legacy.get("id_str") or "").strip()
            text = legacy.get("full_text") or legacy.get("text") or ""
            created_at = legacy.get("created_at") or ""

            if t_id and text and t_id not in seen_ids:
                seen_ids.add(t_id)
                fav_count = legacy.get("favorite_count", 0)
                rt_count = legacy.get("retweet_count", 0)
                views_data = tweet_result.get("views", {})
                views = views_data.get("count", 0) if isinstance(views_data, dict) else 0

                iso_date = ""
                try:
                    dt = datetime.strptime(str(created_at), "%a %b %d %H:%M:%S %z %Y")
                    iso_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    iso_date = str(created_at)

                tweets.append({
                    "id": t_id,
                    "text": text,
                    "created_at": iso_date,
                    "favorite_count": int(fav_count),
                    "retweet_count": int(rt_count),
                    "views": int(views) if str(views).isdigit() else 0,
                    "url": f"https://twitter.com/{screen_name}/status/{t_id}"
                })

    return tweets, next_cursor

def fetch_tweets_with_cursor_pagination(user_id, screen_name, max_pages=8):
    """核心分頁函式：連續追蹤游標，批量回溯大量推文"""
    all_tweets = []
    cursor = None
    headers = get_auth_headers()

    features = json.dumps({
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "responsive_web_home_pinned_timelines_enabled": True,
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "responsive_web_graphql_timeline_navigation_enabled": True,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "tweetypie_unmention_optimization_enabled": True,
        "vibe_api_enabled": True,
        "responsive_web_edit_tweet_api_enabled": True,
        "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
        "view_counts_everywhere_api_enabled": True,
        "longform_notetweets_consumption_enabled": True,
        "tweet_awards_web_tipping_enabled": False,
        "freedom_of_speech_not_reach_fetch_enabled": True,
        "standardized_nudges_misinfo": True,
        "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
        "longform_notetweets_rich_text_read_enabled": True,
        "longform_notetweets_inline_media_enabled": True,
        "responsive_web_enhance_cards_enabled": False
    })

    print(f"🚀 開始執行 GraphQL 連續分頁抓取（目標上限: {max_pages} 頁）...", flush=True)

    for page_idx in range(1, max_pages + 1):
        variables_dict = {
            "userId": user_id,
            "count": 40,
            "includePromotedContent": False,
            "withQuickPromoteEligibilityTweetFields": True,
            "withVoice": True,
            "withV2Timeline": True
        }
        if cursor:
            variables_dict["cursor"] = cursor

        variables = json.dumps(variables_dict)
        url = f"https://x.com/i/api/graphql/V7H0Ap3_Hh2FyS75OCDO3Q/UserTweets?variables={urllib.parse.quote(variables)}&features={urllib.parse.quote(features)}"

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))
                
                page_tweets, next_cursor = parse_graphql_timeline(data, screen_name)
                print(f"  📄 [第 {page_idx} 頁] 成功解析出 {len(page_tweets)} 則推文！", flush=True)

                all_tweets.extend(page_tweets)

                if not next_cursor or next_cursor == cursor or len(page_tweets) == 0:
                    print("  🏁 已抵達時間軸末端或無更多分頁游標。", flush=True)
                    break

                cursor = next_cursor
                time.sleep(1.5)  # 溫和間隔，避免頻率限制

        except Exception as e:
            print(f"  ⚠️ 第 {page_idx} 頁請求中斷: {e}", flush=True)
            break

    return all_tweets

def save_merged_tweets(filepath, new_tweets):
    """比對去重並更新本地推文資料庫"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    existing_tweets = load_existing_tweets(filepath)

    tweets_map = {str(t.get("id", "")).strip(): t for t in existing_tweets if t.get("id")}

    added_count = 0
    for t in new_tweets:
        t_id = str(t.get("id", "")).strip()
        if t_id:
            if t_id not in tweets_map:
                added_count += 1
            # 更新最新數據
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

    user_id = resolve_user_id(TARGET_HANDLE)
    if not user_id:
        print(f"❌ 無法取得 @{TARGET_HANDLE} 的 User ID，終止抓取流程。", flush=True)
        exit(0)

    # 執行 8 頁連續游標分頁抓取（約 150～300 則推文）
    collected_tweets = fetch_tweets_with_cursor_pagination(user_id, TARGET_HANDLE, max_pages=8)
    save_merged_tweets(TWEETS_FILE, collected_tweets)
    print("✅ 任務全部完成。", flush=True)
