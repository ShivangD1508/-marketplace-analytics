"""Prove that int_events is correctly incremental, not merely configured as such.

`materialized='incremental'` is easy to write and easy to get subtly wrong. The
two failure modes that matter are invisible in a normal `dbt build`:

  1. Late-arriving lifecycle data is missed. An order placed in March is
     delivered in May. A naive incremental filter on `event_ts > max(event_ts)`
     never revisits that order, so the order_delivered event never lands and the
     funnel under-reports delivery forever.

  2. Re-running duplicates or diverges. The incremental result must be identical
     to what a full refresh on the same input would produce -- otherwise the
     table's contents depend on the history of how it was built, which is
     unauditable.

This script checks both, against a throwaway database, by replaying an actual
late-arrival scenario:

    t0  Build from a "yesterday" snapshot in which the most recent orders have
        not yet been delivered (delivered_at nulled, status set to 'shipped').
    t1  Swap in the full data and run *incrementally* -- no --full-refresh.
    t2  Assert the delivered events appeared, event_ids are still unique, and
        the incremental table matches a full refresh of the same input row for
        row.

Usage:
    python scripts/verify_incremental.py
Exit code is 0 on success, 1 on any failed assertion.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
DBT_DIR = REPO / "dbt"
RAW = REPO / "data" / "raw"
DB = REPO / "data" / "marketplace_inc_test.duckdb"
TARGET = "incremental_test"

# At t0, hold back every delivery that happened within this many days of the
# newest purchase in the file. Anchoring on the purchase frontier rather than on
# the delivery frontier is what makes the held-back set large enough to be a real
# test: deliveries trail off in a thin tail, so "the last 20 delivery-days" is a
# dozen orders, while "delivered after the last 30 days of purchasing" is
# thousands of genuinely in-flight orders.
HOLDBACK_DAYS = 30


def run_dbt(args: list[str], raw_dir: Path) -> None:
    cmd = [
        "dbt", *args,
        "--target", TARGET,
        "--profiles-dir", str(DBT_DIR),
        "--project-dir", str(DBT_DIR),
        "--vars", f"{{raw_data_dir: '{raw_dir}'}}",
    ]
    result = subprocess.run(cmd, cwd=DBT_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout[-4000:])
        print(result.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"dbt failed: {' '.join(args)}")


def snapshot_events() -> pd.DataFrame:
    con = duckdb.connect(str(DB), read_only=True)
    try:
        return con.execute(
            """
            select event_id, event_name, event_ts, order_id, lifecycle_rank,
                   ts_is_derived, is_unobserved_step, is_out_of_order, properties
            from main_intermediate.int_events
            order by event_id
            """
        ).fetch_df()
    finally:
        con.close()


def main() -> int:
    if not (RAW / "olist_orders_dataset.csv").exists():
        raise SystemExit(f"No raw data in {RAW}. Run `make sample-data` first.")

    failures: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="inc_verify_"))
    yesterday_dir = tmp / "yesterday"
    yesterday_dir.mkdir(parents=True)

    print(f"Staging a 't0' snapshot in {yesterday_dir} ...")
    for csv in RAW.glob("*.csv"):
        if csv.name != "olist_orders_dataset.csv":
            shutil.copy2(csv, yesterday_dir / csv.name)

    orders = pd.read_csv(RAW / "olist_orders_dataset.csv", parse_dates=[
        "order_purchase_timestamp", "order_approved_at",
        "order_delivered_carrier_date", "order_delivered_customer_date",
        "order_estimated_delivery_date",
    ])
    cutoff = orders["order_purchase_timestamp"].max() - pd.Timedelta(days=HOLDBACK_DAYS)
    late = orders["order_delivered_customer_date"] > cutoff
    n_late = int(late.sum())
    print(f"  holding back {n_late:,} deliveries later than {cutoff:%Y-%m-%d}")

    t0 = orders.copy()
    t0.loc[late, "order_delivered_customer_date"] = pd.NaT
    t0.loc[late, "order_status"] = "shipped"
    t0.to_csv(yesterday_dir / "olist_orders_dataset.csv", index=False,
              date_format="%Y-%m-%d %H:%M:%S")

    if DB.exists():
        DB.unlink()

    print("\nt0: full refresh on the held-back snapshot ...")
    run_dbt(["run", "--select", "+int_events", "--full-refresh"], yesterday_dir)
    before = snapshot_events()
    n_delivered_before = int((before.event_name == "order_delivered").sum())
    print(f"  {len(before):,} events, {n_delivered_before:,} order_delivered")

    print("\nt1: incremental run against the complete data (no --full-refresh) ...")
    # `+int_events`, not `int_events`: the staging models are views over
    # read_csv() with the data path baked in at creation time, so they must be
    # recreated against the new path or the incremental run silently re-reads
    # the t0 snapshot and appears to be a no-op.
    run_dbt(["run", "--select", "+int_events"], RAW)
    after = snapshot_events()
    n_delivered_after = int((after.event_name == "order_delivered").sum())
    print(f"  {len(after):,} events, {n_delivered_after:,} order_delivered")

    # --- Assertion 1: the late deliveries were picked up ---------------------
    gained = n_delivered_after - n_delivered_before
    print(f"\n[1] late-arriving deliveries captured: +{gained:,} (expected +{n_late:,})")
    if gained != n_late:
        failures.append(
            f"expected the incremental run to add {n_late:,} order_delivered events, got {gained:,}. "
            "The lookback window is probably too short, or the filter is on event_ts "
            "rather than on order activity."
        )

    # --- Assertion 2: no duplicates -----------------------------------------
    dupes = int(after.event_id.duplicated().sum())
    print(f"[2] duplicate event_ids after incremental run: {dupes}")
    if dupes:
        failures.append(f"{dupes} duplicate event_ids -- delete+insert is not matching on unique_key")

    # --- Assertion 3: restated rows were actually rewritten ------------------
    # The orders that gained a delivery must also have had their *existing*
    # events refreshed, since the model reprocesses whole orders.
    changed_orders = set(orders.loc[late, "order_id"])
    stale = after[
        after.order_id.isin(changed_orders) & (after.event_name == "order_shipped")
    ]
    print(f"[3] restated orders present in output: {stale.order_id.nunique():,}")
    if stale.order_id.nunique() != n_late:
        failures.append("restated orders lost events during the incremental rewrite")

    # --- Assertion 4: incremental == full refresh ----------------------------
    print("\nt2: full refresh on the same complete data, for comparison ...")
    run_dbt(["run", "--select", "+int_events", "--full-refresh"], RAW)
    full = snapshot_events()

    print(f"[4] incremental rows {len(after):,} vs full-refresh rows {len(full):,}")
    if len(after) != len(full):
        failures.append(f"row count diverged: incremental {len(after):,}, full refresh {len(full):,}")
    else:
        merged = after.merge(full, on="event_id", suffixes=("_inc", "_full"), how="outer", indicator=True)
        unmatched = int((merged._merge != "both").sum())
        if unmatched:
            failures.append(f"{unmatched} event_ids present in one build but not the other")
        else:
            diff_cols = []
            for col in ("event_name", "event_ts", "order_id", "lifecycle_rank",
                        "ts_is_derived", "is_unobserved_step", "is_out_of_order", "properties"):
                a, b = merged[f"{col}_inc"], merged[f"{col}_full"]
                n_diff = int((a.astype(str) != b.astype(str)).sum())
                if n_diff:
                    diff_cols.append(f"{col} ({n_diff:,} rows)")
            if diff_cols:
                failures.append("incremental and full-refresh disagree on: " + ", ".join(diff_cols))
            else:
                print("     every column matches row for row")

    shutil.rmtree(tmp, ignore_errors=True)
    if DB.exists():
        DB.unlink()

    print()
    if failures:
        print("FAILED")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASSED -- int_events handles late-arriving data and is idempotent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
