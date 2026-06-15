"""
update_mvrv.py
Τρέχει κάθε μέρα στις 00:05 UTC (cron).
Ελέγχει αν υπάρχει νέο MVRV-Z record και κάνει append στο CSV.

Επιστρέφει exit code:
  0 → update OK (record for today found and saved)
  1 → data not yet available (retry later)
  2 → error (API down, file problem, etc.)
"""

import requests
import pandas as pd
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

# ── Paths & Config ────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
MVRV_FILE   = BASE_DIR / "mvrv_zscore_full_history.csv"
LOG_FILE    = BASE_DIR / "logs" / f"update_mvrv_{datetime.utcnow().strftime('%Y%m%d')}.log"
LOG_FILE.parent.mkdir(exist_ok=True)

API_KEYS  = ["y8zSqnwzJG", "pgtqoKHW4h", "ZM9FGh8kVM"]
API_BASE  = "https://api.bgeometrics.com/v1/mvrv-zscore"

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("update_mvrv")


def fetch_latest(api_keys: list[str]) -> pd.DataFrame:
    """Φέρνει τα τελευταία 3 records από το API (ελαφρύ request)."""
    for i, key in enumerate(api_keys):
        try:
            # limit=3 για να πάρουμε μόνο τα πιο πρόσφατα
            r = requests.get(f"{API_BASE}?token={key}&limit=3", timeout=15)
            if r.status_code == 200:
                data = r.json()
                if not isinstance(data, list) or len(data) == 0:
                    continue
                df = pd.DataFrame(data)
                df = df.rename(columns={"d": "date", "mvrvZscore": "mvrv_zscore"})
                df["date"] = pd.to_datetime(df["date"])
                df["mvrv_zscore"] = pd.to_numeric(df["mvrv_zscore"], errors="coerce")
                df = df.dropna(subset=["mvrv_zscore"])
                df = df.sort_values("date", ascending=False)
                logger.info(f"API key #{i+1} OK — latest={df['date'].iloc[0].date()}")
                return df[["date", "mvrv_zscore"]]
            elif r.status_code == 429:
                logger.warning(f"Key #{i+1} rate limited")
            else:
                logger.warning(f"Key #{i+1} HTTP {r.status_code}")
        except Exception as e:
            logger.error(f"Key #{i+1} error: {e}")
    return pd.DataFrame()


def main() -> int:
    today = datetime.now(timezone.utc)
    today_ts = pd.Timestamp(today.date())
    logger.info(f"=== update_mvrv.py === UTC={today.strftime('%Y-%m-%d %H:%M')}")

    # 1. Φόρτωσε το υπάρχον CSV
    if not MVRV_FILE.exists():
        logger.error(f"MVRV file not found: {MVRV_FILE} — run bootstrap_mvrv.py first")
        return 2

    existing = pd.read_csv(MVRV_FILE)
    existing["date"] = pd.to_datetime(existing["date"])
    logger.info(f"Existing records: {len(existing)} — latest={existing['date'].max().date()}")

    # 2. Ελέγχει αν έχουμε ήδη σημερινό record
    if today_ts in existing["date"].values:
        val = existing[existing["date"] == today_ts]["mvrv_zscore"].iloc[0]
        logger.info(f"Today's MVRV already exists: {val:.4f} — no update needed")
        return 0

    # 3. Φέρε τα τελευταία records από το API
    logger.info("Fetching latest MVRV from API...")
    new_df = fetch_latest(API_KEYS)

    if new_df.empty:
        logger.error("All API keys failed — cannot update MVRV")
        return 2

    # 4. Ελέγχει αν υπάρχει record για σήμερα
    if today_ts not in new_df["date"].values:
        latest_api = new_df["date"].max().date()
        logger.warning(f"Today's MVRV not yet available — API latest={latest_api}")
        return 1   # retry later

    # 5. Append μόνο νέα records
    new_records = new_df[~new_df["date"].isin(existing["date"])]
    if new_records.empty:
        logger.info("No new records to add")
        return 0

    updated = pd.concat([existing, new_records], ignore_index=True)
    updated = updated.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    updated.to_csv(MVRV_FILE, index=False)

    for _, row in new_records.iterrows():
        logger.info(f"✅ Added MVRV: {row['date'].date()} = {row['mvrv_zscore']:.4f}")

    logger.info(f"MVRV file updated: {len(updated)} total records")
    return 0


if __name__ == "__main__":
    sys.exit(main())