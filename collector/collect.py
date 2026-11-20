#!/usr/bin/env python3
"""
TW Stock Lab - data collector / 台股研究室 資料收集程式

Collects, and appends to CSV files in data/:
  prices.csv              daily prices: TW watchlist + worldwide markets (Yahoo)
  snapshots/YYYY-MM.csv   price at the moment of each run (e.g. Korea/Japan at open)
  inst_market.csv         三大法人 market-wide buy/sell (TWSE)
  inst_stocks.csv         三大法人 per watchlist stock (TWSE, listed stocks)
  margin.csv              融資融券 market totals (TWSE)
  revenue.csv             月營收 for watchlist stocks (TWSE OpenAPI, listed stocks)
  news/YYYY-MM.csv        Google News headlines, categorised + sentiment score
  ptt/YYYY-MM.csv         PTT 股票版 post titles + push count + sentiment score
  daily_signals.csv       per-day summary of news & PTT mood (for backtesting)
  run_log.csv             what ran, when, and what failed

Usage:  python collector/collect.py --mode asia_open|tw_close|us_open|us_close|all
        python collector/collect.py --mode all --backfill-days 20   (also fill past TWSE days)
Only uses the Python standard library (nothing to install).
"""
import argparse, csv, html, json, os, re, sys, time, traceback
import urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config")
DATA = os.path.join(ROOT, "data")
TPE = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


# ---------------------------------------------------------------- helpers
def now_tpe():
    return datetime.now(TPE)


def load_cfg(name):
    with open(os.path.join(CFG, name), encoding="utf-8") as f:
        return json.load(f)


def fetch(url, tries=3, timeout=30):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa
            last = e
            time.sleep(2 * (i + 1))
    raise last


def fetch_json(url, **kw):
    return json.loads(fetch(url, **kw))


def upsert_csv(name, fields, rows, key):
    """Add rows to data/<name>; rows with the same key replace older ones."""
    path = os.path.join(DATA, name)
    store = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                store[tuple(r.get(k, "") for k in key)] = r
    added = 0
    for r in rows:
        k = tuple(str(r.get(x, "")) for x in key)
        if k not in store:
            added += 1
        store[k] = {f: r.get(f, "") for f in fields}
    with open(path, "w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig = opens fine in Excel
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for k in sorted(store):
            w.writerow({f: store[k].get(f, "") for f in fields})
    return added


def upsert_monthly(folder, fields, rows, key, date_field):
    """Big tables are split into one file per month (data/<folder>/YYYY-MM.csv) so no file gets too large."""
    groups, added = {}, 0
    for r in rows:
        groups.setdefault((r.get(date_field) or now_tpe().strftime("%Y-%m"))[:7], []).append(r)
    os.makedirs(os.path.join(DATA, folder), exist_ok=True)
    for month, rs in groups.items():
        added += upsert_csv(f"{folder}/{month}.csv", fields, rs, key)
    return added


def read_monthly(folder):
    d = os.path.join(DATA, folder)
    out = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".csv"):
                out += read_csv(f"{folder}/{fn}")
    return out


def short_id(link):
    import hashlib
    return hashlib.sha1(link.encode()).hexdigest()[:12]


