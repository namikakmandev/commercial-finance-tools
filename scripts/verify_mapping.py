"""
verify_mapping.py
=================
Tests every series ID in scripts/series_mapping.json against FRED and TCMB,
and prints a clean report showing which ones work, which return empty data,
and which fail with errors.

Use this BEFORE relying on the GitHub Action — it lets you catch wrong series
IDs (especially on the TCMB side) without burning Action minutes or polluting
your data/*.json files with bad data.

Run locally:
    FRED_API_KEY=xxx TCMB_API_KEY=yyy python scripts/verify_mapping.py

Or test only one market:
    FRED_API_KEY=xxx python scripts/verify_mapping.py --market US
    TCMB_API_KEY=yyy python scripts/verify_mapping.py --market TR

Exit code: 0 if all series passed, 1 if any failed.
"""

import argparse
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

USER_AGENT = "PassthroughCalc-Verifier/1.0"
LOOKBACK_MONTHS = 36
MIN_POINTS = 13  # at least 12 months + base to be useful

# ANSI colors (degrade gracefully if terminal doesn't support them)
class C:
    GREEN = "\033[32m" if sys.stdout.isatty() else ""
    RED   = "\033[31m" if sys.stdout.isatty() else ""
    YEL   = "\033[33m" if sys.stdout.isatty() else ""
    DIM   = "\033[2m"  if sys.stdout.isatty() else ""
    BOLD  = "\033[1m"  if sys.stdout.isatty() else ""
    END   = "\033[0m"  if sys.stdout.isatty() else ""


# ----------------------------------------------------------------------
# Fetchers (copied from fetch_data.py to keep verifier self-contained)
# ----------------------------------------------------------------------
def fetch_fred(series_id: str, api_key: str):
    start_date = (datetime.utcnow() - timedelta(days=(LOOKBACK_MONTHS + 2) * 31)).strftime("%Y-%m-%d")
    params = {
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "observation_start": start_date, "frequency": "m", "aggregation_method": "avg",
    }
    url = f"https://api.stlouisfed.org/fred/series/observations?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    obs = payload.get("observations", [])
    values = [o["value"] for o in obs if o.get("value") not in (".", None, "")]
    return len(values), (values[-1] if values else None), (values[0] if values else None)


