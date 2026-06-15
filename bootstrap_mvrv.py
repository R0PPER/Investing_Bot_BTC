"""
bootstrap_mvrv.py
Τρέχει ΜΙΑ ΦΟΡΑ για να κατεβάσει ολόκληρο το ιστορικό MVRV-Z.
Δημιουργεί: mvrv_zscore_full_history.csv

Χρήση:
    python bootstrap_mvrv.py
"""

import requests
import pandas as pd
import os
import sys
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────
API_KEYS = [
    "y8zSqnwzJG",
    "pgtqoKHW4h",
    "ZM9FGh8kVM",
]
OUTPUT_FILE = Path(__file__).parent / "mvrv_zscore_full_history.csv"
API_BASE    = "https://api.bgeometrics.com/v1/mvrv-zscore"


def fetch_mvrv(api_keys: list[str]) -> pd.DataFrame:
    """Δοκιμάζει API keys διαδοχικά και επιστρέφει DataFrame."""
    for i, key in enumerate(api_keys):
        print(f"  Trying API key #{i+1}...")
        try:
            r = requests.get(f"{API_BASE}?token={key}", timeout=30)
            if r.status_code == 200:
                data = r.json()
                if not isinstance(data, list) or len(data) == 0:
                    print(f"    ⚠️  Empty or unexpected response")
                    continue
                df = pd.DataFrame(data)
                df = df.rename(columns={"d": "date", "mvrvZscore": "mvrv_zscore"})
                df["date"] = pd.to_datetime(df["date"])
                df["mvrv_zscore"] = pd.to_numeric(df["mvrv_zscore"], errors="coerce")
                df = df.dropna(subset=["mvrv_zscore"])
                df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
                print(f"    ✅ OK — {len(df)} records ({df['date'].min().date()} → {df['date'].max().date()})")
                return df[["date", "mvrv_zscore"]]
            elif r.status_code == 429:
                print(f"    ⚠️  Rate limited — trying next key")
            else:
                print(f"    ❌ HTTP {r.status_code}")
        except Exception as e:
            print(f"    ❌ Error: {e}")

    return pd.DataFrame()


def main():
    print("=" * 60)
    print("  MVRV-Z BOOTSTRAP — Download Full History")
    print("=" * 60)

    if OUTPUT_FILE.exists():
        existing = pd.read_csv(OUTPUT_FILE)
        existing["date"] = pd.to_datetime(existing["date"])
        last = existing["date"].max()
        days_old = (pd.Timestamp.now() - last).days
        print(f"\n⚠️  File already exists: {len(existing)} records, last={last.date()} ({days_old}d old)")
        ans = input("Overwrite? (yes/no): ").strip().lower()
        if ans != "yes":
            print("Aborted.")
            sys.exit(0)

    print("\nFetching MVRV history...")
    df = fetch_mvrv(API_KEYS)

    if df.empty:
        print("\n❌ Failed to fetch data. Check API keys and try again.")
        sys.exit(1)

    df.to_csv(OUTPUT_FILE, index=False)
    print(f"\n✅ Saved {len(df)} records → {OUTPUT_FILE}")
    print(f"   Range: {df['date'].min().date()} → {df['date'].max().date()}")
    print(f"   MVRV-Z: {df['mvrv_zscore'].min():.2f} → {df['mvrv_zscore'].max():.2f}")
    print("\nRun this ONCE. From now on, update_mvrv.py handles daily updates.")


if __name__ == "__main__":
    main()