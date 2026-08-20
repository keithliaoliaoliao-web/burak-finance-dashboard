import json
import os
import re
import time
from datetime import datetime
import google.generativeai as genai

TWEETS_FILE = "data/tweets.json"
CACHE_FILE = "data/sentiment_cache.json"

API_KEY = os.environ.get("GEMINI_API_KEY")

def load_tweets(filepath):
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
        print(f"⚠️ 讀取推文失敗: {e}")
        return []

def load_cache(filepath):
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            elif isinstance(data, list):
                cache_dict = {}
                for item in data:
                    if isinstance(item, dict):
                        t_id = str(item.get("id") or item.get("tweet_id") or "")
                        if t_id:
                            cache_dict[t_id] = item
                return cache_dict
            return {}
    except Exception as e:
        print(f"⚠️ 讀取快取失敗: {e}")
        return {}

def save_json(filepath, data):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def extract_tickers(text):
    if not text:
        return []
    matches = re.findall(r"(?<!\w)\$([A-Z]{1,5})\b", text.upper())
    blacklist = {"USD", "CAD", "EUR", "ATH", "CEO", "AI", "FOMC", "FED", "CPI", "GDP", "DD", "EOD", "YOLO"}
    return [t for t in set(matches) if t not in blacklist]

def extract_tweet_id(item):
    for k in ["id", "id_str", "tweet_id", "tweetId", "rest_id", "conversation_id"]:
        if k in item and item[k]:
            return str(item[k]).strip()
    url = item.get("url") or item.get("permanentUrl") or item.get("link") or ""
    if url:
        m = re.search(r"status/(\d+)", str(url))
        if m:
            return m.group(1).strip()
    return ""

def extract_tweet_text(item):
    for k in ["text", "rawContent", "full_text", "content", "tweet", "body"]:
        if k in item and item[k]:
            return str(item[k])
    legacy = item.get("legacy") if isinstance(item.get("legacy"), dict) else {}
    if "full_text" in legacy:
        return str(legacy["full_text"])
    return ""

def parse_sort_key(item):
    raw_date = None
    for k in ["date", "created_at", "createdAt", "timestamp", "datetime", "time"]:
        if k in item and item[k]:
            raw_date = item[k]
            break
    if not raw_date:
        legacy = item.get("legacy") if isinstance(item.get("legacy"), dict) else {}
        raw_date = legacy.get("created_at")

    if not raw_date:
        return ""

    if isinstance(raw_date, (int, float)):
        try:
            dt = datetime.fromtimestamp(raw_date / 1000.0 if raw_date > 1e11 else raw_date)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    s = str(raw_date).strip()
    if s.isdigit():
        try:
            dt = datetime.fromtimestamp(float(s) / 1000.0 if float(s) > 1e11 else float(s))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    try:
        if "T" in s or "+" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass

    try:
        if len(s.split()) >= 6:
            dt = datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass

    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        y, mth, d = m.groups()
        return f"{int(y):04d}-{int(mth):02d}-{int(d):02d}"

    return s

def get_candidate_models():
    if not API_KEY:
        print("❌ 錯誤：未檢測到 GEMINI_API_KEY 環境變數。")
        return []

    genai.configure(api_key=API_KEY)
    
    try:
        available_models = []
        for m in genai.list_models():
            if "generateContent" in m.supported_generation_methods:
                model_name = m.name.replace("models/", "")
                available_models.append(model_name)
        
        preferred_priority = [
            "gemini-3.6-flash",
            "gemini-3.7-flash",
            "gemini-flash-latest",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-flash-lite-latest",
            "gemini-3-flash-preview"
        ]
        
        selected_models = [m for m in preferred_priority if m in available_models]
        for m in available_models:
            if m not in selected_models and ("flash" in m or "pro" in m):
                selected_models.append(m)
                
        if selected_models:
            print(f"📡 已成功動態偵測可用模型: {selected_models}")
            return selected_models
    except Exception as e:
        print(f"⚠️ 動態查詢模型失敗 ({e})，採用備用模型清單。")

    return [
        "gemini-3.6-flash",
        "gemini-3.7-flash",
        "gemini-flash-latest",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite"
    ]

