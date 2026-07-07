"""After SENSEX historical ingest, verify the hardcoded calendar against actual data.
Outputs a corrected list of SENSEX weekly expiries to paste into lib/expiry_calendar.py."""
import duckdb, pandas as pd
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.expiry_calendar import SENSEX_WEEKLY_EXPIRIES, SENSEX_DEFAULT_PLACEHOLDER_END

con = duckdb.connect()
df = con.execute("""
  SELECT DISTINCT expiry, COUNT(DISTINCT CAST(timestamp AS DATE)) AS trade_days
  FROM read_parquet('data/parquet/instrument=SENSEX/**/*.parquet', union_by_name=True)
  WHERE option_type IN ('CE','PE') AND expiry IS NOT NULL
  GROUP BY expiry ORDER BY expiry
""").fetchdf()
df['expiry'] = pd.to_datetime(df['expiry']).dt.date
df['dow'] = pd.to_datetime(df['expiry']).dt.day_name()
df['gap_from_prev'] = (pd.to_datetime(df['expiry']).diff().dt.days).fillna(0).astype(int)

# Filter to weekly cycle (gap 1-14 days)
weekly = df[df.gap_from_prev.between(1, 14) | (df.index == 0)].copy()

print(f"Total SENSEX expiries in parquet: {len(df)}")
print(f"Weekly-cycle expiries: {len(weekly)}")
print()
print("=== ACTUAL SENSEX expiries from data ===")
for _, r in df.iterrows():
    flag = " ⚠ NOT-Thursday" if r['dow'] != 'Thursday' else ""
    flag2 = " ⚠ MONTHLY (gap > 7)" if r['gap_from_prev'] > 8 else ""
    print(f"  {r['expiry']}  ({r['dow']:9s})  +{r['gap_from_prev']}d  {r['trade_days']} trade days{flag}{flag2}")

# Compare against placeholder calendar
print()
print("=== Discrepancies vs hardcoded calendar (placeholders being checked) ===")
placeholder_set = set(SENSEX_WEEKLY_EXPIRIES)
actual_set = set(df['expiry'].tolist())
in_placeholder_not_actual = sorted(placeholder_set - actual_set)
in_actual_not_placeholder = sorted(actual_set - placeholder_set)

print(f"\nIn placeholder list but NOT in actual data:")
for d in in_placeholder_not_actual:
    print(f"  {d}  ({pd.Timestamp(d).day_name()})")
print(f"\nIn actual data but NOT in placeholder list (these need to be ADDED):")
for d in in_actual_not_placeholder:
    print(f"  {d}  ({pd.Timestamp(d).day_name()})")

# Output the new corrected list code
print("\n=== CORRECTED SENSEX_WEEKLY_EXPIRIES (paste into lib/expiry_calendar.py) ===")
print("SENSEX_WEEKLY_EXPIRIES = [")
for _, r in df.iterrows():
    note = "" if r['dow'] == 'Thursday' else f"  # SHIFTED ({r['dow']})"
    if r['gap_from_prev'] > 14: note = f"  # MONTHLY"
    print(f"    date({r['expiry'].year}, {r['expiry'].month}, {r['expiry'].day}),{note}")
print("]")

# Output special notes for holiday-shifted Thursdays
print("\n=== SENSEX_SPECIAL (holiday-shifted) ===")
print("SENSEX_SPECIAL = {")
for _, r in df.iterrows():
    if r['dow'] != 'Thursday' and r['gap_from_prev'] <= 14:
        print(f'    date({r["expiry"].year}, {r["expiry"].month}, {r["expiry"].day}): "{r["dow"]} — likely holiday-shifted",')
print("}")
