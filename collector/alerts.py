#!/usr/bin/env python3
"""
Alert engine / 警示系統

  python3 collector/alerts.py premarket   08:40  watch list: stocks sitting right at an alert point (no Taiwan prices yet)
  python3 collector/alerts.py live <tag>  09:20 / 10:00 / 12:00 / 13:15  scan all stocks with live prices
  python3 collector/alerts.py followup    after close: fill +1/+5/+10 day results, rebuild hit-rate stats

Alert types (Taiwan-style, daily bars; today's live price used as "today"):
  break_high20  breaks above 20-day high (突破壓力)        break_low20   falls below 20-day low (跌破支撐)
  new_high52    new 52-week high (創新高)
  ma_golden     20-day avg crosses above 60-day (均線黃金交叉)  ma_death  crosses below (死亡交叉)
  kd_golden     KD crosses up from below 20 (KD低檔黃金交叉)   kd_death  KD crosses down from above 80 (高檔死叉)
  vol_spike     volume 3x normal with price up 2%+ (爆量上漲)
  rsi_oversold  RSI falls below 30 (超賣)                    rsi_overbought  RSI rises above 70 (超買)
Every alert is saved (scrambled) with the news about that stock and its sector at that moment,
so later we can see whether the alert or the news explained the move.
"""
import csv, io, json, os, re, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vault  # noqa: E402

ROOT = os.path.dirname(HERE)
DATA, CFG = os.path.join(ROOT, "data"), os.path.join(ROOT, "config")
TPE = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
MIN_VALUE, MIN_PRICE = 3e7, 10

TYPES = {
    "break_high20": ("bull", "Breaks 20-day high", "突破20日高點（壓力）"),
    "break_low20": ("bear", "Falls below 20-day low", "跌破20日低點（支撐）"),
    "new_high52": ("bull", "New 52-week high", "創52週新高"),
    "ma_golden": ("bull", "20-day avg crosses above 60-day", "均線黃金交叉（20/60）"),
    "ma_death": ("bear", "20-day avg crosses below 60-day", "均線死亡交叉（20/60）"),
    "kd_golden": ("bull", "KD crosses up from below 20", "KD低檔黃金交叉"),
    "kd_death": ("bear", "KD crosses down from above 80", "KD高檔死亡交叉"),
    "vol_spike": ("bull", "Volume 3x normal, price up 2%+", "爆量上漲（3倍量）"),
    "rsi_oversold": ("bull", "RSI below 30 (oversold)", "RSI 低於30（超賣）"),
    "rsi_overbought": ("bear", "RSI above 70 (overbought)", "RSI 高於70（超買）"),
}
LOG_FIELDS = ["date", "check", "time_tpe", "code", "name", "group", "ai", "type", "direction", "price", "chg_pct",
              "vol_ratio", "level", "stock_news_n", "stock_news_score", "sector_news_n", "sector_news_score",
              "headlines", "hist_hit", "hist_ret10", "rank", "ret_1d", "ret_5d", "ret_10d", "max_up_10", "max_down_10", "done"]


# ---------------------------------------------------------------- data
def load_cfg(n):
    with open(os.path.join(CFG, n), encoding="utf-8") as f:
        return json.load(f)


def universe():
    u = pd.read_csv(os.path.join(DATA, "market", "universe.csv"), dtype={"code": str}, encoding="utf-8-sig")
    return u.set_index("code")


def daily_prices():
    import glob
    df = pd.read_csv(os.path.join(DATA, "market", "history.csv.gz"), dtype={"code": str})
    extra = [pd.read_csv(f, dtype={"code": str}, encoding="utf-8-sig") for f in sorted(glob.glob(os.path.join(DATA, "market", "daily", "*.csv")))]
    if extra:
        df = pd.concat([df] + extra)
    df = df.drop_duplicates(["date", "code"], keep="last")
    df["date"] = pd.to_datetime(df["date"])
    return {k: df.pivot(index="date", columns="code", values=k).sort_index() for k in ["open", "high", "low", "close", "volume"]}


