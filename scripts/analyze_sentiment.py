import os
import json
import time
import re
import urllib.request
import urllib.error

# ==========================================
# 參數設定區 (Burak Finance 專屬)
# ==========================================
CACHE_FILE = "data/sentiment_cache.json"
TWEETS_FILE = "data/tweets.json"
TWITTER_EPOCH = 1288834974657

# 突破 71% 覆蓋率關鍵：將總經、大盤與利率推文一併納入情緒分析
ANALYZE_MACRO_TWEETS = True

# 讀取 GEMINI_API_KEY 並清除可能存在的空白或引號
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip().replace('"', '').replace("'", "")

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

def get_model_score(model_name):
    """計算模型版本權重，確保新版本（如 3.6 > 3.0 > 2.0）排序在最前"""
    score = 0
    lower = model_name.lower()
    ver_match = re.search(r"gemini-(\d+(?:\.\d+)?)", lower)
    if ver_match:
        try:
            score += float(ver_match.group(1)) * 100
        except ValueError:
            pass
    if "flash" in lower:
        score += 50
    if "pro" in lower:
        score += 30
    if "preview" in lower:
        score -= 5
    return score

def fetch_online_gemini_models(api_key):
    """動態向 Google AI Studio 查詢最新可用模型清單"""
    list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key
    }
    req = urllib.request.Request(list_url, headers=headers)
    models = []

    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            if res.status == 200:
                data = json.loads(res.read().decode("utf-8"))
                raw_models = data.get("models", [])
                for m in raw_models:
                    methods = m.get("supportedGenerationMethods", [])
                    name = m.get("name", "").replace("models/", "")
                    if "generateContent" in methods and "gemini" in name.lower() and "vision" not in name.lower() and "embedding" not in name.lower():
                        models.append(name)
                models.sort(key=get_model_score, reverse=True)
    except Exception as e:
        print(f"⚠️ 動態查詢模型清單警告: {e}", flush=True)

    # 保底預設名單（以官方要求的 gemini-3.6-flash 為首選）
    default_candidates = [
        "gemini-3.6-flash",
        "gemini-3.0-flash",
        "gemini-2.0-flash",
        "gemini-pro"
    ]
    
    # 合併去重並維持高版本排序
    combined = []
    for item in models + default_candidates:
        if item not in combined:
            combined.append(item)
    return combined

def detect_working_model(api_key):
    """逐一驗證候選模型，鎖定第一個確認可正常回傳的端點"""
    candidate_models = fetch_online_gemini_models(api_key)
    print(f"🔎 探測候選模型清單: {candidate_models[:4]}", flush=True)

    payload = json.dumps({"contents": [{"parts": [{"text": "Ping"}]}]}).encode("utf-8")

    for model_name in candidate_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as res:
                if res.status == 200:
                    print(f"🤖 動態探測成功！本輪啟用最佳可用模型：{model_name}", flush=True)
                    return model_name
        except Exception:
            continue

    # 若探測皆未成功，回傳最新標準端點
    return "gemini-3.6-flash"

def analyze_tweet_with_gemini(text, model_name, api_key):
    """使用原生 REST API 與 x-goog-api-key 標頭進行推文結構化分析"""
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
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            if res.status == 200:
                result = json.loads(res.read().decode("utf-8"))
                if result.get("candidates") and result["candidates"][0].get("content"):
                    raw_out = result["candidates"][0]["content"]["parts"][0]["text"].strip()
                    # 去除可能包含的 Markdown 語法標記
                    raw_out = re.sub(r"^```json\s*", "", raw_out)
                    raw_out = re.sub(r"\s*```$", "", raw_out)
                    return json.loads(raw_out)
    except Exception as e:
        print(f"⚠️ 分析呼叫異常: {e}", flush=True)
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

    # 依 Twitter Snowflake 時間戳降冪排序（最新推文優先分析）
    tweets_data.sort(key=lambda x: snowflake_to_timestamp(x.get("id") or x.get("id_str") or 0), reverse=True)

    # 篩選出尚未分析的推文
    pending = []
    for t in tweets_data:
        t_id = str(t.get("id") or t.get("id_str") or "")
        t_text = t.get("text") or t.get("full_text") or ""
        if not t_id or not t_text:
            continue
        
        has_ticker = bool(re.search(r"(?<!\w)\$([A-Za-z]{1,6})\b", t_text))
        # 若未包含 $ 標記，依據 ANALYZE_MACRO_TWEETS 決定是否納入分析
        if not has_ticker and not ANALYZE_MACRO_TWEETS:
            continue

        if t_id not in cache_dict:
            pending.append((t_id, t_text))

    print(f"📊 總推文數：{len(tweets_data)} | 已分析：{len(cache_dict)} | 待分析：{len(pending)}")
    
    if pending:
        # 動態鎖定目前確定可用的最高版本模型
        active_model = detect_working_model(GEMINI_API_KEY)
        success_count = 0
        batch_limit = min(len(pending), 30)

        for idx, (tweet_id, text) in enumerate(pending[:batch_limit]):
            print(f"  🔍 [{idx+1}/{batch_limit}] 正在分析推文 {tweet_id}...", flush=True)
            res = analyze_tweet_with_gemini(text, active_model, GEMINI_API_KEY)
            if res:
                cache_dict[tweet_id] = {
                    "id": tweet_id,
                    "sentiment": res.get("sentiment", "Neutral"),
                    "summary": res.get("summary_zh", ""),
                    "translation_zh": res.get("translation_zh", "")
                }
                success_count += 1

                # 每完成 5 筆自動寫入磁碟，避免中斷遺失進度
                if success_count % 5 == 0:
                    save_cache(CACHE_FILE, cache_dict)
                    print(f"  💾 已自動儲存最新進度至 {CACHE_FILE}", flush=True)

            time.sleep(1.2)  # 調用速率保護

        save_cache(CACHE_FILE, cache_dict)
        print(f"🎉 本輪分析完成，新增 {success_count} 筆，累計已分析：{len(cache_dict)} 筆。")
    else:
        print("✅ 所有推文均已分析完畢，覆蓋率 100%！")
