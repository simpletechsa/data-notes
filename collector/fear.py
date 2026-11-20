#!/usr/bin/env python3
"""
Fear meter / 恐慌指標 — AI fear, war fear, Trump fear, rates fear.

  python3 collector/fear.py now            count today's bad-news STORIES per fear type (same story from many outlets = 1)
  python3 collector/fear.py backfill 150   rebuild daily counts for past days (Google News date search), for testing
  python3 collector/fear.py test           does a fear spike come before Taiwan drops?

Saved scrambled: data/fear/daily.csv.enc (counts), data/report/fear.json.enc (latest meter, for the app).
"""
import csv, html, io, json, os, re, sys, time, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vault  # noqa: E402
from collect import fetch, score_text  # noqa: E402

DATA = os.path.join(os.path.dirname(HERE), "data")
TPE = timezone(timedelta(hours=8))

FEARS = {  # one Chinese + one English combined search per fear type (gentle on Google News)
    "ai": {"en": "AI fear", "zh": "AI 恐慌",
           "q": ["AI 泡沫 OR 輝達 調查 OR AI 詐騙 OR AI 砍單 OR 資料中心 取消",
                 "AI bubble OR Nvidia probe OR AI fraud OR AI capex cut OR data center cancel"]},
    "war": {"en": "War fear", "zh": "戰爭恐慌",
            "q": ["台海 軍演 OR 共軍 OR 中東 戰爭 OR 俄烏 升級",
                  "China military drills Taiwan OR Middle East war OR Russia Ukraine escalation"]},
    "trump": {"en": "Trump fear", "zh": "川普恐慌",
              "q": ["川普 關稅 OR 川普 晶片 OR 川普 台灣",
                    "Trump tariff OR Trump chips ban OR Trump Taiwan"]},
    "rates": {"en": "Rates fear", "zh": "利率恐慌",
              "q": ["聯準會 升息 OR 聯準會 鷹派 OR 美債殖利率 飆升",
                    "Fed rate hike OR Fed hawkish OR Treasury yields surge"]},
}


def _norm(t):
    t = re.sub(r"\s+-\s+[^-]+$", "", t)
    return re.sub(r"[\W_]+", "", t.lower())


def _grams(t, n=3):
    t = _norm(t)
    return {t[i:i + n] for i in range(max(1, len(t) - n + 1))}


def group_stories(titles):
    """Same story told by many outlets counts once (titles sharing most of their wording)."""
    groups = []
    for t in titles:
        g = _grams(t)
        for grp in groups:
            if len(g & grp["g"]) / max(1, len(g | grp["g"])) >= 0.35:
                grp["n"] += 1
                break
        else:
            groups.append({"g": g, "title": t, "n": 1})
    return groups


def search(q, day=None):
    en = bool(re.fullmatch(r"[A-Za-z0-9 .&'-]+", q))
    loc = "hl=en-US&gl=US&ceid=US:en" if en else "hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    when = " when:1d" if day is None else f" after:{day.isoformat()} before:{(day + timedelta(days=1)).isoformat()}"
    try:
        root = ET.fromstring(fetch(f"https://news.google.com/rss/search?q={urllib.parse.quote(q + when)}&{loc}", tries=2))
        return [html.unescape(it.findtext("title") or "") for it in root.iter("item")]
    except Exception:
        return []


def measure(day=None):
    out = {}
    for k, f in FEARS.items():
        titles = []
        for q in f["q"]:
            titles += search(q, day)
            time.sleep(0.4 if day is None else 1.5)
        titles = list(dict.fromkeys(titles))  # same headline from 2 searches counts once
        bad = titles  # the searches themselves are fear topics; count every story found
        stories = group_stories(bad)
        stories.sort(key=lambda s: -s["n"])
        out[k] = {"stories": len(stories), "headlines": len(bad), "top": [re.sub(r"\s+-\s+[^-]+$", "", s["title"]) for s in stories[:3]]}
    return out


def read_daily():
    t = vault.read(os.path.join(DATA, "fear", "daily.csv"))
    return {r["date"]: r for r in csv.DictReader(io.StringIO(t))} if t else {}


