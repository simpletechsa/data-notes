#!/usr/bin/env python3
"""
Morning forecast for the Taiwan market (run ~08:40 Taipei, after Japan & Korea open, before Taiwan opens).
台股盤前預測：依美股收盤 + 日韓開盤，預估台股開盤、收盤、今日高低點。

Inputs (all known before 09:00 Taipei):
  US last session: SOX (費半), Nasdaq, TSMC ADR
  Asia this morning: Nikkei, KOSPI, SK Hynix move since their open (live at run time)
Model: simple linear regression on 5 years of daily history, tested on the last 20% it never saw.
Also scores yesterday's forecast against what really happened.
"""
import csv, io, json, os, sys, urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vault

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
TPE = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
US = {"sox": "^SOX", "ndx": "^IXIC", "tsm": "TSM"}
ASIA = {"jp": "^N225", "kr": "^KS11", "hynix": "000660.KS"}
FEATS = ["sox", "ndx", "tsm", "jp", "kr", "hynix"]


def yahoo(sym, rng="5d", interval="1d"):
    import urllib.parse
    d = json.loads(urllib.request.urlopen(urllib.request.Request(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range={rng}&interval={interval}", headers=UA), timeout=30).read())
    return d["chart"]["result"][0]


def history():
    p = pd.read_csv(os.path.join(DATA, "prices.csv"), encoding="utf-8-sig")
    p["date"] = pd.to_datetime(p["date"])
    W = {s: p[p.symbol == s].set_index("date").sort_index() for s in list(US.values()) + list(ASIA.values()) + ["^TWII"]}
    tw = W["^TWII"]
    tw = tw[(tw["open"] != tw["close"]) | (tw["high"] != tw["low"])]  # drop broken bars
    rows = []
    for i in range(1, len(tw)):
        T, prev = tw.index[i], tw.iloc[i - 1]
        if (T - tw.index[i - 1]).days > 5:
            continue
        r = {"date": T, "open_gap": tw["open"].iat[i] / prev["close"] - 1, "close_ret": tw["close"].iat[i] / prev["close"] - 1,
             "high_ret": tw["high"].iat[i] / prev["close"] - 1, "low_ret": tw["low"].iat[i] / prev["close"] - 1}
        for k, s in US.items():
            u = W[s][W[s].index < T]
            if len(u) < 2 or (T - u.index[-1]).days > 5:
                break
            r[k] = u["close"].iat[-1] / u["close"].iat[-2] - 1
        else:
            for k, s in ASIA.items():
                a = W[s]
                if T not in a.index:
                    break
                j = a.index.get_loc(T)
                if j == 0:
                    break
                r[k] = a["open"].iat[j] / a["close"].iat[j - 1] - 1
            else:
                rows.append(r)
    df = pd.DataFrame(rows).set_index("date").replace([np.inf, -np.inf], np.nan).dropna()
    fp = os.path.join(DATA, "futures", "tx_front.csv")
    if os.path.exists(fp):  # night futures (夜盤): night close vs previous day-session close
        fm = pd.read_csv(fp, parse_dates=["date"])
        day, night = fm[fm.session == "day"].set_index("date")["close"], fm[fm.session == "night"].set_index("date")["close"]
        fut = {}
        for T in df.index:
            prev = day[day.index < T]
            if T in night.index and len(prev):
                fut[T] = night[T] / prev.iloc[-1] - 1
        df["fut"] = pd.Series(fut)
    return df


def fit(df, target):
    X = np.c_[np.ones(len(df)), df[FEATS].values]
    coef, *_ = np.linalg.lstsq(X, df[target].values, rcond=None)
    return coef


def predict(coef, x):
    return float(coef[0] + np.dot(coef[1:], [x[k] for k in FEATS]))


def backtest(df):
    n = int(len(df) * 0.8)
    tr, te = df.iloc[:n], df.iloc[n:]
    out = {"test_from": str(te.index[0].date()), "test_to": str(te.index[-1].date()), "test_days": len(te)}
    for t in ["open_gap", "close_ret", "high_ret", "low_ret"]:
        c = fit(tr, t)
        p = np.c_[np.ones(len(te)), te[FEATS].values] @ c
        out[t] = {"direction_hit": float(np.mean(np.sign(p) == np.sign(te[t]))), "mae": float(np.mean(np.abs(p - te[t]))),
                  "always_up_hit": float(np.mean(te[t] > 0))}
        # how often direction is right when the forecast is strong (|pred| > 0.5%)
        strong = np.abs(p) > 0.005
        out[t]["strong_hit"] = float(np.mean(np.sign(p[strong]) == np.sign(te[t][strong]))) if strong.any() else None
        out[t]["strong_share"] = float(strong.mean())
    return out


def taiex_prev_close(today):
    """Yesterday's official TAIEX close from TWSE (Yahoo sometimes misses it)."""
    d = today - timedelta(days=1)
    for _ in range(7):
        if d.weekday() < 5:
            try:
                j = json.loads(urllib.request.urlopen(urllib.request.Request(
                    f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d:%Y%m%d}&type=IND&response=json", headers=UA), timeout=30).read())
                if j.get("stat") == "OK":
                    for t in j.get("tables", []):
                        for r in t.get("data", []):
                            if r and r[0].strip() == "發行量加權股價指數":
                                return d, float(str(r[1]).replace(",", ""))
            except Exception:
                pass
        d -= timedelta(days=1)
    return None, None


def night_futures():
    """Last night's TAIEX futures (台指期夜盤) move vs the previous day session close. Real-time source: TAIFEX."""
    try:
        req = urllib.request.Request("https://mis.taifex.com.tw/futures/api/getQuoteList", method="POST", headers={**UA, "Content-Type": "application/json"},
                                     data=json.dumps({"MarketType": "1", "SymbolType": "F", "KindID": "1", "CID": "TXF", "ExpireMonth": "",
                                                      "RowSize": "全部", "PageNo": "", "SortColumn": "", "AscDesc": "A"}).encode())
        q = json.loads(urllib.request.urlopen(req, timeout=30).read())["RtData"]["QuoteList"]
        q = [r for r in q if r["SymbolID"].startswith("TXF") and not r["SymbolID"].endswith("-S") and r.get("CLastPrice")]
        r = max(q, key=lambda r: float(r.get("CTotalVolume") or 0))
        last, ref = float(r["CLastPrice"]), float(r["CRefPrice"])
        return last / ref - 1, {"contract": r["DispCName"], "last": last, "ref": ref, "date": r["CDate"], "time": r["CTime"]}
    except Exception as e:
        print("night futures unavailable:", e, file=sys.stderr)
        return None, None


def live_inputs(today):
    x, info = {}, {}
    for k, s in US.items():
        r = yahoo(s)
        c = [v for v in r["indicators"]["quote"][0]["close"]]
        if c[-1] is None:
            c[-1] = r["meta"].get("regularMarketPrice")
        c = [v for v in c if v is not None]
        x[k] = c[-1] / c[-2] - 1
        info[k] = x[k]
    for k, s in ASIA.items():
        r = yahoo(s, "5d", "5m")
        ts, cl = r["timestamp"], r["indicators"]["quote"][0]["close"]
        today_pts = [(t, v) for t, v in zip(ts, cl) if v is not None and datetime.fromtimestamp(t, TPE).date() == today]
        prev_pts = [(t, v) for t, v in zip(ts, cl) if v is not None and datetime.fromtimestamp(t, TPE).date() < today]
        if not today_pts or not prev_pts:
            x[k] = 0.0  # market closed today (holiday)
            info[k] = None
            continue
        x[k] = today_pts[-1][1] / prev_pts[-1][1] - 1
        info[k] = x[k]
    return x, info


def score_yesterday(log_rows):
    """Fill in what really happened for earlier forecasts."""
    tw = yahoo("^TWII", "1mo")
    q, off = tw["indicators"]["quote"][0], tw["meta"].get("gmtoffset", 28800)
    bars = {datetime.fromtimestamp(t + off, timezone.utc).strftime("%Y-%m-%d"): (q["open"][i], q["high"][i], q["low"][i], q["close"][i])
            for i, t in enumerate(tw["timestamp"])}
    for r in log_rows:
        if r.get("actual_close") or r["date"] not in bars:
            continue
        o, h, l, c = bars[r["date"]]
        now = datetime.now(TPE)
        if None in (o, h, l, c) or r["date"] > now.strftime("%Y-%m-%d") or (r["date"] == now.strftime("%Y-%m-%d") and now.hour < 14):
            continue  # today's result only after the market closes (13:30)
        pc = float(r["prev_close"])
        r.update({"actual_open": round(o, 2), "actual_high": round(h, 2), "actual_low": round(l, 2), "actual_close": round(c, 2),
                  "open_right": int((o > pc) == (float(r["pred_open"]) > pc)), "close_right": int((c > pc) == (float(r["pred_close"]) > pc))})


def score_only():
    """Evening run: mark today's forecast right/wrong and update the scorecard (no new forecast)."""
    path = os.path.join(DATA, "forecast_log.csv")
    txt = vault.read(path)
    if not txt:
        print("no forecasts yet")
        return
    rows = list(csv.DictReader(io.StringIO(txt.lstrip("\ufeff"))))
    score_yesterday(rows)
    fields = ["date", "made_at", "prev_close", "pred_open", "pred_close", "pred_high", "pred_low",
              "actual_open", "actual_high", "actual_low", "actual_close", "open_right", "close_right"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    for r in sorted(rows, key=lambda r: r["date"]):
        w.writerow({k: r.get(k, "") for k in fields})
    vault.write(path, buf.getvalue())
    done = [r for r in rows if r.get("actual_close")]
    fj = vault.read(os.path.join(DATA, "report", "forecast.json"))
    if fj:
        res = json.loads(fj)
        res["track_record"] = {"days": len(done), "open_right": sum(int(r["open_right"]) for r in done),
                               "close_right": sum(int(r["close_right"]) for r in done), "last": done[-1] if done else None}
        vault.write(os.path.join(DATA, "report", "forecast.json"), json.dumps(res, ensure_ascii=False, default=str))
    print("scored", len(done), "days")


def main():
    if "--score" in sys.argv:
        return score_only()
    now = datetime.now(TPE)
    today = now.date()
    df = history()
    x, info = live_inputs(today)
    fut, fut_info = night_futures()
    global FEATS
    if fut is not None and "fut" in df.columns and fut_info and fut_info["date"] >= (today - timedelta(days=4)).strftime("%Y%m%d"):
        FEATS = FEATS + ["fut"]
        x["fut"], info["fut"] = fut, fut
        df = df.dropna(subset=["fut"])
    bt = backtest(df)
    coefs = {t: fit(df, t) for t in ["open_gap", "close_ret", "high_ret", "low_ret"]}
    pdate, pclose = taiex_prev_close(today)
    pred = {t: predict(coefs[t], x) for t in coefs}
    pred["high_ret"] = max(pred["high_ret"], pred["open_gap"], pred["close_ret"])
    pred["low_ret"] = min(pred["low_ret"], pred["open_gap"], pred["close_ret"])
    # panic / rally mode: big overnight moves -> real day is often bigger than the model says, so widen the range
    big = abs(x.get("fut", 0)) > 0.015 or any(abs(v) > 0.03 for k, v in x.items() if k != "fut")
    if big:
        pred["high_ret"] += bt["high_ret"]["mae"]
        pred["low_ret"] -= bt["low_ret"]["mae"]
    res = {"date": str(today), "made_at": now.strftime("%H:%M"), "prev_close_date": str(pdate), "prev_close": pclose,
           "inputs": info, "pred": pred, "backtest": bt, "panic": bool(big), "night_futures": fut_info, "uses_futures": "fut" in FEATS,
           "levels": {k: round(pclose * (1 + v), 0) for k, v in pred.items()} if pclose else {}}
    if not os.environ.get("DATA_KEY"):
        # no key (e.g. the phone-message task): just print today's forecast, save nothing private
        res["note"] = "no DATA_KEY: printed only, scorecard is in the app"
        print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
        return
    # log + score
    path = os.path.join(DATA, "forecast_log.csv")
    fields = ["date", "made_at", "prev_close", "pred_open", "pred_close", "pred_high", "pred_low",
              "actual_open", "actual_high", "actual_low", "actual_close", "open_right", "close_right"]
    txt = vault.read(path)
    rows = list(csv.DictReader(io.StringIO(txt.lstrip("\ufeff")))) if txt else []
    rows = [r for r in rows if r["date"] != str(today)]
    if pclose:
        rows.append({"date": str(today), "made_at": res["made_at"], "prev_close": pclose,
                     "pred_open": res["levels"]["open_gap"], "pred_close": res["levels"]["close_ret"],
                     "pred_high": res["levels"]["high_ret"], "pred_low": res["levels"]["low_ret"]})
    try:
        score_yesterday(rows)
    except Exception as e:
        print("score error", e, file=sys.stderr)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    for r in sorted(rows, key=lambda r: r["date"]):
        w.writerow({k: r.get(k, "") for k in fields})
    vault.write(path, buf.getvalue())
    done = [r for r in rows if r.get("actual_close")]
    res["track_record"] = {"days": len(done), "open_right": sum(int(r["open_right"]) for r in done),
                           "close_right": sum(int(r["close_right"]) for r in done),
                           "last": done[-1] if done else None}
    os.makedirs(os.path.join(DATA, "report"), exist_ok=True)
    vault.write(os.path.join(DATA, "report", "forecast.json"), json.dumps(res, ensure_ascii=False, default=str))
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
