import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
import email.utils

TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

# 多節點備援清單 (包含 Nitter 鏡像與 Syndication RSS)
RSS_MIRRORS = [
    f"https://xcancel.com/{TARGET_HANDLE}/rss",
    f"https://nitter.poast.org/{TARGET_HANDLE}/rss",
    f"https://nitter.privacydev.net/{TARGET_HANDLE}/rss",
    f"https://nitter.lucabased.xyz/{TARGET_HANDLE}/rss"
]

def load_existing_tweets(filepath):
    """讀取本地現有推文資料庫"""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                for key in ["tweets", "data", "statuses", "results"]:
                    if key in data and isinstance(data[key], list):
                        return data[key]
                return list(data.values())
            return []
    except Exception as e:
        print(f"⚠️ 讀取現有推文失敗: {e}")
        return []

def clean_html_tags(raw_html):
    """清理 RSS 描述中的 HTML 標籤，保留純文字"""
    if not raw_html:
        return ""
    text = re.sub(r'<br\s*/?>', '\n', raw_html)
    text = re.sub(r'<a\s+href="([^"]+)"[^>]*>.*?</a>', r'\1', text)
    text = re.sub(r'<[^>]+>', '', text)
    # 解碼常見 HTML 實體
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"').replace('&#39;', "'")
    return text.strip()

def parse_rss_pubdate(pub_date_str):
    """將 RFC 822 時間格式轉換為標準 ISO 時間字串"""
    try:
        parsed_tuple = email.utils.parsedate_tz(pub_date_str)
        if parsed_tuple:
            timestamp = email.utils.mktime_tz(parsed_tuple)
            dt = datetime.utcfromtimestamp(timestamp)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def fetch_tweets_from_rss():
    """依序嘗試備援鏡像節點抓取推文"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*"
    }

    for url in RSS_MIRRORS:
        try:
            print(f"📡 嘗試透過節點抓取: {url}")
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as response:
                if response.status != 200:
                    continue
                xml_content = response.read()

            root = ET.fromstring(xml_content)
            items = root.findall("./channel/item")
            
            if not items:
                print(f"  ℹ️ 節點未回傳推文，嘗試下一個...")
                continue

            fetched_tweets = []
            for item in items:
                title = item.findtext("title") or ""
                description = item.findtext("description") or ""
                link = item.findtext("link") or ""
                guid = item.findtext("guid") or ""
                pub_date = item.findtext("pubDate") or ""

                # 優先採用 description 內容並清理 HTML
                text_content = clean_html_tags(description) if description else title.strip()
                
                # 從連結擷取推文 ID
                tweet_id = ""
                full_url = link or guid
                id_match = re.search(r"status/(\d+)", full_url)
                if id_match:
                    tweet_id = id_match.group(1)
                elif guid:
                    tweet_id = guid.split("/")[-1].replace("#m", "").strip()

                if tweet_id and text_content:
                    iso_date = parse_rss_pubdate(pub_date)
                    canonical_url = f"https://twitter.com/{TARGET_HANDLE}/status/{tweet_id}"
                    
                    fetched_tweets.append({
                        "id": tweet_id,
                        "text": text_content,
                        "created_at": iso_date,
                        "favorite_count": 0,
                        "retweet_count": 0,
                        "views": 0,
                        "url": canonical_url
                    })

            if fetched_tweets:
                print(f"✅ 成功從 [{url}] 取得 {len(fetched_tweets)} 則最新推文！")
                return fetched_tweets

        except Exception as e:
            print(f"  ⚠️ 節點 [{url}] 連線失敗: {e}")
            continue

    print("⚠️ 所有鏡像節點暫時無法存取，將維持現有推文資料。")
    return []

def save_merged_tweets(filepath, new_tweets):
    """比對去重並更新本地資料庫"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    existing_tweets = load_existing_tweets(filepath)

    tweets_map = {}
    for t in existing_tweets:
        t_id = str(t.get("id", "")).strip()
        if t_id:
            tweets_map[t_id] = t

    added_count = 0
    for t in new_tweets:
        t_id = str(t.get("id", "")).strip()
        if t_id and t_id not in tweets_map:
            tweets_map[t_id] = t
            added_count += 1

    merged_list = list(tweets_map.values())
    # 依時間新至舊排序
    merged_list.sort(key=lambda x: str(x.get("created_at", "") or x.get("date", "")), reverse=True)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(merged_list, f, ensure_ascii=False, indent=2)

    print(f"🎉 資料更新完成！本次新增 {added_count} 則推文，目前推文資料庫總計: {len(merged_list)} 則。")

if __name__ == "__main__":
    print(f"🔄 開始抓取 @{TARGET_HANDLE} 的最新推文...")
    recent_tweets = fetch_tweets_from_rss()
    save_merged_tweets(TWEETS_FILE, recent_tweets)
