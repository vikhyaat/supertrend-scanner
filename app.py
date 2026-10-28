import json
import math
import os
import threading
from datetime import date, datetime
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, redirect, render_template, request

import scanner

IST = ZoneInfo("Asia/Kolkata")
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "scan_cache.json")
COOLDOWN_MIN = float(os.environ.get("COOLDOWN_MIN", "5"))   # gap required after a successful scan
STUCK_MIN = 25                                              # a scan older than this is treated as dead

app = Flask(__name__)
lock = threading.Lock()
state = {"status": "idle", "stage": "", "done": 0, "total": 0, "error": None, "rows": [], "updated": None,
         "data_date": None, "provisional": False, "info": {}, "log": [], "started": None, "scan_id": 0}


def now():
    return datetime.now(IST)


def minutes_since(iso):
    return (now() - datetime.fromisoformat(iso)).total_seconds() / 60


def market_open(t):
    return t.weekday() < 5 and (9, 15) <= (t.hour, t.minute) < (15, 30)


def is_scanning():
    return state["status"] == "scanning" and minutes_since(state["started"]) < STUCK_MIN


def can_scan():
    if is_scanning():
        return False
    return bool(state["error"]) or not state["updated"] or minutes_since(state["updated"]) >= COOLDOWN_MIN


def load_cache():
    try:
        with open(CACHE) as f:
            state.update(json.load(f))
    except Exception:
        pass


def save_cache():
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as f:
        json.dump({k: state[k] for k in ("rows", "updated", "data_date", "provisional", "info")}, f)


def start_scan():
    with lock:
        if is_scanning():
            return False
        state["scan_id"] += 1
        state.update(status="scanning", stage="Starting", done=0, total=0, error=None, log=[],
                     started=now().isoformat())
        sid = state["scan_id"]
    threading.Thread(target=_run, args=(sid,), daemon=True).start()
    return True


def _run(sid):
    def live():
        return state["scan_id"] == sid

    def progress(stage, done=0, total=0):
        if live():
            state.update(stage=stage, done=done, total=total)

    def log(msg):
        if live():
            state["log"] = (state["log"] + [f"{now():%H:%M:%S}  {msg}"])[-40:]

    try:
        rows, info = scanner.run_scan(progress, log)
        if live():
            t = now()
            with lock:
                state.update(rows=rows, info=info, updated=t.isoformat(), data_date=info["data_date"],
                             provisional=market_open(t))
            save_cache()
            log(f"Finished: {len(rows)} setups found")
    except Exception as e:
        log(f"Failed: {e}")
        if live():
            state["error"] = str(e)
    finally:
        if live():
            state.update(status="idle", stage="")


def num(name, default, lo=0.0, hi=None):
    try:
        v = float(request.args.get(name, "").strip())
    except ValueError:
        return default
    if not math.isfinite(v):
        return default
    return min(max(v, lo), hi) if hi is not None else max(v, lo)


@app.route("/")
def index():
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
    updated = state["updated"]
    return render_template(
        "index.html", rows=rows, f=f, groups=["All"] + list(info.get("loaded", {})), failed=info.get("failed", []),
        scanning=is_scanning(), stage=state["stage"], done=state["done"], total=state["total"],
        error=state["error"], log=state["log"], provisional=state["provisional"], scanned=info.get("scanned", 0),
        buys=sum(r["status"] == "BUY" for r in rows), nears=sum(r["status"] == "NEAR" for r in rows),
        updated=datetime.fromisoformat(updated).astimezone(IST).strftime("%d %b %Y, %I:%M %p IST") if updated else None,
        data_date=date.fromisoformat(state["data_date"]).strftime("%d %b %Y") if state["data_date"] else None,
        can_scan=can_scan(), cooldown=int(COOLDOWN_MIN))


@app.route("/scan", methods=["POST"])
def scan():
    if can_scan():
        start_scan()
    return redirect("/")


@app.route("/status")
def status():
    return jsonify(scanning=is_scanning(), stage=state["stage"], done=state["done"], total=state["total"])


@app.route("/healthz")
def healthz():
    return "ok"


load_cache()   # shows the last saved results on start; scans only start when you press the button