def live_quotes(uni):
    """Live price, day high/low, volume for every stock (Yahoo, ~15-20 min delayed). 20 symbols per request."""
    syms = [f"{c}.{m}" for c, m in zip(uni.index, uni["market"])]

    def get(chunk):
        for i in range(3):
            try:
                u = "https://query1.finance.yahoo.com/v7/finance/spark?symbols=" + ",".join(chunk) + "&range=1d&interval=5m"
                return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30).read())["spark"]["result"]
            except Exception:
                time.sleep(2 * (i + 1))
        return []
    out = {}
    with ThreadPoolExecutor(4) as ex:
        for res in ex.map(get, [syms[i:i + 20] for i in range(0, len(syms), 20)]):
            for r in res:
                try:
                    resp = r["response"][0]
                    m = resp["meta"]
                    closes = [c for c in (resp.get("indicators", {}).get("quote", [{}])[0].get("close") or []) if c is not None]
                    t = datetime.fromtimestamp(m["regularMarketTime"], TPE)
                    out[r["symbol"].split(".")[0]] = {"price": m["regularMarketPrice"], "high": m.get("regularMarketDayHigh"),
                                                      "low": m.get("regularMarketDayLow"), "volume": m.get("regularMarketVolume") or 0,
                                                      "open": closes[0] if closes else m["regularMarketPrice"],
                                                      "prev": m.get("previousClose") or m.get("chartPreviousClose"), "time": t}
                except Exception:
                    pass
    return out


# ---------------------------------------------------------------- indicators (wide tables: rows = days, cols = stocks)
def kd(C, H, L, n=9):
    lo, hi = L.rolling(n, min_periods=n).min(), H.rolling(n, min_periods=n).max()
    rsv = ((C - lo) / (hi - lo).replace(0, np.nan) * 100).fillna(50)
    K = np.full(C.shape, 50.0)
    D = np.full(C.shape, 50.0)
    r = rsv.values
    for i in range(1, len(C)):
        K[i] = K[i - 1] * 2 / 3 + r[i] / 3
        D[i] = D[i - 1] * 2 / 3 + K[i] / 3
    return pd.DataFrame(K, index=C.index, columns=C.columns), pd.DataFrame(D, index=C.index, columns=C.columns)


