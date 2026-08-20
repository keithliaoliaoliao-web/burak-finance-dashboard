import asyncio
import json
import os
import re
from datetime import datetime
from twikit import Client

TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

AUTH_TOKEN = os.environ.get("TWITTER_AUTH_TOKEN")
CT0 = os.environ.get("TWITTER_CT0")

def load_existing_tweets(filepath):
    """讀取本地現有推文資料庫"""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"⚠️ 讀取現有推文失敗: {e}", flush=True)
        return []

async def fetch_tweets_with_cookies(screen_name, count=50):
    """透過 Twitter 登入憑證抓取目標帳號真實推文"""
    if not AUTH_TOKEN or not CT0:
        print("❌ 錯誤：未偵測到 TWITTER_AUTH_TOKEN 或 TWITTER_CT0 環境變數。", flush=True)
        return []

    print(f"🔑 正在載入 Twitter 驗證憑證並連線...", flush=True)
    client = Client('en-US')
    
    # 注入瀏覽器 Cookie 憑證
    client.set_cookies({
        'auth_token': AUTH_TOKEN.strip(),
        'ct0': CT0.strip()
    })

    fetched = []
    try:
        print(f"📡 正在查詢 @{screen_name} 的用戶資料與推文清單...", flush=True)
        user = await client.get_user_by_screen_name(screen_name)
        
        # 抓取用戶主推文
        tweets = await user.get_tweets('Tweets', count=count)
        
        for tw in tweets:
            t_id = str(getattr(tw, 'id', '')).strip()
            text = getattr(tw, 'text', '') or getattr(tw, 'full_text', '') or ""
            created_at = getattr(tw, 'created_at', '')
            
            # 解析日期格式
            iso_date = ""
            try:
                if created_at:
                    dt = datetime.strptime(str(created_at), "%a %b %d %H:%M:%S %z %Y")
                    iso_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                iso_date = str(created_at)

            fav_count = getattr(tw, 'favorite_count', 0) or getattr(tw, 'likes', 0) or 0
            rt_count = getattr(tw, 'retweet_count', 0) or getattr(tw, 'retweets', 0) or 0
            views = getattr(tw, 'view_count', 0) or getattr(tw, 'views', 0) or 0

            if t_id and text:
                fetched.append({
                    "id": t_id,
                    "text": text,
                    "created_at": iso_date,
                    "favorite_count": int(fav_count),
                    "retweet_count": int(rt_count),
                    "views": int(views),
                    "url": f"https://twitter.com/{screen_name}/status/{t_id}"
                })

        print(f"✨ 成功透過認證管道擷取 {len(fetched)} 則完整推文！", flush=True)

    except Exception as e:
        print(f"⚠️ 擷取推文時發生異常: {e}", flush=True)

    return fetched

def save_merged_tweets(filepath, new_tweets):
    """與現有資料庫進行比對、去重並增量儲存"""
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

    print(f"📊 [結算報告] 本次新增推文: {added_count} 則 | 目前資料庫總推文數: {len(merged_list)} 則", flush=True)

if __name__ == "__main__":
    print(f"🚀 開始執行 @{TARGET_HANDLE} 認證推文擷取任務...", flush=True)
    tweets = asyncio.run(fetch_tweets_with_cookies(TARGET_HANDLE, count=50))
    save_merged_tweets(TWEETS_FILE, tweets)
    print("✅ 任務完成。", flush=True)
