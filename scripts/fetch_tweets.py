import json
import os
import re
import urllib.request
import urllib.parse
from datetime import datetime
import xml.etree.ElementTree as ET

TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

def load_existing_tweets(filepath):
    """讀取本地現有推文資料庫"""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"⚠️ [DEBUG] 讀取現有推文失敗: {e}")
        return []

def fetch_via_fxtwitter_api(screen_name):
    """軌道一：透過 FxTwitter 開放 API 取得推文與發布時間"""
    url = f"https://api.fxtwitter.com/{screen_name}"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; SerenityBot/1.0; +https://github.com/)"
    }
    
    print(f"🔍 [軌道 1 - FxTwitter] 正在連線: {url}")
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as response:
            status = response.status
            content = response.read().decode("utf-8")
            print(f"  ↳ HTTP 狀態碼: {status}，回傳內容長度: {len(content)} 字元")

            data = json.loads(content)
            user_data = data.get("user", {}) or data
            tweets_raw = user_data.get("tweets", []) or data.get("tweets", [])

            fetched = []
            for tw in tweets_raw:
                t_id = str(tw.get("id") or tw.get("id_str") or "").strip()
                text = tw.get("text") or tw.get("full_text") or ""
                created_at = tw.get("created_at") or tw.get("created_timestamp") or ""
                likes = tw.get("likes", 0) or tw.get("favorite_count", 0)
                rts = tw.get("retweets", 0) or tw.get("retweet_count", 0)
                views = tw.get("views", 0)

                iso_date = ""
                if isinstance(created_at, (int, float)):
                    iso_date = datetime.utcfromtimestamp(created_at).strftime("%Y-%m-%dT%H:%M:%SZ")
                else:
                    iso_date = str(created_at)

                if t_id and text:
                    fetched.append({
                        "id": t_id,
                        "text": text,
                        "created_at": iso_date,
                        "favorite_count": likes,
                        "retweet_count": rts,
                        "views": views,
                        "url": f"https://twitter.com/{screen_name}/status/{t_id}"
                    })

            if fetched:
                print(f"  ✨ [軌道 1 成功] 解析出 {len(fetched)} 則推文！")
                return fetched
            else:
                print("  ℹ️ [軌道 1] 回應中未包含推文陣列。")

    except Exception as e:
        print(f"  ⚠️ [軌道 1 失敗]: {e}")

    return []