def rsi(C, n=14):
    d = C.diff()
    up, dn = d.clip(lower=0).rolling(n).mean(), (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def signals(W, vol_ratio=None):
    """Boolean table per alert type for every day/stock. vol_ratio overrides today's volume ratio (live projection)."""
    C, H, L, V = W["close"], W["high"], W["low"], W["volume"]
    hi20, lo20 = H.shift(1).rolling(20, min_periods=15).max(), L.shift(1).rolling(20, min_periods=15).min()
    hi250 = H.shift(1).rolling(250, min_periods=120).max()
    ma20, ma60 = C.rolling(20, min_periods=15).mean(), C.rolling(60, min_periods=40).mean()
    K, D = kd(C, H, L)
    R = rsi(C)
    vr = V / V.shift(1).rolling(20, min_periods=10).mean()
    if vol_ratio is not None:
        vr.iloc[-1] = vol_ratio.reindex(vr.columns)
    ret1 = C / C.shift(1) - 1
    S = {
        "break_high20": (C > hi20) & (C.shift(1) <= hi20.shift(1)),
        "break_low20": (C < lo20) & (C.shift(1) >= lo20.shift(1)),
        "new_high52": (C > hi250) & (C.shift(1) <= hi250.shift(1)),
        "ma_golden": (ma20 > ma60) & (ma20.shift(1) <= ma60.shift(1)),
        "ma_death": (ma20 < ma60) & (ma20.shift(1) >= ma60.shift(1)),
        "kd_golden": (K > D) & (K.shift(1) <= D.shift(1)) & (K.shift(1) < 20),
        "kd_death": (K < D) & (K.shift(1) >= D.shift(1)) & (K.shift(1) > 80),
        "vol_spike": (vr >= 3) & (ret1 >= 0.02),
        "rsi_oversold": (R < 30) & (R.shift(1) >= 30),
        "rsi_overbought": (R > 70) & (R.shift(1) <= 70),
    }
    levels = {"break_high20": hi20, "break_low20": lo20, "new_high52": hi250, "ma_golden": ma60, "ma_death": ma60}
    extra = {"K": K, "D": D, "R": R, "ma20": ma20, "ma60": ma60, "hi20": hi20, "hi250": hi250, "vr": vr, "ret1": ret1}
    return S, levels, extra


def liquid(W):
    return ((W["close"] * W["volume"]).rolling(20, min_periods=10).mean() >= MIN_VALUE) & (W["close"] >= MIN_PRICE)


# ---------------------------------------------------------------- news at the moment of the alert
def news_rows(hours=24):
    import glob
    rows = []
    for f in sorted(glob.glob(os.path.join(DATA, "news", "*.csv")))[-2:]:
        with open(f, encoding="utf-8-sig") as fh:
            rows += list(csv.DictReader(fh))
    cut = (datetime.now(TPE) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    return [r for r in rows if (r.get("published_tpe") or "") >= cut]


def stock_news(name, code):
    """Search Google News for this stock in the last day."""
    sys.path.insert(0, HERE)
    from collect import fetch, score_text
    import html as H
    import xml.etree.ElementTree as ET
    q = f"{name} {code}"
    try:
        root = ET.fromstring(fetch(f"https://news.google.com/rss/search?q={urllib.parse.quote(q + ' when:1d')}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant", tries=2))
        titles = [re.sub(r"\s+-\s+[^-]+$", "", H.unescape(it.findtext("title") or "")) for it in list(root.iter("item"))[:10]]
        titles = [t for t in titles if name in t or code in t]
        return titles, [score_text(t) for t in titles]
    except Exception:
        return [], []


def group_info(uni):
    themes = load_cfg("themes.json")["themes"]
    focus = set(load_cfg("focus.json")["focus"][0]["themes"])
    code_theme = {}
    for t in themes:
        for c in t["codes"]:
            code_theme.setdefault(c, t)
    ai = {c for t in themes if t["id"] in focus for c in t["codes"]}
    return code_theme, ai


# ---------------------------------------------------------------- history stats (how well each alert type worked)
def hit_stats(W, uni):
    S, _, _ = signals(W)
    C, H, L = W["close"], W["high"], W["low"]
    liq = liquid(W)
    fwd = {f"r{n}": C.shift(-n) / C - 1 for n in (1, 5, 10)}
    up10 = H.shift(-1).iloc[::-1].rolling(10, min_periods=10).max().iloc[::-1] / C - 1
    dn10 = L.shift(-1).iloc[::-1].rolling(10, min_periods=10).min().iloc[::-1] / C - 1
    valid = liq & fwd["r10"].notna()
    _, ai = group_info(uni)
    ai_cols = [c for c in C.columns if c in ai]

    def summarize(mask):
        m = mask & valid
        n = int(m.values.sum())
        if n == 0:
            return None
        g = lambda X: float(np.nanmean(X.values[m.values]))  # noqa: E731
        return {"n": n, "r1": g(fwd["r1"]), "r5": g(fwd["r5"]), "r10": g(fwd["r10"]),
                "up5": float(np.nanmean((up10.values[m.values] >= 0.05))), "down5": float(np.nanmean((dn10.values[m.values] <= -0.05)))}
    out = {"base": summarize(valid), "base_ai": summarize(valid[ai_cols].reindex(columns=C.columns, fill_value=False)), "types": {}}
    for k, sig in S.items():
        sig = sig.fillna(False)
        out["types"][k] = {"all": summarize(sig), "ai": summarize(sig[ai_cols].reindex(columns=C.columns, fill_value=False))}
    out["from"], out["to"] = str(C.index[0].date()), str(C.index[-1].date())
    return out


def edge_of(stats, typ, is_ai):
    """Number that says how good this alert type was historically (for ranking)."""
    t = stats["types"].get(typ, {})
    s = (t.get("ai") if is_ai else None) or t.get("all")
    if not s:
        return 0.0, 0.0
    b = stats["base"]
    hit = s["up5"] if TYPES[typ][0] == "bull" else s["down5"]
    base_hit = b["up5"] if TYPES[typ][0] == "bull" else b["down5"]
    return hit, s["r10"]


# ---------------------------------------------------------------- log helpers
def read_log(month):
    txt = vault.read(os.path.join(DATA, "alerts", f"{month}.csv"))
    return list(csv.DictReader(io.StringIO(txt))) if txt else []


def write_log(month, rows):
    os.makedirs(os.path.join(DATA, "alerts"), exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=LOG_FIELDS)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in LOG_FIELDS})
    vault.write(os.path.join(DATA, "alerts", f"{month}.csv"), buf.getvalue())


