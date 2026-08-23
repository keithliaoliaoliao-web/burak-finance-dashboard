import json
import os
import re
from datetime import datetime
import yfinance as yf

# ==========================================
# 參數設定區 (Burak Finance 專屬)
# ==========================================
TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"
CACHE_FILE = "data/sentiment_cache.json"
OUTPUT_HTML = "docs/index.html"

TWITTER_EPOCH = 1288834974657

# 產業鏈與板塊分類定義 (含生技醫療與美股科技族群)
SECTOR_MAPPING = {
    "生技與醫療製藥": ["HIMS", "MRNA", "JNJ", "TEM", "LLY", "NVO", "ISRG", "CRSP", "VRTX", "AMGN"],
    "光通訊與雷射": ["AAOI", "LITE", "COHR", "POET", "CRDO", "CIEN", "FN"],
    "AI 算力與晶片": ["NBIS", "NVDA", "AMD", "AVGO", "MRVL", "ARM", "INTC", "TSM", "QCOM"],
    "記憶體與儲存": ["SNDK", "MU", "WDC", "PSTG", "STX"],
    "太空與國防科技": ["RKLB", "RCAT", "ASTS", "AVAV", "KTOS", "LMT", "RTX", "PL"],
    "潔淨能源與電力": ["BE", "SMR", "OKLO", "LEU", "UUUU", "VRT", "CEG", "FLNC", "GEV"],
    "高成長雲端軟體": ["PLTR", "SNOW", "NOW", "CRWD", "DDOG", "NET", "PATH", "MDB", "ORCL"]
}

def get_sector_for_ticker(ticker):
    """查詢股票所屬板塊"""
    for sector, symbols in SECTOR_MAPPING.items():
        if ticker.upper() in symbols:
            return sector
    return "其他科技 / 綜合"

def snowflake_to_iso(tweet_id_str):
    """利用 Twitter Snowflake 演算法計算精確發布時間"""
    try:
        t_id = int(str(tweet_id_str).strip())
        timestamp_ms = (t_id >> 22) + TWITTER_EPOCH
        dt = datetime.utcfromtimestamp(timestamp_ms / 1000.0)
        return dt.strftime("%Y-%m-%d %H:%M"), dt.strftime("%Y-%m"), dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None, None, None

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
        print(f"⚠️ 讀取推文檔案失敗 ({filepath}): {e}", flush=True)
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
        print(f"⚠️ 讀取快取檔案失敗 ({filepath}): {e}", flush=True)
        return []

def extract_tickers(text):
    """從推文內文中萃取美股代號"""
    if not text:
        return []
    matches = re.findall(r"(?<!\w)\$([A-Za-z]{1,6})\b", text)
    blacklist = {
        "USD", "USDT", "BTC", "ETH", "CAD", "EUR", "ATH", "CEO", "CFO", "CTO",
        "AI", "FOMC", "FED", "CPI", "PPI", "GDP", "DD", "EOD", "YOLO", "NEW",
        "BUY", "SELL", "HOLD", "CALL", "PUT", "AND", "THE", "TECH", "EV"
    }
    return sorted(list(set(t.upper() for t in matches if t.upper() not in blacklist and t.isalpha())))

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
    for k in ["text", "rawContent", "full_text", "content", "tweet", "body", "message"]:
        if k in item and item[k]:
            return str(item[k])
    legacy = item.get("legacy") if isinstance(item.get("legacy"), dict) else {}
    if "full_text" in legacy:
        return str(legacy["full_text"])
    return ""

def parse_date(item, tweet_id=""):
    """精確解析推文日期"""
    if tweet_id and tweet_id.isdigit() and len(tweet_id) >= 10:
        d_str, m_str, iso_str = snowflake_to_iso(tweet_id)
        if d_str:
            return d_str, m_str, iso_str

    raw_date = None
    for k in ["created_at", "date", "createdAt", "timestamp", "datetime", "time", "pubDate"]:
        val = item.get(k)
        if val and not str(val).startswith("1970"):
            raw_date = val
            break
            
    if not raw_date:
        legacy = item.get("legacy") if isinstance(item.get("legacy"), dict) else {}
        raw_date = legacy.get("created_at")

    if not raw_date or str(raw_date).startswith("1970"):
        return "未知時間", "未知月份", ""

    s = str(raw_date).strip()
    try:
        if "T" in s:
            clean_s = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_s)
            return dt.strftime("%Y-%m-%d %H:%M"), dt.strftime("%Y-%m"), s
    except Exception:
        pass

    return s, "未知月份", s

def extract_metrics(item):
    """解析互動指標（按讚、轉推、瀏覽量）"""
    likes, retweets, views = 0, 0, 0
    containers = [item]
    for sub in ["public_metrics", "metrics", "stats", "legacy"]:
        val = item.get(sub)
        if isinstance(val, dict):
            containers.append(val)

    def get_num(d, keys):
        if not isinstance(d, dict):
            return None
        for k in keys:
            if k in d and d[k] is not None:
                val_str = str(d[k]).strip()
                if val_str.isdigit():
                    return int(val_str)
        return None

    for c in containers:
        if not isinstance(c, dict):
            continue
        if likes == 0:
            val = get_num(c, ["favorite_count", "likeCount", "likes", "like_count", "favorites", "favoriteCount", "favs"])
            if val is not None:
                likes = val
        if retweets == 0:
            val = get_num(c, ["retweet_count", "retweetCount", "retweets", "reposts", "repost_count"])
            if val is not None:
                retweets = val
        if views == 0:
            val = get_num(c, ["view_count", "viewCount", "views", "impression_count", "impressions"])
            if val is not None:
                views = val

    return likes, retweets, views

def clean_tweet_data(raw_tweets, sentiment_cache):
    if isinstance(sentiment_cache, list):
        cache_dict = {}
        for item in sentiment_cache:
            if isinstance(item, dict):
                t_id = str(item.get("id") or item.get("tweet_id") or "")
                if t_id:
                    cache_dict[t_id] = item
        sentiment_cache = cache_dict
    elif not isinstance(sentiment_cache, dict):
        sentiment_cache = {}

    cleaned = []
    ticker_counts = {}
    recent_tickers = []

    for idx, item in enumerate(raw_tweets):
        if not isinstance(item, dict):
            continue

        tweet_id = extract_tweet_id(item)
        text = extract_tweet_text(item)
        if not text:
            continue

        date_str, month_str, iso_date = parse_date(item, tweet_id=tweet_id)
        tickers = extract_tickers(text)
        likes, retweets, views = extract_metrics(item)

        for t in tickers:
            ticker_counts[t] = ticker_counts.get(t, 0) + 1
            if idx < 60 and t not in recent_tickers:
                recent_tickers.append(t)

        ai_data = sentiment_cache.get(tweet_id) if tweet_id else None
        sentiment = "Neutral"
        summary = ""
        translation_zh = ""
        is_analyzed = False

        if ai_data and isinstance(ai_data, dict):
            raw_sent = ai_data.get("sentiment", "Neutral")
            if any(w in raw_sent for w in ["Bull", "多"]):
                sentiment = "Bullish"
            elif any(w in raw_sent for w in ["Bear", "空"]):
                sentiment = "Bearish"
            else:
                sentiment = "Neutral"

            summary = ai_data.get("summary", "") or ai_data.get("summary_zh", "")
            translation_zh = ai_data.get("translation_zh", "") or ai_data.get("chinese", "")
            is_analyzed = bool(summary or translation_zh)

        url = item.get("url") or item.get("permanentUrl") or item.get("link")
        if not url and tweet_id:
            url = f"https://twitter.com/{TARGET_HANDLE}/status/{tweet_id}"

        cleaned.append({
            "id": tweet_id,
            "text": text,
            "date": date_str,
            "month": month_str,
            "iso_date": iso_date,
            "tickers": tickers,
            "likes": likes,
            "retweets": retweets,
            "views": views,
            "sentiment": sentiment,
            "summary": summary,
            "translation_zh": translation_zh,
            "is_analyzed": is_analyzed,
            "url": url or "#"
        })

    cleaned.sort(key=lambda x: str(x.get("iso_date") or x.get("date") or ""), reverse=True)
    return cleaned, ticker_counts, recent_tickers

