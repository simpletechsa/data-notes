#!/usr/bin/env python3
"""
TW Stock Lab - analyzer / 台股研究室 分析程式

1. Sector heat   : rank official industries + hot themes (AI server, memory...) -> hot / warm / cold
2. Picks         : only stocks in the hottest groups; model gives % chance the trade plan works
                   Trade plan: buy next day's open, target +8%, stop -5%, sell after 20 trading days at most
3. Radar         : unusual signals anywhere (cold sectors too): volume spike, 52-week high, revenue jump,
                   institutional buying streak, news burst
4. Paper trading : the app "buys" its own picks with fake money and tracks real results
5. Report        : data/report/latest.html  (English first, then Traditional Chinese)

For analysis only - not investment advice.
"""
import csv, glob, gzip, html, io, json, os, re, sys, warnings
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vault
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA, CFG = os.path.join(ROOT, "data"), os.path.join(ROOT, "config")
MK = os.path.join(DATA, "market")
REP = os.path.join(DATA, "report")
TPE = timezone(timedelta(hours=8))

TARGET, STOP, MAX_DAYS = 0.08, 0.05, 20
COST = 0.00585          # fees + tax for one round trip (買賣手續費＋證交稅)
MIN_VALUE = 3e7         # skip stocks trading under NT$30M/day (太冷門的不看)
MIN_PRICE = 10
N_PICKS, N_RADAR, N_HOT = 5, 5, 3

IND_EN = {"半導體業": "Semiconductors", "電子零組件業": "Electronic components", "電腦及週邊設備業": "Computers & peripherals",
          "光電業": "Optoelectronics", "通信網路業": "Communications & networking", "其他電子業": "Other electronics",
          "電子通路業": "Electronics distribution", "資訊服務業": "IT services", "數位雲端": "Digital cloud",
          "生技醫療業": "Biotech & medical", "電機機械": "Electrical machinery", "建材營造": "Construction",
          "紡織纖維": "Textiles", "觀光餐旅": "Tourism & hospitality", "鋼鐵工業": "Steel", "綠能環保": "Green energy",
          "汽車工業": "Automotive", "化學工業": "Chemicals", "居家生活": "Home & living", "食品工業": "Food",
          "航運業": "Shipping", "金融保險業": "Finance & insurance", "金融業": "Finance", "運動休閒": "Sports & leisure",
          "文化創意業": "Culture & creative", "塑膠工業": "Plastics", "貿易百貨": "Trading & retail",
          "電器電纜": "Electric cables", "油電燃氣業": "Oil, power & gas", "橡膠工業": "Rubber", "水泥工業": "Cement",
          "造紙工業": "Paper", "玻璃陶瓷": "Glass & ceramics", "農業科技": "Agri-tech", "其他": "Other"}


# ---------------------------------------------------------------- loading
def load_prices():
    df = pd.read_csv(os.path.join(MK, "history.csv.gz"), dtype={"code": str})
    extra = [pd.read_csv(f, dtype={"code": str}, encoding="utf-8-sig") for f in sorted(glob.glob(os.path.join(MK, "daily", "*.csv")))]
    if extra:
        df = pd.concat([df] + extra)
    df = df.drop_duplicates(["date", "code"], keep="last")
    df["date"] = pd.to_datetime(df["date"])
    return df


def load_universe():
    u = pd.read_csv(os.path.join(MK, "universe.csv"), dtype={"code": str}, encoding="utf-8-sig")
    return u.set_index("code")


def load_cfg(name):
    with open(os.path.join(CFG, name), encoding="utf-8") as f:
        return json.load(f)


def read_monthly(folder):
    fs = sorted(glob.glob(os.path.join(DATA, folder, "*.csv")))
    return pd.concat([pd.read_csv(f, dtype={"code": str}, encoding="utf-8-sig") for f in fs]) if fs else pd.DataFrame()


