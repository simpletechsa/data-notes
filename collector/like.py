#!/usr/bin/env python3
"""
Stocks like Yageo / 像國巨的股票 — Johnny's checklist, shown next to the market bottom signal.
Checks (his guides):  big drop in 60 days, at least 50% below its 2-year high, strong revenue growth, big monthly revenue, liquid, far below its high.
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
    hl = pd.read_csv(os.path.join(DATA, "market", "health.csv"), dtype={"code": str}, encoding="utf-8-sig").set_index("code")
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
                     "drop60": float(l / g.tail(60).max() - 1), "drop_high": float(l / g.max() - 1), "high": round(float(g.max()), 2), "high_date": str(g.idxmax().date()), "room_to_high": float(g.max() / l - 1), "chg20": float(l / g.iloc[-21] - 1) if len(g) > 21 else None,
                     "rev_yoy": float(rv.yoy_pct[c]) / 100, "rev_mom": float(rv.mom_pct[c]) / 100, "rev_bn": float(rv.revenue[c]) / 1e6,
                     "liq_rank": int(liq.get(c, 9999)),
                     "gross_margin": hl.gross_margin.get(c), "net_margin": hl.net_margin.get(c), "debt_ratio": hl.debt_ratio.get(c),
                     "current_ratio": hl.current_ratio.get(c), "eps": hl.eps.get(c), "net_income": hl.net_income.get(c), "fin_period": hl.period.get(c),
                     "series": [round(float(v), 2) for v in g.tail(130).values], "series_from": str(g.index[-min(130, len(g))].date())})
    D = pd.DataFrame(rows)
    D["c_drop"] = D.drop60 <= -0.40
    D["c_rev"] = D.rev_yoy >= 0.30
    D["c_size"] = D.rev_bn >= 10
    D["c_liq"] = D.liq_rank <= 100
    D["c_room"] = D.drop_high <= -0.50  # at least 50% below its highest price in the 2 years we hold
    # health (my guess, NOT tested): profitable, debt under 60% of assets, current assets cover short-term debts 1.2x
    D["c_health"] = (D.net_income > 0) & (D.debt_ratio <= 0.60) & (D.current_ratio >= 1.2)
    D["checks"] = D[["c_drop", "c_rev", "c_size", "c_liq", "c_room", "c_health"]].sum(axis=1)
    core = D[(D.checks == 6)].sort_values("rev_bn", ascending=False)
    near = D[(D.checks == 5) & D.c_drop & D.c_rev].sort_values("rev_bn", ascending=False).head(15).copy()
    names = {"c_drop": "drop60", "c_rev": "rev", "c_size": "size", "c_liq": "liq", "c_room": "room", "c_health": "health"}
    near["missing"] = near.apply(lambda r: next((v for k, v in names.items() if not r[k]), ""), axis=1)
    bs = None
    try:
        bs = json.loads(vault.read(os.path.join(DATA, "report", "bottom.json")))
    except Exception:
        pass
    out = {"date": str(last_day.date()), "made": datetime.now(TPE).strftime("%Y-%m-%d %H:%M"),
           "bottom_score": bs["score"] if bs else None, "bottom_in_drop": bs["in_drop"] if bs else None,
           "rules": {"drop60": -0.40, "rev_yoy": 0.30, "rev_bn": 10, "liq_rank": 100, "drop_high": -0.50, "high_window": "2 years", "debt_ratio": 0.60, "current_ratio": 1.2},
           "tested": {"price_part": {"episodes": 10, "wins": 8, "avg_ret": 0.132, "other_big": 0.108, "avg_dip": -0.055}, "revenue_part": None, "health_part": None},
           "count": int(len(core)), "list": core.drop(columns=["checks"]).round(4).to_dict("records"),
           "near": near.drop(columns=["checks"]).round(4).to_dict("records")}
    def clean(x):  # phone apps cannot read NaN in JSON: turn missing numbers into null
        if isinstance(x, float) and x != x:
            return None
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items()}
        if isinstance(x, list):
            return [clean(v) for v in x]
        return x
    vault.save_or_print(os.path.join(DATA, "report", "like.json"), json.dumps(clean(out), ensure_ascii=False, allow_nan=False))
    print(f"like-Yageo list: {len(core)} stocks (bottom score {out['bottom_score']})", ", ".join(core.name.head(12)))


if __name__ == "__main__":
    main()
