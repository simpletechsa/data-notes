"""Builds the daily HTML report: full English section first, then full Traditional Chinese section.
每日報告：先完整英文，再完整繁體中文。"""
import html

CSS = """
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,"Segoe UI","Microsoft JhengHei","PingFang TC",sans-serif;
background:#0b1020;color:#e6e9f2;line-height:1.5}
.wrap{max-width:1100px;margin:auto;padding:24px 18px 60px}
h1{font-size:28px;margin:0 0 4px}h2{font-size:20px;margin:34px 0 12px;padding-left:10px;border-left:4px solid #6c8cff}
.sub{color:#9aa3b8;font-size:14px}.lang{margin-top:40px;padding-top:10px;border-top:2px dashed #2a3350}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.card{background:linear-gradient(160deg,#151c33,#10162a);border:1px solid #232c48;border-radius:14px;padding:16px}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}
.code{font-size:13px;color:#9aa3b8}.name{font-size:20px;font-weight:700}
.pct{font-size:30px;font-weight:800;color:#4ade80;text-align:right;line-height:1}.pct small{display:block;font-size:11px;color:#9aa3b8;font-weight:400}
.pct.mid{color:#facc15}.pct.low{color:#f87171}
.tag{display:inline-block;font-size:12px;padding:2px 8px;border-radius:99px;background:#24305a;color:#c7d2fe;margin:6px 0}
.plan{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:10px 0;font-size:13px}
.plan div{background:#0d1326;border-radius:8px;padding:6px;text-align:center}.plan b{display:block;font-size:15px}
ul{margin:6px 0 0;padding-left:18px;font-size:14px}li{margin:2px 0}
.hot{color:#fb923c}.warm{color:#facc15}.cold{color:#60a5fa}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:7px 8px;border-bottom:1px solid #1f2742;text-align:left}
th{color:#9aa3b8;font-weight:600}td.num{text-align:right;font-variant-numeric:tabular-nums}
.pos{color:#4ade80}.neg{color:#f87171}
.box{background:#121a35;border:1px solid #2b3a6b;border-radius:14px;padding:14px 16px;font-size:14px}
.big3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:10px}
h1{padding-right:120px}.big3 div{background:#0d1326;border-radius:10px;padding:10px;text-align:center}.big3 b{display:block;font-size:24px}
.warn{color:#fbbf24}.foot{color:#7c859c;font-size:12px;margin-top:30px}
"""