def load_stats():
    t = vault.read(os.path.join(DATA, "report", "alert_stats.json"))
    return json.loads(t) if t else None


# ---------------------------------------------------------------- live check
def run_live(tag):
    now = datetime.now(TPE)
    today = now.date()
    uni = universe()
    W = daily_prices()
    q = live_quotes(uni)
    if not q:
        print("no live prices")
        return
    qt = max(v["time"] for v in q.values())
    if qt.date() != today:
        print("market closed today (latest price from", qt.date(), ")")
        return
    # append today's live bar
    day = pd.Timestamp(today)
    for k in W:
        if day not in W[k].index:
            W[k].loc[day] = np.nan
    for c, v in q.items():
        if c in W["close"].columns and v["price"]:
            W["close"].at[day, c], W["open"].at[day, c] = v["price"], v["open"]
            W["high"].at[day, c], W["low"].at[day, c] = v["high"] or v["price"], v["low"] or v["price"]
            W["volume"].at[day, c] = v["volume"]
    for k in W:
        W[k] = W[k].sort_index()
    # volume so far -> projected for the whole day (09:00-13:30)
    mins = (qt.hour * 60 + qt.minute) - 9 * 60
    frac = min(1.0, max(0.08, mins / 270))
    avg20 = W["volume"].iloc[:-1].tail(20).mean()
    vol_now = W["volume"].iloc[-1]
    projected = (vol_now / frac) / avg20
    projected = projected.where(vol_now >= avg20 * 0.6)  # need a real chunk of volume already
    S, levels, X = signals(W, vol_ratio=projected)
    liq = liquid(W).iloc[-2]  # liquidity judged on finished days
    code_theme, ai = group_info(uni)
    stats = load_stats()
    news = news_rows()
    rows = []
    for typ, sig in S.items():
        hits = sig.iloc[-1].fillna(False)
        for c in hits.index[hits.values]:
            if not liq.get(c, False):
                continue
            name = str(uni["name"].get(c, c))
            th = code_theme.get(c)
            grp = th["zh"] if th else str(uni["industry"].get(c, ""))
            kws = (th["keywords"] if th else [grp.replace("業", "")])
            sec = [r for r in news if any(k and k.lower() in r["title"].lower() for k in kws)]
            hit, r10 = edge_of(stats, typ, c in ai) if stats else (0.0, 0.0)
            lvl = levels.get(typ)
            rows.append({"date": str(today), "check": tag, "time_tpe": qt.strftime("%H:%M"), "code": c, "name": name, "group": grp,
                         "ai": int(c in ai), "type": typ, "direction": TYPES[typ][0], "price": q[c]["price"],
                         "chg_pct": round((q[c]["price"] / q[c]["prev"] - 1) * 100, 2) if q[c]["prev"] else "",
                         "vol_ratio": round(float(projected.get(c)), 2) if pd.notna(projected.get(c)) else "",
                         "level": round(float(lvl.iloc[-1][c]), 2) if lvl is not None and pd.notna(lvl.iloc[-1][c]) else "",
                         "sector_news_n": len(sec), "sector_news_score": round(float(np.mean([float(r["score"] or 0) for r in sec])), 3) if sec else "",
                         "hist_hit": round(hit, 3), "hist_ret10": round(r10, 4)})
    # rank: historical edge + AI + liquidity of the move
    for r in rows:
        r["rank"] = round(r["hist_hit"] * 100 + (8 if r["ai"] else 0) + min(10, float(r["vol_ratio"] or 0)) +
                          (5 if r["type"] in ("new_high52", "vol_spike") else 0), 2)
    rows.sort(key=lambda r: r["rank"], reverse=True)
    # stock-specific news for the strongest alerts (one search per stock)
    seen = {}
    for r in rows[:25]:
        if r["code"] not in seen:
            seen[r["code"]] = stock_news(r["name"], r["code"])
            time.sleep(0.4)
        titles, scores = seen[r["code"]]
        r.update({"stock_news_n": len(titles), "stock_news_score": round(float(np.mean(scores)), 3) if scores else "",
                  "headlines": " | ".join(titles[:2])})
    month = today.strftime("%Y-%m")
    log = read_log(month)
    have = {(r["date"], r["code"], r["type"]) for r in log}
    new = [r for r in rows if (r["date"], r["code"], r["type"]) not in have]  # each stock+type alerted once per day
    write_log(month, log + new)
    top = new[:10]
    out = {"date": str(today), "check": tag, "time": qt.strftime("%H:%M"), "made": now.strftime("%H:%M"),
           "new_count": len(new), "total_today": len([r for r in log + new if r["date"] == str(today)]),
           "by_type": {k: sum(1 for r in new if r["type"] == k) for k in TYPES}, "top": top,
           "ai": [r for r in new if r["ai"]][:15]}
    prev = vault.read(os.path.join(DATA, "report", "alerts_latest.json"))
    prev = json.loads(prev) if prev else {}
    out["watch"] = prev.get("watch", []) if prev.get("date") == str(today) else []
    out["checks"] = (prev.get("checks", []) if prev.get("date") == str(today) else []) + [{"check": tag, "time": out["time"], "new": len(new)}]
    vault.write(os.path.join(DATA, "report", "alerts_latest.json"), json.dumps(out, ensure_ascii=False, default=str))
    print(f"{tag}: {len(rows)} alerts now, {len(new)} new; top:", [(r["code"], r["name"], r["type"]) for r in top[:5]])