# ---------------------------------------------------------------- features (wide tables: rows = dates, cols = stocks)
def build(df, uni):
    W = {k: df.pivot(index="date", columns="code", values=k).sort_index() for k in ["open", "high", "low", "close", "volume"]}
    C, H, L, O, V = W["close"], W["high"], W["low"], W["open"], W["volume"]
    C = C.ffill(limit=3)
    ma20, ma60 = C.rolling(20, min_periods=15).mean(), C.rolling(60, min_periods=40).mean()
    v60 = V.rolling(60, min_periods=30).mean()
    d = C.diff()
    up, dn = d.clip(lower=0).rolling(14).mean(), (-d.clip(upper=0)).rolling(14).mean()
    F = {
        "r1": C.pct_change(1), "r5": C / C.shift(5) - 1, "r20": C / C.shift(20) - 1, "r60": C / C.shift(60) - 1,
        "g20": C / ma20 - 1, "g60": C / ma60 - 1,
        "ghi": C / H.rolling(250, min_periods=120).max() - 1,
        "volr": V.rolling(5).mean() / v60, "vol1": V / v60,
        "rsi": 100 - 100 / (1 + up / dn.replace(0, np.nan)),
        "vola": C.pct_change().rolling(20).std(),
        "val5": (C * V).rolling(5, min_periods=3).sum(),            # money traded, last 5 days (成交值)
        "val60": (C * V).rolling(60, min_periods=30).mean() * 5,    # normal 5-day money (60-day average)
    }
    value20 = (C * V).rolling(20, min_periods=10).mean()
    ind = uni["industry"].reindex(C.columns).fillna("其他")
    above = (C > ma20).where(ma20.notna())
    # industry-level context (is the whole sector moving?)
    F["ind_r20"] = F["r20"].T.groupby(ind).transform("median").T
    F["ind_breadth"] = above.T.groupby(ind).transform("mean").T
    F["mkt_r20"] = pd.DataFrame(np.repeat(F["r20"].median(axis=1).values[:, None], C.shape[1], 1), index=C.index, columns=C.columns)
    # label: does the trade plan hit +8% before -5% within 20 days? (entry = next day's open)
    entry = O.shift(-1)
    first_t = pd.DataFrame(np.inf, index=C.index, columns=C.columns)
    first_s = first_t.copy()
    for k in range(1, MAX_DAYS + 1):
        ht, ls = H.shift(-k) >= entry * (1 + TARGET), L.shift(-k) <= entry * (1 - STOP)
        first_t = first_t.where(~(ht & np.isinf(first_t)), k)
        first_s = first_s.where(~(ls & np.isinf(first_s)), k)
    end = C.shift(-MAX_DAYS) / entry - 1
    win = (first_t < first_s).astype(float)
    ret = np.where(first_t < first_s, TARGET, np.where(np.isfinite(first_s), -STOP, end)) - COST
    labeled = C.shift(-(MAX_DAYS + 1)).notna() & entry.notna()
    ret = pd.DataFrame(ret, index=C.index, columns=C.columns).where(labeled)
    win = win.where(labeled)
    return W, C, F, value20, ind, win, ret


FEATS = ["r1", "r5", "r20", "r60", "g20", "g60", "ghi", "volr", "vol1", "rsi", "vola", "ind_r20", "ind_breadth", "mkt_r20"]


def to_long(F, value20, C, win, ret):
    parts = {k: F[k].stack(future_stack=True) for k in FEATS}
    X = pd.DataFrame(parts)
    X["value20"], X["close"] = value20.stack(future_stack=True), C.stack(future_stack=True)
    X["win"], X["ret"] = win.stack(future_stack=True), ret.stack(future_stack=True)
    X = X[(X["value20"] >= MIN_VALUE) & (X["close"] >= MIN_PRICE)].dropna(subset=FEATS)
    return X


# ---------------------------------------------------------------- groups & heat
def groups(uni, C):
    out = {}
    for name, codes in uni.groupby("industry").groups.items():
        if name in ("其他", "存託憑證"):
            continue
        short = name.replace("工業", "").replace("業", "")
        out[f"ind:{name}"] = {"zh": name, "en": IND_EN.get(name, name), "codes": [c for c in codes if c in C.columns],
                              "kw": [k for k in {short, short[:2]} if len(k) >= 2]}
    for t in load_cfg("themes.json")["themes"]:
        out[f"theme:{t['id']}"] = {"zh": t["zh"], "en": t["en"], "codes": [c for c in t["codes"] if c in C.columns], "kw": t["keywords"]}
    return out