T = {
    "en": {
        "title": "TW Stock Lab — Daily Report", "date": "Data as of", "disc": "Analysis only — not investment advice. Percentages come from backtesting past data; the future can be different.",
        "honest": "How good is this? (honest backtest)", "h_txt": "Tested on data the model never saw",
        "h_rule": "Win rate of our picks", "h_base": "Win rate of an average stock", "h_ret": "Average result per trade (after fees)",
        "h_note": "\"Win\" = price hits +{t}% before falling −{s}% within {d} trading days.",
        "sectors": "Sector heat — where the money is", "hot3": "🔥 Hottest right now",
        "grp": "Group", "lvl": "Level", "heat": "Heat", "r5": "5-day", "r20": "1-month", "breadth": "Stocks in uptrend", "buzz": "News",
        "lv": {"hot": "🔥 Hot", "warm": "🟡 Warm", "cold": "❄️ Cold"},
        "picks": "Top 5 buy ideas (only from hot sectors)", "chance": "chance", "buy": "Buy around", "target": "Sell target", "stop": "Stop-loss",
        "sellby": "Sell after {d} trading days at the latest if neither target nor stop is hit.",
        "why": "Why", "radar": "📡 Radar — unusual signals (any sector)", "flags": "Signals",
        "noradar": "No strong unusual signals today.", "paper": "Paper trading (fake money) — is the app right?",
        "p_closed": "Closed trades", "p_win": "Wins", "p_avg": "Avg result", "p_open": "Open positions",
        "p_none": "No closed trades yet — results will appear as trades finish (up to 4 weeks).",
        "p_cols": ["Stock", "Bought", "Price", "Now", "Target", "Stop", "Status"],
        "status": {"open": "Holding", "pending": "Buy at next open", "win": "Win", "loss": "Stopped out", "timeout": "Time exit"},
        "ai": "🤖 AI focus — always tracked", "ai_sub": "Sub-groups", "ai_picks": "Best AI ideas today (even if AI is cold)",
        "ai_all": "All AI stocks", "ai_cols": ["Stock", "Group", "Price", "5-day", "1-month", "Chance", "Institutions", "Trend"],
        "sig": {"strong": "🟢 Uptrend + buying", "neutral": "⚪ Mixed", "weak": "🔴 Downtrend"}, "days": "days buying",
        "ai_test": "AI backtest: top 3 AI picks won {w:.0f}% vs {b:.0f}% for an average AI stock ({r:+.1f}% per trade after fees).",
        "ai_cold": "AI is cold right now — ideas below are for watching, the whole group is weak.",
        "sell_alert": "SELL", "foot": "Generated", "nopicks": "No stock in the hot sectors passed the filters today.",
        "noedge": "⚠ NO PROVEN EDGE YET. In testing, these picks did about the same as a random stock (win {w:.0f}% vs {b:.0f}%, avg {r:+.1f}% per trade after fees). Treat the list below as a WATCH LIST, not buy signals, until the backtest improves.",
    },
    "zh": {
        "title": "台股研究室 — 每日報告", "date": "資料日期", "disc": "僅供分析，不是投資建議。機率來自歷史資料回測，未來可能不同。",
        "honest": "準不準？（誠實回測）", "h_txt": "用模型沒看過的資料測試",
        "h_rule": "我們選股的勝率", "h_base": "一般股票的勝率", "h_ret": "每筆平均結果（已扣手續費與稅）",
        "h_note": "「勝」＝ {d} 個交易日內先漲 +{t}%，且沒有先跌 −{s}%。",
        "sectors": "產業熱度 — 資金在哪裡", "hot3": "🔥 目前最熱",
        "grp": "類別", "lvl": "熱度等級", "heat": "熱度", "r5": "5日", "r20": "1個月", "breadth": "上漲趨勢比例", "buzz": "新聞",
        "lv": {"hot": "🔥 熱門", "warm": "🟡 普通", "cold": "❄️ 冷門"},
        "picks": "前 5 名買進建議（只從熱門產業挑）", "chance": "機率", "buy": "買進價位", "target": "目標賣價", "stop": "停損價",
        "sellby": "若 {d} 個交易日內沒到目標也沒停損，就賣出。",
        "why": "理由", "radar": "📡 雷達 — 異常訊號（任何產業）", "flags": "訊號",
        "noradar": "今天沒有明顯的異常訊號。", "paper": "模擬交易（假錢）— 系統到底準不準？",
        "p_closed": "已結束交易", "p_win": "獲利", "p_avg": "平均結果", "p_open": "持有中",
        "p_none": "還沒有結束的交易 — 交易結束後（最多 4 週）就會顯示結果。",
        "p_cols": ["股票", "買進日", "買進價", "現價", "目標", "停損", "狀態"],
        "status": {"open": "持有中", "pending": "下個交易日開盤買進", "win": "達標獲利", "loss": "停損", "timeout": "到期賣出"},
        "ai": "🤖 AI 專區 — 一律追蹤", "ai_sub": "子題材", "ai_picks": "今天最好的 AI 標的（即使 AI 偏冷）",
        "ai_all": "全部 AI 股票", "ai_cols": ["股票", "題材", "股價", "5日", "1個月", "機率", "法人", "趨勢"],
        "sig": {"strong": "🟢 上升＋法人買", "neutral": "⚪ 混合", "weak": "🔴 下降趨勢"}, "days": "天買超",
        "ai_test": "AI 回測：每天前 3 名 AI 股勝率 {w:.0f}%，一般 AI 股 {b:.0f}%（扣費後每筆 {r:+.1f}%）。",
        "ai_cold": "AI 目前偏冷 — 以下僅供觀察，整個族群都弱。",
        "sell_alert": "賣出", "foot": "產生時間", "nopicks": "今天熱門產業中沒有股票通過篩選。",
        "noedge": "⚠ 尚未證明有效。測試中，這些選股的表現和隨便挑一檔差不多（勝率 {w:.0f}% 對 {b:.0f}%，扣費後每筆平均 {r:+.1f}%）。在回測改善之前，下面的清單請當作「觀察名單」，不是買進訊號。",
    },
}


