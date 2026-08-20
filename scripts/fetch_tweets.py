import requests

def fetch_from_fxtwitter(username: str):
    url = f"https://api.fxtwitter.com/{username}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            print(f"⚠️ [軌道 1 失敗]: HTTP {response.status_code}")
            return []
            
        data = response.json()
        
        # 防呆檢查：確認回傳為字典結構
        if not isinstance(data, dict):
            print(f"⚠️ [軌道 1 失敗]: 回傳資料非 JSON 物件")
            return []

        # 取得推文列表：FxTwitter 使用者端點可能回傳 'tweets' 或單篇最新推文
        tweets_data = data.get("tweets")
        
        # 若 tweets 欄位不存在，嘗試檢查是否有單篇推文物件或其他結構
        if tweets_data is None:
            if "tweet" in data and isinstance(data["tweet"], dict):
                tweets_data = [data["tweet"]]
            else:
                tweets_data = []

        # 確保 tweets_data 為清單，避免 'int' object is not iterable
        if not isinstance(tweets_data, list):
            print(f"⚠️ [軌道 1 失敗]: 推文資料型態異常 ({type(tweets_data)})")
            return []

        parsed_tweets = []
        for tweet in tweets_data:
            if not isinstance(tweet, dict):
                continue
            parsed_tweets.append({
                "id": tweet.get("id"),
                "text": tweet.get("text", ""),
                "created_at": tweet.get("created_at"),
                "url": tweet.get("url")
            })

        print(f"✅ [軌道 1 成功]: 成功取得 {len(parsed_tweets)} 則推文")
        return parsed_tweets

    except Exception as e:
        print(f"⚠️ [軌道 1 失敗]: {e}")
        return []
