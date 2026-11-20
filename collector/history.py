#!/usr/bin/env python3
"""One-time (or occasional) download of 2 years of daily prices for ALL TW stocks from Yahoo.
全部台股 2 年日線歷史（一次性下載）。 Output: data/market/history.csv.gz"""
import csv, gzip, json, os, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MK = os.path.join(ROOT, "data", "market")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def one(row, rng="2y"):
    sym = f"{row['code']}.{row['market']}"
    for i in range(3):
        try:
            d = json.loads(urllib.request.urlopen(urllib.request.Request(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d", headers=UA), timeout=30).read())
            r = d["chart"]["result"][0]
            q = r["indicators"]["quote"][0]
            off = r["meta"].get("gmtoffset", 28800)
            out = []
            for k, t in enumerate(r.get("timestamp") or []):
                c = q["close"][k]
                if c is None and k == len(r["timestamp"]) - 1:
                    c = r["meta"].get("regularMarketPrice")  # newest bar often blank on Yahoo
                if c is None or not q["volume"][k]:
                    continue
                out.append((datetime.fromtimestamp(t + off, timezone.utc).strftime("%Y-%m-%d"), row["code"],
                            round(q["open"][k] or c, 2), round(q["high"][k] or c, 2), round(q["low"][k] or c, 2),
                            round(c, 2), int(q["volume"][k])))
            return out
        except Exception:
            time.sleep(2 * (i + 1))
    return []


def main(rng="2y"):
    with open(os.path.join(MK, "universe.csv"), encoding="utf-8-sig") as f:
        uni = list(csv.DictReader(f))
    rows, done = [], 0
    with ThreadPoolExecutor(4) as ex:
        for res in ex.map(lambda r: one(r, rng), uni):
            rows += res
            done += 1
            if done % 200 == 0:
                print(done, len(rows), flush=True)
    rows.sort()
    with gzip.open(os.path.join(MK, "history.csv.gz"), "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "code", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    print("saved", len(rows), "rows,", len({r[1] for r in rows}), "stocks")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "2y")