def fetch_via_playwright_dom(screen_name):
    """軌道二：透過 Playwright 渲染 DOM 樹與攔截結構化數據"""
    print(f"🔍 [軌道 2 - Playwright] 啟動瀏覽器載入 Twitter 串流...")
    fetched = []

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            # 嘗試載入公開嵌入頁面
            target_url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{screen_name}"
            response = page.goto(target_url, timeout=25000, wait_until="domcontentloaded")
            status_code = response.status if response else "Unknown"
            title = page.title()
            html_text = page.content()

            print(f"  ↳ 瀏覽器載入完成: HTTP {status_code} | 標題: '{title}' | 頁面長度: {len(html_text)} 字元")

            # 1. 嘗試解析 __NEXT_DATA__
            match = re.search(r'<script id="__NEXT_DATA__"[^>]*>([^<]+)</script>', html_text)
            if match:
                print("  ↳ 成功偵測到 __NEXT_DATA__ JSON 區塊，正在解析...")
                data = json.loads(match.group(1))
                entries = data.get("props", {}).get("pageProps", {}).get("timeline", {}).get("entries", [])
                for entry in entries:
                    tw = entry.get("content", {}).get("tweet", {})
                    t_id = str(tw.get("id_str") or tw.get("id", "")).strip()
                    text = tw.get("full_text") or tw.get("text", "")
                    if t_id and text:
                        fetched.append({
                            "id": t_id,
                            "text": text,
                            "created_at": tw.get("created_at", datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")),
                            "favorite_count": tw.get("favorite_count", 0),
                            "retweet_count": tw.get("retweet_count", 0),
                            "views": 0,
                            "url": f"https://twitter.com/{screen_name}/status/{t_id}"
                        })

            # 2. 若 JSON 無資料，改由 DOM 選取器直接抓取推文內文
            if not fetched:
                print("  ↳ 正在嘗試以 DOM 選擇器抓取畫面文字...")
                tweet_elements = page.query_selector_all("article, [data-testid='tweetText'], .timeline-Tweet-text")
                print(f"  ↳ 畫面上找到 {len(tweet_elements)} 個推文相關 DOM 節點")
                for el in tweet_elements:
                    txt = el.inner_text().strip()
                    if txt and len(txt) > 10:
                        t_id = str(abs(hash(txt)))[:18]
                        fetched.append({
                            "id": t_id,
                            "text": txt,
                            "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "favorite_count": 0,
                            "retweet_count": 0,
                            "views": 0,
                            "url": f"https://twitter.com/{screen_name}"
                        })

            browser.close()

            if fetched:
                print(f"  ✨ [軌道 2 成功] Playwright 解析出 {len(fetched)} 則推文！")
                return fetched
            else:
                print(f"  ℹ️ [軌道 2] 頁面預覽內容（前 200 字）: {html_text[:200]}")

    except Exception as e:
        print(f"  ⚠️ [軌道 2 失敗]: {e}")

    return []

def fetch_via_public_rss_mirrors(screen_name):
    """軌道三：透過多組公開 RSS 鏡像節點擷取推文"""
    mirrors = [
        f"https://rss.owo.nz/twitter/user/{screen_name}",
        f"https://hub.slqwq.top/twitter/user/{screen_name}",
        f"https://nitter.poast.org/{screen_name}/rss",
        f"https://xcancel.com/{screen_name}/rss"
    ]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*"
    }

    for url in mirrors:
        print(f"🔍 [軌道 3 - RSS 鏡像] 正在嘗試: {url}")
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as response:
                status = response.status
                xml_data = response.read()
                print(f"  ↳ HTTP 狀態碼: {status}，資料長度: {len(xml_data)} 位元組")

                root = ET.fromstring(xml_data)
                items = root.findall("./channel/item")
                
                fetched = []
                for item in items:
                    title = item.findtext("title") or ""
                    desc = item.findtext("description") or ""
                    link = item.findtext("link") or item.findtext("guid") or ""
                    
                    text_content = desc or title
                    text_clean = re.sub(r'<[^>]+>', '', text_content).strip()
                    
                    id_m = re.search(r"status/(\d+)", link)
                    t_id = id_m.group(1) if id_m else str(abs(hash(text_clean)))[:18]

                    if t_id and text_clean:
                        fetched.append({
                            "id": t_id,
                            "text": text_clean,
                            "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "favorite_count": 0,
                            "retweet_count": 0,
                            "views": 0,
                            "url": f"https://twitter.com/{screen_name}/status/{t_id}"
                        })

                if fetched:
                    print(f"  ✨ [軌道 3 成功] 從 [{url}] 解析出 {len(fetched)} 則推文！")
                    return fetched

        except Exception as e:
            print(f"  ⚠️ 鏡像節點連線失敗: {e}")

    return []

def save_merged_tweets(filepath, new_tweets):
    """比對去重並更新本地推文資料庫"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    existing_tweets = load_existing_tweets(filepath)

    tweets_map = {str(t.get("id", "")).strip(): t for t in existing_tweets if t.get("id")}

    added_count = 0
    for t in new_tweets:
        t_id = str(t.get("id", "")).strip()
        if t_id and t_id not in tweets_map:
            tweets_map[t_id] = t
            added_count += 1

    merged_list = list(tweets_map.values())
    merged_list.sort(key=lambda x: str(x.get("created_at", "") or x.get("date", "")), reverse=True)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(merged_list, f, ensure_ascii=False, indent=2)

    print(f"📊 [結算報告] 本次新增: {added_count} 則推文 | 目前資料庫總推文數: {len(merged_list)} 則")

if __name__ == "__main__":
    print(f"🚀 開始針對 @{TARGET_HANDLE} 進行多軌推文擷取與診斷...")
    
    # 依序執行三個軌道
    results = fetch_via_fxtwitter_api(TARGET_HANDLE)
    if not results:
        results = fetch_via_playwright_dom(TARGET_HANDLE)
    if not results:
        results = fetch_via_public_rss_mirrors(TARGET_HANDLE)
        
    save_merged_tweets(TWEETS_FILE, results)