# ---------------------------------------------------------------- premarket watch list
def run_premarket():
    now = datetime.now(TPE)
    uni = universe()
    W = daily_prices()
    S, levels, X = signals(W)
    liq = liquid(W).iloc[-1]
    code_theme, ai = group_info(uni)
    C = W["close"].iloc[-1]
    watch = []
    near = {
        "break_high20": (X["hi20"].iloc[-1] * 0 + W["high"].tail(20).max()) / C - 1,  # % below 20-day high
        "new_high52": W["high"].tail(250).max() / C - 1,
    }
    for c in C.index:
        if not liq.get(c, False) or pd.isna(C[c]):
            continue
        why_en, why_zh = [], []
        g20 = near["break_high20"].get(c)
        if pd.notna(g20) and 0 < g20 <= 0.02:
            why_en.append(f"{g20*100:.1f}% under its 20-day high")
            why_zh.append(f"距20日高點 {g20*100:.1f}%")
        g52 = near["new_high52"].get(c)
        if pd.notna(g52) and 0 < g52 <= 0.02:
            why_en.append(f"{g52*100:.1f}% under its 52-week high")
            why_zh.append(f"距52週新高 {g52*100:.1f}%")
        k, d = X["K"].iloc[-1][c], X["D"].iloc[-1][c]
        if k < 25 and -3 < k - d < 0 and X["K"].iloc[-2][c] < k:
            why_en.append(f"KD about to cross up (K {k:.0f})")
            why_zh.append(f"KD即將低檔交叉（K {k:.0f}）")
        m20, m60 = X["ma20"].iloc[-1][c], X["ma60"].iloc[-1][c]
        if pd.notna(m20) and pd.notna(m60) and -0.005 < m20 / m60 - 1 < 0 and m20 > X["ma20"].iloc[-2][c]:
            why_en.append("20-day avg about to cross above 60-day")
            why_zh.append("20日均線即將上穿60日")
        if S["vol_spike"].iloc[-1].get(c, False):
            why_en.append("Big volume spike yesterday")
            why_zh.append("昨日爆量上漲")
        if why_en:
            th = code_theme.get(c)
            watch.append({"code": c, "name": str(uni["name"].get(c, c)), "group": th["zh"] if th else str(uni["industry"].get(c, "")),
                          "group_en": th["en"] if th else "", "ai": int(c in ai), "price": round(float(C[c]), 2),
                          "why_en": why_en, "why_zh": why_zh, "n": len(why_en)})
    watch.sort(key=lambda w: (w["n"], w["ai"]), reverse=True)
    out = {"date": str(now.date()), "check": "premarket", "time": now.strftime("%H:%M"), "made": now.strftime("%H:%M"),
           "watch": watch[:40], "watch_total": len(watch), "top": [], "ai": [], "new_count": 0, "total_today": 0, "by_type": {},
           "checks": [{"check": "premarket", "time": now.strftime("%H:%M"), "new": 0}]}
    vault.write(os.path.join(DATA, "report", "alerts_latest.json"), json.dumps(out, ensure_ascii=False, default=str))
    print("premarket watch list:", len(watch), "stocks; top:", [(w["code"], w["name"]) for w in watch[:5]])


