#!/usr/bin/env python3
"""Backfill 2 years of per-stock institutional flow (三大法人, listed stocks) from TWSE T86.
Resumable: skips days already saved. 補 2 年法人歷史（可中斷續跑）。"""
import csv, gzip, json, os, sys, time, urllib.request
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "market", "flow_history.csv.gz")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
F, T, D = "外陸資買賣超股數(不含外資自營商)", "投信買賣超股數", "自營商買賣超股數"


def num(s):
    try:
        return int(float(str(s).replace(",", "")))
    except Exception:
        return 0


def main(start, end):
    have, rows = set(), []
    if os.path.exists(OUT):
        with gzip.open(OUT, "rt", encoding="utf-8") as f:
            for r in csv.reader(f):
                if r[0] != "date":
                    rows.append(r)
                    have.add(r[0])
    with open(os.path.join(ROOT, "data", "market", "trading_days.txt")) as f:
        days = [d.strip() for d in f if start <= d.strip() <= end and d.strip() not in have]
    print("to fetch:", len(days), flush=True)
    fails = 0
    for i, d in enumerate(days):
        ds = d.replace("-", "")
        try:
            j = json.loads(urllib.request.urlopen(urllib.request.Request(
                f"https://www.twse.com.tw/rwd/zh/fund/T86?date={ds}&selectType=ALLBUT0999&response=json", headers=UA), timeout=30).read())
            if j.get("stat") == "OK":
                ix = {n: k for k, n in enumerate(j["fields"])}
                for r in j["data"]:
                    c = r[0].strip()
                    if len(c) == 4 and c.isdigit():
                        rows.append([d, c, num(r[ix[F]]) if F in ix else 0, num(r[ix[T]]) if T in ix else 0, num(r[ix[D]]) if D in ix else 0])
            fails = 0
        except Exception as e:
            fails += 1
            print(d, "fail", e, flush=True)
            time.sleep(20 * fails)  # TWSE throttling: back off
        if i % 25 == 0 or i == len(days) - 1:
            rows.sort()
            with gzip.open(OUT, "wt", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["date", "code", "foreign_net", "trust_net", "dealer_net"])
                w.writerows(rows)
            print(i + 1, "/", len(days), d, flush=True)
        time.sleep(3.2)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
