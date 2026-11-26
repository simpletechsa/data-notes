#!/usr/bin/env python3
"""
Stocks like Yageo / 像國巨的股票 — Johnny's checklist, shown next to the market bottom signal.
Checks (his guides):  big drop in 60 days, strong revenue growth, big monthly revenue, liquid, far below its high.
Test status shown with each check:
  price part (big liquid stocks down 30%+ at a bottom signal): TESTED, beat other big stocks in 8 of 10 episodes (+13.2% vs +10.8% in 20 days)
  revenue part: NOT TESTED (no revenue history yet)
Private: report is encrypted. Analysis only, not advice.
"""
import glob, json, os, sys
from datetime import datetime, timedelta, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vault  # noqa: E402

DATA = os.path.join(os.path.dirname(HERE), "data")
TPE = timezone(timedelta(hours=8))


def main():
    h = pd.read_csv(os.path.join(DATA, "market", "history.csv.gz"), dtype={"code": str})
    parts = [h] + [pd.read_csv(f, dtype={"code": str}) for f in sorted(glob.glob(os.path.join(DATA, "market", "daily", "*.csv")))]
    a = pd.concat(parts).drop_duplicates(["date", "code"], keep="last")
    a["date"] = pd.to_datetime(a["date"])
    a = a[a.code.str.len() == 4].sort_values(["code", "date"])
    last_day = a.date.max()
    uni = pd.read_csv(os.path.join(DATA, "market", "universe.csv"), dtype={"code": str}, encoding="utf-8-sig").set_index("code")
    rv = pd.read_csv(os.path.join(DATA, "market", "revenue_all.csv"), dtype={"code": str}, encoding="utf-8-sig").set_index("code")
    a["tv"] = a.close * a.volume
    recent = a[a.date > last_day - pd.Timedelta(days=90)]
    liq = recent.groupby("code").tv.mean().rank(ascending=False)
    rows = []
    for c, g in a.groupby("code"):
        g = g.set_index("date")["close"].astype(float)
        if len(g) < 60 or g.index[-1] != last_day or c not in rv.index or c not in uni.index:
            continue
        l = g.iloc[-1]
        rows.append({"code": c, "name": uni.loc[c, "name"], "industry": uni.loc[c, "industry"], "price": round(float(l), 2),
                     "drop60": float(l / g.tail(60).max() - 1), "drop_high": float(l / g.max() - 1), "chg20": float(l / g.iloc[-21] - 1) if len(g) > 21 else None,
                     "rev_yoy": float(rv.yoy_pct[c]) / 100, "rev_mom": float(rv.mom_pct[c]) / 100, "rev_bn": float(rv.revenue[c]) / 1e6,
                     "liq_rank": int(liq.get(c, 9999))})
    D = pd.DataFrame(rows)
    D["c_drop"] = D.drop60 <= -0.40
    D["c_rev"] = D.rev_yoy >= 0.30
    D["c_size"] = D.rev_bn >= 10
    D["c_liq"] = D.liq_rank <= 100
    D["checks"] = D[["c_drop", "c_rev", "c_size", "c_liq"]].sum(axis=1)
    core = D[(D.checks == 4)].sort_values("rev_bn", ascending=False)
    near = D[(D.checks == 3) & D.c_drop & D.c_rev].sort_values("rev_bn", ascending=False).head(15)
    bs = None
    try:
        bs = json.loads(vault.read(os.path.join(DATA, "report", "bottom.json")))
    except Exception:
        pass
    out = {"date": str(last_day.date()), "made": datetime.now(TPE).strftime("%Y-%m-%d %H:%M"),
           "bottom_score": bs["score"] if bs else None, "bottom_in_drop": bs["in_drop"] if bs else None,
           "rules": {"drop60": -0.40, "rev_yoy": 0.30, "rev_bn": 10, "liq_rank": 100},
           "tested": {"price_part": {"episodes": 10, "wins": 8, "avg_ret": 0.132, "other_big": 0.108, "avg_dip": -0.055}, "revenue_part": None},
           "count": int(len(core)), "list": core.drop(columns=["checks"]).round(4).to_dict("records"),
           "near": near.drop(columns=["checks"]).round(4).to_dict("records")}
    vault.save_or_print(os.path.join(DATA, "report", "like.json"), json.dumps(out, ensure_ascii=False))
    print(f"like-Yageo list: {len(core)} stocks (bottom score {out['bottom_score']})", ", ".join(core.name.head(12)))


if __name__ == "__main__":
    main()