def heat_on(day, G, F, value20, extra=None):
    """
    HOT (熱度) = where the money and attention are: share of all money traded (成交值占比),
                 money vs its normal level (量能放大), and news count (when available). Price is NOT part of it.
    TREND (趨勢) = price direction only: up / down / flat.
    """
    rows = []
    liq_all = [c for c in value20.columns if value20.at[day, c] >= MIN_VALUE] if day in value20.index else []
    total5 = float(F["val5"].loc[day, liq_all].sum()) or 1.0
    for gid, g in G.items():
        cs = [c for c in g["codes"] if value20.at[day, c] >= MIN_VALUE] if day in value20.index else []
        if len(cs) < 3:
            continue
        v5, v60 = float(F["val5"].loc[day, cs].sum()), float(F["val60"].loc[day, cs].sum())
        r = {"gid": gid, "n": len(cs),
             "r5": F["r5"].loc[day, cs].median(), "r20": F["r20"].loc[day, cs].median(),
             "breadth": (F["g20"].loc[day, cs] > 0).mean(), "volr": F["volr"].loc[day, cs].median(),
             "share": v5 / total5, "surge": v5 / v60 if v60 else 1.0}
        if extra:
            r.update(extra(gid, g, cs))
        rows.append(r)
    h = pd.DataFrame(rows).set_index("gid")
    parts = {"share": 0.4, "surge": 0.3}
    if "buzz" in h.columns and h["buzz"].sum() > 0:
        parts["buzz"] = 0.3
    else:  # no news data (history): money only
        parts = {"share": 0.55, "surge": 0.45}
    h["heat"] = sum(h[k].rank(pct=True) * w for k, w in parts.items()) * 100
    h = h.sort_values("heat", ascending=False)
    h["level"] = np.where(h["heat"].rank(ascending=False) <= N_HOT, "hot",
                          np.where(h["heat"] >= h["heat"].quantile(0.5), "warm", "cold"))
    up = (h["r20"] > 0.03) & (h["r5"] > -0.02) & (h["breadth"] >= 0.5)
    down = (h["r20"] < -0.03) & (h["r5"] < 0.02) & (h["breadth"] < 0.5)
    h["trend"] = np.where(up, "up", np.where(down, "down", "flat"))
    return h


# ---------------------------------------------------------------- model + honest backtest
def focus_codes(G):
    f = load_cfg("focus.json")["focus"][0]
    themes = [f"theme:{t}" for t in f["themes"] if f"theme:{t}" in G]
    return f, themes, sorted({c for t in themes for c in G[t]["codes"]})