# ---------------------------------------------------------------- follow-up + stats
def run_followup():
    import glob
    uni = universe()
    W = daily_prices()
    C, H, L = W["close"], W["high"], W["low"]
    dates = C.index
    for f in sorted(glob.glob(os.path.join(DATA, "alerts", "*.csv.enc"))):
        month = os.path.basename(f)[:7]
        rows = read_log(month)
        for r in rows:
            if r.get("done") == "1" or r["code"] not in C.columns:
                continue
            d = pd.Timestamp(r["date"])
            after = dates[dates > d]
            p = float(r["price"])
            for n in (1, 5, 10):
                if len(after) >= n and pd.notna(C.at[after[n - 1], r["code"]]):
                    r[f"ret_{n}d"] = round(C.at[after[n - 1], r["code"]] / p - 1, 4)
            if len(after) >= 1:
                w = after[:10]
                r["max_up_10"] = round(float(H.loc[w, r["code"]].max()) / p - 1, 4)
                r["max_down_10"] = round(float(L.loc[w, r["code"]].min()) / p - 1, 4)
            if len(after) >= 10:
                r["done"] = "1"
        write_log(month, rows)
    stats = hit_stats(W, uni)
    # live results so far: alert alone vs alert + news (does the news explain the move?)
    live = {}
    allrows = []
    for f in sorted(glob.glob(os.path.join(DATA, "alerts", "*.csv.enc"))):
        allrows += read_log(os.path.basename(f)[:7])
    for typ in TYPES:
        rs = [r for r in allrows if r["type"] == typ and r.get("ret_5d") not in ("", None)]
        if not rs:
            continue
        def summ(xs):
            if not xs:
                return None
            r5 = [float(x["ret_5d"]) for x in xs]
            return {"n": len(xs), "avg_r5": float(np.mean(r5)), "up_share": float(np.mean([v > 0 for v in r5]))}
        live[typ] = {"all": summ(rs), "with_news": summ([r for r in rs if int(float(r.get("stock_news_n") or 0)) > 0]),
                     "no_news": summ([r for r in rs if int(float(r.get("stock_news_n") or 0)) == 0])}
    stats["live"] = live
    stats["labels"] = {k: {"dir": v[0], "en": v[1], "zh": v[2]} for k, v in TYPES.items()}
    vault.write(os.path.join(DATA, "report", "alert_stats.json"), json.dumps(stats, ensure_ascii=False, default=str))
    print("follow-up done; history", stats["from"], "->", stats["to"])
    for k, v in stats["types"].items():
        a = v["all"]
        if a:
            print(f"  {k:15} n={a['n']:6}  +1d {a['r1']*100:+.2f}%  +10d {a['r10']*100:+.2f}%  up5% {a['up5']*100:.0f}%  down5% {a['down5']*100:.0f}%")
    b = stats["base"]
    print(f"  {'(any stock)':15} n={b['n']:6}  +1d {b['r1']*100:+.2f}%  +10d {b['r10']*100:+.2f}%  up5% {b['up5']*100:.0f}%  down5% {b['down5']*100:.0f}%")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "live"
    if cmd == "premarket":
        run_premarket()
    elif cmd == "followup":
        run_followup()
    else:
        run_live(sys.argv[2] if len(sys.argv) > 2 else datetime.now(TPE).strftime("%H%M"))
