#!/usr/bin/env python3
"""
Event calendar / 事件行事曆 — warns at 08:40 on the LAST TRADING DAY BEFORE an event hits Taiwan,
so there is still a full session (09:00-13:30) to act.

  same_session : the event lands before/within that Taiwan session (e.g. Fed decision at 03:00, futures settlement)
  next_session : the event happens after Taiwan closes (e.g. US CPI 20:30) -> hits the next Taiwan session
Output: data/report/events.json.enc  {today: [...warnings to act on today], week: [...next 7 trading days]}
"""
import json, os, sys, urllib.request
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vault  # noqa: E402

ROOT = os.path.dirname(HERE)
TPE = timezone(timedelta(hours=8))


def holidays():
    """Taiwan market holidays (TWSE). Entries named '...開始交易日/最後交易日' are trading days, not holidays."""
    days = set()
    try:
        for r in json.loads(urllib.request.urlopen("https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule", timeout=30).read()):
            if "交易日" in r["Name"] and "無交易" not in r["Name"]:
                continue
            ds = r["Date"]
            days.add(date(int(ds[:-4]) + 1911, int(ds[-4:-2]), int(ds[-2:])))
    except Exception as e:
        print("holiday list unavailable:", e, file=sys.stderr)
    cache = os.path.join(ROOT, "data", "market", "holidays.json")
    if days:
        old = set(json.load(open(cache))) if os.path.exists(cache) else set()
        json.dump(sorted(old | {d.isoformat() for d in days}), open(cache, "w"))
    elif os.path.exists(cache):
        days = {date.fromisoformat(d) for d in json.load(open(cache))}
    return days


def is_trading(d, hol):
    return d.weekday() < 5 and d not in hol


def next_trading(d, hol, include=True):
    d = d if include else d + timedelta(days=1)
    while not is_trading(d, hol):
        d += timedelta(days=1)
    return d


def prev_trading(d, hol):
    d -= timedelta(days=1)
    while not is_trading(d, hol):
        d -= timedelta(days=1)
    return d


def generated(start, end):
    """Futures settlement (3rd Wednesday) and TSMC monthly revenue (~10th) for each month."""
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        first = date(y, m, 1)
        wed = first + timedelta(days=(2 - first.weekday()) % 7 + 14)
        out.append({"date": wed.isoformat(), "time_tpe": "13:30", "en": "Futures settlement day (台指期結算) — extra swings",
                    "zh": "台指期結算日 — 波動通常較大", "impact": "same_session", "weight": 2})
        out.append({"date": date(y, m, 10).isoformat(), "time_tpe": "after close", "en": "TSMC monthly revenue (around the 10th)",
                    "zh": "台積電月營收公布（約每月10日）", "impact": "next_session", "weight": 1, "approx": True})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def build(today=None):
    today = today or datetime.now(TPE).date()
    hol = holidays()
    with open(os.path.join(ROOT, "config", "events.json"), encoding="utf-8") as f:
        evs = json.load(f)["events"]
    evs += generated(today - timedelta(days=5), today + timedelta(days=60))
    rows = []
    for e in evs:
        d = date.fromisoformat(e["date"])
        hit = next_trading(d, hol) if e["impact"] == "same_session" else next_trading(d, hol, include=False)
        warn = prev_trading(hit, hol)  # last trading day BEFORE the session that feels it
        rows.append({**e, "hits_session": hit.isoformat(), "warn_on": warn.isoformat()})
    rows.sort(key=lambda r: (r["hits_session"], -r.get("weight", 1)))
    tday = today.isoformat()
    week_end = today
    for _ in range(7):
        week_end = next_trading(week_end, hol, include=False)
    out = {"date": tday, "trading_today": is_trading(today, hol),
           "act_today": [r for r in rows if r["warn_on"] == tday],
           "week": [r for r in rows if tday < r["hits_session"] <= week_end.isoformat()],
           "next_trading_day": next_trading(today, hol, include=False).isoformat()}
    vault.save_or_print(os.path.join(ROOT, "data", "report", "events.json"), json.dumps(out, ensure_ascii=False))
    return out


if __name__ == "__main__":
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    o = build(d)
    print("act today:", [(r["en"], r["hits_session"]) for r in o["act_today"]])
    print("this week:", [(r["date"], r["en"][:40], "-> hits", r["hits_session"], "warn", r["warn_on"]) for r in o["week"]])
