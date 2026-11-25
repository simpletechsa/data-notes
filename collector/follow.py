#!/usr/bin/env python3
"""
Follow list / 追蹤清單 — track chosen stocks over months and score the idea honestly.
Private: the list and the report are encrypted (.enc). Nothing readable is uploaded.
Config (encrypted): data/follow/cfg.json.enc = {"stocks":[{"code","name_en","name_zh","start_date","start_price","target","end_date","vs":"2330"}]}
Report (encrypted): data/report/follow.json.enc  (price since start, vs TSMC and vs TAIEX, worst dip, distance to target)
Run:  python3 collector/follow.py            (daily, after the Taiwan close)
      python3 collector/follow.py add 2327 "Yageo" "國巨" 2026-11-25 309 600 2027-03-25
"""
import glob, json, os, sys
from datetime import datetime, timedelta, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vault  # noqa: E402

DATA = os.path.join(os.path.dirname(HERE), "data")
CFG = os.path.join(DATA, "follow", "cfg.json")
TPE = timezone(timedelta(hours=8))


def closes(codes):
    h = pd.read_csv(os.path.join(DATA, "market", "history.csv.gz"), dtype={"code": str})
    parts = [h] + [pd.read_csv(f, dtype={"code": str}) for f in sorted(glob.glob(os.path.join(DATA, "market", "daily", "*.csv")))]
    a = pd.concat(parts).drop_duplicates(["date", "code"], keep="last")
    a["date"] = pd.to_datetime(a["date"])
    out = {c: a[a.code == c].set_index("date").sort_index() for c in codes}
    return out


def taiex():
    p = pd.read_csv(os.path.join(DATA, "prices.csv"), encoding="utf-8-sig")
    p["date"] = pd.to_datetime(p["date"])
    t = p[p.symbol == "^TWII"].set_index("date").sort_index()["close"]
    return t[~t.index.duplicated(keep="last")].astype(float)


def add(args):
    code, en, zh, start, price, target, end = args
    cfg = json.loads(vault.read(CFG, '{"stocks": []}'))
    cfg["stocks"] = [s for s in cfg["stocks"] if s["code"] != code]
    cfg["stocks"].append({"code": code, "name_en": en, "name_zh": zh, "start_date": start, "start_price": float(price),
                          "target": float(target), "end_date": end, "vs": "2330"})
    os.makedirs(os.path.dirname(CFG), exist_ok=True)
    vault.write(CFG, json.dumps(cfg, ensure_ascii=False, indent=1))
    print("saved follow list (encrypted):", len(cfg["stocks"]), "stock(s)")


def main():
    cfg = json.loads(vault.read(CFG, '{"stocks": []}'))
    if not cfg["stocks"]:
        print("follow list empty")
        return
    codes = sorted({s["code"] for s in cfg["stocks"]} | {s.get("vs", "2330") for s in cfg["stocks"]})
    px = closes(codes)
    tx = taiex()
    now = datetime.now(TPE)
    res = []
    for s in cfg["stocks"]:
        d0 = pd.Timestamp(s["start_date"])
        c = px[s["code"]]["close"].astype(float)
        c = c[c.index >= d0]
        if c.empty:
            continue
        ref = px[s.get("vs", "2330")]["close"].astype(float)
        ref0 = ref[ref.index <= d0].iloc[-1] if (ref.index <= d0).any() else ref.iloc[0]
        tx0 = tx[tx.index <= d0].iloc[-1] if (tx.index <= d0).any() else tx.iloc[0]
        p0 = s["start_price"]
        last = float(c.iloc[-1])
        series = []
        for dte, v in c.items():
            r = ref[ref.index <= dte]
            t = tx[tx.index <= dte]
            series.append({"d": str(dte.date()), "stock": round(v / p0 - 1, 4),
                           "vs": round(float(r.iloc[-1]) / ref0 - 1, 4) if len(r) else None,
                           "taiex": round(float(t.iloc[-1]) / tx0 - 1, 4) if len(t) else None})
        end = pd.Timestamp(s["end_date"])
        res.append({"code": s["code"], "en": s["name_en"], "zh": s["name_zh"], "start_date": s["start_date"], "start_price": p0,
                    "target": s["target"], "end_date": s["end_date"], "last_date": str(c.index[-1].date()), "last": last,
                    "since_start": last / p0 - 1, "to_target": s["target"] / last - 1,
                    "worst_close": float(c.min()), "worst_dip": float(c.min() / p0 - 1), "best_close": float(c.max()),
                    "target_hit": bool((c >= s["target"]).any()), "days_left": max(0, (end - pd.Timestamp(now.date())).days),
                    "vs_code": s.get("vs", "2330"), "vs_since_start": float(series[-1]["vs"]) if series[-1]["vs"] is not None else None,
                    "taiex_since_start": series[-1]["taiex"], "series": series[-130:]})
    out = {"made": now.strftime("%Y-%m-%d %H:%M"), "stocks": res}
    vault.save_or_print(os.path.join(DATA, "report", "follow.json"), json.dumps(out, ensure_ascii=False))
    for r in res:
        print(f"follow {r['code']}: {r['last']} ({r['since_start']*100:+.1f}% since start, {r['to_target']*100:+.0f}% to target, {r['days_left']} days left)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "add":
        add(sys.argv[2:])
    else:
        main()
