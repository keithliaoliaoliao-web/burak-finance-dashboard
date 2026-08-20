import json
import os
import re
from datetime import datetime
import yfinance as yf

# 核心設定與路徑
TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"
CACHE_FILE = "data/sentiment_cache.json"
OUTPUT_HTML = "docs/index.html"

# 常見非股票代號的雜訊過濾清單
IGNORE_SYMBOLS = {
    "USD", "USDT", "BTC", "ETH", "AI", "ATH", "CEO", "CFO", "CTO", "FED", 
    "FOMC", "CPI", "PPI", "GDP", "PE", "EPS", "IPO", "SPY", "QQQ", "IWM",
    "NEW", "HOLD", "BUY", "SELL", "CALL", "PUT", "TECH", "EV", "SAFE", "AND", "THE"
}

def load_json(filepath):
    """安全載入 JSON 檔案"""
    if not os.path.exists(filepath):
        return [] if "tweets" in filepath else {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ 載入 {filepath} 失敗: {e}", flush=True)
        return [] if "tweets" in filepath else {}

def extract_all_dynamic_tickers(tweets, sentiment_cache):
    """全面動態挖掘推文與 AI 分析中所有出現過的美股代號（2~6 字元）"""
    ticker_counts = {}
    ticker_latest_tweet = {}

    # 擷取 2 到 6 個字母的美股代號，避免單字母雜訊
    ticker_pattern = re.compile(r'\$([A-Za-z]{2,6})\b')

    for tw in tweets:
        text = tw.get("text", "")
        created_at = tw.get("created_at", "")
        matches = ticker_pattern.findall(text)
        
        for sym in matches:
            sym_upper = sym.upper()
            if sym_upper not in IGNORE_SYMBOLS and sym_upper.isalpha():
                ticker_counts[sym_upper] = ticker_counts.get(sym_upper, 0) + 1
                if sym_upper not in ticker_latest_tweet or created_at > ticker_latest_tweet[sym_upper]:
                    ticker_latest_tweet[sym_upper] = created_at

    for item in sentiment_cache.values():
        if isinstance(item, dict):
            tickers = item.get("tickers", [])
            for sym in tickers:
                sym_clean = sym.replace("$", "").upper().strip()
                if len(sym_clean) >= 2 and sym_clean not in IGNORE_SYMBOLS and sym_clean.isalpha():
                    ticker_counts[sym_clean] = ticker_counts.get(sym_clean, 0) + 1

    sorted_tickers = sorted(
        ticker_counts.keys(),
        key=lambda x: (ticker_counts[x], ticker_latest_tweet.get(x, "")),
        reverse=True
    )

    print(f"🎯 全面掃描完成！共挖掘出 {len(sorted_tickers)} 個美股標的：{', '.join(sorted_tickers[:15])}...", flush=True)
    return sorted_tickers

def fetch_market_quotes(ticker_list):
    """批次抓取動態標的之即時市場數據"""
    quotes_data = {}
    if not ticker_list:
        return quotes_data

    print(f"📡 正在抓取 {len(ticker_list)} 檔標的的即時市場行情...", flush=True)
    
    symbols_query = " ".join(ticker_list)
    try:
        tickers = yf.Tickers(symbols_query)
        for sym in ticker_list:
            try:
                tk = tickers.tickers.get(sym)
                if not tk:
                    continue
                info = tk.fast_info
                
                price = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
                prev_close = getattr(info, "previous_close", None)
                
                if price is not None and float(price) > 0:
                    pct_change = 0.0
                    if prev_close and prev_close > 0:
                        pct_change = ((price - prev_close) / prev_close) * 100.0
                    
                    quotes_data[sym] = {
                        "price": round(float(price), 2),
                        "change_pct": round(float(pct_change), 2),
                        "currency": getattr(info, "currency", "USD") or "USD",
                        "year_high": round(float(getattr(info, "year_high", 0) or 0), 2),
                        "year_low": round(float(getattr(info, "year_low", 0) or 0), 2)
                    }
            except Exception:
                continue
    except Exception as e:
        print(f"⚠️ 批次抓取行情異常: {e}", flush=True)

    print(f"✨ 成功取得 {len(quotes_data)} 檔標的的即時報價！", flush=True)
    return quotes_data

def generate_html_dashboard(tweets, sentiment_cache, quotes):
    """產生全新防破版、垂直結構響應式儀表板 HTML"""
    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
    update_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    # 組合垂直結構標的卡片（絕不重疊）
    quotes_cards = ""
    for sym, q in quotes.items():
        color_text = "text-emerald-400" if q["change_pct"] >= 0 else "text-rose-400"
        color_border = "border-emerald-500/30" if q["change_pct"] >= 0 else "border-rose-500/30"
        color_bg = "bg-emerald-500/10" if q["change_pct"] >= 0 else "bg-rose-500/10"
        sign = "+" if q["change_pct"] >= 0 else ""
        
        quotes_cards += f"""
        <div class="bg-slate-800/90 border border-slate-700/80 rounded-xl p-3 flex flex-col justify-between shadow-lg hover:border-sky-500/60 transition duration-150">
            <!-- 頂層：代號與漲跌幅 -->
            <div class="flex justify-between items-center">
                <span class="text-xs font-black text-sky-400 tracking-wider font-mono">${sym}</span>
                <span class="px-1.5 py-0.5 rounded text-[11px] font-bold {color_text} {color_bg} border {color_border}">
                    {sign}{q['change_pct']:.2f}%
                </span>
            </div>
            <!-- 中層：現價（獨立一行） -->
            <div class="my-2 text-xl font-black text-white font-mono tracking-tight">
                ${q['price']:.2f}
            </div>
            <!-- 底層：52週高低點 -->
            <div class="text-[10px] text-slate-400 font-mono pt-1.5 border-t border-slate-700/50 flex justify-between items-center">
                <span class="text-slate-500">52W</span>
                <span class="text-slate-300 font-medium">{q['year_low']} - {q['year_high']}</span>
            </div>
        </div>
        """

    # 組合推文串流與 AI 觀點卡片
    tweets_list_html = ""
    for tw in tweets[:60]:
        t_id = tw.get("id", "")
        text = tw.get("text", "")
        created_at = tw.get("created_at", "")
        fav = tw.get("favorite_count", 0)
        rt = tw.get("retweet_count", 0)
        url = tw.get("url", f"https://twitter.com/{TARGET_HANDLE}/status/{t_id}" if t_id else f"https://twitter.com/{TARGET_HANDLE}")
        
        ai_data = sentiment_cache.get(t_id, {})
        sentiment = ai_data.get("sentiment", "中立") if isinstance(ai_data, dict) else "中立"
        summary = ai_data.get("summary_zh", "") if isinstance(ai_data, dict) else ""
        
        badge_color = "bg-emerald-500/10 text-emerald-400 border-emerald-500/30" if "多" in sentiment or "Bull" in sentiment else ("bg-rose-500/10 text-rose-400 border-rose-500/30" if "空" in sentiment or "Bear" in sentiment else "bg-slate-500/10 text-slate-400 border-slate-500/30")

        tweets_list_html += f"""
        <div class="bg-slate-800/70 border border-slate-700/60 rounded-xl p-5 hover:border-slate-600 transition shadow-md flex flex-col justify-between">
            <div>
                <div class="flex justify-between items-start mb-3">
                    <span class="px-2.5 py-0.5 rounded-full text-xs font-medium border {badge_color}">
                        情緒判定：{sentiment}
                    </span>
                    <span class="text-xs text-slate-500 font-mono">{created_at.replace('T', ' ').replace('Z', '')}</span>
                </div>
                {f'<div class="text-sm font-semibold text-sky-300 mb-2.5 p-2 bg-sky-950/30 border border-sky-800/40 rounded-lg">💡 AI 觀點：{summary}</div>' if summary else ''}
                <p class="text-sm text-slate-200 leading-relaxed whitespace-pre-wrap">{text}</p>
            </div>
            <div class="mt-4 pt-3 border-t border-slate-700/40 flex justify-between items-center text-xs text-slate-400">
                <div class="flex space-x-4">
                    <span>❤️ {fav}</span>
                    <span>🔁 {rt}</span>
                </div>
                <a href="{url}" target="_blank" class="text-sky-400 hover:text-sky-300 font-medium transition">開啟原推文 ↗</a>
            </div>
        </div>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="zh-TW" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Burak Finance 即時情報與動態標的儀表板</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen p-4 sm:p-6 lg:p-8 font-sans">
    <div class="max-w-7xl mx-auto space-y-8">
        <!-- 頁首 Header -->
        <header class="flex flex-col sm:flex-row justify-between items-start sm:items-center pb-6 border-b border-slate-800 gap-4">
            <div>
                <h1 class="text-2xl sm:text-3xl font-black bg-gradient-to-r from-sky-400 via-teal-300 to-indigo-400 bg-clip-text text-transparent">
                    Burak Finance 即時情報與動態標的儀表板
                </h1>
                <p class="text-xs sm:text-sm text-slate-400 mt-1">全面追蹤推文提及標的、即時行情與 Gemini AI 深度觀點</p>
            </div>
            <div class="text-xs text-slate-400 bg-slate-800 px-3.5 py-2 rounded-xl border border-slate-700/60 shadow">
                最後更新時間: <span class="text-sky-400 font-mono">{update_time}</span>
            </div>
        </header>

        <!-- 全面動態行情看板 (完全防破版結構) -->
        <section class="space-y-4">
            <h2 class="text-lg font-bold text-slate-200 flex items-center gap-2">
                <span>📈</span> 動態挖掘標的即時行情 ({len(quotes)} 檔)
            </h2>
            <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
                {quotes_cards if quotes_cards else '<div class="col-span-full text-slate-500 text-sm">尚無標的行情資料</div>'}
            </div>
        </section>

        <!-- 推文時間軸與 AI 深度論點 -->
        <section class="space-y-4">
            <h2 class="text-lg font-bold text-slate-200 flex items-center gap-2">
                <span>💬</span> 最新推文串流與 AI 深度論點 ({len(tweets)} 則)
            </h2>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                {tweets_list_html}
            </div>
        </section>
    </div>
</body>
</html>
"""
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"🎉 儀表板 HTML 建置完成！輸出路徑: {OUTPUT_HTML}", flush=True)

if __name__ == "__main__":
    tweets = load_json(TWEETS_FILE)
    sentiment_cache = load_json(CACHE_FILE)
    
    # 1. 全面動態挖掘美股標的
    dynamic_tickers = extract_all_dynamic_tickers(tweets, sentiment_cache)
    
    # 2. 抓取最新市場行情
    market_quotes = fetch_market_quotes(dynamic_tickers)
    
    # 3. 編譯產生乾淨清晰的 HTML
    generate_html_dashboard(tweets, sentiment_cache, market_quotes)
