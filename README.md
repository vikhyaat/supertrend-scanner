# Supertrend Scanner

A small web app that scans NSE stocks and lists the ones where the **Supertrend has just turned bullish and several filters agree**. It is built to cut down the false signals Supertrend gives in sideways markets.

**Live site:** https://supertrend-scanner-6k7x.onrender.com

The site runs on Render's free plan, so the first visit after a quiet period can take up to a minute to wake up.

> **For education and paper trading only.** This is not investment advice and not a recommendation to buy or sell anything. Prices come from Yahoo Finance and can be delayed or wrong.

## How it works

A scan checks every stock in the stock lists (Nifty 500, Nifty Smallcap 250, Nifty Microcap 250) on daily candles and labels it:

| Label | Meaning |
|---|---|
| **BUY** | The Supertrend turned bullish within the last few days **and all 5 filters pass** |
| **NEAR** | A recent bullish flip with **4 of 5 filters** passing. The "Missing" column shows which filter is blocking it. Watch it, do not buy yet |

The 5 filters:

1. **ADX** at least 22, with +DI above −DI (the trend is strong and pointing up)
2. **Choppiness Index** at most 55 (the market is trending, not ranging)
3. **Price above the 200 EMA**
4. **RSI above 50**
5. **Volume above its 20-day average**

For each stock the page also shows a stop-loss (the Supertrend line), a target (2 times the risk), and a suggested paper-trading quantity based on your capital and risk % per trade. Stocks with less than about a year of price history are skipped.

The same logic exists as a TradingView Pine Script indicator, `supertrend_pro_filter.pine`, if you keep it in this repo.

## Features

- Scans only when you press **Start scan**. Nothing runs automatically.
- Live progress bar while scanning, and a scan log for troubleshooting.
- Filters on the page: universe, buy/near, price range, minimum turnover, maximum stop distance, signal age, capital and risk %.
- Links each stock to its TradingView chart.
- The results are shown on the page only. There is no CSV or file download.

## Project structure

```
app.py                Flask web app and scan control
scanner.py            Indicator maths and scanning logic
templates/index.html  The web page
universe/             Stock list CSV files (see below)
requirements.txt      Python packages
render.yaml           Render deployment settings
```

## Stock lists (important)

Put the NSE index CSV files in the `universe/` folder. Each file needs a `Symbol` column. The scanner uses these files first, which is much more reliable on a hosted server than downloading from NSE during a scan.

| Index | File name |
|---|---|
| Nifty 500 | `ind_nifty500list.csv` or `nifty500.csv` |
| Nifty Smallcap 250 | `ind_niftysmallcap250list.csv` or `smallcap250.csv` |
| Nifty Microcap 250 | `ind_niftymicrocap250_list.csv` or `microcap250.csv` |

You can download these lists from the NSE index pages on [niftyindices.com](https://www.niftyindices.com). The Nifty 500 file alone is enough to start. If a file is missing, the scanner tries to download it, and a failure is shown on the page.

## Run locally

Requires Python 3.11 or newer.

```
pip install -r requirements.txt
flask --app app run
```

Open http://127.0.0.1:5000 and press **Start scan**. A full scan takes a few minutes.

## Deploy on Render

1. Push this repo to GitHub.
2. In Render, create a **New + > Web Service** and connect the repo.
3. Use these settings:

| Setting | Value |
|---|---|
| Runtime | Python 3 |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn app:app --workers 1 --threads 4 --timeout 120` |
| Health check path | `/healthz` |
| Environment variable | `PYTHON_VERSION` = `3.11.9` |

Alternatively, use **New + > Blueprint**, which reads `render.yaml`.

Keep the gunicorn worker count at **1**, because scan results are held in that worker's memory.

### Optional settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `COOLDOWN_MIN` | `5` | Minutes that must pass after a successful scan before another can start |

## Notes and limitations

- On Render's free plan the service sleeps when unused and its saved results are lost on restart or redeploy. Press **Start scan** again.
- Yahoo Finance may slow down or block requests from cloud servers. If a scan fails, the page shows the error and the scan log.
- A scan run while the market is open uses today's unfinished candle, so signals can change by the close. Scan again after 3:30 PM IST.
- Anyone with the site's link can open it and start a scan. Do not put anything private in the repo.
- Indicator values are very close to, but not always identical to, TradingView's, because of different data feeds.

## Disclaimer

This project is for learning and paper trading. Trading, especially in small and micro-cap stocks, involves a risk of loss. Check results, upcoming events and the price band (circuit limit) yourself before any decision. The author is not a SEBI-registered advisor.