def write_daily(rows):
    os.makedirs(os.path.join(DATA, "fear"), exist_ok=True)
    fields = ["date"] + [f"{k}_{x}" for k in FEARS for x in ("stories", "headlines")]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    for d in sorted(rows):
        w.writerow({f: rows[d].get(f, "") for f in fields})
    vault.write(os.path.join(DATA, "fear", "daily.csv"), buf.getvalue())


def level(ratio):
    return "extreme" if ratio >= 4 else "high" if ratio >= 2.5 else "elevated" if ratio >= 1.5 else "calm"


def run_now():
    now = datetime.now(TPE)
    m = measure()
    try:
        rows = read_daily()
    except vault.NoKey:
        rows = {}
    rows[str(now.date())] = {"date": str(now.date()), **{f"{k}_{x}": m[k][x] for k in m for x in ("stories", "headlines")}}
    if os.environ.get("DATA_KEY", "").strip():
        write_daily(rows)
    past = [rows[d] for d in sorted(rows) if d < str(now.date())][-30:]
    meter = {}
    for k, f in FEARS.items():
        base = sorted(float(r.get(f"{k}_stories") or 0) for r in past)
        normal = base[len(base) // 2] if base else 0
        ratio = (m[k]["stories"] + 1) / (normal + 1)
        meter[k] = {"en": f["en"], "zh": f["zh"], "stories": m[k]["stories"], "normal": normal, "ratio": round(ratio, 2),
                    "level": level(ratio) if len(past) >= 10 else "learning", "top": m[k]["top"]}
    try:
        react = load_reaction()
    except vault.NoKey:
        react = None
    out = {"date": str(now.date()), "time": now.strftime("%H:%M"), "meter": meter, "days_of_history": len(past),
           "reaction": react}
    vault.save_or_print(os.path.join(DATA, "report", "fear.json"), json.dumps(out, ensure_ascii=False))
    print(json.dumps({k: (v["stories"], v["normal"], v["level"]) for k, v in meter.items()}, ensure_ascii=False))


def backfill(days):
    rows = read_daily()
    today = datetime.now(TPE).date()
    for i in range(1, days + 1):
        d = today - timedelta(days=i)
        if str(d) in rows:
            continue
        m = measure(d)
        rows[str(d)] = {"date": str(d), **{f"{k}_{x}": m[k][x] for k in m for x in ("stories", "headlines")}}
        if i % 10 == 0:
            write_daily(rows)
            print("backfilled", i, d, {k: m[k]["stories"] for k in m}, flush=True)
    write_daily(rows)


def reaction_table():
    """For each fear type: what did the TAIEX do on days when that fear spiked? (learns which fear really matters)"""
    import numpy as np
    import pandas as pd
    rows = read_daily()
    if len(rows) < 30:
        return None
    f = pd.DataFrame(rows.values()).set_index("date")
    f.index = pd.to_datetime(f.index)
    f = f.apply(pd.to_numeric, errors="coerce").sort_index()
    p = pd.read_csv(os.path.join(DATA, "prices.csv"), encoding="utf-8-sig")
    tw = p[p.symbol == "^TWII"].copy()
    tw["date"] = pd.to_datetime(tw["date"])
    c = tw.set_index("date")["close"].sort_index()
    nxt = {}
    for d in f.index:  # next Taiwan session after the news day (news at night hits the next morning)
        after = c[c.index > d]
        before = c[c.index <= d]
        if len(after) and len(before):
            nxt[d] = after.iloc[0] / before.iloc[-1] - 1
    f["next"] = pd.Series(nxt)
    out = {}
    for k in FEARS:
        s = f[f"{k}_stories"]
        spike = s > s.rolling(30, min_periods=10).median().shift(1) * 2.5 + 1
        x = f[spike & f["next"].notna()]
        out[k] = {"spike_days": int(len(x)), "next_avg": float(x["next"].mean()) if len(x) else None,
                  "next_down_share": float((x["next"] < 0).mean()) if len(x) else None,
                  "normal_avg": float(f["next"].mean())}
    return out


def load_reaction():
    t = vault.read(os.path.join(DATA, "report", "fear_reaction.json"))
    return json.loads(t) if t else None


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "now"
    if cmd == "backfill":
        backfill(int(sys.argv[2]) if len(sys.argv) > 2 else 120)
    elif cmd == "test":
        r = reaction_table()
        vault.write(os.path.join(DATA, "report", "fear_reaction.json"), json.dumps(r, ensure_ascii=False))
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        run_now()
