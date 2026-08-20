import json
import os
import re
import time
from datetime import datetime
from playwright.sync_api import sync_playwright

TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

def load_existing_tweets(filepath):
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"⚠️ 讀取現有推文失敗: {e}")
        return []

def fetch_tweets_with_playwright(screen_name, scroll_times=15):
    """使用 Playwright 模擬真實瀏覽器環境擷取推文"""
    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{screen_name}"
    fetched_tweets = []

    print(f"🚀 啟動無頭瀏覽器，正在載入 @{screen_name} 的推文串流...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        try:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            time.sleep(3)

            # 嘗試直接從頁面的 __NEXT_DATA__ 擷取結構化資料
            content = page.content()
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', content)
            
            if match:
                data = json.loads(match.group(1))
                entries = data.get("props", {}).get("pageProps", {}).get("timeline", {}).get("entries", [])
                
                for entry in entries:
                    tweet_raw = entry.get("content", {}).get("tweet")
                    if not tweet_raw:
                        continue

                    t_id = str(tweet_raw.get("id_str") or tweet_raw.get("id", "")).strip()
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

                    if t_id and text:
                        fetched_tweets.append({
                            "id": t_id,
                            "text": text,
                            "created_at": iso_date,
                            "favorite_count": fav_count,
                            "retweet_count": rt_count,
                            "views": int(views) if views else 0,
                            "url": f"https://twitter.com/{screen_name}/status/{t_id}"
                        })

                print(f"✨ 成功從結構化資料中解析出 {len(fetched_tweets)} 則推文！")

        except Exception as e:
            print(f"⚠️ 瀏覽器渲染推文頁面失敗: {e}")
        finally:
            browser.close()

    return fetched_tweets

def save_merged_tweets(filepath, new_tweets):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    existing_tweets = load_existing_tweets(filepath)

    tweets_map = {str(t.get("id", "")).strip(): t for t in existing_tweets if t.get("id")}

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
    tweets = fetch_tweets_with_playwright(TARGET_HANDLE)
    save_merged_tweets(TWEETS_FILE, tweets)
