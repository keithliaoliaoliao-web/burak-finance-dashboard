import os
import json
import time
import re
import urllib.request
import urllib.error

CACHE_FILE = "data/sentiment_cache.json"
TWEETS_FILE = "data/tweets.json"
TWITTER_EPOCH = 1288834974657

# 突破 71% 覆蓋率關鍵：將總經、大盤與利率推文一併納入情緒分析
ANALYZE_MACRO_TWEETS = True

# 讀取 GEMINI_API_KEY
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

def snowflake_to_timestamp(tweet_id_str):
    try:
        t_id = int(str(tweet_id_str).strip())
        return (t_id >> 22) + TWITTER_EPOCH
    except Exception:
        return 0

def load_json(filepath):
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, (list, dict)) else []
    except Exception:
        return []

def save_cache(filepath, data_dict):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data_dict, f, ensure_ascii=False, indent=2)

def detect_available_model(api_key):
    """向 Google AI Studio 原生 REST 端點探測最新可用模型"""
    candidate_models = ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-1.5-flash", "gemini-pro"]
    for m in candidate_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
        payload = json.dumps({"contents": [{"parts": [{"text": "Ping"}]}]}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                if res.status == 200:
                    print(f"🤖 動態探測成功！啟用模型：{m}", flush=True)
                    return m
        except Exception:
            continue
    return "gemini-2.0-flash"

def analyze_tweet_with_gemini(text, model_name, api_key):
    """使用原生 REST API 進行情緒分析，無 SDK 棄用警告"""
    prompt = f"""
你是一位專業的美股社群量化情報專家。請分析以下這篇美股情報推文：
\"\"\"{text}\"\"\"

請嚴格僅回傳以下格式的 JSON 字串（不要包含 Markdown 引號標籤）：
{{
  "sentiment": "Bullish 或 Bearish 或 Neutral",
  "summary_zh": "繁體中文核心觀點摘要（25字以內，直指重點）",
  "translation_zh": "流暢自然的繁體中文翻譯"
}}
"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            result = json.loads(res.read().decode("utf-8"))
            if result.get("candidates"):
                raw_out = result["candidates"][0]["content"]["parts"][0]["text"].strip()
                # 剔除可能包含的 markdown 區塊
                raw_out = re.sub(r"^```json\s*", "", raw_out)
                raw_out = re.sub(r"\s*```$", "", raw_out)
                return json.loads(raw_out)
    except Exception as e:
        print(f"⚠️ 分析呼叫異常: {e}")
    return None

if __name__ == "__main__":
    if not GEMINI_API_KEY:
        print("⚠️ 未檢測到 GEMINI_API_KEY 環境變數，跳過 AI 情感分析步驟。")
        exit(0)

    tweets_data = load_json(TWEETS_FILE)
    if not isinstance(tweets_data, list) or len(tweets_data) == 0:
        print("推文庫為空，結束分析。")
        exit(0)

    raw_cache = load_json(CACHE_FILE)
    cache_dict = {}
    if isinstance(raw_cache, dict):
        cache_dict = raw_cache
    elif isinstance(raw_cache, list):
        for item in raw_cache:
            if isinstance(item, dict) and item.get("id"):
                cache_dict[str(item["id"])] = item

    # 嚴格執行「最新推文優先（Newest First）」排序
    tweets_data.sort(key=lambda x: snowflake_to_timestamp(x.get("id") or x.get("id_str") or 0), reverse=True)

    # 找出待分析清單
    pending = []
    for t in tweets_data:
        t_id = str(t.get("id") or t.get("id_str") or "")
        t_text = t.get("text") or t.get("full_text") or ""
        if not t_id or not t_text:
            continue
        
        # 突破 71% 覆蓋率核心：若無 $ 標籤但 ANALYZE_MACRO_TWEETS=True，照常納入分析
        has_ticker = bool(re.search(r"(?<!\w)\$([A-Za-z]{1,6})\b", t_text))
        if not has_ticker and not ANALYZE_MACRO_TWEETS:
            continue

        if t_id not in cache_dict:
            pending.append((t_id, t_text))

    print(f"📊 總推文數：{len(tweets_data)} | 已分析：{len(cache_dict)} | 待分析：{len(pending)}")
    
    if pending:
        active_model = detect_available_model(GEMINI_API_KEY)
        success_count = 0

        for idx, (tweet_id, text) in enumerate(pending[:30]): # 每次執行分析前 30 則
            print(f"  🔍 [{idx+1}/{min(len(pending), 30)}] 正在分析推文 {tweet_id}...", flush=True)
            res = analyze_tweet_with_gemini(text, active_model, GEMINI_API_KEY)
            if res:
                cache_dict[tweet_id] = {
                    "id": tweet_id,
                    "sentiment": res.get("sentiment", "Neutral"),
                    "summary": res.get("summary_zh", ""),
                    "translation_zh": res.get("translation_zh", "")
                }
                success_count += 1

                # 每完成 5 筆自動寫入快取防丟失
                if success_count % 5 == 0:
                    save_cache(CACHE_FILE, cache_dict)
                    print(f"  💾 已自動儲存最新進度至 {CACHE_FILE}", flush=True)

            time.sleep(1.2) # API 調用速率保護

        save_cache(CACHE_FILE, cache_dict)
        print(f"🎉 本輪分析完成，新增 {success_count} 筆，累計分析：{len(cache_dict)} 筆。")
    else:
        print("✅ 所有推文均已分析完畢，覆蓋率 100%！")
