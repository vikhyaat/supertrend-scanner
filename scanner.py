"""Supertrend Pro Filter scanner for NSE stocks. Same logic as the Pine indicator. Education only."""
import io
import logging
import math
import os
import time
import urllib.request
from collections import Counter

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE = "https://niftyindices.com/IndexConstituent/"
GROUPS = [  # (file key, label, NSE file name)
    ("nifty500", "Nifty 500", "ind_nifty500list.csv"),
    ("smallcap250", "Nifty Smallcap 250", "ind_niftysmallcap250list.csv"),
    ("microcap250", "Nifty Microcap 250", "ind_niftymicrocap250_list.csv"),
]
NAMES = ["ADX", "CHOP", "EMA200", "RSI", "VOL"]
RR = 2.0          # target = 2 x risk
MAX_BUY_AGE = 3   # keep BUY signals up to 3 trading days old (page filters further)


def rma(s, n):
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def true_range(df):
    pc = df["Close"].shift()
    return pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1).max(axis=1)


def supertrend(df, n=10, mult=3.0):
    """Same maths as Pine's ta.supertrend. direction -1 = uptrend, 1 = downtrend."""
    hl2 = ((df["High"] + df["Low"]) / 2).to_numpy()
    atr = rma(true_range(df), n).to_numpy()
    up, lo = hl2 + mult * atr, hl2 - mult * atr
    c = df["Close"].to_numpy()
    d = np.ones(len(df))
    st = np.full(len(df), np.nan)
    for i in range(len(df)):
        if np.isnan(atr[i]):
            continue
        if i == 0 or np.isnan(atr[i - 1]):
            st[i] = up[i]
            continue
        pl, pu = lo[i - 1], up[i - 1]
        if not (lo[i] > pl or c[i - 1] < pl):
            lo[i] = pl
        if not (up[i] < pu or c[i - 1] > pu):
            up[i] = pu
        if st[i - 1] == pu:
            d[i] = -1 if c[i] > up[i] else 1
        else:
            d[i] = 1 if c[i] < lo[i] else -1
        st[i] = lo[i] if d[i] == -1 else up[i]
    return d, st


def analyse(df, wait=3, adx_min=22.0, ci_max=55.0):
    df = df.dropna(subset=["Open", "High", "Low", "Close"]).copy()
    if len(df) < 250:
        return None
    c = df["Close"]
    d, st = supertrend(df)
    tr = true_range(df)
    up_move, dn_move = df["High"].diff(), -df["Low"].diff()
    pdm = pd.Series(np.where((up_move > dn_move) & (up_move > 0), up_move, 0.0), index=df.index)
    mdm = pd.Series(np.where((dn_move > up_move) & (dn_move > 0), dn_move, 0.0), index=df.index)
    trn = rma(tr, 14)
    di_p, di_m = 100 * rma(pdm, 14) / trn, 100 * rma(mdm, 14) / trn
    adx = rma(100 * (di_p - di_m).abs() / (di_p + di_m).replace(0, np.nan), 14)
    rng = (df["High"].rolling(14).max() - df["Low"].rolling(14).min()).replace(0, np.nan)
    ci = 100 * np.log10(tr.rolling(14).sum() / rng) / np.log10(14)
    ema200 = c.ewm(span=200, adjust=False).mean()
    delta = c.diff()
    rsi = 100 - 100 / (1 + rma(delta.clip(lower=0), 14) / rma((-delta).clip(lower=0), 14))
    vol_ratio = df["Volume"] / df["Volume"].rolling(20).mean()
    turnover = (c * df["Volume"]).rolling(20).mean() / 1e7

    checks = pd.concat([(adx >= adx_min) & (di_p > di_m), ci <= ci_max, c > ema200, rsi > 50, vol_ratio > 1.0], axis=1)
    checks.columns = NAMES
    score = checks.sum(axis=1).to_numpy()
    missing = ", ".join(n for n in NAMES if not bool(checks.iloc[-1][n]))

    flip = np.zeros(len(d), dtype=bool)
    flip[1:] = (d[1:] < 0) & (d[:-1] > 0)
    bs, done, fired = np.inf, False, []
    for i in range(len(d)):
        if flip[i]:
            bs, done = 0, False
        else:
            bs += 1
        if d[i] < 0 and bs <= wait and score[i] == 5 and not done:
            fired.append(i)
            done = True
    last = len(d) - 1
    in_leg = bs != np.inf
    current = [f for f in fired if in_leg and f >= last - bs]
    close = float(c.iloc[-1])
    stop = float(st[last]) if d[last] < 0 else float("nan")
    risk_ps = close - stop
    return {
        "date": df.index[-1].date().isoformat(), "close": close, "stop": stop, "risk_ps": risk_ps,
        "risk_pct": 100 * risk_ps / close, "target": close + RR * risk_ps,
        "days_ago": (last - current[-1]) if current else None,
        "near": bool(d[last] < 0 and bs <= wait and not done and score[last] >= 4),
        "filters_ok": int(score[last]), "missing": missing,
        "bars_since_flip": int(bs) if in_leg else None,
        "adx": float(adx.iloc[-1]), "adx_rising": bool(adx.iloc[-1] > adx.iloc[-2]),
        "chop": float(ci.iloc[-1]), "rsi": float(rsi.iloc[-1]),
        "vol_x": float(vol_ratio.iloc[-1]), "turnover_cr": float(turnover.iloc[-1]),
    }


