"""Fetch the real Olist Brazilian E-Commerce dataset from Kaggle.

This is the production path. `scripts/generate_sample_data.py` exists so the
project builds without credentials and so CI is reproducible, but nothing in the
dbt project distinguishes the two: both write the same nine CSVs, with the same
column names, into the same directory, and every model reads them through
`raw_data_dir`.

Prerequisites:
  1. A Kaggle account, and its API token saved to ~/.kaggle/kaggle.json
     (Kaggle -> Settings -> API -> Create New Token), chmod 600.
     Or set KAGGLE_USERNAME and KAGGLE_KEY in the environment.
  2. pip install kaggle

Usage:
    python scripts/download_olist.py --out data/raw
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

DATASET = "olistbr/brazilian-ecommerce"

EXPECTED_FILES = [
    "olist_customers_dataset.csv",
    "olist_geolocation_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv",
    "olist_order_reviews_dataset.csv",
    "olist_orders_dataset.csv",
    "olist_products_dataset.csv",
    "olist_sellers_dataset.csv",
    "product_category_name_translation.csv",
]


def have_credentials() -> bool:
    import os

    if os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"):
        return True
    return (Path.home() / ".kaggle" / "kaggle.json").exists()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("data/raw"))
    args = ap.parse_args()

    if shutil.which("kaggle") is None:
        print("The `kaggle` CLI is not installed.  pip install kaggle", file=sys.stderr)
        return 1

    if not have_credentials():
        print(
            "No Kaggle credentials found.\n"
            "  Set KAGGLE_USERNAME and KAGGLE_KEY, or save a token to ~/.kaggle/kaggle.json.\n"
            "  To work without credentials, run `make sample-data` instead -- it produces\n"
            "  the same schema and every model builds against it unchanged.",
            file=sys.stderr,
        )
        return 1

    args.out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        print(f"Downloading {DATASET} ...")
        result = subprocess.run(
            ["kaggle", "datasets", "download", "-d", DATASET, "-p", str(tmp_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            return 1

        archives = list(tmp_path.glob("*.zip"))
        if not archives:
            print("Download produced no archive.", file=sys.stderr)
            return 1

        print(f"Extracting into {args.out}/ ...")
        with zipfile.ZipFile(archives[0]) as zf:
            for name in zf.namelist():
                if name.endswith(".csv"):
                    zf.extract(name, tmp_path)
                    shutil.move(str(tmp_path / name), args.out / Path(name).name)

    missing = [f for f in EXPECTED_FILES if not (args.out / f).exists()]
    if missing:
        print(f"Download is incomplete, missing: {', '.join(missing)}", file=sys.stderr)
        return 1

    print(f"\nAll nine files are in {args.out}/. Run `make build`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
