"""
fetch_data.py
=============
Pulls inflation series from FRED (US) and TCMB EVDS (Türkiye), normalizes
them, and writes data/us.json and data/tr.json for the Inflation Pass-Through
Calculator to consume.

Run locally:
    FRED_API_KEY=xxx TCMB_API_KEY=yyy python scripts/fetch_data.py

Or via GitHub Actions (see .github/workflows/refresh-data.yml).

Output JSON shape (per market):
{
  "market": "US",
  "source": "FRED",
  "last_updated": "2026-05-23",
  "lookback_months": 36,
  "buckets": [
    {
      "id": "us_steel",
      "label": "Steel & iron",
      "series_id": "WPU101",
      "series": [102.3, 102.8, 103.1, ...],   # 37 monthly values (lookback + base)
      "dates":  ["2023-04-01", ...]
    },
    ...
  ]
}
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent.parent
MAPPING_FILE = ROOT / "scripts" / "series_mapping.json"
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

LOOKBACK_MONTHS = 36
USER_AGENT = "PassthroughCalc/1.0 (+github actions)"


def log(msg: str, level: str = "info"):
    prefix = {"info": "·", "warn": "!", "err": "✗", "ok": "✓"}.get(level, "·")
    print(f"  {prefix} {msg}", flush=True)


# ----------------------------------------------------------------------
# FRED
# ----------------------------------------------------------------------
def fetch_fred_series(series_id: str, api_key: str) -> tuple[list[str], list[float]]:
    """Fetch monthly observations for one FRED series. Returns (dates, values)."""
    start_date = (datetime.utcnow() - timedelta(days=(LOOKBACK_MONTHS + 2) * 31)).strftime("%Y-%m-%d")
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start_date,
        "frequency": "m",
        "aggregation_method": "avg",
    }
    url = f"https://api.stlouisfed.org/fred/series/observations?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    obs = payload.get("observations", [])
    dates, values = [], []
    for o in obs:
        if o.get("value") in (".", None, ""):
            continue
        try:
            values.append(float(o["value"]))
            dates.append(o["date"])
        except (ValueError, KeyError):
            continue
    return dates, values


# ----------------------------------------------------------------------
# TCMB EVDS
# ----------------------------------------------------------------------
def fetch_tcmb_series(series_id: str, api_key: str) -> tuple[list[str], list[float]]:
    """Fetch monthly observations for one TCMB EVDS series. Returns (dates, values)."""
    today = datetime.utcnow()
    start = today - timedelta(days=(LOOKBACK_MONTHS + 2) * 31)
    start_str = start.strftime("%d-%m-%Y")
    end_str = today.strftime("%d-%m-%Y")

    # EVDS API format: /service/evds/series=<id>&startDate=...&endDate=...&type=json&aggregationTypes=avg&formulas=0&frequency=5
    # frequency=5 = monthly
    url = (
        f"https://evds2.tcmb.gov.tr/service/evds/"
        f"series={series_id}&startDate={start_str}&endDate={end_str}"
        f"&type=json&aggregationTypes=avg&formulas=0&frequency=5"
    )
    req = Request(url, headers={"key": api_key, "User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    rows = payload.get("items", [])
    # value key is series_id with dots replaced by underscores
    value_key = series_id.replace(".", "_")
    dates, values = [], []
    for r in rows:
        v = r.get(value_key)
        if v in (None, "", "null"):
            continue
        try:
            values.append(float(v))
            # EVDS returns dates like "2024-3" or "31-3-2024" depending on freq; normalize
            tarih = r.get("Tarih", "")
            dates.append(normalize_tcmb_date(tarih))
        except (ValueError, TypeError):
            continue
    return dates, values


def normalize_tcmb_date(tarih: str) -> str:
    """TCMB monthly dates come as 'YYYY-M' or 'M-YYYY'. Normalize to YYYY-MM-01."""
    tarih = tarih.strip()
    if "-" not in tarih:
        return tarih
    parts = tarih.split("-")
    if len(parts) == 2:
        if len(parts[0]) == 4:  # YYYY-M
            y, m = parts[0], parts[1].zfill(2)
        else:  # M-YYYY
            m, y = parts[0].zfill(2), parts[1]
        return f"{y}-{m}-01"
    return tarih


# ----------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------
def trim_to_lookback(dates: list[str], values: list[float]) -> tuple[list[str], list[float]]:
    """Keep only the last (LOOKBACK_MONTHS + 1) observations."""
    keep = LOOKBACK_MONTHS + 1
    if len(values) <= keep:
        return dates, values
    return dates[-keep:], values[-keep:]


def process_market(market_code: str, market_cfg: dict, api_key: str) -> dict:
    log(f"Market: {market_code} — source: {market_cfg['source']}")
    fetcher = fetch_fred_series if market_code == "US" else fetch_tcmb_series

    bucket_results = []
    failures = []

    for bucket in market_cfg["buckets"]:
        sid = bucket["series_id"]
        label = bucket["label"]
        try:
            dates, values = fetcher(sid, api_key)
            if len(values) < 13:
                failures.append((bucket["id"], "insufficient data points"))
                log(f"  {bucket['id']:20s} {sid:25s} INSUFFICIENT ({len(values)} points)", "warn")
                continue
            dates, values = trim_to_lookback(dates, values)
            bucket_results.append({
                "id": bucket["id"],
                "label": label,
                "series_id": sid,
                "notes": bucket.get("notes", ""),
                "series": [round(v, 4) for v in values],
                "dates": dates,
            })
            log(f"  {bucket['id']:20s} {sid:25s} OK ({len(values)} points)", "ok")
        except HTTPError as e:
            failures.append((bucket["id"], f"HTTP {e.code}"))
            log(f"  {bucket['id']:20s} {sid:25s} HTTP {e.code}", "err")
        except URLError as e:
            failures.append((bucket["id"], f"URL error: {e.reason}"))
            log(f"  {bucket['id']:20s} {sid:25s} URL error: {e.reason}", "err")
        except Exception as e:
            failures.append((bucket["id"], str(e)))
            log(f"  {bucket['id']:20s} {sid:25s} ERROR: {e}", "err")

        # be gentle on the APIs
        time.sleep(0.4)

    return {
        "market": market_code,
        "source": market_cfg["source"],
        "last_updated": datetime.utcnow().strftime("%Y-%m-%d"),
        "lookback_months": LOOKBACK_MONTHS,
        "buckets": bucket_results,
        "failures": failures,
    }


def process_industries(mapping: dict, fred_key: str | None, tcmb_key: str | None) -> dict:
    """Process the INDUSTRIES section: fetch output + input series for each industry,
    compute derived input baskets for TR, write data/industries.json."""
    items = mapping.get("INDUSTRIES", {}).get("items", [])
    if not items:
        return {"items": [], "failures": []}

    print("\n[3/3] Industries — Pricing Power Index")
    log(f"Processing {len(items)} industries")

    results = []
    failures = []

    for ind in items:
        market = ind["market"]
        ind_id = ind["id"]
        label = ind["label"]
        key = fred_key if market == "US" else tcmb_key
        if not key:
            log(f"  {ind_id:25s} SKIPPED (no {market} API key)", "warn")
            failures.append((ind_id, f"no {market} API key"))
            continue

        fetcher = fetch_fred_series if market == "US" else fetch_tcmb_series

        # Fetch output series
        try:
            out_dates, out_vals = fetcher(ind["output_series_id"], key)
            if len(out_vals) < 13:
                failures.append((ind_id, "insufficient output points"))
                log(f"  {ind_id:25s} OUTPUT thin ({len(out_vals)})", "warn")
                continue
        except Exception as e:
            failures.append((ind_id, f"output: {str(e)[:50]}"))
            log(f"  {ind_id:25s} OUTPUT err: {e}", "err")
            continue
        time.sleep(0.3)

        # Fetch input series (single or basket)
        try:
            if "input_basket" in ind:
                # TR-style: simple-average index from multiple series
                basket_series = []
                for bid in ind["input_basket"]:
                    try:
                        _, bvals = fetcher(bid, key)
                        if len(bvals) >= 13:
                            basket_series.append(bvals)
                        time.sleep(0.3)
                    except Exception:
                        continue
                if not basket_series:
                    raise ValueError("no basket series fetched")
                # Trim all to common length, average element-wise
                min_len = min(len(s) for s in basket_series)
                basket_trimmed = [s[-min_len:] for s in basket_series]
                in_vals = [sum(s[i] for s in basket_trimmed) / len(basket_trimmed) for i in range(min_len)]
                in_dates = []  # not used for basket
            else:
                _, in_vals = fetcher(ind["input_series_id"], key)
                if len(in_vals) < 13:
                    failures.append((ind_id, "insufficient input points"))
                    log(f"  {ind_id:25s} INPUT thin ({len(in_vals)})", "warn")
                    continue
        except Exception as e:
            failures.append((ind_id, f"input: {str(e)[:50]}"))
            log(f"  {ind_id:25s} INPUT err: {e}", "err")
            continue
        time.sleep(0.3)

        # Trim both to same length
        common = min(len(out_vals), len(in_vals), LOOKBACK_MONTHS + 1)
        results.append({
            "id": ind_id,
            "market": market,
            "label": label,
            "category": ind.get("category"),
            "approx": ind.get("approx", False),
            "output_series_id": ind["output_series_id"],
            "input_series_id": ind.get("input_series_id", "derived"),
            "notes": ind.get("notes", ""),
            "output": [round(v, 4) for v in out_vals[-common:]],
            "input":  [round(v, 4) for v in in_vals[-common:]],
        })
        log(f"  {ind_id:25s} OK ({common} points)", "ok")

    return {"items": results, "failures": failures}


def main():
    print("=" * 60)
    print("  Inflation Tools — Data Refresh")
    print(f"  Run timestamp: {datetime.utcnow().isoformat()}Z")
    print("=" * 60)

    with open(MAPPING_FILE) as f:
        mapping = json.load(f)

    fred_key = os.environ.get("FRED_API_KEY")
    tcmb_key = os.environ.get("TCMB_API_KEY")

    overall_failures = 0

    if fred_key:
        print("\n[1/3] United States — FRED (cost buckets)")
        us_data = process_market("US", mapping["US"], fred_key)
        out_path = DATA_DIR / "us.json"
        with open(out_path, "w") as f:
            json.dump(us_data, f, indent=2)
        log(f"Wrote {out_path}  ({len(us_data['buckets'])} buckets, {len(us_data['failures'])} failures)", "ok")
        overall_failures += len(us_data["failures"])
    else:
        log("FRED_API_KEY not set — skipping US", "warn")
        overall_failures += 1

    if tcmb_key:
        print("\n[2/3] Türkiye — TCMB EVDS (cost buckets)")
        tr_data = process_market("TR", mapping["TR"], tcmb_key)
        out_path = DATA_DIR / "tr.json"
        with open(out_path, "w") as f:
            json.dump(tr_data, f, indent=2)
        log(f"Wrote {out_path}  ({len(tr_data['buckets'])} buckets, {len(tr_data['failures'])} failures)", "ok")
        overall_failures += len(tr_data["failures"])
    else:
        log("TCMB_API_KEY not set — skipping Türkiye buckets", "warn")
        overall_failures += 1

    # Industries (for Pricing Power Index) — needs at least one key
    if fred_key or tcmb_key:
        ind_data = process_industries(mapping, fred_key, tcmb_key)
        out_path = DATA_DIR / "industries.json"
        payload = {
            "last_updated": datetime.utcnow().strftime("%Y-%m-%d"),
            "lookback_months": LOOKBACK_MONTHS,
            "items": ind_data["items"],
            "failures": ind_data["failures"],
        }
        with open(out_path, "w") as f:
            json.dump(payload, f, indent=2)
        log(f"Wrote {out_path}  ({len(ind_data['items'])} industries, {len(ind_data['failures'])} failures)", "ok")
        overall_failures += len(ind_data["failures"])

    print("\n" + "=" * 60)
    if overall_failures == 0:
        print("  ✓ Refresh complete — no failures")
    else:
        print(f"  ! Refresh complete — {overall_failures} failures (see logs above)")
    print("=" * 60)

    if overall_failures > 25:
        sys.exit(1)


if __name__ == "__main__":
    main()
