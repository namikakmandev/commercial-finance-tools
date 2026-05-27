# Commercial Finance Tools

A suite of browser-based tools for pricing and commercial finance teams. Built on GitHub Pages, refreshed monthly via GitHub Actions, zero servers, zero hosting cost.

**Tools in this repo:**
1. **Inflation Pass-Through Calculator** (`index.html`) — Map your cost structure to FRED + TCMB inflation indices, compute price action needed to hold margin.
2. **Pricing Power Index** (`pricing-power.html`) — Rank industries by their pricing power (output PPI − input PPI) across US and Türkiye markets.
3. **FX Exposure Simulator** (`fx-exposure.html`) — Model currency exposure across revenue and costs, with realized impact, scenarios, hedge simulation, and volatility analysis. Uses live FX rates from frankfurter.app (ECB) — no API key needed.

The first two tools share one data pipeline. The FX tool is independent and fetches its data directly from frankfurter.app in the browser.

---

## Project structure

```
.
├── index.html                       # Pass-Through Calculator
├── pricing-power.html               # Pricing Power Index
├── fx-exposure.html                 # FX Exposure Simulator (independent — no setup)
├── data/
│   ├── us.json                      # cost buckets (US)         ← refreshed monthly
│   ├── tr.json                      # cost buckets (TR)         ← refreshed monthly
│   └── industries.json              # industry pairs            ← refreshed monthly
├── scripts/
│   ├── series_mapping.json          # series ID catalog (shared by both tools)
│   ├── fetch_data.py                # pulls APIs, writes data/*.json
│   └── verify_mapping.py            # tests series IDs before live runs
└── .github/workflows/
    └── refresh-data.yml             # monthly cron job
```

---

## Setup (one-time, ~1 hour total)

### Step 1: Get the API keys (15 min)

**FRED** (US data) — required
- Go to <https://fredaccount.stlouisfed.org/apikeys>
- Sign up (free), request an API key — instant

**TCMB EVDS** (Türkiye data) — required
- Go to <https://evds2.tcmb.gov.tr>
- Register an account, then go to your profile → "API Anahtarı" — usually approved same day

(No key needed for FX exposure tool — it uses frankfurter.app's free public API)

### Step 2: Set up the GitHub repo (15 min)

1. Create a new GitHub repo (e.g. `commercial-finance-tools`)
2. Unzip this project into it, push to GitHub
3. **Settings → Secrets and variables → Actions → New repository secret**
   - Add `FRED_API_KEY` with your FRED key
   - Add `TCMB_API_KEY` with your TCMB key
4. **Settings → Actions → General**
   - "Allow all actions and reusable workflows"
   - Workflow permissions → "Read and write permissions" (so the Action can commit)
5. **Settings → Pages**
   - Source: Deploy from branch → `main` / `/ (root)`
   - Wait ~1 min, your tools will be live at `https://<username>.github.io/<reponame>/`

### Step 3: Verify the mapping locally first (10 min)

Before running the Action and committing junk data, test the series IDs:

```bash
# In the project folder, on your computer:
FRED_API_KEY=xxx TCMB_API_KEY=yyy python3 scripts/verify_mapping.py
```

You'll get a clean report per market and for the industries. Any FAILED or EMPTY series need fixing in `scripts/series_mapping.json` — the report tells you exactly which.

**Expected at first run:** all US (FRED) series should be OK. Some TR (TCMB) series may be EMPTY or ERROR because TCMB classifies its codes differently from FRED. The verifier will tell you which to fix.

### Step 4: Run the Action (5 min)

1. Push any fixes to `series_mapping.json`
2. Go to the **Actions** tab → "Refresh Inflation Data" → "Run workflow"
3. Wait ~30 seconds
4. Check that `data/us.json`, `data/tr.json`, and `data/industries.json` were committed
5. Open your GitHub Pages URL — both tools should now show "Live data" in the masthead

After this, the Action runs automatically on the 15th of every month.

The **FX Exposure Simulator** works immediately on deploy — no Action needed.

---

## Tool URLs after deployment

If your repo is `https://github.com/USERNAME/commercial-finance-tools`, your tools will be at:

- `https://USERNAME.github.io/commercial-finance-tools/` — Pass-Through Calculator (default landing)
- `https://USERNAME.github.io/commercial-finance-tools/pricing-power.html` — Pricing Power Index
- `https://USERNAME.github.io/commercial-finance-tools/fx-exposure.html` — FX Exposure Simulator

The internal nav bar at the top of each tool links between them.

---

## Important notes

**TCMB series IDs need verification.** The IDs in `series_mapping.json` marked `verify code` are best-effort guesses. Run `verify_mapping.py` first to catch any wrong ones — they'll return EMPTY and the report links you straight to the TCMB portal to look up the right code.

**Türkiye Pricing Power Index uses derived inputs.** TÜİK does not publish matched output/input PPI pairs like FRED does. The fetcher computes a simple-average index from upstream cost categories defined in `input_basket` for each TR industry. Results are directional, not exact. The UI marks these with an "approx" tag.

**Adding new series** — edit `series_mapping.json`, push, re-run the Action. The HTML files auto-discover whatever the JSON files contain.

---

## Local development

To preview the tools without setting up the full pipeline:

```bash
python3 -m http.server 8000
# open http://localhost:8000  → see both tools with demo data
```

Opening the HTML files directly via `file://` triggers demo mode because browsers block `fetch()` on local files.

---

## License & attribution

Tool code: MIT.
Data sources retain original licenses:
- FRED data: public domain (US Federal Reserve, BLS)
- TCMB / TÜİK data: free for non-commercial use; see TCMB terms
- FX rates: ECB via frankfurter.app (free, public)

Credit the data sources visibly when publishing (the tool footers already do this).