def fetch_stock_quotes_and_fundamentals(tickers):
    """獲取美股行情與基本面估值數據 (含財報日、市值、P/E、P/S、YoY)"""
    print(f"📈 正在擷取 {len(tickers)} 個關注標的的市場行情與基本面估值數據...", flush=True)
    quotes = {}
    
    for symbol in tickers:
        try:
            ticker_obj = yf.Ticker(symbol)
            fast = getattr(ticker_obj, "fast_info", None)
            info = ticker_obj.info or {}
            
            current_price = getattr(fast, "last_price", None) or getattr(fast, "regular_market_price", None) or info.get("currentPrice") or info.get("regularMarketPrice")
            prev_close = getattr(fast, "previous_close", None) or info.get("regularMarketPreviousClose") or info.get("previousClose")
            high_52 = getattr(fast, "year_high", None) or info.get("fiftyTwoWeekHigh")
            low_52 = getattr(fast, "year_low", None) or info.get("fiftyTwoWeekLow")
            volume = getattr(fast, "last_volume", None) or getattr(fast, "regular_market_volume", None) or info.get("volume")

            # 基本面指標
            market_cap = getattr(fast, "market_cap", None) or info.get("marketCap")
            forward_pe = info.get("forwardPE")
            trailing_pe = info.get("trailingPE")
            price_to_sales = info.get("priceToSalesTrailing12Months")
            revenue_growth = info.get("revenueGrowth")

            # 下次財報公布日
            earnings_date_str = None
            try:
                cal = ticker_obj.calendar
                if isinstance(cal, dict) and "Earnings Date" in cal and len(cal["Earnings Date"]) > 0:
                    earnings_date_str = str(cal["Earnings Date"][0])[:10]
            except Exception:
                pass

            if current_price is not None and float(current_price) > 0:
                change = (current_price - prev_close) if prev_close else 0.0
                change_pct = ((change / prev_close) * 100) if prev_close else 0.0

                quotes[symbol] = {
                    "price": round(float(current_price), 2),
                    "prevClose": round(float(prev_close), 2) if prev_close else round(float(current_price), 2),
                    "change": round(float(change), 2),
                    "changePct": round(float(change_pct), 2),
                    "high52": round(float(high_52), 2) if high_52 else None,
                    "low52": round(float(low_52), 2) if low_52 else None,
                    "volume": int(volume) if volume else 0,
                    "marketCap": int(market_cap) if market_cap else None,
                    "forwardPE": round(float(forward_pe), 2) if forward_pe else None,
                    "trailingPE": round(float(trailing_pe), 2) if trailing_pe else None,
                    "priceToSales": round(float(price_to_sales), 2) if price_to_sales else None,
                    "revenueGrowth": round(float(revenue_growth) * 100, 1) if revenue_growth else None,
                    "earningsDate": earnings_date_str,
                    "sector": get_sector_for_ticker(symbol)
                }
                print(f"  ✅ ${symbol}: ${current_price:.2f} ({change_pct:+.2f}%) | 板塊: {get_sector_for_ticker(symbol)}", flush=True)
        except Exception:
            continue

    return quotes

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-TW" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Burak Finance 美股情報與 AI 對話助理</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: {
              50: '#fffbeb',
              500: '#f59e0b',
              600: '#d97706',
              900: '#78350f',
            }
          }
        }
      }
    }
  </script>
  <style>
    body { background-color: #0b0f17; }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #0f172a; }
    ::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #475569; }
  </style>
</head>
<body class="text-slate-200 min-h-screen font-sans antialiased selection:bg-amber-500 selection:text-white">

  <!-- 頂部導航 (Burak 亮橘品牌設計) -->
  <header class="border-b border-slate-800/80 bg-slate-900/70 backdrop-blur sticky top-0 z-40">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded-lg bg-gradient-to-tr from-amber-500 to-orange-400 flex items-center justify-center font-bold text-slate-950 text-base shadow-lg shadow-amber-500/20">
          B
        </div>
        <div>
          <h1 class="font-bold text-base sm:text-lg tracking-tight text-white flex items-center gap-2">
            Burak Tracker
            <span class="text-[10px] uppercase font-semibold px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20">Live</span>
          </h1>
          <p class="text-xs text-slate-400 hidden sm:block">美股社群情報、AI 對話問答與個股論點脈絡</p>
        </div>
      </div>

      <!-- 四大核心視圖切換器 -->
      <div class="flex items-center bg-slate-950/80 p-1 rounded-xl border border-slate-800">
        <button onclick="setViewMode('all')" class="view-btn px-2.5 sm:px-3 py-1 rounded-lg text-xs font-medium text-slate-400 hover:text-white transition" data-view="all">全部</button>
        <button onclick="setViewMode('daily')" class="view-btn px-2.5 sm:px-3 py-1 rounded-lg text-xs font-medium text-slate-400 hover:text-white transition" data-view="daily">📅 每日</button>
        <button onclick="setViewMode('weekly')" class="view-btn px-2.5 sm:px-3 py-1 rounded-lg text-xs font-medium text-slate-400 hover:text-white transition" data-view="weekly">📆 每週 (7D)</button>
        <button onclick="setViewMode('monthly')" class="view-btn px-2.5 sm:px-3 py-1 rounded-lg text-xs font-medium text-slate-400 hover:text-white transition" data-view="monthly">📈 每月 (28D)</button>
        <button onclick="setViewMode('quarterly')" class="view-btn px-2.5 sm:px-3 py-1 rounded-lg text-xs font-medium text-slate-400 hover:text-white transition" data-view="quarterly">📊 季度 (90D)</button>
      </div>

      <div class="text-xs text-slate-400 font-mono hidden lg:block" id="last-update-time"></div>
    </div>
  </header>

  <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">

    <!-- 概覽數據統計指標 -->
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-4" id="stats-container">
      <div class="bg-slate-900/70 border border-slate-800 rounded-xl p-4 flex flex-col justify-between">
        <div>
          <span class="text-xs font-medium text-slate-400">當前視圖提及推文</span>
          <div class="text-2xl font-bold text-white mt-1" id="stat-total">0</div>
        </div>
        <div class="mt-2 text-[11px] text-amber-400 font-mono" id="stat-ai-coverage">AI 分析：0 / 0 (0%)</div>
      </div>
      <div class="bg-slate-900/70 border border-slate-800 rounded-xl p-4 flex flex-col justify-between">
        <div>
          <span class="text-xs font-medium text-emerald-400">看多觀點 (Bullish)</span>
          <div class="text-2xl font-bold text-emerald-400 mt-1" id="stat-bullish">0</div>
        </div>
        <div class="mt-2 text-[11px] text-slate-400">多方偏向佔比</div>
      </div>
      <div class="bg-slate-900/70 border border-slate-800 rounded-xl p-4 flex flex-col justify-between">
        <div>
          <span class="text-xs font-medium text-rose-400">看空/警戒 (Bearish)</span>
          <div class="text-2xl font-bold text-rose-400 mt-1" id="stat-bearish">0</div>
        </div>
        <div class="mt-2 text-[11px] text-slate-400">風險警戒貼文</div>
      </div>
      <div class="bg-slate-900/70 border border-slate-800 rounded-xl p-4 flex flex-col justify-between">
        <div>
          <span class="text-xs font-medium text-amber-400">視圖關注標的數</span>
          <div class="text-2xl font-bold text-amber-400 mt-1" id="stat-tickers">0</div>
        </div>
        <div class="mt-2 text-[11px] text-slate-400">活躍討論股票</div>
      </div>
    </div>

    <!-- 模組 1：產業鏈/板塊分類導航列 (含生技醫療與自選股切換) -->
    <div class="bg-slate-900/60 border border-slate-800/80 rounded-xl p-3.5 flex flex-wrap items-center gap-1.5" id="sector-bar">
      <span class="text-xs font-bold text-slate-400 mr-2 flex items-center gap-1">🏢 產業板塊：</span>
      <button onclick="setSectorFilter('ALL')" class="sector-btn active px-3 py-1 rounded-lg text-xs font-medium border border-slate-700 bg-slate-800 text-white transition" data-sector="ALL">全部板塊</button>
      <button onclick="setSectorFilter('WATCHLIST')" class="sector-btn px-3 py-1 rounded-lg text-xs font-medium border border-amber-500/30 text-amber-300 hover:bg-amber-500/10 transition" data-sector="WATCHLIST">⭐ 我的自選股</button>
      <button onclick="setSectorFilter('生技與醫療製藥')" class="sector-btn px-3 py-1 rounded-lg text-xs font-medium border border-transparent text-slate-400 hover:text-emerald-400 hover:bg-slate-800 transition" data-sector="生技與醫療製藥">💊 生技與醫療製藥</button>
      <button onclick="setSectorFilter('光通訊與雷射')" class="sector-btn px-3 py-1 rounded-lg text-xs font-medium border border-transparent text-slate-400 hover:text-amber-400 hover:bg-slate-800 transition" data-sector="光通訊與雷射">⚡ 光通訊與雷射</button>
      <button onclick="setSectorFilter('AI 算力與晶片')" class="sector-btn px-3 py-1 rounded-lg text-xs font-medium border border-transparent text-slate-400 hover:text-amber-400 hover:bg-slate-800 transition" data-sector="AI 算力與晶片">🧠 AI 算力與晶片</button>
      <button onclick="setSectorFilter('記憶體與儲存')" class="sector-btn px-3 py-1 rounded-lg text-xs font-medium border border-transparent text-slate-400 hover:text-amber-400 hover:bg-slate-800 transition" data-sector="記憶體與儲存">💾 記憶體與儲存</button>
      <button onclick="setSectorFilter('太空與國防科技')" class="sector-btn px-3 py-1 rounded-lg text-xs font-medium border border-transparent text-slate-400 hover:text-amber-400 hover:bg-slate-800 transition" data-sector="太空與國防科技">🚀 太空與國防科技</button>
      <button onclick="setSectorFilter('潔淨能源與電力')" class="sector-btn px-3 py-1 rounded-lg text-xs font-medium border border-transparent text-slate-400 hover:text-amber-400 hover:bg-slate-800 transition" data-sector="潔淨能源與電力">🔋 潔淨能源與電力</button>
      <button onclick="setSectorFilter('高成長雲端軟體')" class="sector-btn px-3 py-1 rounded-lg text-xs font-medium border border-transparent text-slate-400 hover:text-amber-400 hover:bg-slate-800 transition" data-sector="高成長雲端軟體">☁️ 高成長雲端軟體</button>
    </div>

    <!-- 熱門與近期關注標的快速過濾區 -->
    <div class="bg-slate-900/40 border border-slate-800/80 rounded-xl p-4">
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
          <span>🔥</span> 標的快速篩選 ($TICKER)
        </h2>
        <div class="flex items-center gap-3">
          <button onclick="openCompareModal()" class="text-xs font-semibold text-amber-400 hover:text-amber-300 flex items-center gap-1">
            ⚖️ 雙標的橫向對比
          </button>
          <button id="clear-ticker-btn" onclick="filterByTicker('')" class="text-xs text-amber-400 hover:underline hidden">清除標的篩選</button>
        </div>
      </div>
      <div class="flex flex-wrap gap-1.5" id="top-tickers-bar"></div>
    </div>

    <!-- 模組 2 & 3 & 4 & 5：個股即時行情 ＋ 基本面估值 ＋ 財報倒數 ＋ 情緒趨勢專區 -->
    <div id="stock-quote-section" class="bg-gradient-to-r from-slate-900/90 via-slate-900/70 to-slate-900/90 border border-slate-700/80 rounded-xl p-5 shadow-lg relative overflow-hidden hidden space-y-4">
      <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        
        <div class="flex items-center gap-4 flex-wrap">
          <div class="flex items-center gap-2">
            <div class="px-3 py-2 bg-slate-800 rounded-lg border border-slate-700 font-mono font-bold text-xl text-amber-400 shadow-inner" id="quote-ticker-name">
              $TICKER
            </div>
            <button id="quote-star-btn" onclick="toggleWatchlist(currentTicker, event)" class="text-xl text-slate-500 hover:text-amber-400 transition" title="加到自選股">
              ★
            </button>
          </div>
          <div>
            <div class="flex items-baseline gap-2">
              <span class="text-3xl font-bold font-mono text-white" id="quote-price">$0.00</span>
              <span class="text-xs text-slate-400" id="quote-currency-label">USD</span>
            </div>
            <div class="flex items-center gap-2 mt-0.5 text-sm font-semibold font-mono" id="quote-change-container">
              <span id="quote-change">$0.00</span>
              <span id="quote-change-pct">(0.00%)</span>
              <span class="text-xs font-normal text-slate-400">相對前一日收盤</span>
            </div>
          </div>
          <div class="flex items-center gap-2 ml-0 sm:ml-4">
            <button onclick="openDeepDiveModal(currentTicker)" class="px-3 py-1.5 text-xs font-semibold rounded-lg bg-amber-500 text-slate-950 hover:bg-amber-400 transition flex items-center gap-1 shadow-sm font-bold">
              🧠 AI 論點脈絡
            </button>
            <button onclick="toggleSentimentChart()" class="px-2.5 py-1.5 text-xs font-medium rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 transition">
              📈 <span id="sentiment-chart-btn-text">情緒趨勢</span>
            </button>
            <button onclick="toggleTvChart()" class="px-2.5 py-1.5 text-xs font-medium rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 transition">
              📊 <span id="tv-chart-btn-text">K 線圖</span>
            </button>
            <a id="link-tradingview" href="#" target="_blank" rel="noopener noreferrer" class="px-2.5 py-1.5 text-xs font-medium rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 transition">
              TradingView ↗
            </a>
          </div>
        </div>

        <!-- 基本面估值指標卡 -->
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 border-t lg:border-t-0 lg:border-l border-slate-800 pt-3 lg:pt-0 lg:pl-6">
          <div class="bg-slate-950/40 p-2.5 rounded-lg border border-slate-800/80">
            <div class="text-[11px] text-slate-400">市值 (Market Cap)</div>
            <div class="text-sm font-bold font-mono text-slate-200 mt-0.5" id="val-mkt-cap">-</div>
          </div>
          <div class="bg-slate-950/40 p-2.5 rounded-lg border border-slate-800/80">
            <div class="text-[11px] text-slate-400">前瞻本益比 (Fwd P/E)</div>
            <div class="text-sm font-bold font-mono text-amber-400 mt-0.5" id="val-fwd-pe">-</div>
          </div>
          <div class="bg-slate-950/40 p-2.5 rounded-lg border border-slate-800/80">
            <div class="text-[11px] text-slate-400">市銷率 (P/S) / 營收年增</div>
            <div class="text-sm font-bold font-mono text-slate-200 mt-0.5" id="val-ps-yoy">-</div>
          </div>
          <div class="bg-slate-950/40 p-2.5 rounded-lg border border-slate-800/80">
            <div class="text-[11px] text-slate-400">下次財報倒數 (Catalyst)</div>
            <div class="text-sm font-bold font-mono text-orange-400 mt-0.5" id="val-earnings-date">-</div>
          </div>
        </div>

      </div>

      <!-- 52 週水位 -->
      <div id="quote-52w-container" class="border-t border-slate-800/80 pt-3">
        <div class="flex justify-between text-xs text-slate-400 font-mono mb-1.5">
          <span>52 週最低：<b class="text-slate-200" id="quote-low52">$0.00</b></span>
          <span class="text-amber-400 font-semibold" id="quote-range-pct">52 週區間水位 0%</span>
          <span>52 週最高：<b class="text-slate-200" id="quote-high52">$0.00</b></span>
        </div>
        <div class="w-full bg-slate-800 rounded-full h-2 overflow-hidden flex items-center">
          <div id="quote-range-bar" class="bg-gradient-to-r from-amber-600 to-yellow-400 h-2 rounded-full transition-all duration-500" style="width: 0%"></div>
        </div>
      </div>

      <!-- 歷史多空情緒趨勢圖容器 (Chart.js) -->
      <div id="sentiment-chart-wrapper" class="hidden border-t border-slate-800/80 pt-4">
        <div class="text-xs font-semibold text-slate-400 mb-2 flex items-center gap-1.5">
          <span>📈</span> 近期 6 個月多空立場演變趨勢 (Sentiment Trend)
        </div>
        <div class="w-full h-[220px] bg-slate-950/70 p-3 rounded-lg border border-slate-800">
          <canvas id="sentimentChartCanvas"></canvas>
        </div>
      </div>

      <!-- K 線圖容器 -->
      <div id="tv-chart-wrapper" class="hidden border-t border-slate-800/80 pt-4">
        <div class="w-full h-[380px] rounded-lg overflow-hidden border border-slate-800" id="tv-chart-container"></div>
      </div>

    </div>

    <!-- 搜尋、排序與觀點篩選 -->
    <div class="flex flex-col md:flex-row gap-3 items-stretch md:items-center justify-between">
      <div class="flex items-center gap-2 flex-1 max-w-lg">
        <input type="text" id="search-input" placeholder="搜尋推文內容、摘要或 $標的（例如: HIMS, NBIS, RKLB）..." 
          class="w-full bg-slate-900 border border-slate-700/80 rounded-lg px-3.5 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-amber-500 focus:ring-1 focus:ring-amber-500 transition" />
        
        <select id="sort-select" onchange="changeSort(this.value)" class="bg-slate-900 border border-slate-700/80 rounded-lg px-3 py-2 text-xs text-slate-300 focus:outline-none focus:border-amber-500 transition cursor-pointer shrink-0">
          <option value="date_desc">🕒 最新發布</option>
          <option value="likes_desc">❤️ 最多按讚</option>
          <option value="views_desc">👁️ 最多瀏覽</option>
        </select>
      </div>

      <div class="flex items-center gap-1.5 overflow-x-auto pb-1 md:pb-0">
        <button onclick="setSentimentFilter('ALL')" class="filter-btn active px-3 py-1.5 rounded-lg text-xs font-medium border border-slate-700 bg-slate-800 text-white transition" data-val="ALL">全部觀點</button>
        <button onclick="setSentimentFilter('Bullish')" class="filter-btn px-3 py-1.5 rounded-lg text-xs font-medium border border-transparent text-slate-400 hover:text-emerald-400 hover:bg-slate-900 transition" data-val="Bullish">看多</button>
        <button onclick="setSentimentFilter('Bearish')" class="filter-btn px-3 py-1.5 rounded-lg text-xs font-medium border border-transparent text-slate-400 hover:text-rose-400 hover:bg-slate-900 transition" data-val="Bearish">看空</button>
        <button onclick="setSentimentFilter('Neutral')" class="filter-btn px-3 py-1.5 rounded-lg text-xs font-medium border border-transparent text-slate-400 hover:text-blue-400 hover:bg-slate-900 transition" data-val="Neutral">中立</button>
      </div>
    </div>

    <!-- 推文卡片列表 -->
    <div class="space-y-4" id="tweets-list"></div>

    <!-- 載入更多按鈕 -->
    <div class="text-center pt-2 pb-8" id="load-more-container">
      <button onclick="loadMore()" class="px-6 py-2.5 bg-slate-900 hover:bg-slate-800 border border-slate-700 hover:border-amber-500/50 rounded-xl text-xs font-semibold text-slate-300 hover:text-white transition shadow-sm">
        載入更多推文 (<span id="load-more-count">0 / 0</span>)
      </button>
    </div>

  </main>

  <!-- 雙標的橫向對比矩陣 Modal -->
  <div id="compare-modal" class="fixed inset-0 z-50 bg-slate-950/85 backdrop-blur-sm hidden flex items-center justify-center p-4">
    <div class="bg-slate-900 border border-slate-700/80 rounded-2xl w-full max-w-3xl max-h-[90vh] overflow-y-auto p-6 space-y-6 shadow-2xl">
      <div class="flex items-center justify-between border-b border-slate-800 pb-4">
        <div class="flex items-center gap-2">
          <span class="text-xl">⚖️</span>
          <h3 class="text-lg font-bold text-white">雙標的橫向深度對比矩陣</h3>
        </div>
        <button onclick="closeCompareModal()" class="text-slate-400 hover:text-white text-xl p-1 font-bold">✕</button>
      </div>

      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="text-xs text-slate-400 font-semibold mb-1 block">標的 A</label>
          <select id="compare-select-a" onchange="renderComparisonMatrix()" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm text-amber-400 font-mono font-bold"></select>
        </div>
        <div>
          <label class="text-xs text-slate-400 font-semibold mb-1 block">標的 B</label>
          <select id="compare-select-b" onchange="renderComparisonMatrix()" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm text-orange-400 font-mono font-bold"></select>
        </div>
      </div>

      <div class="overflow-x-auto">
        <table class="w-full text-xs text-left border-collapse">
          <thead>
            <tr class="border-b border-slate-800 text-slate-400">
              <th class="py-2.5 px-3">對比指標</th>
              <th class="py-2.5 px-3 text-amber-400 font-mono font-bold" id="th-comp-a">標的 A</th>
              <th class="py-2.5 px-3 text-orange-400 font-mono font-bold" id="th-comp-b">標的 B</th>
            </tr>
          </thead>
          <tbody id="compare-table-body" class="divide-y divide-slate-800/60 font-mono"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- 浮動 AI 對話助理按鈕與對話面板 (Burak 橘金設計) -->
  <div class="fixed bottom-6 right-6 z-50">
    <button id="ai-chat-btn" onclick="toggleChatDrawer()" class="bg-gradient-to-r from-amber-500 to-orange-500 text-slate-950 px-4 py-3 rounded-full font-bold shadow-2xl flex items-center gap-2 hover:scale-105 transition-all">
      💬 <span class="text-sm">問問 Burak AI</span>
    </button>
  </div>

  <div id="ai-chat-drawer" class="fixed bottom-20 right-6 z-50 w-[92vw] sm:w-[420px] bg-slate-900/95 backdrop-blur-md border border-slate-700/80 rounded-2xl shadow-2xl overflow-hidden hidden flex-col h-[520px]">
    <div class="p-4 bg-slate-950 border-b border-slate-800 flex items-center justify-between">
      <div class="flex items-center gap-2">
        <span class="w-2 h-2 rounded-full bg-amber-400 animate-ping"></span>
        <span class="font-bold text-sm text-white">Burak Finance AI 問答</span>
      </div>
      <button onclick="toggleChatDrawer()" class="text-slate-400 hover:text-white font-bold p-1">✕</button>
    </div>

    <div id="chat-messages" class="flex-1 p-4 overflow-y-auto space-y-3 text-xs leading-relaxed">
      <div class="bg-slate-800/80 border border-slate-700/70 p-3 rounded-xl text-slate-200">
        👋 你好！我是 Burak AI 助理，你可以直接點擊或詢問：
        <div class="mt-2 flex flex-wrap gap-1.5">
          <button onclick="handleQuickAsk('日報')" class="bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 px-2 py-0.5 rounded border border-amber-500/30">📅 日報</button>
          <button onclick="handleQuickAsk('目前有哪些生技醫療股票？')" class="bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 px-2 py-0.5 rounded border border-amber-500/30">💊 生技醫療股</button>
          <button onclick="handleQuickAsk('幫我看 HIMS')" class="bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 px-2 py-0.5 rounded border border-amber-500/30">🔍 幫我看 HIMS</button>
          <button onclick="handleQuickAsk('幫我看 NBIS')" class="bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 px-2 py-0.5 rounded border border-amber-500/30">🔍 幫我看 NBIS</button>
        </div>
      </div>
    </div>

    <form onsubmit="handleChatSubmit(event)" class="p-3 bg-slate-950 border-t border-slate-800 flex gap-2">
      <input type="text" id="chat-input" placeholder="輸入問題（例如：幫我看 HIMS 或 NBIS）..." class="flex-1 bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-amber-500" />
      <button type="submit" class="px-4 py-2 bg-amber-500 hover:bg-amber-400 text-slate-950 font-bold rounded-xl text-xs transition">發送</button>
    </form>
  </div>

  <!-- 個股 AI 深度論點脈絡 Modal -->
  <div id="deepdive-modal" class="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm hidden flex items-center justify-center p-4">
    <div class="bg-slate-900 border border-slate-700/80 rounded-2xl w-full max-w-4xl max-h-[90vh] overflow-y-auto p-6 space-y-6 shadow-2xl">
      <div class="flex items-center justify-between border-b border-slate-800 pb-4">
        <div class="flex items-center gap-3">
          <span class="px-3 py-1 bg-amber-500/10 border border-amber-500/30 text-amber-400 font-mono font-bold text-lg rounded-lg" id="modal-ticker-title">$TICKER</span>
          <h3 class="text-lg font-bold text-white">AI 個股投資論點脈絡與歷史</h3>
        </div>
        <button onclick="closeDeepDiveModal()" class="text-slate-400 hover:text-white text-xl p-1 font-bold">✕</button>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs font-mono">
        <div class="bg-slate-950/60 p-3 rounded-xl border border-slate-800">
          <div class="text-slate-400">首次提及時間</div>
          <div class="text-sm font-bold text-white mt-1" id="modal-first-date">-</div>
        </div>
        <div class="bg-slate-950/60 p-3 rounded-xl border border-slate-800">
          <div class="text-slate-400">總提及次數 / AI 分析</div>
          <div class="text-sm font-bold text-amber-400 mt-1" id="modal-mention-count">-</div>
        </div>
        <div class="bg-slate-950/60 p-3 rounded-xl border border-slate-800">
          <div class="text-slate-400">當前最新立場</div>
          <div class="text-sm font-bold text-emerald-400 mt-1" id="modal-latest-stance">-</div>
        </div>
      </div>

      <div>
        <h4 class="text-sm font-bold text-slate-200 flex items-center gap-2 mb-3">
          📌 關鍵代表性觀點 (Key Milestones)
        </h4>
        <div class="space-y-2.5" id="modal-key-points"></div>
      </div>

      <div>
        <h4 class="text-sm font-bold text-slate-200 flex items-center gap-2 mb-3">
          ⏳ 立場變化與歷史軌跡
        </h4>
        <div class="space-y-2 max-h-48 overflow-y-auto pr-1" id="modal-timeline"></div>
      </div>

      <div>
        <h4 class="text-sm font-bold text-rose-400 flex items-center gap-2 mb-3">
          ⚠️ 曾提及的風險與疑慮因素
        </h4>
        <div class="space-y-2" id="modal-risks"></div>
      </div>
    </div>
  </div>

  <script>
    const allTweets = __TWEETS_DATA__;
    const initialTopTickers = __TOP_TICKERS__;
    const stockQuotes = __STOCK_QUOTES__;
    const sectorMapping = __SECTOR_MAPPING__;

    let currentViewMode = 'all';
    let currentSentiment = 'ALL';
    let currentSector = 'ALL';
    let currentTicker = '';
    let searchQuery = '';
    let currentSort = 'date_desc';
    let displayLimit = 25;
    
    let tvChartVisible = false;
    let sentimentChartVisible = false;
    let sentimentChartInstance = null;

    let watchlist = JSON.parse(localStorage.getItem('burak_watchlist') || '[]');
    let clientTranslations = JSON.parse(localStorage.getItem('burak_trans_cache') || '{}');

    function formatNumber(num) {
      if (!num) return '-';
      if (num >= 1e12) return (num / 1e12).toFixed(2) + 'T';
      if (num >= 1e9) return (num / 1e9).toFixed(2) + 'B';
      if (num >= 1e6) return (num / 1e6).toFixed(2) + 'M';
      if (num >= 1e3) return (num / 1e3).toFixed(1) + 'K';
      return num.toLocaleString();
    }

    function toggleWatchlist(ticker, event) {
      if (event) event.stopPropagation();
      ticker = ticker.toUpperCase();
      const idx = watchlist.indexOf(ticker);
      if (idx >= 0) {
        watchlist.splice(idx, 1);
      } else {
        watchlist.push(ticker);
      }
      localStorage.setItem('burak_watchlist', JSON.stringify(watchlist));
      updateAggregatedView();
    }

    function isWatchlisted(ticker) {
      return watchlist.includes(ticker.toUpperCase());
    }

    function highlightText(text) {
      if (!text) return '';
      return text
        .replace(/(\\$[A-Za-z]{1,6})/g, '<button onclick="filterByTicker(\\'$1\\'.replace(\\'$\\', \\'\\').toUpperCase())" class="font-bold text-amber-400 bg-amber-950/60 hover:bg-amber-900/80 px-1 py-0.5 rounded border border-amber-500/30 transition inline-block">$1</button>')
        .replace(/(https?:\\/\\/[^\\s]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer" class="text-cyan-400 hover:underline break-all">$1</a>');
    }

    function setViewMode(mode) {
      currentViewMode = mode;
      document.querySelectorAll('.view-btn').forEach(btn => {
        const active = btn.dataset.view === mode;
        btn.className = `view-btn px-2.5 sm:px-3 py-1 rounded-lg text-xs font-medium transition ${
          active ? 'bg-amber-500 text-slate-950 font-bold shadow-sm' : 'text-slate-400 hover:text-white'
        }`;
      });
      displayLimit = 25;
      updateAggregatedView();
    }

    function setSectorFilter(sector) {
      currentSector = sector;
      document.querySelectorAll('.sector-btn').forEach(btn => {
        const active = btn.dataset.sector === sector;
        btn.className = `sector-btn px-3 py-1 rounded-lg text-xs font-medium border transition ${
          active ? 'border-slate-700 bg-slate-800 text-white font-bold' : 'border-transparent text-slate-400 hover:bg-slate-800'
        }`;
      });
      displayLimit = 25;
      updateAggregatedView();
    }

    function getFilteredByView(tweetsList) {
      let filtered = tweetsList;
      if (currentViewMode !== 'all') {
        const now = new Date();
        filtered = filtered.filter(t => {
          if (!t.iso_date) return true;
          const d = new Date(t.iso_date);
          const diffDays = (now - d) / (1000 * 60 * 60 * 24);
          if (currentViewMode === 'daily') return diffDays <= 1.5;
          if (currentViewMode === 'weekly') return diffDays <= 7;
          if (currentViewMode === 'monthly') return diffDays <= 28;
          if (currentViewMode === 'quarterly') return diffDays <= 90;
          return true;
        });
      }

      if (currentSector === 'WATCHLIST') {
        filtered = filtered.filter(t => t.tickers.some(sym => isWatchlisted(sym)));
      } else if (currentSector !== 'ALL') {
        const sectorTickers = sectorMapping[currentSector] || [];
        filtered = filtered.filter(t => t.tickers.some(sym => sectorTickers.includes(sym)));
      }

      return filtered;
    }

    function updateAggregatedView() {
      const viewTweets = getFilteredByView(allTweets);
      
      const counts = {};
      const recencyScores = {};

      viewTweets.forEach((t, index) => {
        t.tickers.forEach(sym => {
          counts[sym] = (counts[sym] || 0) + 1;
          if (!recencyScores[sym]) {
            recencyScores[sym] = Math.max(1, 100 - index);
          }
        });
      });

      const topTickers = Object.keys(counts).sort((a, b) => {
        const aStar = isWatchlisted(a) ? 10000 : 0;
        const bStar = isWatchlisted(b) ? 10000 : 0;
        const scoreA = aStar + (recencyScores[a] || 0) * 2 + (counts[a] || 0);
        const scoreB = bStar + (recencyScores[b] || 0) * 2 + (counts[b] || 0);
        return scoreB - scoreA;
      }).slice(0, 50).map(sym => [sym, counts[sym]]);

      const total = viewTweets.length;
      const bullish = viewTweets.filter(t => t.sentiment === 'Bullish').length;
      const bearish = viewTweets.filter(t => t.sentiment === 'Bearish').length;
      const analyzed = viewTweets.filter(t => t.is_analyzed).length;
      const uniqueTickers = Object.keys(counts).length;
      const coveragePct = total ? Math.round((analyzed / total) * 100) : 0;

      document.getElementById('stat-total').innerText = total.toLocaleString();
      document.getElementById('stat-ai-coverage').innerText = `AI 分析：${analyzed.toLocaleString()} / ${total.toLocaleString()} (${coveragePct}%)`;
      document.getElementById('stat-bullish').innerText = `${bullish} (${total ? Math.round(bullish/total*100) : 0}%)`;
      document.getElementById('stat-bearish').innerText = `${bearish} (${total ? Math.round(bearish/total*100) : 0}%)`;
      document.getElementById('stat-tickers').innerText = uniqueTickers.toLocaleString();

      renderTopTickers(topTickers);
      if (currentTicker) {
        renderStockQuote(currentTicker);
      } else {
        document.getElementById('stock-quote-section').classList.add('hidden');
      }
      render();
    }

    function renderTopTickers(tickersList) {
      const bar = document.getElementById('top-tickers-bar');
      if (tickersList.length === 0) {
        bar.innerHTML = '<span class="text-xs text-slate-500">該條件下無符合推文標的</span>';
        return;
      }
      bar.innerHTML = tickersList.map(([t, count]) => {
        const quote = stockQuotes[t];
        const starred = isWatchlisted(t);
        let miniBadge = '';
        if (quote && quote.changePct !== undefined) {
          const isPos = quote.changePct >= 0;
          miniBadge = `<span class="text-[10px] ml-1 ${isPos ? 'text-emerald-400' : 'text-rose-400'}">${isPos ? '+' : ''}${quote.changePct.toFixed(1)}%</span>`;
        }
        return `
          <div class="inline-flex items-center rounded-md border transition ${currentTicker === t ? 'bg-amber-500 text-slate-950 border-amber-400 font-bold shadow-md' : 'bg-slate-800/80 border-slate-700/60 text-slate-300 hover:border-amber-500/50'}">
            <button onclick="filterByTicker('${t}')" class="px-2.5 py-1 text-xs font-mono font-medium">
              \\$${t} ${miniBadge} <span class="text-[10px] opacity-70">(${count})</span>
            </button>
            <button onclick="toggleWatchlist('${t}', event)" class="pr-2 pl-0.5 text-xs ${starred ? 'text-amber-400 font-bold' : 'text-slate-500 hover:text-amber-400'}" title="${starred ? '取消自選' : '加入自選'}">
              ${starred ? '★' : '☆'}
            </button>
          </div>
        `;
      }).join('');
    }

    function toggleSentimentChart() {
      sentimentChartVisible = !sentimentChartVisible;
      const wrapper = document.getElementById('sentiment-chart-wrapper');
      const btnText = document.getElementById('sentiment-chart-btn-text');
      if (sentimentChartVisible) {
        wrapper.classList.remove('hidden');
        btnText.innerText = '收合趨勢';
        renderSentimentChart(currentTicker);
      } else {
        wrapper.classList.add('hidden');
        btnText.innerText = '情緒趨勢';
      }
    }

    function renderSentimentChart(ticker) {
      if (!ticker) return;
      const tickerTweets = allTweets.filter(t => t.tickers.includes(ticker) && t.month && t.month !== '未知月份');
      
      const monthsMap = {};
      tickerTweets.forEach(t => {
        if (!monthsMap[t.month]) monthsMap[t.month] = { bullish: 0, bearish: 0 };
        if (t.sentiment === 'Bullish') monthsMap[t.month].bullish++;
        if (t.sentiment === 'Bearish') monthsMap[t.month].bearish++;
      });

      const sortedMonths = Object.keys(monthsMap).sort().slice(-6);
      const bullishData = sortedMonths.map(m => monthsMap[m].bullish);
      const bearishData = sortedMonths.map(m => monthsMap[m].bearish);

      const ctx = document.getElementById('sentimentChartCanvas').getContext('2d');
      if (sentimentChartInstance) sentimentChartInstance.destroy();

      sentimentChartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: sortedMonths,
          datasets: [
            {
              label: '看多 (Bullish)',
              data: bullishData,
              backgroundColor: '#10b981',
              borderRadius: 4
            },
            {
              label: '看空 (Bearish)',
              data: bearishData,
              backgroundColor: '#f43f5e',
              borderRadius: 4
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { labels: { color: '#94a3b8', font: { size: 11 } } }
          },
          scales: {
            x: { ticks: { color: '#94a3b8' }, grid: { color: '#1e293b' } },
            y: { ticks: { color: '#94a3b8', stepSize: 1 }, grid: { color: '#1e293b' } }
          }
        }
      });
    }

    function toggleTvChart() {
      tvChartVisible = !tvChartVisible;
      const wrapper = document.getElementById('tv-chart-wrapper');
      const btnText = document.getElementById('tv-chart-btn-text');
      if (tvChartVisible) {
        wrapper.classList.remove('hidden');
        btnText.innerText = '收合 K 線';
        loadTradingViewWidget(currentTicker);
      } else {
        wrapper.classList.add('hidden');
        btnText.innerText = 'K 線圖';
      }
    }

    function loadTradingViewWidget(ticker) {
      const container = document.getElementById('tv-chart-container');
      if (!ticker || !container) return;
      container.innerHTML = `
        <iframe 
          src="https://s.tradingview.com/widgetembed/?frameElementId=tradingview_widget&symbol=${ticker}&interval=D&hidesidetoolbar=1&symboledit=0&saveimage=0&toolbarbg=f1f3f6&studies=[]&theme=dark&style=1&timezone=Asia%2FTaipei&studies_overrides={}&overrides={}&enabled_features=[]&disabled_features=[]&locale=zh_TW" 
          width="100%" 
          height="100%" 
          frameborder="0" 
          allowtransparency="true" 
          scrolling="no">
        </iframe>
      `;
    }

    function renderStockQuote(ticker) {
      const section = document.getElementById('stock-quote-section');
      if (!ticker) {
        section.classList.add('hidden');
        return;
      }

      const data = stockQuotes[ticker] || null;
      section.classList.remove('hidden');

      document.getElementById('quote-ticker-name').innerText = `$${ticker}`;
      const starBtn = document.getElementById('quote-star-btn');
      starBtn.innerText = isWatchlisted(ticker) ? '★' : '☆';
      starBtn.className = isWatchlisted(ticker) ? 'text-xl text-amber-400 font-bold' : 'text-xl text-slate-500 hover:text-amber-400';
      
      const changeEl = document.getElementById('quote-change-container');
      const currencyLabel = document.getElementById('quote-currency-label');

      if (data && data.price) {
        document.getElementById('quote-price').innerText = `$${data.price.toFixed(2)}`;
        currencyLabel.innerText = 'USD';
        
        const isPositive = data.change >= 0;
        const changeVal = `${isPositive ? '+' : ''}${data.change.toFixed(2)}`;
        const changePctVal = `(${isPositive ? '+' : ''}${data.changePct.toFixed(2)}%)`;

        changeEl.innerHTML = `
          <span id="quote-change">${changeVal}</span>
          <span id="quote-change-pct">${changePctVal}</span>
          <span class="text-xs font-normal text-slate-400">相對前收盤</span>
        `;
        changeEl.className = `flex items-center gap-2 mt-0.5 text-sm font-semibold font-mono ${isPositive ? 'text-emerald-400' : 'text-rose-400'}`;

        document.getElementById('val-mkt-cap').innerText = formatNumber(data.marketCap);
        document.getElementById('val-fwd-pe').innerText = data.forwardPE ? `${data.forwardPE}x` : (data.trailingPE ? `${data.trailingPE}x (TTM)` : '-');
        
        const psText = data.priceToSales ? `${data.priceToSales}x` : '-';
        const yoyText = data.revenueGrowth ? `${data.revenueGrowth > 0 ? '+' : ''}${data.revenueGrowth}%` : '-';
        document.getElementById('val-ps-yoy').innerText = `${psText} / ${yoyText}`;

        document.getElementById('val-earnings-date').innerText = data.earningsDate ? data.earningsDate : '即將公布';

        document.getElementById('quote-low52').innerText = data.low52 ? `$${data.low52.toFixed(2)}` : '-';
        document.getElementById('quote-high52').innerText = data.high52 ? `$${data.high52.toFixed(2)}` : '-';

        if (data.low52 && data.high52 && data.high52 > data.low52) {
          const rangePct = Math.max(0, Math.min(100, Math.round(((data.price - data.low52) / (data.high52 - data.low52)) * 100)));
          document.getElementById('quote-range-pct').innerText = `52 週區間水位 ${rangePct}%`;
          document.getElementById('quote-range-bar').style.width = `${rangePct}%`;
          document.getElementById('quote-52w-container').classList.remove('hidden');
        } else {
          document.getElementById('quote-52w-container').classList.add('hidden');
        }
      } else {
        document.getElementById('quote-price').innerText = '即時行情模式';
        currencyLabel.innerText = '';
        changeEl.innerHTML = '<span class="text-xs font-normal text-amber-400 font-mono">可點擊下方「K 線圖」展開即時走勢</span>';
        changeEl.className = 'flex items-center gap-2 mt-0.5 text-sm font-medium';
        document.getElementById('val-mkt-cap').innerText = '-';
        document.getElementById('val-fwd-pe').innerText = '-';
        document.getElementById('val-ps-yoy').innerText = '-';
        document.getElementById('val-earnings-date').innerText = '-';
        document.getElementById('quote-52w-container').classList.add('hidden');
      }

      document.getElementById('link-tradingview').href = `https://www.tradingview.com/symbols/${ticker}/`;

      if (tvChartVisible) loadTradingViewWidget(ticker);
      if (sentimentChartVisible) renderSentimentChart(ticker);
    }

    function openCompareModal() {
      const modal = document.getElementById('compare-modal');
      const selectA = document.getElementById('compare-select-a');
      const selectB = document.getElementById('compare-select-b');
      
      const allTickers = Object.keys(stockQuotes).sort();
      const optionsHtml = allTickers.map(t => `<option value="${t}">$${t} (${stockQuotes[t].sector || '科技'})</option>`).join('');
      
      selectA.innerHTML = optionsHtml;
      selectB.innerHTML = optionsHtml;

      if (currentTicker && allTickers.includes(currentTicker)) {
        selectA.value = currentTicker;
      }
      if (allTickers.length > 1) {
        selectB.value = allTickers.find(t => t !== selectA.value) || allTickers[1];
      }

      renderComparisonMatrix();
      modal.classList.remove('hidden');
    }

    function closeCompareModal() {
      document.getElementById('compare-modal').classList.add('hidden');
    }

    function renderComparisonMatrix() {
      const symA = document.getElementById('compare-select-a').value;
      const symB = document.getElementById('compare-select-b').value;
      
      document.getElementById('th-comp-a').innerText = `$${symA}`;
      document.getElementById('th-comp-b').innerText = `$${symB}`;

      const dataA = stockQuotes[symA] || {};
      const dataB = stockQuotes[symB] || {};

      const tweetsA = allTweets.filter(t => t.tickers.includes(symA));
      const tweetsB = allTweets.filter(t => t.tickers.includes(symB));

      const bullA = tweetsA.filter(t => t.sentiment === 'Bullish').length;
      const bullB = tweetsB.filter(t => t.sentiment === 'Bullish').length;

      const rows = [
        ['所屬板塊', dataA.sector || '-', dataB.sector || '-'],
        ['即時股價', dataA.price ? `$${dataA.price}` : '-', dataB.price ? `$${dataB.price}` : '-'],
        ['單日漲跌幅', dataA.changePct ? `${dataA.changePct > 0 ? '+' : ''}${dataA.changePct}%` : '-', dataB.changePct ? `${dataB.changePct > 0 ? '+' : ''}${dataB.changePct}%` : '-'],
        ['社群提及總數', `${tweetsA.length} 則`, `${tweetsB.length} 則`],
        ['看多偏向度 (Bullish %)', `${tweetsA.length ? Math.round(bullA/tweetsA.length*100) : 0}% (${bullA}多)`, `${tweetsB.length ? Math.round(bullB/tweetsB.length*100) : 0}% (${bullB}多)`],
        ['市值 (Market Cap)', formatNumber(dataA.marketCap), formatNumber(dataB.marketCap)],
        ['前瞻本益比 (Forward P/E)', dataA.forwardPE ? `${dataA.forwardPE}x` : '-', dataB.forwardPE ? `${dataB.forwardPE}x` : '-'],
        ['市銷率 (P/S)', dataA.priceToSales ? `${dataA.priceToSales}x` : '-', dataB.priceToSales ? `${dataB.priceToSales}x` : '-'],
        ['營收年增率 (YoY)', dataA.revenueGrowth ? `${dataA.revenueGrowth}%` : '-', dataB.revenueGrowth ? `${dataB.revenueGrowth}%` : '-'],
        ['下次財報日', dataA.earningsDate || '-', dataB.earningsDate || '-']
      ];

      const tbody = document.getElementById('compare-table-body');
      tbody.innerHTML = rows.map(([label, valA, valB]) => `
        <tr class="hover:bg-slate-800/40">
          <td class="py-2 px-3 text-slate-400 font-sans">${label}</td>
          <td class="py-2 px-3 text-slate-200 font-semibold">${valA}</td>
          <td class="py-2 px-3 text-slate-200 font-semibold">${valB}</td>
        </tr>
      `).join('');
    }

    function openDeepDiveModal(ticker) {
      if (!ticker) return;
      const modal = document.getElementById('deepdive-modal');
      const tickerTweets = allTweets.filter(t => t.tickers.includes(ticker));
      
      document.getElementById('modal-ticker-title').innerText = `$${ticker}`;
      if (tickerTweets.length === 0) return;

      const chronological = [...tickerTweets].sort((a, b) => (a.iso_date || a.date).localeCompare(b.iso_date || b.date));
      const firstMention = chronological[0];
      const latestMention = chronological[chronological.length - 1];

      document.getElementById('modal-first-date').innerText = firstMention.date;
      document.getElementById('modal-mention-count').innerText = `${tickerTweets.length} 則 (${tickerTweets.filter(t=>t.is_analyzed).length} 則已分析)`;
      document.getElementById('modal-latest-stance').innerText = latestMention.sentiment === 'Bullish' ? '看多 (Bullish)' : (latestMention.sentiment === 'Bearish' ? '看空 (Bearish)' : '中立 (Neutral)');

      const keyPoints = [...tickerTweets]
        .filter(t => t.summary)
        .sort((a, b) => (b.views + b.likes * 10) - (a.views + a.likes * 10))
        .slice(0, 3);

      const keyContainer = document.getElementById('modal-key-points');
      if (keyPoints.length > 0) {
        keyContainer.innerHTML = keyPoints.map((item, idx) => `
          <div class="bg-slate-950/70 p-3.5 rounded-xl border border-slate-800 space-y-1.5">
            <div class="flex items-center justify-between text-xs">
              <span class="text-amber-400 font-bold font-mono">里程碑 #${idx + 1} (${item.date})</span>
              <a href="${item.url}" target="_blank" class="text-slate-400 hover:text-white text-xs">原始推文 ↗</a>
            </div>
            <div class="text-sm font-semibold text-slate-100">${item.summary}</div>
            <div class="text-xs text-slate-300 leading-relaxed">${item.translation_zh || item.text}</div>
          </div>
        `).join('');
      } else {
        keyContainer.innerHTML = '<div class="text-xs text-slate-500">尚無足夠的 AI 重點摘要紀錄。</div>';
      }

      const timelineContainer = document.getElementById('modal-timeline');
      timelineContainer.innerHTML = chronological.map(item => `
        <div class="flex items-center gap-3 text-xs border-l-2 border-slate-800 pl-3 py-1 font-mono">
          <span class="text-slate-500">${item.date}</span>
          <span class="px-1.5 py-0.5 rounded text-[10px] font-bold ${item.sentiment==='Bullish'?'bg-emerald-500/10 text-emerald-400':(item.sentiment==='Bearish'?'bg-rose-500/10 text-rose-400':'bg-blue-500/10 text-blue-400')}">${item.sentiment}</span>
          <span class="text-slate-300 truncate max-w-md">${item.summary || item.text}</span>
        </div>
      `).join('');

      const riskTweets = tickerTweets.filter(t => t.sentiment === 'Bearish' || (t.summary && (t.summary.includes('風險') || t.summary.includes('警戒') || t.summary.includes('跌'))));
      const riskContainer = document.getElementById('modal-risks');
      if (riskTweets.length > 0) {
        riskContainer.innerHTML = riskTweets.slice(0, 3).map(item => `
          <div class="bg-rose-950/20 border border-rose-900/40 p-3 rounded-xl text-xs space-y-1">
            <div class="flex justify-between text-rose-400 font-mono font-semibold">
              <span>${item.date}</span>
              <a href="${item.url}" target="_blank" class="hover:underline">來源 ↗</a>
            </div>
            <div class="text-rose-200">${item.summary || item.translation_zh || item.text}</div>
          </div>
        `).join('');
      } else {
        riskContainer.innerHTML = '<div class="text-xs text-slate-500">歷史貼文中未出現重大看空或風險警語。</div>';
      }

      modal.classList.remove('hidden');
    }

    function closeDeepDiveModal() {
      document.getElementById('deepdive-modal').classList.add('hidden');
    }

    function filterByTicker(ticker) {
      currentTicker = ticker.toUpperCase();
      displayLimit = 25;
      document.getElementById('clear-ticker-btn').classList.toggle('hidden', !ticker);
      updateAggregatedView();
    }

    function setSentimentFilter(val) {
      currentSentiment = val;
      displayLimit = 25;
      document.querySelectorAll('.filter-btn').forEach(btn => {
        const active = btn.dataset.val === val;
        btn.className = `filter-btn px-3 py-1.5 rounded-lg text-xs font-medium border transition ${
          active ? 'border-slate-700 bg-slate-800 text-white' : 'border-transparent text-slate-400 hover:bg-slate-900'
        }`;
      });
      render();
    }

    function changeSort(val) {
      currentSort = val;
      displayLimit = 25;
      render();
    }

    function loadMore() {
      displayLimit += 25;
      render();
    }

    async function translateByIndex(idx) {
      const transEl = document.getElementById(`trans-text-${idx}`);
      const btnEl = document.getElementById(`trans-btn-${idx}`);
      const item = allTweets[idx];
      if (!transEl || !item) return;

      const rawText = item.text;
      if (clientTranslations[rawText]) {
        transEl.innerHTML = highlightText(clientTranslations[rawText]);
        transEl.classList.remove('hidden');
        if (btnEl) btnEl.remove();
        return;
      }

      try {
        if (btnEl) btnEl.innerText = '翻譯中...';
        const url = 'https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-TW&dt=t&q=' + encodeURIComponent(rawText);
        const res = await fetch(url);
        const json = await res.json();
        const translated = json[0].map(row => row[0]).join('');
        clientTranslations[rawText] = translated;
        localStorage.setItem('burak_trans_cache', JSON.stringify(clientTranslations));
        transEl.innerHTML = highlightText(translated);
        transEl.classList.remove('hidden');
        if (btnEl) btnEl.remove();
      } catch (err) {
        console.error('翻譯失敗', err);
        if (btnEl) btnEl.innerText = '⚠️ 翻譯失敗，點擊重試';
      }
    }

    function render() {
      const container = document.getElementById('tweets-list');
      const viewFiltered = getFilteredByView(allTweets);

      let filtered = viewFiltered.filter(t => {
        const matchSentiment = currentSentiment === 'ALL' || t.sentiment === currentSentiment;
        const matchTicker = !currentTicker || t.tickers.includes(currentTicker);
        const matchSearch = !searchQuery || 
          t.text.toLowerCase().includes(searchQuery) || 
          t.summary.toLowerCase().includes(searchQuery) ||
          t.tickers.some(tick => tick.toLowerCase().includes(searchQuery));
        return matchSentiment && matchTicker && matchSearch;
      });

      if (currentSort === 'likes_desc') {
        filtered.sort((a, b) => b.likes - a.likes);
      } else if (currentSort === 'views_desc') {
        filtered.sort((a, b) => b.views - a.views);
      } else {
        filtered.sort((a, b) => (b.iso_date || b.date).localeCompare(a.iso_date || a.date));
      }

      const totalFiltered = filtered.length;
      const visibleItems = filtered.slice(0, displayLimit);

      const loadMoreContainer = document.getElementById('load-more-container');
      const loadMoreCount = document.getElementById('load-more-count');
      if (visibleItems.length < totalFiltered) {
        loadMoreContainer.classList.remove('hidden');
        loadMoreCount.innerText = `${visibleItems.length} / ${totalFiltered}`;
      } else {
        loadMoreContainer.classList.add('hidden');
      }

      if (visibleItems.length === 0) {
        container.innerHTML = `
          <div class="text-center py-16 text-slate-500 bg-slate-900/30 rounded-xl border border-slate-800">
            當前時間視圖或篩選條件下沒有推文。
          </div>
        `;
        return;
      }

      container.innerHTML = visibleItems.map(item => {
        const globalIdx = allTweets.indexOf(item);
        const cachedClient = clientTranslations[item.text];

        let sentimentBadge = '';
        if (item.sentiment === 'Bullish') {
          sentimentBadge = '<span class="px-2 py-0.5 rounded text-[11px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">看多 Bullish</span>';
        } else if (item.sentiment === 'Bearish') {
          sentimentBadge = '<span class="px-2 py-0.5 rounded text-[11px] font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20">看空 Bearish</span>';
        } else {
          sentimentBadge = '<span class="px-2 py-0.5 rounded text-[11px] font-semibold bg-blue-500/10 text-blue-400 border border-blue-500/20">中立 Neutral</span>';
        }

        let contentHtml = '';
        if (item.summary || item.translation_zh) {
          contentHtml = `
            ${item.summary ? `<div class="text-sm font-semibold text-amber-300 mb-1.5 flex items-start gap-1.5"><span class="text-amber-400 font-mono">⚡ 觀點：</span>${item.summary}</div>` : ''}
            ${item.translation_zh ? `<div class="text-slate-200 text-sm leading-relaxed whitespace-pre-wrap">${highlightText(item.translation_zh)}</div>` : ''}
            <details class="mt-2 text-xs text-slate-500">
              <summary class="cursor-pointer hover:text-slate-400 select-none">查看原文</summary>
              <div class="mt-1 text-slate-400 whitespace-pre-wrap border-l-2 border-slate-700 pl-2 py-1">${highlightText(item.text)}</div>
            </details>
          `;
        } else {
          contentHtml = `
            <div id="trans-text-${globalIdx}" class="text-slate-200 text-sm leading-relaxed whitespace-pre-wrap font-medium mb-1 ${cachedClient ? '' : 'hidden'}">${cachedClient ? highlightText(cachedClient) : ''}</div>
            <div class="text-slate-300 text-sm leading-relaxed whitespace-pre-wrap">${highlightText(item.text)}</div>
            ${cachedClient ? '' : `<button id="trans-btn-${globalIdx}" onclick="translateByIndex(${globalIdx})" class="text-xs text-amber-400 hover:text-amber-300 flex items-center gap-1 mt-2 transition">🌐 翻譯為繁體中文</button>`}
          `;
        }

        return `
          <article class="bg-slate-900/60 border border-slate-800 rounded-xl p-4 sm:p-5 hover:border-slate-700/80 transition space-y-3">
            <div class="flex items-center justify-between flex-wrap gap-2">
              <div class="flex items-center gap-2">
                ${sentimentBadge}
                <div class="flex flex-wrap gap-1">
                  ${item.tickers.map(tk => `<button onclick="filterByTicker('${tk}')" class="text-xs font-mono font-bold text-amber-400 bg-slate-800 hover:bg-slate-700 px-1.5 py-0.5 rounded border border-slate-700 transition">\\$${tk}</button>`).join('')}
                </div>
              </div>
              <div class="text-xs text-slate-500 font-mono">${item.date}</div>
            </div>

            <div class="py-1">${contentHtml}</div>

            <div class="flex items-center justify-between text-xs text-slate-400 border-t border-slate-800/80 pt-2.5">
              <div class="flex items-center gap-4 font-mono">
                <span>❤️ ${item.likes.toLocaleString()}</span>
                <span>🔁 ${item.retweets.toLocaleString()}</span>
                <span>👁️ ${item.views.toLocaleString()}</span>
              </div>
              <a href="${item.url}" target="_blank" rel="noopener noreferrer" class="text-slate-400 hover:text-white transition flex items-center gap-1">
                開啟推文 ↗
              </a>
            </div>
          </article>
        `;
      }).join('');
    }

    // AI 對話抽屜邏輯與意圖解析引擎
    function toggleChatDrawer() {
      const drawer = document.getElementById('ai-chat-drawer');
      drawer.classList.toggle('hidden');
      drawer.classList.toggle('flex');
    }

    function appendChatMessage(sender, htmlContent) {
      const container = document.getElementById('chat-messages');
      const isUser = sender === 'user';
      const msgDiv = document.createElement('div');
      msgDiv.className = isUser 
        ? 'bg-amber-500/20 text-amber-200 border border-amber-500/30 p-2.5 rounded-xl ml-6'
        : 'bg-slate-800/90 text-slate-200 border border-slate-700/80 p-3 rounded-xl mr-4 space-y-1.5';
      msgDiv.innerHTML = isUser ? `<b>你：</b>${htmlContent}` : `<b>Burak AI 助理：</b><br>${htmlContent}`;
      container.appendChild(msgDiv);
      container.scrollTop = container.scrollHeight;
    }

    function handleQuickAsk(text) {
      document.getElementById('chat-input').value = text;
      handleChatSubmit();
    }

    function handleChatSubmit(e) {
      if (e) e.preventDefault();
      const input = document.getElementById('chat-input');
      const query = input.value.trim();
      if (!query) return;

      appendChatMessage('user', query);
      input.value = '';

      processChatIntent(query);
    }

    function processChatIntent(query) {
      const q = query.toUpperCase();
      
      if (q.includes('日報') || q.includes('今日')) {
        setViewMode('daily');
        const viewTweets = getFilteredByView(allTweets);
        const bullish = viewTweets.filter(t => t.sentiment === 'Bullish').length;
        const total = viewTweets.length;
        const counts = {};
        viewTweets.forEach(t => t.tickers.forEach(sym => counts[sym] = (counts[sym] || 0) + 1));
        const topSymbols = Object.keys(counts).slice(0, 5).join(', ');

        appendChatMessage('ai', `
          📅 <b>已為你切換至「今日日報」視圖！</b><br>
          • 今日提及推文：<b>${total}</b> 則<br>
          • 看多佔比：<b>${bullish} 則 (${total ? Math.round(bullish/total*100) : 0}%)</b><br>
          • 今日熱門標的：<b>${topSymbols || '無'}</b>
        `);
        return;
      }

      if (q.includes('生技') || q.includes('醫療') || q.includes('藥')) {
        setSectorFilter('生技與醫療製藥');
        appendChatMessage('ai', '💊 <b>已為你篩選「生技與醫療製藥」板塊！</b><br>包含 \\$HIMS, \\$MRNA, \\$JNJ, \\$TEM 等相關討論推文。');
        return;
      }

      if (q.includes('偏多') || q.includes('看多') || q.includes('BULLISH')) {
        const counts = {};
        allTweets.forEach(t => {
          t.tickers.forEach(sym => {
            if (!counts[sym]) counts[sym] = { bullish: 0, bearish: 0, total: 0 };
            if (t.sentiment === 'Bullish') counts[sym].bullish++;
            if (t.sentiment === 'Bearish') counts[sym].bearish++;
            counts[sym].total++;
          });
        });

        const bullishList = Object.entries(counts)
          .filter(([sym, data]) => data.total >= 2 && (data.bullish / (data.bullish + data.bearish || 1)) >= 0.6)
          .sort((a, b) => b[1].bullish - a[1].bullish)
          .slice(0, 6);

        let resHtml = '🚀 <b>目前社群立場偏多的精選標的：</b><br>';
        bullishList.forEach(([sym, data]) => {
          const qData = stockQuotes[sym];
          const priceStr = qData ? `$${qData.price.toFixed(2)} (${qData.changePct>=0?'+':''}${qData.changePct.toFixed(1)}%)` : '';
          resHtml += `• <button onclick="filterByTicker('${sym}')" class="text-amber-400 font-bold font-mono">\\$${sym}</button> ${priceStr}：${data.bullish} 則看多 (${Math.round(data.bullish/data.total*100)}%)<br>`;
        });
        appendChatMessage('ai', resHtml);
        return;
      }

      const tickerMatch = q.match(/\\$?([A-Za-z]{1,6})/);
      const symbol = tickerMatch ? tickerMatch[1].toUpperCase() : null;

      if (symbol) {
        filterByTicker(symbol);
        const tickerTweets = allTweets.filter(t => t.tickers.includes(symbol));
        if (tickerTweets.length > 0) {
          const chronological = [...tickerTweets].sort((a, b) => (a.iso_date || a.date).localeCompare(b.iso_date || b.date));
          const first = chronological[0];
          const latest = chronological[chronological.length - 1];
          const qData = stockQuotes[symbol];
          const priceText = qData ? `$${qData.price.toFixed(2)} (${qData.changePct>=0?'+':''}${qData.changePct.toFixed(2)}%)` : '即時行情模式';

          if (q.includes('風險') || q.includes('疑慮') || q.includes('看空')) {
            const riskTweets = tickerTweets.filter(t => t.sentiment === 'Bearish' || (t.summary && (t.summary.includes('風險') || t.summary.includes('跌'))));
            if (riskTweets.length > 0) {
              let riskHtml = `⚠️ <b>關於 \\$${symbol} 被提及的風險與疑慮：</b><br>`;
              riskTweets.slice(0, 3).forEach(t => {
                riskHtml += `• <b>[${t.date}]</b> ${t.summary || t.translation_zh || t.text} (<a href="${t.url}" target="_blank" class="text-cyan-400 hover:underline">來源</a>)<br>`;
              });
              appendChatMessage('ai', riskHtml);
            } else {
              appendChatMessage('ai', `✅ <b>\\$${symbol}</b> 在歷史推文中未出現顯著的看空或風險警語。`);
            }
            return;
          }

          let analysisHtml = `
            🎯 <b>\\$${symbol} 即時論點脈絡分析：</b><br>
            • <b>所屬板塊：</b>${getSectorForTicker(symbol)}<br>
            • <b>當前股價：</b>${priceText}<br>
            • <b>提及次數：</b>共 ${tickerTweets.length} 則推文<br>
            • <b>最新立場：</b>${latest ? latest.sentiment : '中立'}<br>
            • <b>最新觀點：</b>${latest && latest.summary ? latest.summary : (latest ? latest.text.slice(0, 60) + '...' : '尚無摘要')}<br>
            <div class="mt-2 flex gap-2">
              <button onclick="openDeepDiveModal('${symbol}')" class="px-2 py-1 bg-amber-500 text-slate-950 font-bold rounded">開啟 AI 論點脈絡 ↗</button>
            </div>
          `;
          appendChatMessage('ai', analysisHtml);
          return;
        }
      }

      searchQuery = query.toLowerCase();
      document.getElementById('search-input').value = query;
      render();
      appendChatMessage('ai', `已為你篩選包含關鍵字「<b>${query}</b>」的推文內容。`);
    }

    document.getElementById('search-input').addEventListener('input', (e) => {
      searchQuery = e.target.value.trim().toLowerCase();
      displayLimit = 25;
      render();
    });

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeDeepDiveModal();
        closeCompareModal();
      }
    });

    document.getElementById('last-update-time').innerText = `建置時間：${new Date().toLocaleString('zh-TW', { hour12: false })}`;

    setViewMode('all');
  </script>