def read_csv(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return None


# ---------------------------------------------------------------- sentiment
SENT = load_cfg("sentiment.json")


def score_text(text):
    """Rough mood score: -1 (bad news) .. +1 (good news). 0 = neutral / unknown."""
    t = text.lower()
    pos = sum(t.count(w.lower()) for w in SENT["positive"])
    neg = sum(t.count(w.lower()) for w in SENT["negative"])
    if pos + neg == 0:
        return 0.0
    return round((pos - neg) / (pos + neg), 3)


# ---------------------------------------------------------------- prices (Yahoo)
def all_symbols():
    out = []
    for s in load_cfg("watchlist.json")["stocks"]:
        out.append({"symbol": f"{s['code']}.{s['market']}", "zh": s["zh"], "en": s["en"], "group": "watchlist"})
    out += load_cfg("markets.json")["markets"]
    return out


def collect_prices(mode, log):
    have = {r["symbol"] for r in read_csv("prices.csv")}
    rows, snaps = [], []
    stamp = now_tpe().strftime("%Y-%m-%d %H:%M")
    for s in all_symbols():
        sym = s["symbol"]
        rng = "1mo" if sym in have else "5y"  # first time: grab 5 years of history for backtesting
        try:
            d = fetch_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range={rng}&interval=1d")
            res = d["chart"]["result"][0]
            meta, ts = res["meta"], res.get("timestamp") or []
            q = res["indicators"]["quote"][0]
            off = meta.get("gmtoffset", 0)
            bars = []
            for i, t in enumerate(ts):
                c = q["close"][i]
                if c is None and i == len(ts) - 1 and meta.get("regularMarketPrice"):
                    c = meta["regularMarketPrice"]  # Yahoo often leaves the newest bar blank; use the live price
                    q["open"][i] = q["open"][i] or meta.get("regularMarketOpen") or c
                    q["high"][i] = q["high"][i] or meta.get("regularMarketDayHigh") or c
                    q["low"][i] = q["low"][i] or meta.get("regularMarketDayLow") or c
                if c is None:
                    continue
                day = datetime.fromtimestamp(t + off, timezone.utc).strftime("%Y-%m-%d")
                bars.append({"date": day, "symbol": sym, "name_zh": s["zh"], "name_en": s["en"], "group": s["group"],
                             "open": round(q["open"][i] or c, 4), "high": round(q["high"][i] or c, 4),
                             "low": round(q["low"][i] or c, 4), "close": round(c, 4), "volume": q["volume"][i] or 0})
            rows += bars
            if len(bars) >= 2:
                last, prev = bars[-1], bars[-2]
                mt = meta.get("regularMarketTime")
                snaps.append({"run_time_tpe": stamp, "mode": mode, "symbol": sym, "name_zh": s["zh"], "name_en": s["en"],
                              "group": s["group"], "bar_date": last["date"],
                              "market_time_tpe": datetime.fromtimestamp(mt, TPE).strftime("%Y-%m-%d %H:%M") if mt else "",
                              "price": last["close"], "prev_close": prev["close"],
                              "change_pct": round((last["close"] / prev["close"] - 1) * 100, 3) if prev["close"] else ""})
        except Exception as e:
            log.append(f"price {sym}: {e}")
        time.sleep(0.3)
    a = upsert_csv("prices.csv", ["date", "symbol", "name_zh", "name_en", "group", "open", "high", "low", "close", "volume"],
                   rows, ["date", "symbol"])
    b = upsert_monthly("snapshots", ["run_time_tpe", "mode", "symbol", "name_zh", "name_en", "group", "bar_date",
                                     "market_time_tpe", "price", "prev_close", "change_pct"], snaps, ["run_time_tpe", "symbol"], "run_time_tpe")
    return f"prices +{a} new rows, snapshots +{b}"


# ---------------------------------------------------------------- TWSE money flow & margin
def twse_days(backfill):
    """Today plus N past weekdays (Taipei dates)."""
    days, d = [], now_tpe().date()
    while len(days) < backfill + 1:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return days


def collect_twse(log, backfill=0, days=None):
    watch = {s["code"] for s in load_cfg("watchlist.json")["stocks"] if s["market"] == "TW"}
    mk, st, mg, allst = [], [], [], []
    if days is None:
        days = twse_days(backfill)
        # self-heal: re-try recent weekdays that are still missing (e.g. a request failed last time)
        have_m = {r["date"] for r in read_csv("inst_market.csv")}
        have_g = {r["date"] for r in read_csv("margin.csv")}
        have_a = {r["date"] for r in read_monthly("inst_all") if r.get("market") == "TW"}
        for d in twse_days(10)[1:]:
            if d not in days and (d.isoformat() not in have_m or d.isoformat() not in have_g or d.isoformat() not in have_a):
                days.append(d)
    for d in days:
        ds = d.strftime("%Y%m%d")
        day = d.strftime("%Y-%m-%d")
        try:  # market-wide 三大法人
            j = fetch_json(f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?response=json&dayDate={ds}&type=day")
            if j.get("stat") == "OK":
                for r in j["data"]:
                    mk.append({"date": day, "investor": r[0], "buy": num(r[1]), "sell": num(r[2]), "net": num(r[3])})
        except Exception as e:
            log.append(f"BFI82U {ds}: {e}")
        time.sleep(3)  # TWSE blocks fast repeated requests
        try:  # per-stock 三大法人
            j = fetch_json(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={ds}&selectType=ALLBUT0999&response=json")
            if j.get("stat") != "OK" and d < now_tpe().date():
                log.append(f"T86 {ds}: {str(j.get('stat'))[:40]}")
            if j.get("stat") == "OK":
                f = j["fields"]
                ix = {name: i for i, name in enumerate(f)}
                for r in j["data"]:
                    code = r[0].strip()
                    if len(code) == 4 and code.isdigit():
                        allst.append({"date": day, "code": code, "market": "TW",
                                      "foreign_net": num(r[ix["外陸資買賣超股數(不含外資自營商)"]]) if "外陸資買賣超股數(不含外資自營商)" in ix else "",
                                      "trust_net": num(r[ix["投信買賣超股數"]]) if "投信買賣超股數" in ix else "",
                                      "dealer_net": num(r[ix["自營商買賣超股數"]]) if "自營商買賣超股數" in ix else ""})
                    if code in watch:
                        def g(col):
                            return num(r[ix[col]]) if col in ix else ""
                        st.append({"date": day, "code": code, "name": r[1].strip(),
                                   "foreign_net": g("外陸資買賣超股數(不含外資自營商)"),
                                   "trust_net": g("投信買賣超股數"),
                                   "dealer_net": g("自營商買賣超股數"),
                                   "total_net": g("三大法人買賣超股數")})
        except Exception as e:
            log.append(f"T86 {ds}: {e}")
        time.sleep(3)
        try:  # 融資融券
            j = fetch_json(f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={ds}&selectType=MS&response=json")
            if j.get("stat") == "OK":
                for r in j["tables"][0]["data"]:
                    mg.append({"date": day, "item": r[0], "buy": num(r[1]), "sell": num(r[2]), "repay": num(r[3]),
                               "prev_balance": num(r[4]), "balance": num(r[5])})
        except Exception as e:
            log.append(f"MARGN {ds}: {e}")
        time.sleep(3)
    a = upsert_csv("inst_market.csv", ["date", "investor", "buy", "sell", "net"], mk, ["date", "investor"])
    b = upsert_csv("inst_stocks.csv", ["date", "code", "name", "foreign_net", "trust_net", "dealer_net", "total_net"], st, ["date", "code"])
    c = upsert_csv("margin.csv", ["date", "item", "buy", "sell", "repay", "prev_balance", "balance"], mg, ["date", "item"])
    # OTC (上櫃) institutional flow - TPEx only publishes the latest day
    try:
        for r in fetch_json("https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading", timeout=60):
            code = r.get("SecuritiesCompanyCode", "").strip()
            if len(code) == 4 and code.isdigit():
                ds = r["Date"]
                allst.append({"date": f"{int(ds[:-4]) + 1911}-{ds[-4:-2]}-{ds[-2:]}", "code": code, "market": "TWO",
                              "foreign_net": num(r.get("Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference")),
                              "trust_net": num(r.get("SecuritiesInvestmentTrustCompanies-Difference")),
                              "dealer_net": num(r.get("Dealers-Difference"))})
    except Exception as e:
        log.append(f"TPEx 3insti: {e}")
    x = upsert_monthly("inst_all", ["date", "code", "market", "foreign_net", "trust_net", "dealer_net"], allst, ["date", "code"], "date")
    return f"money-flow +{a}, per-stock +{b}, margin +{c}, all-stock flow +{x}"


def collect_revenue_all(log):
    """Monthly revenue + industry for every listed and OTC company (also refreshes the stock universe)."""
    rows, uni = [], []
    for mk, url in (("TW", "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"),
                    ("TWO", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O")):
        try:
            for r in fetch_json(url, timeout=90):
                code = r.get("公司代號", "").strip()
                if not (len(code) == 4 and code.isdigit()):
                    continue
                ym = r.get("資料年月", "")
                ym = f"{int(ym[:-2]) + 1911}-{ym[-2:]}" if len(ym) >= 4 else ym
                uni.append({"code": code, "market": mk, "name": r.get("公司名稱", "").strip(), "industry": r.get("產業別", "").strip()})
                rows.append({"year_month": ym, "code": code, "market": mk, "revenue": num(r.get("營業收入-當月營收")),
                             "mom_pct": round(num(r.get("營業收入-上月比較增減(%)")) or 0, 2),
                             "yoy_pct": round(num(r.get("營業收入-去年同月增減(%)")) or 0, 2)})
        except Exception as e:
            log.append(f"revenue_all {mk}: {e}")
    a = upsert_csv("market/revenue_all.csv", ["year_month", "code", "market", "revenue", "mom_pct", "yoy_pct"], rows, ["year_month", "code"])
    if len(uni) > 1500:  # only replace the universe if the download looks complete
        upsert_csv("market/universe.csv", ["code", "market", "name", "industry"], uni, ["code"])
    return f"revenue(all) +{a}"


def collect_market_prices(log):
    """Last month of daily prices for every stock (fills gaps automatically). Saved per month."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from history import one
    from concurrent.futures import ThreadPoolExecutor
    uni = read_csv("market/universe.csv")
    rows = []
    with ThreadPoolExecutor(4) as ex:
        for res in ex.map(lambda r: one(r, "1mo"), uni):
            rows += [{"date": x[0], "code": x[1], "open": x[2], "high": x[3], "low": x[4], "close": x[5], "volume": x[6]} for x in res]
    if not rows:
        log.append("market prices: nothing downloaded")
    a = upsert_monthly("market/daily", ["date", "code", "open", "high", "low", "close", "volume"], rows, ["date", "code"], "date")
    return f"market prices +{a} rows ({len({r['code'] for r in rows})} stocks)"


def run_analysis(log):
    try:
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "analyze.py")],
                           capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            log.append("analysis failed: " + r.stderr[-800:])
            return "analysis FAILED"
        return "analysis + report done"
    except Exception as e:
        log.append(f"analysis: {e}")
        return "analysis FAILED"


def run_script(name, log):
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), name)],
                       capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        log.append(f"{name} failed: " + r.stderr[-600:])
        return f"{name} FAILED"
    return f"{name} done"


def collect_revenue(log):
    watch = {s["code"] for s in load_cfg("watchlist.json")["stocks"] if s["market"] == "TW"}
    rows = []
    try:
        for r in fetch_json("https://openapi.twse.com.tw/v1/opendata/t187ap05_L", timeout=60):
            if r.get("公司代號") in watch:
                ym = r.get("資料年月", "")  # ROC year e.g. 11510
                ym = f"{int(ym[:-2]) + 1911}-{ym[-2:]}" if len(ym) >= 4 else ym
                rows.append({"year_month": ym, "code": r["公司代號"], "name": r.get("公司名稱", ""),
                             "revenue": num(r.get("營業收入-當月營收")),
                             "mom_pct": round(num(r.get("營業收入-上月比較增減(%)")) or 0, 2),
                             "yoy_pct": round(num(r.get("營業收入-去年同月增減(%)")) or 0, 2),
                             "ytd_yoy_pct": round(num(r.get("累計營業收入-前期比較增減(%)")) or 0, 2)})
    except Exception as e:
        log.append(f"revenue: {e}")
    a = upsert_csv("revenue.csv", ["year_month", "code", "name", "revenue", "mom_pct", "yoy_pct", "ytd_yoy_pct"], rows, ["year_month", "code"])
    return f"revenue +{a}"


# ---------------------------------------------------------------- news (Google News RSS)
def tag_categories(title, cats):
    t = title.lower()
    return [c["id"] for c in cats if any(k.lower() in t for k in c["keywords"])]


MAX_PER_QUERY = 15  # top headlines per search, keeps files small


def collect_news(mode, log, day=None):
    """day=None: last 24 hours. day=date: that past day only (used by catch-up)."""
    cats = load_cfg("news.json")["categories"]
    rows, seen = [], set()
    stamp = now_tpe().strftime("%Y-%m-%d %H:%M")
    when = " when:1d" if day is None else f" after:{day.isoformat()} before:{(day + timedelta(days=1)).isoformat()}"
    for c in cats:
        for q in c["queries"]:
            en = bool(re.fullmatch(r"[A-Za-z0-9 .&'-]+", q))
            loc = "hl=en-US&gl=US&ceid=US:en" if en else "hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
            url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q + when)}&{loc}"
            try:
                root = ET.fromstring(fetch(url))
                for it in list(root.iter("item"))[:MAX_PER_QUERY]:
                    title = html.unescape(it.findtext("title") or "").strip()
                    link = it.findtext("link") or ""
                    if not title or link in seen:
                        continue
                    seen.add(link)
                    src = it.findtext("source") or ""
                    pub = it.findtext("pubDate") or ""
                    try:
                        pt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc).astimezone(TPE)
                        pub_tpe = pt.strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        pub_tpe = ""
                    clean = re.sub(r"\s+-\s+[^-]+$", "", title)  # drop " - Source" at end
                    tags = set(tag_categories(clean, cats)) | {c["id"]}
                    rows.append({"published_tpe": pub_tpe, "collected_tpe": stamp, "mode": mode, "category": c["id"],
                                 "all_tags": "|".join(sorted(tags)), "query": q, "lang": "en" if en else "zh",
                                 "title": clean, "source": src, "score": score_text(clean), "link": link})
            except Exception as e:
                log.append(f"news '{q}': {e}")
            time.sleep(0.5)
    a = upsert_monthly("news", ["published_tpe", "collected_tpe", "mode", "category", "all_tags", "query", "lang",
                                "title", "source", "score", "link"], rows, ["link"], "published_tpe")  # local only (not uploaded)
    upsert_monthly("news_scores", ["published_tpe", "collected_tpe", "mode", "category", "all_tags", "lang", "source", "score", "id"],
                   [dict(r, id=short_id(r["link"])) for r in rows], ["id"], "published_tpe")  # uploaded: scores only, no text
    return f"news +{a} new headlines"


# ---------------------------------------------------------------- PTT Stock board
BULL = ["多", "噴", "漲停", "歐印", "抄底", "買", "看漲", "起飛", "🚀"]
BEAR = ["空", "崩", "跌停", "逃", "賣", "看跌", "套牢", "畢業", "斷頭"]


def collect_ptt(mode, log, pages=3, since=None):
    rows = []
    stamp = now_tpe().strftime("%Y-%m-%d %H:%M")
    if since is None:  # self-heal: if PTT was down / runs were missed, read further back
        posted = [r["posted_tpe"] for r in read_monthly("ptt_scores")]
        if posted:
            last = datetime.strptime(max(posted), "%Y-%m-%d %H:%M").replace(tzinfo=TPE)
            if now_tpe() - last > timedelta(hours=36):
                since, pages = last, 80
    url = "https://www.ptt.cc/bbs/Stock/index.html"
    for _ in range(pages):
        try:
            page = fetch(url)
        except Exception as e:
            log.append(f"ptt: {e}")
            break
        for blk in re.findall(r'<div class="r-ent">(.*?)<div class="mark">', page, re.S):
            m = re.search(r'<a href="(/bbs/Stock/M\.(\d+)\.[^"]+)">(.*?)</a>', blk, re.S)
            if not m:
                continue
            link, epoch, title = "https://www.ptt.cc" + m.group(1), int(m.group(2)), html.unescape(m.group(3)).strip()
            push = re.search(r'<div class="nrec"><span[^>]*>(.*?)</span>', blk)
            p = push.group(1) if push else "0"
            pushes = 100 if p == "爆" else (-int(p[1:]) * 10 if p.startswith("X") and p[1:].isdigit() else (-100 if p == "XX" else (int(p) if p.isdigit() else 0)))
            tag = re.match(r"\s*(?:Re:\s*)?\[(.*?)\]", title)
            max_age = 7 * 86400 if since is None else time.time() - since.timestamp() + 86400
            if (tag and tag.group(1) == "公告") or time.time() - epoch > max_age:
                continue  # skip pinned board rules / old posts
            body = re.sub(r"^\s*(Re:\s*)?\[.*?\]\s*", "", title)
            rows.append({"posted_tpe": datetime.fromtimestamp(epoch, TPE).strftime("%Y-%m-%d %H:%M"), "collected_tpe": stamp,
                         "mode": mode, "tag": tag.group(1) if tag else "", "title": title, "pushes": pushes,
                         "bull_words": sum(body.count(w) for w in BULL), "bear_words": sum(body.count(w) for w in BEAR),
                         "score": score_text(body), "link": link})
        if since is not None:
            eps = [int(e) for e in re.findall(r'href="/bbs/Stock/M\.(\d+)\.', page)]
            if eps and min(eps) < since.timestamp():
                break  # reached posts older than the gap
        prev = re.search(r'href="(/bbs/Stock/index\d+\.html)">&lsaquo;', page)
        if not prev:
            break
        url = "https://www.ptt.cc" + prev.group(1)
        time.sleep(1)
    upsert_monthly("ptt_scores", ["posted_tpe", "collected_tpe", "mode", "tag", "pushes", "bull_words", "bear_words", "score", "id"],
                   [dict(r, id=short_id(r["link"])) for r in rows], ["id"], "posted_tpe")  # uploaded: scores only
    a = upsert_monthly("ptt", ["posted_tpe", "collected_tpe", "mode", "tag", "title", "pushes", "bull_words", "bear_words",
                               "score", "link"], rows, ["link"], "posted_tpe")
    return f"ptt +{a} new posts"



# ---------------------------------------------------------------- catch-up (fill missed days)
CATCHUP_MAX_DAYS = 45


def last_run_date():
    runs = [r["run_time_tpe"][:10] for r in read_csv("run_log.csv") if r.get("result")]
    return datetime.strptime(max(runs), "%Y-%m-%d").date() if runs else None


def missed_days(since=None):
    """Weekdays after the last run (or `since`) up to yesterday."""
    today = now_tpe().date()
    start = since or last_run_date()
    if start is None:
        return []
    start = max(start, today - timedelta(days=CATCHUP_MAX_DAYS))
    d, out = start + timedelta(days=1) if since is None else start, []
    while d < today:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def rebuild_open_snapshots(days, log):
    """Rebuild 'price at 08:35 Taipei' for Asia + watchlist from 5-minute bars (Yahoo keeps ~60 days)."""
    syms = [s for s in all_symbols() if s["group"] in ("asia", "watchlist", "taiwan")]
    want = {d.isoformat() for d in days}
    snaps = []
    for s in syms:
        try:
            d = fetch_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(s['symbol'])}?range=60d&interval=5m")
            res = d["chart"]["result"][0]
            ts, closes = res.get("timestamp") or [], res["indicators"]["quote"][0]["close"]
            by_day = {}
            for t, c in zip(ts, closes):
                if c is None:
                    continue
                dt = datetime.fromtimestamp(t, TPE)
                by_day.setdefault(dt.date().isoformat(), []).append((dt, c))
            days_sorted = sorted(by_day)
            for i, day in enumerate(days_sorted):
                if day not in want or i == 0:
                    continue
                prev_close = by_day[days_sorted[i - 1]][-1][1]
                cutoff = datetime.strptime(day + " 08:35", "%Y-%m-%d %H:%M").replace(tzinfo=TPE)
                upto = [x for x in by_day[day] if x[0] <= cutoff] or by_day[day][:1]
                dt, price = upto[-1]
                snaps.append({"run_time_tpe": f"{day} 08:40", "mode": "asia_open_catchup", "symbol": s["symbol"],
                              "name_zh": s["zh"], "name_en": s["en"], "group": s["group"], "bar_date": day,
                              "market_time_tpe": dt.strftime("%Y-%m-%d %H:%M"), "price": round(price, 4),
                              "prev_close": round(prev_close, 4),
                              "change_pct": round((price / prev_close - 1) * 100, 3) if prev_close else ""})
        except Exception as e:
            log.append(f"intraday {s['symbol']}: {e}")
        time.sleep(0.3)
    return upsert_monthly("snapshots", ["run_time_tpe", "mode", "symbol", "name_zh", "name_en", "group", "bar_date",
                                        "market_time_tpe", "price", "prev_close", "change_pct"], snaps,
                          ["run_time_tpe", "symbol"], "run_time_tpe")


def catch_up(log, since=None):
    days = missed_days(since)
    if not days:
        return "catch-up: nothing missed"
    out = [f"catch-up {days[0]}..{days[-1]} ({len(days)} weekdays)"]
    out.append(collect_twse(log, days=days))
    out.append(f"open-snapshots +{rebuild_open_snapshots(days, log)}")
    for d in days + [d + timedelta(days=1) for d in days if d.weekday() == 4]:  # weekends' news too
        out.append(f"{d}: " + collect_news("catchup", log, day=d))
        if d.weekday() == 4:
            out.append(f"{d + timedelta(days=2)}: " + collect_news("catchup", log, day=d + timedelta(days=2)))
    out.append(collect_ptt("catchup", log, pages=80, since=datetime.combine(days[0], datetime.min.time(), TPE)))
    return " ; ".join(out)


# ---------------------------------------------------------------- daily summary (for backtesting)
def build_daily_signals():
    cats = {c["id"]: c for c in load_cfg("news.json")["categories"]}
    agg = {}
    for r in read_monthly("news_scores"):
        day = (r["published_tpe"] or r["collected_tpe"])[:10]
        for tag in (r["all_tags"] or r["category"]).split("|"):
            if tag not in cats:
                continue
            a = agg.setdefault((day, tag), {"n": 0, "sum": 0.0, "pos": 0, "neg": 0})
            s = float(r["score"] or 0)
            a["n"] += 1
            a["sum"] += s
            a["pos"] += s > 0
            a["neg"] += s < 0
    rows = []
    for (day, tag), a in agg.items():
        avg = a["sum"] / a["n"]
        rows.append({"date": day, "source": "news", "category": tag, "name_zh": cats[tag]["zh"], "name_en": cats[tag]["en"],
                     "weight": cats[tag]["weight"], "count": a["n"], "good": a["pos"], "bad": a["neg"],
                     "avg_score": round(avg, 3), "weighted_score": round(avg * cats[tag]["weight"], 3)})
    pt = {}
    for r in read_monthly("ptt_scores"):
        a = pt.setdefault(r["posted_tpe"][:10], {"n": 0, "sum": 0.0, "bull": 0, "bear": 0})
        a["n"] += 1
        a["sum"] += float(r["score"] or 0)
        a["bull"] += int(r["bull_words"] or 0)
        a["bear"] += int(r["bear_words"] or 0)
    for day, a in pt.items():
        rows.append({"date": day, "source": "ptt", "category": "ptt_mood", "name_zh": "PTT股票版情緒", "name_en": "PTT Stock board mood",
                     "weight": 1, "count": a["n"], "good": a["bull"], "bad": a["bear"],
                     "avg_score": round(a["sum"] / a["n"], 3), "weighted_score": round(a["sum"] / a["n"], 3)})
    fields = ["date", "source", "category", "name_zh", "name_en", "weight", "count", "good", "bad", "avg_score", "weighted_score"]
    path = os.path.join(DATA, "daily_signals.csv")
    if os.path.exists(path):
        os.remove(path)  # always rebuilt from news/ + ptt/
    upsert_csv("daily_signals.csv", fields, rows, ["date", "source", "category"])
    return f"daily signals {len(rows)} rows"


# ---------------------------------------------------------------- main
PLAN = {
    # 08:40 Taipei - Japan & Korea just opened, before Taiwan opens
    "asia_open": ["prices", "news", "ptt", "forecast"],
    # 16:40 Taipei - Taiwan closed, money-flow + revenue published
    "tw_close": ["prices", "twse", "revenue", "news", "ptt", "market", "revenue_all", "analyze"],
    # 22:50 Taipei - US just opened; TWSE margin data is out by now
    "us_open": ["prices", "twse", "news", "ptt"],
    # 05:40 Taipei - US closed: final prices + why it moved
    "us_close": ["prices", "news", "ptt"],
    "all": ["prices", "twse", "revenue", "news", "ptt", "market", "revenue_all", "analyze"],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="all", choices=list(PLAN))
    ap.add_argument("--backfill-days", type=int, default=0, help="also fetch this many past weekdays of TWSE data")
    ap.add_argument("--catchup-from", default=None, help="YYYY-MM-DD: force catch-up from this date (normally automatic)")
    a = ap.parse_args()
    os.makedirs(DATA, exist_ok=True)
    log, done = [], []
    start = time.time()
    try:  # automatic: if earlier runs were missed (e.g. GitHub blocked), fill the gap first
        since = datetime.strptime(a.catchup_from, "%Y-%m-%d").date() if a.catchup_from else None
        done.append(catch_up(log, since))
    except Exception as e:
        log.append(f"catch-up crashed: {e} {traceback.format_exc(limit=1)}")
    for step in PLAN[a.mode]:
        try:
            if step == "prices":
                done.append(collect_prices(a.mode, log))
            elif step == "twse":
                done.append(collect_twse(log, a.backfill_days))
            elif step == "revenue":
                done.append(collect_revenue(log))
            elif step == "news":
                done.append(collect_news(a.mode, log))
            elif step == "ptt":
                done.append(collect_ptt(a.mode, log))
            elif step == "market":
                done.append(collect_market_prices(log))
            elif step == "revenue_all":
                done.append(collect_revenue_all(log))
            elif step == "analyze":
                done.append(run_analysis(log))
            elif step == "forecast":
                done.append(run_script("forecast.py", log))
        except Exception as e:
            log.append(f"{step} crashed: {e} {traceback.format_exc(limit=1)}")
    done.append(build_daily_signals())
    upsert_csv("run_log.csv", ["run_time_tpe", "mode", "seconds", "result", "errors"],
               [{"run_time_tpe": now_tpe().strftime("%Y-%m-%d %H:%M:%S"), "mode": a.mode, "seconds": round(time.time() - start),
                 "result": " ; ".join(done), "errors": " | ".join(log)[:2000]}], ["run_time_tpe", "mode"])
    print("\n".join(done))
    if log:
        print("WARNINGS:\n  " + "\n  ".join(log))


if __name__ == "__main__":
    main()