def e(s):
    return html.escape(str(s))


def pct(x, sign=True):
    if x is None:
        return "–"
    v = x * 100
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<span class="{cls}">{v:+.1f}%</span>' if sign else f"{v:.0f}%"


def section(d, L):
    t = T[L]
    s, st = [], d["stats"]
    s.append(f'<h1>{e(t["title"])}</h1><div class="sub">{e(t["date"])} {e(d["date"])} · {e(t["disc"])}</div>')
    if st["rule_ret"] <= 0 or st["auc"] < 0.53 or st["rule_win"] - st["base_win"] < 0.03:
        s.append(f'<div class="box" style="border-color:#b45309;background:#2a1d0b;margin-top:16px"><b class="warn">'
                 f'{e(t["noedge"].format(w=st["rule_win"]*100, b=st["base_win"]*100, r=st["rule_ret"]*100))}</b></div>')
    # honesty box
    s.append(f'<h2>{e(t["honest"])}</h2><div class="box">{e(t["h_txt"])}: {e(st["test_from"])} → {e(st["test_to"])}'
             f'<div class="big3"><div>{e(t["h_rule"])}<b class="pos">{st["rule_win"]*100:.0f}%</b></div>'
             f'<div>{e(t["h_base"])}<b>{st["base_win"]*100:.0f}%</b></div>'
             f'<div>{e(t["h_ret"])}<b>{pct(st["rule_ret"])}</b></div></div>'
             f'<div class="sub" style="margin-top:8px">{e(t["h_note"].format(t=int(d["plan"]["target"]*100), s=int(d["plan"]["stop"]*100), d=d["plan"]["max_days"]))}</div></div>')
    # sectors
    s.append(f'<h2>{e(t["sectors"])}</h2>')
    hot = [h for h in d["heat"] if h["level"] == "hot"]
    s.append('<div class="grid">' + "".join(
        f'<div class="card"><div class="tag hot">{e(t["lv"]["hot"])}</div><div class="name">{e(h[L])}</div>'
        f'<div class="plan"><div>{e(t["r5"])}<b>{pct(h["r5"])}</b></div><div>{e(t["r20"])}<b>{pct(h["r20"])}</b></div>'
        f'<div>{e(t["breadth"])}<b>{h["breadth"]*100:.0f}%</b></div></div></div>' for h in hot) + "</div>")
    rows = d["heat"][:12] + [h for h in d["heat"][-5:] if h not in d["heat"][:12]]
    s.append(f'<div class="card" style="margin-top:14px"><table><tr><th>{e(t["grp"])}</th><th>{e(t["lvl"])}</th><th class="num">{e(t["heat"])}</th>'
             f'<th class="num">{e(t["r5"])}</th><th class="num">{e(t["r20"])}</th><th class="num">{e(t["breadth"])}</th><th class="num">{e(t["buzz"])}</th></tr>'
             + "".join(f'<tr><td>{e(h[L])}</td><td class="{h["level"]}">{e(t["lv"][h["level"]])}</td><td class="num">{h["heat"]:.0f}</td>'
                       f'<td class="num">{pct(h["r5"])}</td><td class="num">{pct(h["r20"])}</td><td class="num">{h["breadth"]*100:.0f}%</td>'
                       f'<td class="num">{h["buzz"]}</td></tr>' for h in rows) + "</table></div>")
    # picks

    def card(p, extra=""):
        cls = "" if p["prob"] >= 0.45 else ("mid" if p["prob"] >= 0.35 else "low")
        why = p["why_" + L]
        return (f'<div class="card"><div class="top"><div><div class="code">{e(p["code"])} · {e(p["industry"])}</div>'
                f'<div class="name">{e(p["name"])}</div><div class="tag">{e(p["group_" + L] or p["group_zh"])}</div></div>'
                f'<div class="pct {cls}">{p["prob"]*100:.0f}%<small>{e(t["chance"])}</small></div></div>'
                f'<div class="plan"><div>{e(t["buy"])}<b>{p["price"]}</b></div><div>{e(t["target"])}<b class="pos">{p["target"]}</b></div>'
                f'<div>{e(t["stop"])}<b class="neg">{p["stop"]}</b></div></div>{extra}'
                + (f'<b style="font-size:13px">{e(t["why"])}</b><ul>' + "".join(f"<li>{e(w)}</li>" for w in why) + "</ul>" if why else "")
                + "</div>")
    s.append(f'<h2>{e(t["picks"])}</h2>')
    s.append('<div class="grid">' + "".join(card(p) for p in d["picks"]) + "</div>" if d["picks"] else f'<div class="box">{e(t["nopicks"])}</div>')
    s.append(f'<div class="sub" style="margin-top:8px">{e(t["sellby"].format(d=d["plan"]["max_days"]))}</div>')
    # AI focus
    fo = d.get("focus")
    if fo:
        s.append(f'<h2>{e(t["ai"])}</h2>')
        if fo.get("avg_heat") is not None and fo["avg_heat"] < 45:
            s.append(f'<div class="box" style="border-color:#1d4ed8"><b class="cold">{e(t["ai_cold"])}</b></div>')
        if "ai_rule_win" in st:
            s.append(f'<div class="sub" style="margin:8px 0">{e(t["ai_test"].format(w=st["ai_rule_win"]*100, b=st["ai_base_win"]*100, r=st["ai_rule_ret"]*100))}</div>')
        s.append(f'<div class="card"><b>{e(t["ai_sub"])}</b><table><tr><th>{e(t["grp"])}</th><th>{e(t["lvl"])}</th><th class="num">{e(t["heat"])}</th>'
                 f'<th class="num">{e(t["r5"])}</th><th class="num">{e(t["r20"])}</th><th class="num">{e(t["breadth"])}</th></tr>'
                 + "".join(f'<tr><td>{e(h[L])}</td><td class="{h["level"]}">{e(t["lv"][h["level"]])}</td><td class="num">{h["heat"]:.0f}</td>'
                           f'<td class="num">{pct(h["r5"])}</td><td class="num">{pct(h["r20"])}</td><td class="num">{h["breadth"]*100:.0f}%</td></tr>'
                           for h in fo["heat"]) + "</table></div>")
        if fo["picks"]:
            s.append(f'<h3 style="margin:18px 0 10px">{e(t["ai_picks"])}</h3><div class="grid">' + "".join(card(p) for p in fo["picks"]) + "</div>")
        s.append(f'<div class="card" style="margin-top:14px;overflow-x:auto"><b>{e(t["ai_all"])}</b><table><tr>' + "".join(f"<th>{e(c)}</th>" for c in t["ai_cols"]) + "</tr>"
                 + "".join(f'<tr><td>{e(x["code"])} {e(x["name"])}</td><td>{e(x["group_" + L] or x["group_zh"])}</td><td class="num">{x["price"]}</td>'
                           f'<td class="num">{pct(x["r5"])}</td><td class="num">{pct(x["r20"])}</td><td class="num">{x["prob"]*100:.0f}%</td>'
                           f'<td>{(str(x["streak"]) + " " + t["days"]) if x["streak"] else "–"}</td><td>{e(t["sig"][x["signal"]])}</td></tr>'
                           for x in fo["stocks"]) + "</table></div>")
    # radar
    s.append(f'<h2>{e(t["radar"])}</h2>')
    if d["radar"]:
        s.append('<div class="grid">' + "".join(
            card(p, f'<b style="font-size:13px" class="warn">{e(t["flags"])}</b><ul>' + "".join(f"<li>{e(x)}</li>" for x in p["flags_" + L]) + "</ul>")
            for p in d["radar"]) + "</div>")
    else:
        s.append(f'<div class="box">{e(t["noradar"])}</div>')
    # paper trading
    pp = d["paper"]
    s.append(f'<h2>{e(t["paper"])}</h2><div class="box">')
    if pp["closed"]:
        s.append(f'<div class="big3"><div>{e(t["p_closed"])}<b>{pp["closed"]}</b></div><div>{e(t["p_win"])}<b>{pp["wins"]} ({pp["wins"]/pp["closed"]*100:.0f}%)</b></div>'
                 f'<div>{e(t["p_avg"])}<b>{pct((pp["avg_ret"] or 0)/100)}</b></div></div>')
    else:
        s.append(e(t["p_none"]))
    s.append("</div>")
    if pp["open"]:
        s.append(f'<div class="card" style="margin-top:12px"><b>{e(t["p_open"])}</b><table><tr>' + "".join(f"<th>{e(c)}</th>" for c in t["p_cols"]) + "</tr>"
                 + "".join(f'<tr><td>{e(o["code"])} {e(o["name"])}</td><td>{e(o.get("entry_date",""))}</td><td>{e(o.get("entry_price",""))}</td>'
                           f'<td>{pct(float(o["return_pct"])/100) if str(o.get("return_pct","")) not in ("","nan") else "–"}</td><td>{e(o.get("target",""))}</td><td>{e(o.get("stop",""))}</td>'
                           f'<td>{e(t["status"].get(o["status"], o["status"]))}</td></tr>' for o in pp["open"]) + "</table></div>")
    return "\n".join(s)