</body>
</html>
"""

def generate_html(tweets, ticker_counts, recent_tickers, stock_quotes):
    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
    tweets_json_str = json.dumps(tweets, ensure_ascii=False)
    
    ordered_tickers = []
    for t in recent_tickers:
        if t not in ordered_tickers:
            ordered_tickers.append(t)
            
    for t, _ in sorted(ticker_counts.items(), key=lambda x: x[1], reverse=True):
        if t not in ordered_tickers:
            ordered_tickers.append(t)

    top_tickers_sorted = [[t, ticker_counts.get(t, 0)] for t in ordered_tickers[:60]]
    top_tickers_json_str = json.dumps(top_tickers_sorted, ensure_ascii=False)
    stock_quotes_json_str = json.dumps(stock_quotes, ensure_ascii=False)
    sector_mapping_json_str = json.dumps(SECTOR_MAPPING, ensure_ascii=False)

    html_rendered = HTML_TEMPLATE.replace("__TWEETS_DATA__", tweets_json_str) \
                                 .replace("__TOP_TICKERS__", top_tickers_json_str) \
                                 .replace("__STOCK_QUOTES__", stock_quotes_json_str) \
                                 .replace("__SECTOR_MAPPING__", sector_mapping_json_str)

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_rendered)
    print(f"✅ Burak Finance 儀表板成功產出至 {OUTPUT_HTML} (亮橘金主題、生技板塊與估值卡已全部就緒)", flush=True)

if __name__ == "__main__":
    tweets_raw = load_tweets(TWEETS_FILE)
    sentiment_cache = load_cache(CACHE_FILE)
    cleaned_tweets, counts, recent_tickers = clean_tweet_data(tweets_raw, sentiment_cache)
    
    all_sector_symbols = [s for sub in SECTOR_MAPPING.values() for s in sub]
    combined_target_list = list(dict.fromkeys(recent_tickers + all_sector_symbols + [t[0] for t in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:60]]))[:90]
    
    stock_quotes = fetch_stock_quotes_and_fundamentals(combined_target_list)
    generate_html(cleaned_tweets, counts, recent_tickers, stock_quotes)
