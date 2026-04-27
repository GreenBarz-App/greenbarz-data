#!/usr/bin/env python3
"""
============================================================================
GREENBARZ · CFTC COT SCRAPER
----------------------------------------------------------------------------
Fetches the Commitments of Traders (COT) report from CFTC every Friday and
writes a JSON file consumable by the Greenbarz dashboard. Targets the
Disaggregated Futures-Only report which classifies traders into:
  - Producer/Merchant/Processor/User (commercial hedgers)
  - Swap Dealers
  - Managed Money (speculators — the key signal)
  - Other Reportables
  - Non-Reportable (small retail)

The "Managed Money net long" is the signal traders care about most. Extreme
values tend to mark turning points.

============================================================================
DEPLOYMENT (free, runs every Friday at 4 PM ET via GitHub Actions):
1. Place this file in your repo at: scrapers/cftc_cot.py
2. The workflow at .github/workflows/cftc-cot.yml runs it on schedule.
3. Greenbarz will fetch the JSON via:
   https://raw.githubusercontent.com/GreenBarz-App/greenbarz-data/main/data/cot_metals.json
============================================================================
"""

import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone

# CFTC publishes the Disaggregated Futures-Only report as a CSV via Socrata.
# Endpoint reference: https://publicreporting.cftc.gov/resource/72hh-3qpy.json
CFTC_ENDPOINT = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"

# Contracts we track for Greenbarz, mapped to clean symbols. The CFTC
# market_and_exchange_names text varies; we match by substring.
METALS_CONTRACTS = {
    "GOLD":      "GOLD - COMMODITY EXCHANGE INC.",
    "SILVER":    "SILVER - COMMODITY EXCHANGE INC.",
    "COPPER":    "COPPER- #1 - COMMODITY EXCHANGE INC.",
    "PLATINUM":  "PLATINUM - NEW YORK MERCANTILE EXCHANGE",
    "PALLADIUM": "PALLADIUM - NEW YORK MERCANTILE EXCHANGE",
}
ENERGY_CONTRACTS = {
    "WTI":      "CRUDE OIL, LIGHT SWEET-WTI - ICE FUTURES EUROPE",
    "WTI_NYM":  "WTI FINANCIAL CRUDE OIL - NEW YORK MERCANTILE EXCHANGE",
    "BRENT":    "BRENT CRUDE OIL LAST DAY - NEW YORK MERCANTILE EXCHANGE",
    "NG":       "NATURAL GAS - NEW YORK MERCANTILE EXCHANGE",
    "GASOLINE": "GASOLINE BLENDSTOCK (RBOB) - NEW YORK MERCANTILE EXCHANGE",
}

# How many weeks of history to keep in the output JSON
HISTORY_WEEKS = 26


def fetch_cftc_rows(contract_name: str, weeks: int = HISTORY_WEEKS) -> list:
    """Fetch the most-recent N weekly rows for one contract from the Socrata API."""
    where = "market_and_exchange_names like '%{}%'".format(contract_name.replace("'", "''"))
    params = {
        "$where": where,
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": weeks,
    }
    url = CFTC_ENDPOINT + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "greenbarz-cot-bot/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_row(row: dict) -> dict:
    """Extract the fields Greenbarz cards need, with safe int conversion."""
    def i(k):
        v = row.get(k)
        if v is None or v == "":
            return 0
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0
    mm_long = i("m_money_positions_long_all")
    mm_short = i("m_money_positions_short_all")
    com_long = i("prod_merc_positions_long_all")
    com_short = i("prod_merc_positions_short_all")
    return {
        "date": row.get("report_date_as_yyyy_mm_dd", "")[:10],
        "mm_long": mm_long,
        "mm_short": mm_short,
        "mm_net": mm_long - mm_short,
        "com_long": com_long,
        "com_short": com_short,
        "com_net": com_long - com_short,
        "oi": i("open_interest_all"),
    }


def build_dataset(contracts: dict) -> dict:
    """For each contract, fetch + parse + return Greenbarz-shaped record."""
    out = []
    for sym, cftc_name in contracts.items():
        try:
            rows = fetch_cftc_rows(cftc_name)
        except Exception as e:
            print(f"  ! {sym}: fetch failed: {e}", file=sys.stderr)
            continue
        if not rows:
            print(f"  ! {sym}: no rows returned", file=sys.stderr)
            continue
        parsed = [parse_row(r) for r in rows]
        # Latest row first in the API response; reverse for chronological history
        parsed_chrono = list(reversed(parsed))
        latest = parsed_chrono[-1]
        record = {
            "contract": sym,
            "cftc_name": cftc_name,
            **latest,
            "history": parsed_chrono,
        }
        out.append(record)
        print(f"  ✓ {sym}: {len(parsed)} weeks, latest {latest['date']}")
    return {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "source": "CFTC Disaggregated Futures-Only",
        "contracts": out,
    }


def write_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  → wrote {path}")


def main():
    print("Greenbarz CFTC COT scraper")
    print("==========================")
    print("Fetching METALS contracts...")
    metals = build_dataset(METALS_CONTRACTS)
    write_json("data/cot_metals.json", metals)
    print()
    print("Fetching ENERGY contracts...")
    energy = build_dataset(ENERGY_CONTRACTS)
    write_json("data/cot_energy.json", energy)
    print()
    print("Done.")


if __name__ == "__main__":
    main()
