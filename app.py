import json
import math
import os
import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, redirect, render_template, request

import scanner

IST = ZoneInfo("Asia/Kolkata")
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "scan_cache.json")
REFRESH_HOURS = float(os.environ.get("REFRESH_HOURS", "6"))   # auto-rescan when results are older than this
COOLDOWN_MIN = float(os.environ.get("COOLDOWN_MIN", "30"))    # minimum gap between scans

app = Flask(__name__)
lock = threading.Lock()
state = {"status": "idle", "progress": "", "error": None, "rows": [], "updated": None, "data_date": None,
         "provisional": False, "info": {}, "last_attempt": None}


def now():
    return datetime.now(IST)


def market_open(t):
    return t.weekday() < 5 and (9, 15) <= (t.hour, t.minute) < (15, 30)


def load_cache():
    try:
        with open(CACHE) as f:
            state.update(json.load(f))
    except Exception:
        pass


def save_cache():
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    keys = ["rows", "updated", "data_date", "provisional", "info"]
    with open(CACHE, "w") as f:
        json.dump({k: state[k] for k in keys}, f)


def minutes_since(iso):
    return (now() - datetime.fromisoformat(iso)).total_seconds() / 60


def needs_refresh():
    if state["last_attempt"] and minutes_since(state["last_attempt"]) < 10:
        return False
    if not state["updated"]:
        return True
    if minutes_since(state["updated"]) > REFRESH_HOURS * 60:
        return True
    return bool(state["provisional"] and not market_open(now()))   # re-scan once the day's candle is final


def start_scan():
    with lock:
        if state["status"] == "scanning":
            return False
        state.update(status="scanning", progress="starting", error=None, last_attempt=now().isoformat())
    threading.Thread(target=_run, daemon=True).start()
    return True


def _run():
    try:
        rows, info = scanner.run_scan(lambda d, t: state.update(progress=f"{d} of {t} stocks checked"))
        t = now()
        with lock:
            state.update(rows=rows, info=info, updated=t.isoformat(), data_date=info["data_date"],
                         provisional=market_open(t))
        save_cache()
    except Exception as e:
        state["error"] = str(e)
    finally:
        state.update(status="idle", progress="")


def num(name, default, lo=0.0, hi=None):
    raw = request.args.get(name, "").strip()
    try:
        v = float(raw)
    except ValueError:
        return default
    if not math.isfinite(v):
        return default
    return min(max(v, lo), hi) if hi is not None else max(v, lo)


@app.route("/")
def index():
    if needs_refresh():
        start_scan()
    f = {"group": request.args.get("group", "All"), "show": request.args.get("show", "both"),
         "min_price": num("min_price", 5.0), "max_price": num("max_price", None),
         "min_turnover": num("min_turnover", 1.0), "max_risk": num("max_risk", 12.0, 0.1, 100),
         "recent": int(num("recent", 0, 0, scanner.MAX_BUY_AGE)),
         "capital": num("capital", 100000.0, 1000), "risk": num("risk", 1.0, 0.1, 5)}
    rows = []
    for r in state["rows"]:
        if f["group"] != "All" and r["group"] != f["group"]:
            continue
        if f["show"] in ("BUY", "NEAR") and r["status"] != f["show"]:
            continue
        if r["status"] == "BUY" and r["days_ago"] > f["recent"]:
            continue
        if r["close"] < f["min_price"] or (f["max_price"] and r["close"] > f["max_price"]):
            continue
        if r["turnover_cr"] < f["min_turnover"] or r["risk_pct"] > f["max_risk"]:
            continue
        qty = math.floor(f["capital"] * f["risk"] / 100 / r["risk_ps"])
        qty = min(qty, math.floor(f["capital"] / r["close"]))
        rows.append({**r, "qty": qty if r["status"] == "BUY" else qty // 2})
    rows.sort(key=lambda r: (r["status"] != "BUY", -r["filters_ok"], -r["adx"]))
    info = state["info"]
    last = state["last_attempt"]
    return render_template(
        "index.html", rows=rows, f=f, groups=["All"] + list(info.get("loaded", {})), failed=info.get("failed", []),
        scanning=state["status"] == "scanning", progress=state["progress"], error=state["error"],
        provisional=state["provisional"], scanned=info.get("scanned", 0), total=len(state["rows"]),
        buys=sum(r["status"] == "BUY" for r in rows), nears=sum(r["status"] == "NEAR" for r in rows),
        updated=datetime.fromisoformat(state["updated"]).astimezone(IST).strftime("%d %b %Y, %I:%M %p IST") if state["updated"] else None,
        data_date=date.fromisoformat(state["data_date"]).strftime("%d %b %Y") if state["data_date"] else None,
        can_refresh=state["status"] != "scanning" and (not last or minutes_since(last) >= COOLDOWN_MIN),
        cooldown=int(COOLDOWN_MIN))


@app.route("/refresh", methods=["POST"])
def refresh():
    last = state["last_attempt"]
    if not last or minutes_since(last) >= COOLDOWN_MIN:
        start_scan()
    return redirect("/")


@app.route("/healthz")
def healthz():
    return "ok"


load_cache()
if needs_refresh():
    start_scan()
