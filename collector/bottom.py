#!/usr/bin/env python3
"""
Bottom watch / 止跌訊號 — is the drop ending?

Tested on 5 years of TAIEX (only days already 8%+ below the 60-day high):
  4 best signs, each counted if it appeared in the last 3 trading days:
    back_above_ma5   close back above the 5-day average after 5+ days below (站回5日線)
    rsi_rebound      RSI fell under 30, now back above 35 (RSI回升)
    vix_peak         US fear index (VIX) went above 25, now 15% off its 10-day peak (恐慌見頂)
    futures_premium  futures back above the index after a discount (逆價差轉正)
  Score 2+ of 4  -> 20 days later TAIEX was up 77% of the time, avg +5.9% (2022-23: 75%, 2024-26: 78%)
  Score 0        -> 20 days later up 52% (coin flip), and it fell a further -5.1% on average
Extra info (too few cases to score): foreign futures shorts at a 1-year extreme, put/call ratio low.
"""
import json, os, sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vault  # noqa: E402

DATA = os.path.join(os.path.dirname(HERE), "data")
TPE = timezone(timedelta(hours=8))
TRACK = {"score2": {"up": 0.77, "avg20": 0.059, "further": -0.023, "events": 10},
         "score0": {"up": 0.52, "avg20": -0.001, "further": -0.051},
         "any_drop_day": {"up": 0.63, "avg20": 0.024}, "years": "2021-11 to 2026-11"}
SIGNS = {
    "back_above_ma5": ("Back above 5-day average", "站回5日線"),
    "rsi_rebound": ("RSI rebound from under 30", "RSI 跌破30後回升"),
    "vix_peak": ("US fear (VIX) past its peak", "美國恐慌指數見頂回落"),
    "futures_premium": ("Futures back above the index", "期貨逆價差轉正價差"),
}


def main():
    p = pd.read_csv(os.path.join(DATA, "prices.csv"), encoding="utf-8-sig")
    p["date"] = pd.to_datetime(p["date"])
    tw = p[p.symbol == "^TWII"].set_index("date").sort_index()
    tw = tw[~tw.index.duplicated(keep="last")]
    C = tw["close"].astype(float)
    vix = p[p.symbol == "^VIX"].set_index("date")["close"].sort_index()
    vix = vix[~vix.index.duplicated(keep="last")].reindex(C.index, method="ffill")
    fm = pd.read_csv(os.path.join(DATA, "futures", "tx_front.csv"), parse_dates=["date"])
    fut = fm[fm.session == "day"].drop_duplicates("date", keep="last").set_index("date")["close"].astype(float)
    basis = fut.reindex(C.index) / C - 1
    ma5 = C.rolling(5).mean()
    d = C.diff()
    rsi = 100 - 100 / (1 + d.clip(lower=0).rolling(14).mean() / (-d.clip(upper=0)).rolling(14).mean())
    sig = {
        "back_above_ma5": (C > ma5) & ((C.shift(1) < ma5.shift(1)).rolling(5).sum() == 5),
        "rsi_rebound": (rsi > 35) & (rsi.rolling(5).min() < 30),
        "vix_peak": (vix.rolling(10).max() > 25) & (vix < 0.85 * vix.rolling(10).max()),
        "futures_premium": (basis > 0) & (basis.rolling(5).min() < -0.003),
    }
    recent = {k: bool(v.fillna(False).tail(3).any()) for k, v in sig.items()}
    score = sum(recent.values())
    dd = float(C.iloc[-1] / C.rolling(60).max().iloc[-1] - 1)
    extras = {}
    try:
        ff = pd.read_csv(os.path.join(DATA, "futures", "foreign_futures.csv"), parse_dates=["date"]).set_index("date")["foreign_net_oi"].astype(float)
        extras["foreign_shorts"] = {"net_oi": float(ff.iloc[-1]), "date": str(ff.index[-1].date()), "change5": float(ff.iloc[-1] - ff.iloc[-6]),
                                    "extreme": bool(ff.iloc[-1] < ff.tail(250).quantile(0.1))}
        pc = pd.read_csv(os.path.join(DATA, "futures", "put_call.csv"), parse_dates=["date"]).set_index("date")["pc_oi"].astype(float)
        extras["put_call"] = {"pc_oi": float(pc.iloc[-1]), "date": str(pc.index[-1].date()), "low": bool(pc.iloc[-1] < pc.tail(250).quantile(0.2))}
    except Exception as e:
        print("extras:", e, file=sys.stderr)
    out = {"date": str(C.index[-1].date()), "made": datetime.now(TPE).strftime("%Y-%m-%d %H:%M"), "in_drop": dd <= -0.08,
           "drop_from_high": dd, "rsi": float(rsi.iloc[-1]), "vix": float(vix.iloc[-1]), "basis": float(basis.iloc[-1]) if pd.notna(basis.iloc[-1]) else None,
           "signs": [{"id": k, "en": SIGNS[k][0], "zh": SIGNS[k][1], "on": recent[k]} for k in SIGNS], "score": score, "track": TRACK, "extras": extras}
    vault.save_or_print(os.path.join(DATA, "report", "bottom.json"), json.dumps(out, ensure_ascii=False))
    print(f"bottom score {score}/4 (drop {dd*100:.1f}% from 60-day high)", {k: v for k, v in recent.items()}, extras)


if __name__ == "__main__":
    main()