def analyze_sub_batch(items, candidate_models):
    prompt = """你是一位資深美股分析師。請分析以下 Twitter 貼文：
1. 判斷對標的 ($TICKER) 的交易情緒："Bullish" (看多/買進), "Bearish" (看空/警戒), 或 "Neutral" (中立/客觀分析)。
2. 將推文完整翻譯為繁體中文 (translation_zh)。
3. 提供 15 字以內的繁體中文核心觀點摘要 (summary)。

請嚴格輸出 JSON 格式物件（以推文 ID 作為 Key），範例如下：
{
  "推文ID_1": {
    "sentiment": "Bullish",
    "summary": "看好本季營收爆發突破新高",
    "translation_zh": "完整中文翻譯內容..."
  }
}

待分析推文清單：
"""
    for item in items:
        prompt += f"\nID: {item['id']}\nText: {item['text']}\n---"

    for m_name in candidate_models:
        try:
            model = genai.GenerativeModel(m_name)
            try:
                response = model.generate_content(
                    prompt,
                    generation_config={"response_mime_type": "application/json"}
                )
            except Exception:
                response = model.generate_content(prompt)

            raw_text = response.text.strip()
            json_match = re.search(r"\{[\s\S]*\}", raw_text)
            if not json_match:
                continue
                
            result = json.loads(json_match.group(0))
            if isinstance(result, dict) and result:
                print(f"    ✨ 模型 [{m_name}] 成功解析 {len(result)} 則推文！")
                return result
        except Exception as e:
            print(f"    ⚠️ 模型 [{m_name}] 調用失敗 ({e})，切換至下一個備用模型...")
            time.sleep(1)

    return {}

def run_sentiment_pipeline(total_target=50, chunk_size=10):
    candidate_models = get_candidate_models()
    if not candidate_models:
        return

    raw_data = load_tweets(TWEETS_FILE)
    sentiment_cache = load_cache(CACHE_FILE)

    raw_data.sort(key=parse_sort_key, reverse=True)

    unprocessed = []
    for item in raw_data:
        t_id = extract_tweet_id(item)
        text = extract_tweet_text(item)
        if not t_id or not text:
            continue

        cached = sentiment_cache.get(t_id)
        needs_analysis = (
            not cached or 
            not isinstance(cached, dict) or 
            not cached.get("summary") or 
            not cached.get("translation_zh")
        )

        if extract_tickers(text) and needs_analysis:
            unprocessed.append({"id": t_id, "text": text})

    print(f"🔍 待分析個股推文總數: {len(unprocessed)} 則")
    if not unprocessed:
        print("所有最新推文均已在快取中。")
        return

    to_process = unprocessed[:total_target]
    print(f"🚀 本次將分批處理最新 {len(to_process)} 則推文（每批 {chunk_size} 則）...")

    added_count = 0
    for i in range(0, len(to_process), chunk_size):
        chunk = to_process[i:i + chunk_size]
        print(f"  正在分析第 {i + 1} ~ {i + len(chunk)} 則...")
        results = analyze_sub_batch(chunk, candidate_models)

        batch_updated = False
        for item in chunk:
            t_id = item["id"]
            res = results.get(t_id) or results.get(str(t_id))
            if res and isinstance(res, dict):
                sentiment_cache[t_id] = {
                    "sentiment": res.get("sentiment", "Neutral"),
                    "summary": res.get("summary", ""),
                    "translation_zh": res.get("translation_zh", "")
                }
                added_count += 1
                batch_updated = True
                print(f"    ✅ 已儲存 [{t_id}]: {res.get('summary')}")

        if batch_updated:
            save_json(CACHE_FILE, sentiment_cache)

        time.sleep(2)

    print(f"🎉 分析作業完成！本次更新 {added_count} 則推文。目前總快取量: {len(sentiment_cache)} 則。")

if __name__ == "__main__":
    target = int(os.environ.get("TOTAL_TARGET", 50))
    run_sentiment_pipeline(total_target=target, chunk_size=10)
