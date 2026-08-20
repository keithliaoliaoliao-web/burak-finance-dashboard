import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
import email.utils

TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"

# 多來源高可用備援節點清單 (包含 Farside 動態路由器與分散式 RSSHub 節點)
ENDPOINTS = [
    # 1. Farside 智慧路由器 (自動轉向全球可用 Nitter 實例)
    f"https://farside.link/nitter/{TARGET_HANDLE}/rss",
    # 2. 多組獨立 RSSHub 公開鏡像節點
    f"https://rsshub.app/twitter/user/{TARGET_HANDLE}",
    f"https://rss.fatpandaph.com/twitter/user/{TARGET_HANDLE}",
    f"https://hub.slqwq.top/twitter/user/{TARGET_HANDLE}",
    f"https://rss.owo.nz/twitter/user/{TARGET_HANDLE}",
    # 3. 備援 Nitter / xcancel 節點
    f"https://nitter.net/{TARGET_HANDLE}/rss",
    f"https://xcancel.com/{TARGET_HANDLE}/rss"
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
    """清理 RSS 描述中的 HTML 標籤，還原純文字"""
    if not raw_html:
        return ""
    text = re.sub(r'<br\s*/?>', '\n', raw_html)
    text = re.sub(r'<a\s+href="([^"]+)"[^>]*>.*?</a>', r'\1', text)
    text = re.sub(r'<[^>]+>', '', text)
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

def fetch_tweets_from_feed():
    """依序向備援清單發送請求，取得最新推文"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, application/atom+xml, */*",
        "Accept-Language": "en-US,en;q=0.9"
    }

    for url in ENDPOINTS:
        try:
            print(f"📡 正在探測節點: {url}")
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status != 200:
                    print(f"  ⚠️ 狀態碼異常 ({response.status})，切換下一個...")
                    continue
                content = response.read()

            # 解析 XML
            root = ET.fromstring(content)
            
            # 支援 RSS 2.0 (<channel><item>) 與 Atom (<feed><entry>) 結構
            items = root.findall("./channel/item")
            is_atom = False
            if not items:
                items = root.findall("{http://www.w3.org/2005/Atom}entry")
                is_atom = bool(items)

            if not items:
                print("  ℹ️ 節點回傳內容無推文項目，繼續嘗試備援節點...")
                continue

            fetched_tweets = []
            for item in items:
                if not is_atom:
                    title = item.findtext("title") or ""
                    desc = item.findtext("description") or ""
                    link = item.findtext("link") or ""
                    guid = item.findtext("guid") or ""
                    pub_date = item.findtext("pubDate") or ""
                else:
                    title = item.findtext("{http://www.w3.org/2005/Atom}title") or ""
                    desc = item.findtext("{http://www.w3.org/2005/Atom}content") or item.findtext("{http://www.w3.org/2005/Atom}summary") or ""
                    link_el = item.find("{http://www.w3.org/2005/Atom}link")
                    link = link_el.attrib.get("href", "") if link_el is not None else ""
                    guid = item.findtext("{http://www.w3.org/2005/Atom}id") or ""
                    pub_date = item.findtext("{http://www.w3.org/2005/Atom}published") or item.findtext("{http://www.w3.org/2005/Atom}updated") or ""

                text_content = clean_html_tags(desc) if desc else title.strip()
                
                # 擷取推文 ID
                full_url = link or guid
                tweet_id = ""
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
                print(f"✨ 成功從節點取得 {len(fetched_tweets)} 則最新推文！")
                return fetched_tweets

        except Exception as e:
            print(f"  ⚠️ 節點連線失敗: {e}")
            continue

    print("⚠️ 所有代理節點皆未回應，將維持既有推文快照。")
    return []

def save_merged_tweets(filepath, new_tweets):
    """將新推文與現有資料庫合併去重並儲存"""
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
    merged_list.sort(key=lambda x: str(x.get("created_at", "") or x.get("date", "")), reverse=True)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(merged_list, f, ensure_ascii=False, indent=2)

    print(f"🎉 本次新增 {added_count} 則推文，目前推文資料庫總數: {len(merged_list)} 則。")

if __name__ == "__main__":
    print(f"🔄 開始抓取 @{TARGET_HANDLE} 最新推文...")
    recent_tweets = fetch_tweets_from_feed()
    save_merged_tweets(TWEETS_FILE, recent_tweets)
