"""Dagster definitions for the marketplace event layer.

The whole dbt project is loaded as a graph of Dagster assets, so the lineage
Dagster shows is the real dbt lineage -- staging models, int_events, int_sessions
and the four marts each appear as their own asset, and dbt's own tests run as
asset checks attached to the model they guard.

Three things here are deliberate rather than boilerplate:

  * The raw CSVs are their own assets, declared under the same keys dbt gives
    its sources, so "the data landed" and "the models ran" are separate nodes
    that fail and retry independently. Folding ingestion into the dbt run would
    make a Kaggle outage look like a broken model.

  * int_events runs incrementally on the schedule and is never full-refreshed
    automatically. A full refresh of the event table is a deliberate act -- see
    README "Incrementality" -- so it is exposed as a separate job an operator
    triggers, not as something a retry can trip into.

  * The schedule is daily rather than hourly. The upstream is a daily CSV drop;
    running more often would just re-read the same file and rewrite the same
    120-day restatement window.

Run it locally with:
    pip install -r orchestration/requirements.txt
    dagster dev -f orchestration/dagster_pipeline.py
"""

import subprocess
from pathlib import Path

from dagster import (
    AssetExecutionContext,
    AssetKey,
    AssetSpec,
    Config,
    DefaultScheduleStatus,
    Definitions,
    MaterializeResult,
    RetryPolicy,
    ScheduleDefinition,
    define_asset_job,
    multi_asset,
)
from dagster_dbt import (
    DagsterDbtTranslator,
    DbtCliResource,
    DbtProject,
    dbt_assets,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DBT_PROJECT_DIR = REPO_ROOT / "dbt"
RAW_DATA_DIR = REPO_ROOT / "data" / "raw"

dbt_project = DbtProject(
    project_dir=DBT_PROJECT_DIR,
    profiles_dir=DBT_PROJECT_DIR,
)
dbt_project.prepare_if_dev()

# dbt sources appear in Dagster under the key ["olist", "<table>"]. The
# ingestion asset declares exactly those keys, so the raw files become genuine
# upstream nodes of the dbt graph with no key remapping: land the CSVs, and
# every staging model downstream of them is ready to run.
SOURCE_TABLES = [
    "olist_customers_dataset",
    "olist_geolocation_dataset",
    "olist_order_items_dataset",
    "olist_order_payments_dataset",
    "olist_order_reviews_dataset",
    "olist_orders_dataset",
    "olist_products_dataset",
    "olist_sellers_dataset",
    "product_category_name_translation",
]


@multi_asset(
    specs=[
        AssetSpec(
            key=AssetKey(["olist", table]),
            group_name="ingestion",
            kinds={"python", "csv"},
            description=f"Raw {table}.csv on disk.",
        )
        for table in SOURCE_TABLES
    ],
    retry_policy=RetryPolicy(max_retries=3, delay=60),
)
def olist_raw_csv(context: AssetExecutionContext):
    """Land the nine raw Olist CSVs.

    Kaggle where credentials are configured, and the seeded generator otherwise,
    so the pipeline is runnable end to end without them. Kept as its own asset
    rather than folded into the dbt run: a Kaggle outage should show up as a
    failed ingestion, not as a broken model.
    """
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

    download = subprocess.run(
        ["python", str(REPO_ROOT / "scripts" / "download_olist.py"), "--out", str(RAW_DATA_DIR)],
        capture_output=True,
        text=True,
    )
    if download.returncode == 0:
        source = "kaggle"
        context.log.info("Fetched the Kaggle dataset.")
    else:
        source = "generated"
        detail = download.stderr.strip().splitlines()[-1] if download.stderr.strip() else "no detail"
        context.log.warning("Kaggle download unavailable (%s). Falling back to the generator.", detail)
        subprocess.run(
            [
                "python", str(REPO_ROOT / "scripts" / "generate_sample_data.py"),
                "--orders", "100000", "--out", str(RAW_DATA_DIR),
            ],
            check=True,
        )

    for table in SOURCE_TABLES:
        path = RAW_DATA_DIR / f"{table}.csv"
        yield MaterializeResult(
            asset_key=AssetKey(["olist", table]),
            metadata={
                "source": source,
                "path": str(path),
                "size_bytes": path.stat().st_size if path.exists() else 0,
            },
        )


class MarketplaceDbtTranslator(DagsterDbtTranslator):
    """Group dbt assets by layer so the graph reads staging -> intermediate -> marts."""

    def get_group_name(self, dbt_resource_props: dict):
        path = dbt_resource_props.get("fqn", [])
        for layer in ("staging", "intermediate", "marts"):
            if layer in path:
                return layer
        return super().get_group_name(dbt_resource_props)


class DbtBuildConfig(Config):
    """Run-time knobs for the dbt build.

    `full_refresh` is exposed as config rather than hardcoded so that rebuilding
    int_events from scratch is an explicit, auditable run rather than something
    that can happen by accident on a retry.
    """

    full_refresh: bool = False


@dbt_assets(
    manifest=dbt_project.manifest_path,
    dagster_dbt_translator=MarketplaceDbtTranslator(),
)
def marketplace_dbt_assets(
    context: AssetExecutionContext,
    dbt: DbtCliResource,
    config: DbtBuildConfig,
):
    """Every dbt model and test, as assets and asset checks.

    `dbt build` rather than `run` then `test`: build interleaves them, so a model
    whose test fails does not have its downstream models built on top of
    known-bad data.
    """
    args = ["build"]
    if config.full_refresh:
        args.append("--full-refresh")
    yield from dbt.cli(args, context=context).stream()


daily_refresh = define_asset_job(
    name="daily_marketplace_refresh",
    selection="*",
    description="Land the raw files, then build and test every model incrementally.",
)

# dbt asset keys are prefixed with their model directory, so int_events is
# addressed as intermediate/int_events.
INT_EVENTS_KEY = AssetKey(["intermediate", "int_events"])

full_refresh = define_asset_job(
    name="int_events_full_refresh",
    selection=[INT_EVENTS_KEY],
    config={
        "ops": {
            marketplace_dbt_assets.op.name: {
                "config": {"full_refresh": True},
            }
        }
    },
    description=(
        "Rebuild int_events from scratch. Manual only: a full refresh discards "
        "the incremental history, so it is an operator decision -- run it after "
        "changing the event taxonomy or the restatement window, not on a timer."
    ),
)

daily_schedule = ScheduleDefinition(
    job=daily_refresh,
    cron_schedule="0 6 * * *",
    default_status=DefaultScheduleStatus.STOPPED,
    description="06:00 daily, after the upstream marketplace export lands.",
)

defs = Definitions(
    assets=[olist_raw_csv, marketplace_dbt_assets],
    jobs=[daily_refresh, full_refresh],
    schedules=[daily_schedule],
    resources={
        "dbt": DbtCliResource(
            project_dir=dbt_project,
            profiles_dir=str(DBT_PROJECT_DIR),
        )
    },
)