def train_and_test(X, G, F, value20):
    lab = X.dropna(subset=["win"])
    dates = lab.index.get_level_values(0).unique().sort_values()
    cut = dates[int(len(dates) * 0.6)]
    gap = cut + pd.Timedelta(days=35)  # no overlap between train labels and test
    tr = lab[lab.index.get_level_values(0) < cut]
    te = lab[lab.index.get_level_values(0) > gap]
    m = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=200, l2_regularization=1.0)
    m.fit(tr[FEATS], tr["win"])
    p = m.predict_proba(te[FEATS])[:, 1]
    te = te.assign(p=p)
    stats = {"train_from": str(tr.index.get_level_values(0).min().date()), "train_to": str(cut.date()),
             "test_from": str(te.index.get_level_values(0).min().date()), "test_to": str(te.index.get_level_values(0).max().date()),
             "test_rows": int(len(te)), "base_win": float(te["win"].mean()), "base_ret": float(te["ret"].mean()),
             "auc": float(roc_auc_score(te["win"], te["p"]))}
    te["bucket"] = pd.qcut(te["p"], 10, labels=False, duplicates="drop")
    cal = te.groupby("bucket").agg(pred=("p", "mean"), actual=("win", "mean"), ret=("ret", "mean"), n=("win", "size"))
    stats["calibration"] = cal.round(4).reset_index().to_dict("records")
    # the real rule: each day, top-5 by model inside the 3 hottest groups
    picks = []
    for day in te.index.get_level_values(0).unique()[::1]:
        h = heat_on(day, G, F, value20)
        hot = set(c for gid in h.index[h["level"] == "hot"] for c in G[gid]["codes"])
        d = te.loc[day]
        d = d[d.index.isin(hot)].sort_values("p", ascending=False).head(N_PICKS)
        picks.append(d)
    pk = pd.concat(picks)
    stats.update({"rule_trades": int(len(pk)), "rule_win": float(pk["win"].mean()), "rule_ret": float(pk["ret"].mean())})
    top = te.sort_values("p", ascending=False).groupby(level=0).head(N_PICKS)
    stats.update({"model_only_win": float(top["win"].mean()), "model_only_ret": float(top["ret"].mean())})
    _, _, ai = focus_codes(G)
    tai = te[te.index.get_level_values(1).isin(ai)]
    if len(tai):
        aitop = tai.sort_values("p", ascending=False).groupby(level=0).head(3)
        stats.update({"ai_base_win": float(tai["win"].mean()), "ai_base_ret": float(tai["ret"].mean()),
                      "ai_rule_win": float(aitop["win"].mean()), "ai_rule_ret": float(aitop["ret"].mean()), "ai_rule_trades": int(len(aitop))})
    final = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=200, l2_regularization=1.0)
    final.fit(lab[FEATS], lab["win"])
    # map model score -> honest % using the test-period calibration
    edges = te.groupby("bucket")["p"].max().values
    actual = cal["actual"].values
    rets = cal["ret"].values

    def honest(pv):
        i = int(np.searchsorted(edges, pv)) if len(edges) else 0
        i = min(i, len(actual) - 1)
        return float(actual[i]), float(rets[i])
    return final, honest, stats


# ---------------------------------------------------------------- extra live data (money flow, revenue, news)
def live_extras(uni):
    inst = read_monthly("inst_all")
    rev = pd.read_csv(os.path.join(MK, "revenue_all.csv"), dtype={"code": str}, encoding="utf-8-sig") if os.path.exists(os.path.join(MK, "revenue_all.csv")) else pd.DataFrame()
    news = read_monthly("news")
    return inst, rev, news


def flow_info(inst, code):
    """(net shares last 5 days by foreign+trust, consecutive buy days)"""
    if inst.empty:
        return 0.0, 0
    d = inst[inst["code"] == code].sort_values("date")
    if d.empty:
        return 0.0, 0
    net = (d["foreign_net"].fillna(0) + d["trust_net"].fillna(0)).values
    streak = 0
    for v in net[::-1]:
        if v > 0:
            streak += 1
        else:
            break
    return float(net[-5:].sum()), streak


def news_today(news, words, since_hours=30):
    if news.empty:
        return 0
    cutoff = (datetime.now(TPE) - timedelta(hours=since_hours)).strftime("%Y-%m-%d %H:%M")
    t = news[news["published_tpe"].fillna("") >= cutoff]["title"].astype(str)
    return int(sum(t.str.contains(re.escape(w), case=False).sum() for w in words if w))


# ---------------------------------------------------------------- paper trading
PT_FIELDS = ["signal_date", "code", "name", "group", "prob", "entry_date", "entry_price", "target", "stop",
             "exit_date", "exit_price", "status", "return_pct", "days_held"]