TOGGLE_CSS = """
.switch{position:fixed;top:14px;right:14px;z-index:9;display:flex;background:#151c33;border:1px solid #2b3a6b;border-radius:99px;padding:3px;box-shadow:0 4px 18px #0008}
.switch button{border:0;background:none;color:#9aa3b8;font-size:14px;font-weight:600;padding:6px 14px;border-radius:99px;cursor:pointer;font-family:inherit}
.switch button.on{background:#6c8cff;color:#fff}
body[data-lang=en] #zh, body[data-lang=zh] #en{display:none}
"""
TOGGLE_JS = """
function setLang(l){document.body.dataset.lang=l;document.documentElement.lang=l==='zh'?'zh-Hant':'en';
document.querySelectorAll('.switch button').forEach(b=>b.classList.toggle('on',b.dataset.l===l));
try{localStorage.setItem('twlab-lang',l)}catch(e){}}
var saved='en';try{saved=localStorage.getItem('twlab-lang')||'en'}catch(e){}
setLang(saved);
"""


def render(d):
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>TW Stock Lab {e(d["date"])}</title><style>{CSS}{TOGGLE_CSS}</style></head><body data-lang="en">'
            f'<div class="switch"><button data-l="en" onclick="setLang(\'en\')">EN</button>'
            f'<button data-l="zh" onclick="setLang(\'zh\')">繁中</button></div><div class="wrap">'
            f'<div id="en">{section(d, "en")}<div class="foot">{e(T["en"]["foot"])}: {e(d["generated_tpe"])} (Taipei)</div></div>'
            f'<div id="zh">{section(d, "zh")}<div class="foot">{e(T["zh"]["foot"])}：{e(d["generated_tpe"])}（台北時間）</div></div>'
            f'</div><script>{TOGGLE_JS}</script></body></html>')