def _symbols(src):
    df = pd.read_csv(src)
    col = next(c for c in df.columns if c.strip().lower() == "symbol")
    return df[col].astype(str).str.strip().tolist()


def _read_group(key, label, filename, log):
    """Use a CSV from the 'universe' folder if present (most reliable on a server); else try NSE."""
    for name in (key + ".csv", filename):
        path = os.path.join(BASE_DIR, "universe", name)
        if os.path.exists(path):
            log(f"{label}: using universe/{name}")
            return _symbols(path)
    try:
        log(f"{label}: downloading list from NSE")
        req = urllib.request.Request(BASE + filename, headers={"User-Agent": "Mozilla/5.0"})
        return _symbols(io.StringIO(urllib.request.urlopen(req, timeout=15).read().decode()))
    except Exception as e:
        log(f"{label}: could not download ({type(e).__name__}) - add the CSV to the universe folder")
        return None


def load_universe(log):
    names, loaded, failed = {}, {}, []
    for key, label, filename in GROUPS:
        syms = _read_group(key, label, filename, log)
        if syms is None:
            failed.append(label)
            continue
        loaded[label] = len(syms)
        for s in syms:
            if "DUMMY" not in s.upper():
                names.setdefault(s, label)
    if not names:
        raise RuntimeError("No stock list available. Put the NSE index CSV files in the 'universe' folder.")
    return names, {"loaded": loaded, "failed": failed}


def _download_chunk(tickers):
    import yfinance as yf
    try:
        return yf.download(tickers, period="2y", interval="1d", group_by="ticker", auto_adjust=True,
                           threads=4, progress=False, timeout=20)
    except Exception:
        return None


def run_scan(progress=lambda *a: None, log=lambda m: None, chunk=40):
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    progress("Loading stock lists")
    names, info = load_universe(log)
    symbols = list(names)
    total = len(symbols)
    log(f"{total} stocks to check")
    rows, dates, analysed = [], [], 0
    for k in range(0, total, chunk):
        part = symbols[k:k + chunk]
        progress("Downloading prices and checking signals", k, total)
        data = _download_chunk([s + ".NS" for s in part])
        if data is None or getattr(data, "empty", True):
            log(f"No price data returned for stocks {k + 1}-{k + len(part)}")
        for s in part:
            try:
                res = analyse(data[s + ".NS"].dropna(how="all"))
            except Exception:
                res = None
            if not res:
                continue
            analysed += 1
            dates.append(res["date"])
            if res["days_ago"] is not None and res["days_ago"] <= MAX_BUY_AGE:
                status = "BUY"
            elif res["near"]:
                status = "NEAR"
            else:
                continue
            row = {**res, "symbol": s, "group": names[s], "status": status}
            if any(isinstance(v, float) and math.isnan(v) for v in row.values()) or row["risk_ps"] <= 0:
                continue
            rows.append(row)
        done = min(k + chunk, total)
        progress("Downloading prices and checking signals", done, total)
        log(f"Checked {done} of {total}; {analysed} had usable data")
        time.sleep(1)
    if analysed < 0.3 * total:
        raise RuntimeError(f"Yahoo Finance gave usable prices for only {analysed} of {total} stocks "
                           "(it may be blocking this server). Try again later.")
    info.update(scanned=analysed, data_date=Counter(dates).most_common(1)[0][0])
    return rows, info