def fetch_tcmb(series_id: str, api_key: str):
    today = datetime.utcnow()
    start = today - timedelta(days=(LOOKBACK_MONTHS + 2) * 31)
    url = (
        f"https://evds2.tcmb.gov.tr/service/evds/"
        f"series={series_id}&startDate={start.strftime('%d-%m-%Y')}&endDate={today.strftime('%d-%m-%Y')}"
        f"&type=json&aggregationTypes=avg&formulas=0&frequency=5"
    )
    req = Request(url, headers={"key": api_key, "User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    rows = payload.get("items", [])
    value_key = series_id.replace(".", "_")
    values = [r.get(value_key) for r in rows if r.get(value_key) not in (None, "", "null")]
    return len(values), (values[-1] if values else None), (values[0] if values else None)


# ----------------------------------------------------------------------
# Verification loop
# ----------------------------------------------------------------------
def verify_market(market_code: str, market_cfg: dict, api_key: str):
    fetcher = fetch_fred if market_code == "US" else fetch_tcmb

    print(f"\n{C.BOLD}{'=' * 78}{C.END}")
    print(f"{C.BOLD}  {market_code} — {market_cfg['source']}{C.END}")
    print(f"{C.BOLD}{'=' * 78}{C.END}")
    print(f"  {'BUCKET ID':<22} {'SERIES ID':<28} {'STATUS':<14} POINTS")
    print(f"  {'-' * 22} {'-' * 28} {'-' * 14} ------")

    results = {"ok": [], "thin": [], "empty": [], "error": []}

    for bucket in market_cfg["buckets"]:
        bid = bucket["id"]
        sid = bucket["series_id"]
        try:
            n, latest, oldest = fetcher(sid, api_key)
            if n == 0:
                status = f"{C.RED}EMPTY{C.END}"
                results["empty"].append((bid, sid, "no observations returned"))
            elif n < MIN_POINTS:
                status = f"{C.YEL}THIN{C.END}"
                results["thin"].append((bid, sid, f"only {n} points"))
            else:
                status = f"{C.GREEN}OK{C.END}"
                results["ok"].append((bid, sid, n))
        except HTTPError as e:
            status = f"{C.RED}HTTP {e.code}{C.END}"
            results["error"].append((bid, sid, f"HTTP {e.code}"))
            n = 0
        except URLError as e:
            status = f"{C.RED}URL ERR{C.END}"
            results["error"].append((bid, sid, f"URL: {e.reason}"))
            n = 0
        except Exception as e:
            status = f"{C.RED}ERROR{C.END}"
            results["error"].append((bid, sid, str(e)[:40]))
            n = 0

        # Account for ANSI escape chars in alignment
        plain_status = status
        for code in ["\033[32m", "\033[31m", "\033[33m", "\033[0m"]:
            plain_status = plain_status.replace(code, "")
        pad = 14 - len(plain_status)

        print(f"  {bid:<22} {sid:<28} {status}{' ' * pad} {n}")
        time.sleep(0.4)  # be gentle on the APIs

    return results


def print_summary(market_code: str, results: dict):
    total = sum(len(v) for v in results.values())
    print(f"\n  {C.BOLD}{market_code} Summary:{C.END}  "
          f"{C.GREEN}✓ {len(results['ok'])} OK{C.END}  ·  "
          f"{C.YEL}⚠ {len(results['thin'])} thin{C.END}  ·  "
          f"{C.RED}✗ {len(results['empty']) + len(results['error'])} failed{C.END}  "
          f"(of {total})")

    failed = results["empty"] + results["error"]
    if failed:
        print(f"\n  {C.BOLD}Series to fix in scripts/series_mapping.json:{C.END}")
        for bid, sid, reason in failed:
            print(f"    • {bid:<22} {sid:<28} → {reason}")
        print(f"\n  {C.DIM}Lookup the correct ID at:{C.END}")
        if market_code == "US":
            print(f"  {C.DIM}  https://fred.stlouisfed.org{C.END}")
        else:
            print(f"  {C.DIM}  https://evds2.tcmb.gov.tr/index.php?/evds/serieMarket{C.END}")

    if results["thin"]:
        print(f"\n  {C.YEL}Thin series (fewer than {MIN_POINTS} points — may still work but check):{C.END}")
        for bid, sid, reason in results["thin"]:
            print(f"    • {bid:<22} {sid:<28} → {reason}")


def verify_industries(mapping: dict, fred_key: str | None, tcmb_key: str | None):
    """Test the output + input series for every industry."""
    items = mapping.get("INDUSTRIES", {}).get("items", [])
    if not items:
        return {"ok": [], "thin": [], "empty": [], "error": []}

    print(f"\n{C.BOLD}{'=' * 78}{C.END}")
    print(f"{C.BOLD}  INDUSTRIES — Pricing Power Index ({len(items)} industries){C.END}")
    print(f"{C.BOLD}{'=' * 78}{C.END}")
    print(f"  {'INDUSTRY':<25} {'OUTPUT SERIES':<22} {'INPUT':<10} STATUS")
    print(f"  {'-' * 25} {'-' * 22} {'-' * 10} ------")

    results = {"ok": [], "thin": [], "empty": [], "error": []}

    for ind in items:
        ind_id = ind["id"]
        market = ind["market"]
        key = fred_key if market == "US" else tcmb_key
        if not key:
            print(f"  {ind_id:<25} {ind['output_series_id']:<22} {'?':<10} {C.YEL}NO KEY{C.END}")
            continue
        fetcher = fetch_fred if market == "US" else fetch_tcmb

        # Output check
        try:
            n_out, _, _ = fetcher(ind["output_series_id"], key)
        except Exception as e:
            results["error"].append((ind_id, ind["output_series_id"], f"output: {str(e)[:30]}"))
            print(f"  {ind_id:<25} {ind['output_series_id']:<22} {'—':<10} {C.RED}OUTPUT ERR{C.END}")
            time.sleep(0.3)
            continue
        time.sleep(0.3)

        # Input check (basket or single)
        if "input_basket" in ind:
            basket_results = []
            for bid in ind["input_basket"]:
                try:
                    n_b, _, _ = fetcher(bid, key)
                    basket_results.append(n_b)
                    time.sleep(0.3)
                except Exception:
                    basket_results.append(0)
            n_in = min(basket_results) if basket_results else 0
            input_label = f"basket({len(ind['input_basket'])})"
        else:
            try:
                n_in, _, _ = fetcher(ind["input_series_id"], key)
                input_label = ind["input_series_id"][:10]
            except Exception as e:
                results["error"].append((ind_id, ind["input_series_id"], f"input: {str(e)[:30]}"))
                print(f"  {ind_id:<25} {ind['output_series_id']:<22} {ind['input_series_id'][:10]:<10} {C.RED}INPUT ERR{C.END}")
                continue
        time.sleep(0.3)

        if n_out == 0 or n_in == 0:
            status = f"{C.RED}EMPTY{C.END}"
            results["empty"].append((ind_id, ind["output_series_id"], f"out={n_out} in={n_in}"))
        elif n_out < MIN_POINTS or n_in < MIN_POINTS:
            status = f"{C.YEL}THIN{C.END}"
            results["thin"].append((ind_id, ind["output_series_id"], f"out={n_out} in={n_in}"))
        else:
            status = f"{C.GREEN}OK{C.END}"
            results["ok"].append((ind_id, ind["output_series_id"], f"out={n_out} in={n_in}"))

        print(f"  {ind_id:<25} {ind['output_series_id']:<22} {input_label:<10} {status}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Verify series IDs in series_mapping.json")
    parser.add_argument("--market", choices=["US", "TR"], help="Test only one market (default: both)")
    parser.add_argument("--skip-industries", action="store_true", help="Skip the industries section")
    args = parser.parse_args()

    with open(MAPPING_FILE) as f:
        mapping = json.load(f)

    markets_to_test = [args.market] if args.market else ["US", "TR"]
    all_failed = 0

    print(f"{C.BOLD}Inflation Tools — Mapping Verifier{C.END}")
    print(f"{C.DIM}Run timestamp: {datetime.utcnow().isoformat()}Z{C.END}")

    fred_key = os.environ.get("FRED_API_KEY")
    tcmb_key = os.environ.get("TCMB_API_KEY")

    for mc in markets_to_test:
        api_key = fred_key if mc == "US" else tcmb_key
        if not api_key:
            key_name = mapping[mc]["auth_env"]
            print(f"\n{C.RED}{key_name} not set — skipping {mc} buckets{C.END}")
            all_failed += 1
            continue
        results = verify_market(mc, mapping[mc], api_key)
        print_summary(mc, results)
        all_failed += len(results["empty"]) + len(results["error"])

    # Industries section
    if not args.skip_industries:
        ind_results = verify_industries(mapping, fred_key, tcmb_key)
        ok = len(ind_results["ok"])
        thin = len(ind_results["thin"])
        failed = len(ind_results["empty"]) + len(ind_results["error"])
        total = ok + thin + failed
        print(f"\n  {C.BOLD}INDUSTRIES Summary:{C.END}  "
              f"{C.GREEN}✓ {ok} OK{C.END}  ·  "
              f"{C.YEL}⚠ {thin} thin{C.END}  ·  "
              f"{C.RED}✗ {failed} failed{C.END}  (of {total})")
        if failed > 0:
            print(f"\n  {C.BOLD}Industries to fix:{C.END}")
            for ind_id, sid, reason in ind_results["empty"] + ind_results["error"]:
                print(f"    • {ind_id:<25} {sid:<22} → {reason}")
        all_failed += failed

    print(f"\n{C.BOLD}{'=' * 78}{C.END}")
    if all_failed == 0:
        print(f"  {C.GREEN}✓ All series verified successfully. Safe to enable the GitHub Action.{C.END}")
    else:
        print(f"  {C.RED}✗ {all_failed} issue(s) found. Fix series IDs above before enabling the Action.{C.END}")
    print(f"{C.BOLD}{'=' * 78}{C.END}\n")

    sys.exit(0 if all_failed == 0 else 1)


if __name__ == "__main__":
    main()