def update_paper(W, new_picks, today):
    path = os.path.join(DATA, "paper_trades.csv")
    txt = vault.read(path)
    trades = pd.read_csv(io.StringIO(txt), dtype={"code": str}).to_dict("records") if txt else []
    O, H, L, C = W["open"], W["high"], W["low"], W["close"]
    dates = C.index
    for t in trades:
        if t["status"] in ("win", "loss", "timeout"):
            continue
        sd = pd.Timestamp(t["signal_date"])
        after = dates[dates > sd]
        if not len(after) or t["code"] not in C.columns:
            continue
        if pd.isna(t.get("entry_price")) or t.get("entry_price") in ("", None):
            ed = after[0]
            ep = O.at[ed, t["code"]]
            if pd.isna(ep):
                continue
            t.update({"entry_date": str(ed.date()), "entry_price": round(float(ep), 2),
                      "target": round(float(ep) * (1 + TARGET), 2), "stop": round(float(ep) * (1 - STOP), 2), "status": "open"})
        ed = pd.Timestamp(t["entry_date"])
        held = dates[dates >= ed]
        for i, d in enumerate(held):
            hi, lo, cl = H.at[d, t["code"]], L.at[d, t["code"]], C.at[d, t["code"]]
            if pd.isna(cl):
                continue
            if lo <= t["stop"]:
                t.update({"exit_date": str(d.date()), "exit_price": t["stop"], "status": "loss"})
            elif hi >= t["target"]:
                t.update({"exit_date": str(d.date()), "exit_price": t["target"], "status": "win"})
            elif i + 1 >= MAX_DAYS:
                t.update({"exit_date": str(d.date()), "exit_price": round(float(cl), 2), "status": "timeout"})
            t["days_held"] = i + 1
            if t["status"] != "open":
                break
        last = t["exit_price"] if t["status"] != "open" else C[t["code"]].dropna().iloc[-1]
        t["return_pct"] = round((float(last) / float(t["entry_price"]) - 1 - (COST if t["status"] != "open" else 0)) * 100, 2)
    open_codes = {t["code"] for t in trades if t["status"] in ("open", "pending")}
    for p in new_picks:
        if p["code"] not in open_codes and not any(t["signal_date"] == today and t["code"] == p["code"] for t in trades):
            trades.append({"signal_date": today, "code": p["code"], "name": p["name"], "group": p["group_zh"],
                           "prob": round(p["prob"] * 100, 1), "status": "pending"})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=PT_FIELDS)
    w.writeheader()
    for t in trades:
        w.writerow({k: ("" if (isinstance(t.get(k), float) and np.isnan(t.get(k))) else t.get(k, "")) for k in PT_FIELDS})
    vault.write(path, buf.getvalue())
    return trades


