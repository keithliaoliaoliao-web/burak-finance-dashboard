import json
import os
import re
from datetime import datetime
import yfinance as yf

TARGET_HANDLE = "burak_finance"
TWEETS_FILE = "data/tweets.json"
CACHE_FILE = "data/sentiment_cache.json"
OUTPUT_HTML = "docs/index.html"

# 過濾非股票雜訊代號
IGNORE_SYMBOLS = {
    "USD", "USDT", "BTC", "ETH", "AI", "ATH", "CEO", "CFO", "CTO", "FED", 
    "FOMC", "CPI", "PPI", "GDP", "PE", "EPS", "IPO", "SPY", "QQQ", "IWM",
    "NEW", "HOLD", "BUY", "SELL", "CALL", "PUT", "TECH", "EV", "SAFE", "AND", "THE"
}

def load_json(filepath):
    """安全載入 JSON 資料"""
    if not os.path.exists(filepath):
        return [] if "tweets" in filepath else {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ 載入 {filepath} 失敗: {e}", flush=True)
        return [] if "tweets" in filepath else {}

def extract_dynamic_tickers(tweets, sentiment_cache):
    """動態掃描所有提及的美股代號並按頻率排序"""
    ticker_counts = {}
    ticker_pattern = re.compile(r'\$([A-Za-z]{2,6})\b')

    for tw in tweets:
        text = tw.get("text", "")
        for sym in ticker_pattern.findall(text):
            sym_u = sym.upper()
            if sym_u not in IGNORE_SYMBOLS and sym_u.isalpha():
                ticker_counts[sym_u] = ticker_counts.get(sym_u, 0) + 1

    for item in sentiment_cache.values():
        if isinstance(item, dict):
            for sym in item.get("tickers", []):
                sym_clean = sym.replace("$", "").upper().strip()
                if len(sym_clean) >= 2 and sym_clean not in IGNORE_SYMBOLS and sym_clean.isalpha():
                    ticker_counts[sym_clean] = ticker_counts.get(sym_clean, 0) + 1

    sorted_tickers = sorted(ticker_counts.keys(), key=lambda x: ticker_counts[x], reverse=True)
    print(f"🎯 成功挖掘出 {len(sorted_tickers)} 個美股標的", flush=True)
    return sorted_tickers

def fetch_market_quotes(ticker_list):
    """抓取各標的之最新行情數據"""
    quotes = {}
    if not ticker_list:
        return quotes

    print(f"📡 正在抓取 {len(ticker_list)} 檔標的的市場行情...", flush=True)
    symbols_query = " ".join(ticker_list[:40])  # 取前 40 檔熱門標的
    
    try:
        tickers = yf.Tickers(symbols_query)
        for sym in ticker_list[:40]:
            try:
                tk = tickers.tickers.get(sym)
                if not tk:
                    continue
                info = tk.fast_info
                price = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
                prev_close = getattr(info, "previous_close", None)
                
                if price is not None and float(price) > 0:
                    pct = 0.0
                    if prev_close and prev_close > 0:
                        pct = ((price - prev_close) / prev_close) * 100.0
                    
                    quotes[sym] = {
                        "price": round(float(price), 2),
                        "change_pct": round(float(pct), 2),
                        "year_high": round(float(getattr(info, "year_high", 0) or 0), 2),
                        "year_low": round(float(getattr(info, "year_low", 0) or 0), 2)
                    }
            except Exception:
                continue
    except Exception as e:
        print(f"⚠️ 行情抓取異常: {e}", flush=True)

    return quotes

def generate_html_dashboard(tweets, sentiment_cache, quotes, popular_tickers):
    """生成完整 Serenity 旗艦級金融終端儀表板"""
    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
    update_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    # 1. 組裝行情看板卡片（防擠壓獨立垂直佈局）
    market_cards_html = ""
    for sym, q in quotes.items():
        color_text = "text-emerald-400" if q["change_pct"] >= 0 else "text-rose-400"
        color_bg = "bg-emerald-500/10 border-emerald-500/20" if q["change_pct"] >= 0 else "bg-rose-500/10 border-rose-500/20"
        sign = "+" if q["change_pct"] >= 0 else ""
        
        market_cards_html += f"""
        <div class="bg-[#111827]/90 border border-slate-800 rounded-xl p-3 flex flex-col justify-between hover:border-sky-500/40 transition cursor-pointer" onclick="filterByTicker('{sym}')">
            <div class="flex justify-between items-center">
                <span class="text-xs font-bold text-sky-400 font-mono tracking-wider">${sym}</span>
                <span class="px-1.5 py-0.5 rounded text-[11px] font-semibold {color_text} {color_bg} border">
                    {sign}{q['change_pct']:.2f}%
                </span>
            </div>
            <div class="my-1.5 text-lg font-extrabold text-white font-mono">
                ${q['price']:.2f}
            </div>
            <div class="text-[10px] text-slate-400 font-mono pt-1 border-t border-slate-800/80 flex justify-between">
                <span class="text-slate-400">52W</span>
                <span>{q['year_low']} - {q['year_high']}</span>
            </div>
        </div>
        """

    # 2. 組裝熱門標的快速過濾按鈕
    ticker_buttons_html = ""
    for sym in popular_tickers[:18]:
        ticker_buttons_html += f"""
        <button onclick="filterByTicker('{sym}')" class="ticker-btn px-2.5 py-1 bg-[#162032] hover:bg-sky-950/60 border border-slate-800 hover:border-sky-500/40 rounded-lg text-xs font-mono text-sky-300 transition">
            ${sym}
        </button>
        """

    # 3. 組裝推文卡片（Serenity 經典黑金卡片樣式）
    tweets_cards_html = ""
    for tw in tweets:
        t_id = str(tw.get("id", "")).strip()
        text = tw.get("text", "")
        created_at = tw.get("created_at", "").replace("T", " ").replace("Z", "")
        fav = tw.get("favorite_count", 0)
        rt = tw.get("retweet_count", 0)
        views = tw.get("views", 0)
        url = tw.get("url", f"https://twitter.com/{TARGET_HANDLE}/status/{t_id}" if t_id else f"https://twitter.com/{TARGET_HANDLE}")

        ai_data = sentiment_cache.get(t_id, {})
        sentiment = ai_data.get("sentiment", "中立 Neutral") if isinstance(ai_data, dict) else "中立 Neutral"
        summary = ai_data.get("summary_zh", "") if isinstance(ai_data, dict) else ""
        card_tickers = ai_data.get("tickers", []) if isinstance(ai_data, dict) else []

        # 若 AI 快取無標籤，自動從正則補齊
        if not card_tickers:
            card_tickers = [f"${s}" for s in re.findall(r'\$([A-Za-z]{2,6})\b', text)]

        # 情緒色彩設定
        if any(w in sentiment for w in ["多", "Bull", "看多"]):
            sentiment_tag = "看多 Bullish"
            badge_class = "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
            sentiment_category = "bullish"
        elif any(w in sentiment for w in ["空", "Bear", "看空"]):
            sentiment_tag = "看空 Bearish"
            badge_class = "bg-rose-500/10 text-rose-400 border-rose-500/30"
            sentiment_category = "bearish"
        else:
            sentiment_tag = "中立 Neutral"
            badge_class = "bg-slate-500/10 text-slate-400 border-slate-500/30"
            sentiment_category = "neutral"

        # 個股 Tag HTML
        ticker_tags_html = "".join([
            f'<span class="px-2 py-0.5 bg-sky-950/40 text-sky-400 border border-sky-800/40 rounded text-xs font-mono">{tk}</span>'
            for tk in card_tickers[:4]
        ])

        # 替換正文中的 $TICKER 為高亮標籤
        highlighted_text = re.sub(
            r'(\$[A-Za-z]{2,6})\b', 
            r'<span class="text-sky-300 font-semibold">\1</span>', 
            text
        )

        tweets_cards_html += f"""
        <div class="tweet-card bg-[#0f172a]/95 border border-slate-800 hover:border-slate-700/80 rounded-xl p-5 transition shadow-lg flex flex-col justify-between" 
             data-sentiment="{sentiment_category}" 
             data-tickers="{' '.join(card_tickers)}" 
             data-text="{text.lower()}">
            <div>
                <!-- 卡片頂部：情緒標籤、股票標籤、發布時間 -->
                <div class="flex flex-wrap justify-between items-center gap-2 mb-3">
                    <div class="flex flex-wrap items-center gap-1.5">
                        <span class="px-2.5 py-0.5 rounded text-xs font-semibold border {badge_class}">
                            {sentiment_tag}
                        </span>
                        {ticker_tags_html}
                    </div>
                    <span class="text-xs font-mono text-slate-400">{created_at[:16] if len(created_at)>=16 else created_at}</span>
                </div>

                <!-- AI 核心觀點 -->
                {f'''
                <div class="text-amber-300 font-medium text-sm mb-3 flex items-start gap-1.5 bg-amber-950/20 border border-amber-800/30 p-2.5 rounded-lg">
                    <span class="text-amber-400 font-bold shrink-0">⚡ 觀點：</span>
                    <span class="leading-relaxed">{summary}</span>
                </div>
                ''' if summary else ''}

                <!-- 推文正文 -->
                <p class="text-sm text-slate-200 leading-relaxed whitespace-pre-wrap">{highlighted_text}</p>
            </div>

            <!-- 卡片底部：互動數據與原推連結 -->
            <div class="mt-5 pt-3 border-t border-slate-800/70 flex justify-between items-center text-xs text-slate-400 font-mono">
                <div class="flex items-center space-x-4">
                    <span class="flex items-center gap-1"><span class="text-rose-500">❤️</span> {fav}</span>
                    <span class="flex items-center gap-1"><span class="text-sky-400">🔁</span> {rt}</span>
                    {f'<span class="flex items-center gap-1">👁️ {views:,}</span>' if views > 0 else ''}
                </div>
                <a href="{url}" target="_blank" class="text-sky-400 hover:text-sky-300 font-sans font-medium transition">開啟推文 ↗</a>
            </div>
        </div>
        """

    full_html = f"""<!DOCTYPE html>
<html lang="zh-TW" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Burak Finance 即時情報與市場情緒儀表板</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body {{
            background-color: #0b0f19;
            color: #e2e8f0;
        }}
    </style>
</head>
<body class="min-h-screen p-4 sm:p-6 lg:p-8 font-sans antialiased">
    <div class="max-w-7xl mx-auto space-y-7">
        
        <!-- 頁首 Header -->
        <header class="flex flex-col md:flex-row justify-between items-start md:items-center pb-6 border-b border-slate-800 gap-4">
            <div>
                <h1 class="text-2xl sm:text-3xl font-black bg-gradient-to-r from-sky-400 via-teal-300 to-indigo-400 bg-clip-text text-transparent">
                    Burak Finance 即時情報與市場情緒儀表板
                </h1>
                <p class="text-xs sm:text-sm text-slate-400 mt-1">全面追蹤推文提及標的、即時行情與 Gemini AI 深度觀點</p>
            </div>
            <div class="text-xs text-slate-400 bg-[#111827] px-4 py-2 rounded-xl border border-slate-800 font-mono shadow">
                最後更新時間: <span class="text-sky-400">{update_time}</span>
            </div>
        </header>

        <!-- 行情看板區塊 -->
        <section class="space-y-3">
            <div class="flex justify-between items-center">
                <h2 class="text-base font-bold text-slate-200 flex items-center gap-2">
                    <span>📈</span> 動態追蹤標的即時行情 ({len(quotes)} 檔)
                </h2>
                <span class="text-xs text-slate-400">點擊標的卡片可直接篩選推文</span>
            </div>
            <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
                {market_cards_html if market_cards_html else '<div class="col-span-full text-slate-400 text-sm">尚無標的行情資料</div>'}
            </div>
        </section>

        <!-- 多維度篩選與搜尋工具列 -->
        <section class="bg-[#111827]/80 border border-slate-800 rounded-2xl p-4 sm:p-5 space-y-4 shadow-xl">
            <div class="flex flex-col md:flex-row justify-between items-stretch md:items-center gap-4">
                <!-- 關鍵字搜尋框 -->
                <div class="relative flex-1">
                    <input type="text" id="searchInput" oninput="applyFilters()" 
                           placeholder="搜尋推文關鍵字、股票代號（例如: NVDA, TSMC）..." 
                           class="w-full bg-[#0b0f19] border border-slate-700/80 rounded-xl px-4 py-2.5 text-sm text-white placeholder-slate-400 focus:outline-none focus:border-sky-500 transition">
                </div>

                <!-- 情緒標籤切換 -->
                <div class="flex items-center gap-1.5 overflow-x-auto pb-1 md:pb-0">
                    <button onclick="setSentimentFilter('all')" id="btn-all" class="sentiment-btn px-3 py-1.5 rounded-lg text-xs font-medium bg-sky-500 text-white transition">全部</button>
                    <button onclick="setSentimentFilter('bullish')" id="btn-bullish" class="sentiment-btn px-3 py-1.5 rounded-lg text-xs font-medium bg-[#162032] text-slate-300 hover:text-white border border-slate-800 transition">看多</button>
                    <button onclick="setSentimentFilter('bearish')" id="btn-bearish" class="sentiment-btn px-3 py-1.5 rounded-lg text-xs font-medium bg-[#162032] text-slate-300 hover:text-white border border-slate-800 transition">看空</button>
                    <button onclick="setSentimentFilter('neutral')" id="btn-neutral" class="sentiment-btn px-3 py-1.5 rounded-lg text-xs font-medium bg-[#162032] text-slate-300 hover:text-white border border-slate-800 transition">中立</button>
                </div>
            </div>

            <!-- 熱門標的快速標籤列 -->
            <div class="flex flex-wrap items-center gap-2 pt-2 border-t border-slate-800/80">
                <span class="text-xs text-slate-400 font-medium mr-1">熱門標的:</span>
                {ticker_buttons_html}
                <button onclick="resetFilters()" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 rounded-lg text-xs text-slate-300 transition">清除篩選</button>
            </div>
        </section>

        <!-- 推文時間軸 -->
        <section class="space-y-4">
            <div class="flex justify-between items-center">
                <h2 class="text-base font-bold text-slate-200 flex items-center gap-2">
                    <span>💬</span> 即時推文情報串流 (<span id="visibleCount">{len(tweets)}</span> / {len(tweets)} 則)
                </h2>
            </div>
            <div id="tweetsContainer" class="grid grid-cols-1 md:grid-cols-2 gap-4">
                {tweets_cards_html}
            </div>
        </section>
    </div>

    <!-- 即時前端篩選邏輯腳本 -->
    <script>
        let currentSentiment = 'all';
        let currentTicker = '';

        function setSentimentFilter(type) {{
            currentSentiment = type;
            document.querySelectorAll('.sentiment-btn').forEach(btn => {{
                btn.className = 'sentiment-btn px-3 py-1.5 rounded-lg text-xs font-medium bg-[#162032] text-slate-300 hover:text-white border border-slate-800 transition';
            }});
            const activeBtn = document.getElementById('btn-' + type);
            if (activeBtn) {{
                activeBtn.className = 'sentiment-btn px-3 py-1.5 rounded-lg text-xs font-medium bg-sky-500 text-white transition';
            }}
            applyFilters();
        }}

        function filterByTicker(ticker) {{
            currentTicker = '$' + ticker.replace('$', '').toUpperCase();
            document.getElementById('searchInput').value = currentTicker;
            applyFilters();
        }}

        function resetFilters() {{
            currentSentiment = 'all';
            currentTicker = '';
            document.getElementById('searchInput').value = '';
            setSentimentFilter('all');
        }}

        function applyFilters() {{
            const searchKeyword = document.getElementById('searchInput').value.toLowerCase().trim();
            const cards = document.querySelectorAll('.tweet-card');
            let visibleCount = 0;

            cards.forEach(card => {{
                const sentiment = card.getAttribute('data-sentiment');
                const tickers = card.getAttribute('data-tickers').toLowerCase();
                const text = card.getAttribute('data-text');

                const matchesSentiment = (currentSentiment === 'all') || (sentiment === currentSentiment);
                const matchesSearch = !searchKeyword || text.includes(searchKeyword) || tickers.includes(searchKeyword);

                if (matchesSentiment && matchesSearch) {{
                    card.style.display = 'flex';
                    visibleCount++;
                }} else {{
                    card.style.display = 'none';
                }}
            }});

            document.getElementById('visibleCount').innerText = visibleCount;
        }}
    </script>
</body>
</html>
"""
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(full_html)

    print(f"🎉 Serenity 旗艦版儀表板 HTML 建置完成！輸出至: {OUTPUT_HTML}", flush=True)

if __name__ == "__main__":
    tweets = load_json(TWEETS_FILE)
    sentiment_cache = load_json(CACHE_FILE)
    
    # 1. 動態挖掘標的
    dynamic_tickers = extract_dynamic_tickers(tweets, sentiment_cache)
    
    # 2. 抓取行情報價
    market_quotes = fetch_market_quotes(dynamic_tickers)
    
    # 3. 編譯產出完整功能儀表板
    generate_html_dashboard(tweets, sentiment_cache, market_quotes, dynamic_tickers)