# ---------------------------------------------------------------- main
def main():
    os.makedirs(REP, exist_ok=True)
    uni = load_universe()
    df = load_prices()
    W, C, F, value20, ind, win, ret = build(df, uni)
    X = to_long(F, value20, C, win, ret)
    G = groups(uni, C)
    model, honest, stats = train_and_test(X, G, F, value20)
    today = C.index[-1]
    inst, rev, news = live_extras(uni)

    def extra(gid, g, cs):
        flow = sum(flow_info(inst, c)[0] * float(C.at[today, c]) for c in cs) / max(1.0, float(value20.loc[today, cs].sum()))
        return {"flow": flow, "buzz": news_today(news, g["kw"])}
    heat = heat_on(today, G, F, value20, extra)
    hot_ids = list(heat.index[heat["level"] == "hot"])
    code_group = {}
    for gid in heat.index:  # hottest group wins
        for c in G[gid]["codes"]:
            code_group.setdefault(c, gid)

    Xt = X.loc[today] if today in X.index.get_level_values(0) else pd.DataFrame()
    Xt = Xt.assign(score=model.predict_proba(Xt[FEATS])[:, 1]) if len(Xt) else Xt
    revl = rev.sort_values("year_month").groupby("code").tail(1).set_index("code") if not rev.empty else pd.DataFrame()

    def describe(code, row):
        name = uni["name"].get(code, code)
        gid = code_group.get(code)
        prob, eret = honest(row["score"])
        f5, streak = flow_info(inst, code)
        yoy = float(revl.at[code, "yoy_pct"]) if code in revl.index else None
        why_en, why_zh = [], []
        if gid in hot_ids:
            why_en.append(f"Sector is hot: {G[gid]['en']}")
            why_zh.append(f"產業正熱：{G[gid]['zh']}")
        if row["r20"] > 0.10:
            why_en.append(f"Strong 1-month trend (+{row['r20']*100:.0f}%)")
            why_zh.append(f"近一個月強勢（+{row['r20']*100:.0f}%）")
        if row["volr"] > 1.5:
            why_en.append(f"Volume {row['volr']:.1f}x normal")
            why_zh.append(f"成交量是平常的 {row['volr']:.1f} 倍")
        if row["ghi"] > -0.03:
            why_en.append("At / near 52-week high")
            why_zh.append("接近或創 52 週新高")
        if streak >= 2:
            why_en.append(f"Foreign + trust buying {streak} days in a row")
            why_zh.append(f"外資＋投信連續買超 {streak} 天")
        if yoy is not None and yoy >= 30:
            why_en.append(f"Monthly revenue +{yoy:.0f}% vs last year")
            why_zh.append(f"月營收年增 {yoy:.0f}%")
        if row["rsi"] > 75:
            why_en.append(f"⚠ Overheated (RSI {row['rsi']:.0f}) - may pull back first")
            why_zh.append(f"⚠ 短線過熱（RSI {row['rsi']:.0f}），可能先拉回")
        price = float(C.at[today, code])
        return {"code": code, "name": name, "market": uni["market"].get(code, ""), "industry": uni["industry"].get(code, ""),
                "group_zh": G[gid]["zh"] if gid else uni["industry"].get(code, ""), "group_en": G[gid]["en"] if gid else "",
                "price": round(price, 2), "prob": prob, "exp_ret": eret, "score": float(row["score"]),
                "target": round(price * (1 + TARGET), 2), "stop": round(price * (1 - STOP), 2),
                "r20": float(row["r20"]), "volr": float(row["volr"]), "why_en": why_en, "why_zh": why_zh,
                "news": news_today(news, [name]) if len(name) >= 2 else 0}

    hot_codes = set(c for gid in hot_ids for c in G[gid]["codes"])
    picks = []
    if len(Xt):
        cand = Xt[Xt.index.isin(hot_codes)].sort_values("score", ascending=False)
        picks = [describe(c, r) for c, r in cand.head(N_PICKS).iterrows()]
    # radar: unusual signals anywhere outside the picks
    radar = []
    if len(Xt):
        pick_codes = {p["code"] for p in picks}
        for c, r in Xt.iterrows():
            if c in pick_codes:
                continue
            flags_en, flags_zh = [], []
            if r["vol1"] >= 3 and r["r1"] > 0.03:
                flags_en.append(f"Volume spike {r['vol1']:.1f}x with +{r['r1']*100:.1f}% today")
                flags_zh.append(f"爆量 {r['vol1']:.1f} 倍且今天漲 {r['r1']*100:.1f}%")
            if r["ghi"] >= -0.001 and r["r1"] > 0:
                flags_en.append("New 52-week high")
                flags_zh.append("創 52 週新高")
            if c in revl.index and float(revl.at[c, "yoy_pct"]) >= 50 and float(revl.at[c, "mom_pct"]) > 0:
                flags_en.append(f"Revenue +{float(revl.at[c, 'yoy_pct']):.0f}% YoY")
                flags_zh.append(f"營收年增 {float(revl.at[c, 'yoy_pct']):.0f}%")
            f5, streak = flow_info(inst, c)
            if streak >= 3 and f5 * float(r["close"]) > 0.05 * float(r["value20"]) * 5:
                flags_en.append(f"Institutions buying {streak} days straight")
                flags_zh.append(f"法人連續買超 {streak} 天")
            nm = uni["name"].get(c, "")
            nn = news_today(news, [nm]) if len(str(nm)) >= 2 else 0
            if nn >= 3:
                flags_en.append(f"News burst: {nn} headlines")
                flags_zh.append(f"新聞突然大增：{nn} 則")
            if len(flags_en) >= 2 or (flags_en and r["score"] > Xt["score"].quantile(0.9)):
                d = describe(c, r)
                d.update({"flags_en": flags_en, "flags_zh": flags_zh, "nflags": len(flags_en)})
                radar.append(d)
        radar = sorted(radar, key=lambda d: (d["nflags"], d["score"]), reverse=True)[:N_RADAR]

    # ---- AI focus: always analysed, hot or cold
    fcfg, fthemes, fcodes = focus_codes(G)
    ai_rows, ai_picks = [], []
    if len(Xt):
        for c in fcodes:
            if c not in Xt.index:
                continue
            d = describe(c, Xt.loc[c])
            sub = next(t for t in fthemes if c in G[t]["codes"])  # show the AI sub-group, not the official industry
            d.update({"group_zh": G[sub]["zh"], "group_en": G[sub]["en"]})
            f5, streak = flow_info(inst, c)
            g20, g60 = float(Xt.at[c, "g20"]), float(Xt.at[c, "g60"])
            # trend label = facts, not a prediction: above/below 20- and 60-day average price + institutional buying
            d.update({"r5": float(Xt.at[c, "r5"]), "streak": streak,
                      "signal": "strong" if (g20 > 0 and g60 > 0 and streak >= 1) else ("weak" if (g20 < 0 and g60 < 0) else "neutral")})
            ai_rows.append(d)
        ai_rows.sort(key=lambda d: d["score"], reverse=True)
        ai_picks = [d for d in ai_rows if d["code"] not in {p["code"] for p in picks}][:3]
    ai_heat = [{"gid": g, "zh": G[g]["zh"], "en": G[g]["en"], "level": heat.at[g, "level"], "heat": float(heat.at[g, "heat"]),
                "trend": heat.at[g, "trend"], "share": float(heat.at[g, "share"]), "surge": float(heat.at[g, "surge"]),
                "r5": float(heat.at[g, "r5"]), "r20": float(heat.at[g, "r20"]), "breadth": float(heat.at[g, "breadth"])}
               for g in fthemes if g in heat.index]
    ai_heat.sort(key=lambda h: h["heat"], reverse=True)
    focus = {"zh": fcfg["zh"], "en": fcfg["en"], "heat": ai_heat, "picks": ai_picks, "stocks": ai_rows,
             "avg_heat": float(np.mean([h["heat"] for h in ai_heat])) if ai_heat else None}
    for p in ai_picks:
        p["group_zh"] = p["group_zh"] + "（AI追蹤）"
    trades = update_paper(W, picks + ai_picks, str(today.date()))
    closed = [t for t in trades if t["status"] in ("win", "loss", "timeout")]
    openp = [t for t in trades if t["status"] == "open"]
    clean = lambda t: {k: ("" if (isinstance(v, float) and np.isnan(v)) else v) for k, v in t.items()}
    paper = {"closed": len(closed), "wins": sum(t["status"] == "win" for t in closed),
             "avg_ret": float(np.mean([float(t["return_pct"]) for t in closed])) if closed else None,
             "open": [clean(t) for t in openp], "pending": [clean(t) for t in trades if t["status"] == "pending"],
             "recent_closed": [clean(t) for t in sorted(closed, key=lambda t: str(t.get("exit_date", "")))[-30:]]}
    heat_rows = [{"gid": gid, "zh": G[gid]["zh"], "en": G[gid]["en"], "level": r["level"], "heat": float(r["heat"]),
                  "trend": r["trend"], "share": float(r["share"]), "surge": float(r["surge"]),
                  "r5": float(r["r5"]), "r20": float(r["r20"]), "breadth": float(r["breadth"]), "n": int(r["n"]),
                  "buzz": int(r.get("buzz", 0) or 0)} for gid, r in heat.iterrows()]
    out = {"date": str(today.date()), "generated_tpe": datetime.now(TPE).strftime("%Y-%m-%d %H:%M"), "stats": stats, "focus": focus,
           "heat": heat_rows, "picks": picks, "radar": radar, "paper": paper,
           "plan": {"target": TARGET, "stop": STOP, "max_days": MAX_DAYS, "cost": COST}}
    vault.write(os.path.join(REP, "latest.json"), json.dumps(out, ensure_ascii=False, default=str))
    from report import render
    vault.write(os.path.join(REP, "latest.html"), render(out))  # private: scrambled in the public repo
    print(json.dumps({"date": out["date"], "hot": [h["zh"] for h in heat_rows[:N_HOT]],
                      "picks": [(p["code"], p["name"], round(p["prob"] * 100)) for p in picks],
                      "radar": [(p["code"], p["name"], p["flags_en"]) for p in radar],
                      "stats": {k: v for k, v in stats.items() if k != "calibration"}}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
