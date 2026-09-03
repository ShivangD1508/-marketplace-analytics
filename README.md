# marketplace-analytics

A dbt project that turns ~100K raw marketplace orders into a documented event
layer and four analytics marts, on the
[Olist Brazilian E-Commerce](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
dataset.

The dataset ships no event stream. The interesting part of this project is that
the event schema is **designed here** — every decision a product analyst makes
when instrumenting a flow (what counts as an event, what its name is, what goes
in a column versus a property, whose identity it belongs to, what to do with
timestamps that do not exist or arrive out of order) had to be made explicitly
and is written down below rather than left implicit in SQL.

- **Lineage docs:** <https://shivangd1508.github.io/-marketplace-analytics/> —
  published from `dbt docs generate`, clickable DAG, every model and column
  documented. *(Needs GitHub Pages set to "GitHub Actions" once, in repo
  Settings → Pages.)*
- **Analysis:** [ANALYSIS.md](ANALYSIS.md) — funnel drop-off, retention curves,
  delivery time by region.

```
15 models · 247 tests · 690,828 events from 99,441 real orders · dbt build passes clean in ~25s
```

---

## Quickstart

```bash
make setup         # install dependencies
make sample-data   # write the nine raw CSVs (6 seconds, deterministic)
make build         # dbt build: 15 models, 247 tests
make verify        # prove int_events handles late-arriving data
make charts        # regenerate the three figures in ANALYSIS.md
make docs          # build the documentation site locally
```

`make all` runs the lot. There is nothing to configure: the warehouse is DuckDB,
`profiles.yml` is checked in because it holds no credentials, and the project has
**zero dbt package dependencies**, so a build works offline and CI never depends
on hub.getdbt.com being reachable. The handful of things that would normally come
from `dbt_utils` — a null-safe surrogate key, `expression_is_true`,
`accepted_range`, `unique_combination_of_columns` — are in [`dbt/macros/`](dbt/macros/).

## Getting the data

Two paths, and **the dbt project cannot tell them apart**. Both write the same
nine CSVs, with the same filenames and column names, into `data/raw/`; every
model reads them through the `raw_data_dir` var.

| | Command | Needs |
|---|---|---|
| Real dataset | `make download-data` | Kaggle API credentials in `~/.kaggle/kaggle.json` |
| Generated | `make sample-data` | nothing |

**The models are validated against the real Olist download**; the committed
charts in ANALYSIS.md have not yet been regenerated from it, and the repository
says so rather than quietly implying otherwise. The Olist
data is not redistributable and Kaggle needs credentials, so
[`scripts/generate_sample_data.py`](scripts/generate_sample_data.py) produces a
seeded, deterministic dataset with the real one's exact schema — and, more to the
point, with its exact *awkwardness*: nullable lifecycle timestamps, terminal
statuses that truncate the timestamp chain, ~2% of delivered orders whose
approval time was never recorded, ~2% of products with no category, orders
settled across two payment instruments, reviews stamped before the survey that
prompted them, and a repeat-purchase rate near 3%. Those are the cases the event
layer has to have an opinion about; a clean synthetic dataset would let every
hard decision below go unmade — and running against the real download proved the
point, since it broke four assumptions the generated data was too clean to
surface (see *What the real data changed*, below). Point `raw_data_dir` at either
one and `make build` runs unchanged.

## Layout

```
marketplace-analytics/
├── README.md                     event schema decisions, and why
├── ANALYSIS.md                   three charts, a paragraph each
├── dbt/
│   ├── models/staging/           one model per source; typing, renaming, tests
│   ├── models/intermediate/
│   │   ├── int_events.sql        the long event table (incremental)
│   │   └── int_sessions.sql      sessionization rules, made explicit
│   ├── models/marts/
│   │   ├── fct_orders.sql
│   │   ├── fct_funnel_steps.sql
│   │   ├── agg_cohort_retention.sql
│   │   └── dim_customers.sql
│   ├── tests/                    4 singular tests
│   └── macros/                   surrogate key, region mapping, generic tests
├── orchestration/dagster_pipeline.py
├── scripts/
│   ├── generate_sample_data.py   schema-identical dataset
│   ├── download_olist.py         the real Kaggle fetch
│   ├── verify_incremental.py     proves int_events is genuinely incremental
│   └── build_charts.py           the three figures
└── .github/workflows/            dbt build on every push; docs to Pages
```

---

# Event schema

`int_events` is **one row per occurrence**. Nine event types, 690,828 rows over
99,441 orders on the published Olist dataset.

Occurrence, not `(order_id, event_name)` — a distinction the real data forced.
547 orders carry more than one review, so that grain would have had to silently
drop a buyer's second review to stay unique, and an event table whose key cannot
represent something happening twice is not an event table. `event_source_id`
carries whatever distinguishes repeat occurrences (`review_id` on the review
events, null on the lifecycle events, which happen at most once per order) and
is part of both the grain and `event_id`.

## The taxonomy

| Event | Rank | Actor | Timestamp | `event_source_id` | Source |
|---|---|---|---|---|---|
| `order_placed` | 1 | buyer | `order_purchase_timestamp` | — | orders |
| `payment_confirmed` | 2 | buyer | **derived** — borrowed from `order_approved_at` | — | payments |
| `order_approved` | 3 | system | `order_approved_at` | — | orders |
| `order_shipped` | 4 | seller | `order_delivered_carrier_date` | — | orders |
| `order_delivered` | 5 | system | `order_delivered_customer_date` | — | orders |
| `order_canceled` / `order_unavailable` | 5 | system | **derived** — last point the order was alive | — | orders |
| `review_requested` | 6 | system | `review_creation_date` | `review_id` | reviews |
| `review_submitted` | 7 | buyer | `review_answer_timestamp` | `review_id` | reviews |

### Naming

`object_pastTenseVerb`, lower snake case. Past tense because an event is a fact
that already happened — `order_ship` would be a command, `order_shipped` is a
record. Object first because it sorts usefully and lets a consumer glob a whole
object's stream with `order_%` without a taxonomy lookup.

### Columns versus properties

The rule: **a field is a column if it is meaningful for every event type.** Ids,
timestamps, actor, and provenance flags are columns. Everything specific to one
event type — `review_score`, `payment_installments`, `delivery_days`,
`is_late` — goes into a single JSON `properties` column.

This is not just tidiness. It means adding a tenth event type never adds a
column, so the incremental model never has to migrate its schema to carry a new
event. The alternative, a wide table with one nullable column per event-specific
field, would be 99% null and would need `on_schema_change` gymnastics on every
taxonomy change.

`properties` is JSON-encoded text rather than a native struct so the model ports
to a warehouse without one, and a schema test asserts `json_valid(properties)` on
every row.

### Identity resolution

Two identities, and getting this wrong silently ruins the analysis:

- **Buyer:** `customer_unique_id`. The raw file also has `customer_id`, which
  Olist mints fresh for every order — it identifies a *checkout*, not a person.
  Keying the event stream on it would make every buyer a first-time buyer,
  reporting a 0% repeat rate and a flat retention curve that looks like a
  finding but is an artefact. `customer_id` is carried through as a column, for
  lineage back to the raw file, and used for nothing else.
- **Seller:** `seller_id`, but only for orders with exactly one seller. An order
  fulfilled by three sellers has no single seller actor, so `actor_id` is left
  null and `properties.seller_count` says why, rather than arbitrarily picking
  the first one and quietly attributing a shipment to a seller who did not make it.

`actor_type` is `buyer`, `seller` or **`system`**. The third value is a real
decision: approval is a payment-gateway automation and delivery is the carrier's
act. Neither marketplace party performs them, and labelling them "buyer" because
they happen to a buyer's order would make any "buyer activity" metric wrong. A
schema test asserts system events carry no `actor_id`, and buyer events always do.

### Derived timestamps

Some things that plainly happen have no timestamp anywhere in the source. The
options are to drop the event, invent a time, or borrow one and say so. This
project borrows and flags:

- **`payment_confirmed`** — the payments table has no timestamp column at all.
  Approval *is* the payment authorisation clearing, so it borrows
  `order_approved_at`, falling back to purchase time for orders that never
  reached approval.
- **`order_canceled` / `order_unavailable`** — stamped at the last point the
  order was demonstrably alive (carrier handoff, else approval, else purchase).

Every such row has `ts_is_derived = true` and a `properties.ts_borrowed_from`
naming the column it came from, so any timing analysis can exclude them with one
predicate instead of re-deriving which events are trustworthy.

### Unobserved versus unreached

The subtlest distinction in the model. An order with `status = 'delivered'` and a
null `order_approved_at` **was** approved — the timestamp simply was not recorded.
Emitting no `order_approved` event there would understate approvals and put a
phantom drop-off in the funnel.

So `stg_orders` separates two ideas that a wide table conflates: `reached_approved`
is a statement about status, `approved_at is not null` is a statement about
observability. The event is emitted whenever the order *reached* the step, with a
borrowed timestamp and `is_unobserved_step = true` when the time is unknown. On the real
dataset that is 24 steps; on the generated one it is 1,977, deliberately
exaggerated so the case is exercised at a visible scale.

### Out-of-order events

Some events are stamped earlier than events that canonically precede them —
review answers logged before the survey that prompted them, carrier handoffs
recorded before the purchase. The policy is: **retain the row with its raw
timestamp, flag it, never silently reorder or drop it.** `is_out_of_order` marks
15,155 such events on the real dataset.

Correspondingly, **event ordering is by `lifecycle_rank`, never by timestamp.**
Two reasons. `payment_confirmed` and `order_approved` deliberately share a
borrowed timestamp, so a timestamp sort would order them arbitrarily; and an
out-of-order arrival must not be allowed to rewrite an order's funnel position.

This project originally asserted something stronger — that no event ever
precedes its own order — and the real data proved it false. 166 orders are
stamped as handed to the carrier *before* they were purchased, most by minutes
but one by 171 days; 303 events in total precede their order.

Clamping those timestamps would fabricate data and dropping the orders would
lose real ones, so the sharper flag `is_before_order_placed` names the condition
and `hours_since_order_placed` stays **signed** — negative, honestly, rather than
clamped to a zero that would understate ship times while looking clean. Anything
aggregating durations filters on that flag first.

[`assert_no_event_precedes_order_placed.sql`](dbt/tests/assert_no_event_precedes_order_placed.sql)
therefore asserts the claim that is actually defensible: **the source is allowed
to be wrong; the model is not allowed to be wrong about the source.** It fails if
an early event is unflagged, or if the flag and the signed offset drift apart.

### Source defects, flagged not fixed

`stg_orders` carries three flags for anomalies that are the source's, not the
model's — `has_shipment_before_purchase` (166), `has_delivery_before_shipment`
(23), `has_delivery_without_delivered_status` (6). The corresponding tests **warn
at the known counts and error if the rate roughly doubles**, which keeps a build
green on "Olist is still Olist" and turns it red on "something changed
upstream". Failing rows are persisted to the `test_failures` schema either way.

### What is deliberately not an event

- **`seller_shipping_deadline_set`** — `shipping_limit_date` is a contractual
  deadline, not something that occurred. It lives in
  `properties.shipping_limit_at` on `order_shipped`, where it is still available
  to compute SLA breaches, without inflating the stream by one row per item.
- **Per-item events.** The order is the grain at which this marketplace's
  lifecycle actually moves; item counts and seller counts ride along as
  properties. Item-grain events would 1.2× the table for no question anyone asked.

---

# Sessions

`int_sessions` groups buyer events into sessions on a 30-minute inactivity gap.
Three rules, each of which changes the answer:

1. **Only buyer-initiated events open or extend a session.** `order_shipped` and
   `order_delivered` happen to a buyer while they are asleep. Feeding fulfilment
   events into sessionization would glue a January purchase to its February
   delivery into one 40-day "session".
2. **Events with derived timestamps are excluded from the input.**
   `payment_confirmed` is buyer-initiated but borrows a timestamp from hours
   later, so admitting it would inflate session duration with a payment-gateway
   artefact rather than buyer behaviour. It is still an event; it just does not
   define session boundaries.
3. **A gap over `session_inactivity_minutes` (default 30) starts a new session.**
   30 minutes is the web-analytics convention, and matching it is the point: a
   session here means what it means in the clickstream this layer is designed to
   eventually merge with.

**The honest consequence:** 99.99% of sessions contain exactly one event. Olist
buyers place one order and never return, so a session table over transaction data
is not a busy object. That is a finding, not a defect — and defining sessions
explicitly is what makes it a statement rather than a guess. The model header
says so in the code, too.

---

# Incrementality

`int_events` is `materialized='incremental'`, `delete+insert` on `event_id`,
which is a deterministic `md5(order_id, event_name)`. Determinism is what makes
the rewrite idempotent: re-running a window replaces rows rather than duplicating
them.

**The restatement window is the part that is easy to get wrong and impossible to
notice.** The window is anchored on the *purchase frontier* — `max(order_placed_at)` —
and selects orders by when they were **placed**:

```sql
where o.placed_at >= (
    select max(order_placed_at) - interval '270' day from {{ this }}
)
```

The obvious alternative, `event_ts > max(event_ts)`, is wrong here, and wrong
silently. An order's events keep arriving long after the order does: a March
purchase is delivered in April and reviewed in May. So the newest `event_ts` in
the table always belongs to an *old* order and runs months ahead of the orders
still in flight — in this dataset the last event is 2019-01-17 while the last
purchase is 2018-10-17. A watermark of `max(event_ts) - lookback` therefore sits
in the future relative to everything still changing, and skips precisely the
late-arriving deliveries the window exists to catch. The model builds, every test
passes, and the funnel under-reports delivery forever.

### Sizing it is a trade-off with no free answer

Measured on the real dataset, placement-to-last-event is 13 days at p50, 56 at
p99, 185 at p99.9 — and **528 at the maximum**. That long tail means correctness
and cost trade off directly:

| Window | Orders whose lifecycle outruns it | Share of table reprocessed |
|---|---|---|
| 90d | 338 (0.34%) | 9.6% |
| 190d | 89 (0.09%) | 30.8% |
| **270d** | **33 (0.03%)** | **49.7%** |
| 550d | 0 (0.00%) | **93.3%** |

A window wide enough to be strictly correct reprocesses 93% of the table, at
which point the model is incremental in name only. So the default is **270 days**,
and the remaining 33 orders are swept up by the **weekly full refresh** the
Dagster DAG schedules — that job is load-bearing, not hygiene.

The real fix is a source-side `updated_at` column, which Olist does not have.
With one, the window collapses to "rows changed since the last run" and the
trade-off disappears entirely. Absent that, a time-windowed strategy can be cheap
or exhaustive but not both, and the gap has to be closed by something else rather
than wished away.

Re-measure before trusting these numbers elsewhere:

```sql
select quantile_cont(span, [0.5, 0.99, 0.999]) , max(span) from (
  select date_diff('hour', min(order_placed_at), max(event_ts)) / 24.0 as span
  from int_events group by order_id
)
```

## Proving it

Configuration is not behaviour, so [`scripts/verify_incremental.py`](scripts/verify_incremental.py)
(`make verify`, and a step in CI) replays an actual late-arrival scenario against
a throwaway database:

```
t0  build from a snapshot where recent orders are not yet delivered
t1  swap in the complete data and run INCREMENTALLY
t2  assert: the deliveries appeared, no duplicate event_ids, restated orders
    kept their earlier events, and the incremental table matches a full
    refresh row for row, column for column
```

```
  restatement window: 270 days back from 2018-10-17
  of 11 held-back deliveries, 11 are in-window and 0 predate it
  [1] in-window late deliveries captured: +11 (expected +11)
  [2] duplicate event_ids after incremental run: 0
  [3] restated orders present in output: 11
  [4] incremental rows 690,828 vs full-refresh rows 690,828
       all 690,828 shared rows match column for column
  PASSED
```

Note what it does **not** assert: that no event is ever missed. It cannot,
because the model no longer claims it. The contract it checks is the one actually
on offer — every order *inside* the window is captured exactly, and any
divergence from a full refresh consists only of orders outside it. A test that
asserted more than the design promises would either fail forever or force the
window wider than is useful.

This script has earned its place twice. It caught the `max(event_ts)` watermark
bug described above, which passed all 228 dbt tests — and then, on the real data,
it failed again and forced the window from 120 days to 270.

---

# Testing

247 tests across all 15 models, plus 4 singular tests. Failures are persisted
(`store_failures: true`) into a `test_failures` schema so a red build can be
inspected rather than re-run.

**Three tests warn rather than fail, on purpose.** Shipped-before-purchase (166
orders), delivered-before-shipped (23) and cancelled-with-a-delivery-time (6)
describe *source quality*, which this project does not control. They warn at the
known counts and **error if the rate roughly doubles** — so the build stays green
on "Olist is still Olist" and turns red on "something changed upstream". That
distinction is the whole point: how much bad data arrived is a question about the
source, whereas whether the model described it correctly is a question about this
repository, and only the second one is a bug here.

Schema tests cover the usual keys, nullability, accepted values and referential
integrity, plus grain assertions (`unique_combination_of_columns`) on every model
whose key is compound, and range checks on every duration and money column.

The four singular tests each guard something no generic test can express:

| Test | What it guards |
|---|---|
| [`assert_no_event_precedes_order_placed`](dbt/tests/assert_no_event_precedes_order_placed.sql) | That anomalies are *labelled*. 166 real orders ship before they are purchased, so the stronger invariant is false; this asserts the flag tracks the condition and the signed offset agrees with it. Relational — compares a row against a *different* row's timestamp. |
| [`assert_funnel_steps_monotonic`](dbt/tests/assert_funnel_steps_monotonic.sql) | A funnel must not widen as it descends. Monotonicity is structural in `fct_funnel_steps`, but "structural" is a claim about a window frame; change the partition and the guarantee vanishes while every row count still looks plausible. |
| [`assert_events_reconcile_to_source_rows`](dbt/tests/assert_events_reconcile_to_source_rows.sql) | Nine union'd CTEs each with their own `WHERE`. A wrong predicate in one loses events silently — the model builds, uniqueness passes, the funnel just reports a smaller number. Nine row-count identities against the raw tables. |
| [`assert_sessions_partition_buyer_events`](dbt/tests/assert_sessions_partition_buyer_events.sql) | Gap-based sessionization is three stacked window functions; a mismatched `ORDER BY` between them double-counts events at boundaries without breaking uniqueness on `session_id`. Reconciles event totals and buyer sets in both directions. |

---

# Orchestration

[`orchestration/dagster_pipeline.py`](orchestration/dagster_pipeline.py) loads the
whole dbt project as a Dagster asset graph, so the lineage Dagster shows is the
real dbt lineage and dbt's tests run as asset checks on the models they guard.

```bash
pip install -r orchestration/requirements.txt
dagster dev -f orchestration/dagster_pipeline.py
```

Three choices worth naming:

- **The raw CSVs are their own assets**, declared under the same keys dbt gives
  its sources, so "the data landed" and "the models ran" fail and retry
  independently. Folding ingestion into the dbt run would make a Kaggle outage
  look like a broken model.
- **`int_events` is never automatically full-refreshed.** Discarding incremental
  history is an operator decision, so it is a separate manual job
  (`int_events_full_refresh`) rather than something a retry can trip into.
- **The schedule is daily, not hourly.** The upstream is a daily export; running
  more often would re-read the same file and rewrite the same 120-day window.

Verified end to end — `dagster job execute -j daily_marketplace_refresh` lands the
data and runs all 243 dbt nodes green.

---

# Notes and limitations

- **The geolocation source is pre-aggregated to one centroid per zip prefix**
  (19K rows rather than the raw ~1M). `stg_geolocation` performs exactly that
  collapse anyway, so every downstream number is identical; only the raw-row
  count differs.
- **The event layer is only as ordered as its source.** 15,155 events (2.2%) are
  flagged out-of-order and 303 precede their own order. They are carried, not
  corrected — any aggregate over durations must filter on
  `is_before_order_placed` first. That is a contract, and contracts get missed.
- **Incrementality is a documented approximation**, not a guarantee. See above:
  0.03% of orders outrun the window and rely on the weekly full refresh.
- **Sessions are thin by construction.** With ~1.03 orders per buyer there is
  little to sessionize. The rules are written for a stream that also carries
  clickstream events, which is the point of matching the 30-minute convention.
- **`fct_orders` is built from staging, not from `int_events`.** The event table
  is the *narrative* of an order; the fact table is its *state*. Deriving state by
  pivoting the event stream back into columns would make every order metric
  depend on the event taxonomy staying frozen.
- **No fourth mart.** A category-mix or seller-performance mart would be easy and
  would answer nothing that was asked.

---

# What the real data changed

The project was built against a schema-identical generated dataset, then run
against the real Kaggle download. Four assumptions broke — each is worth more
than the code that fixed it:

1. **166 orders ship before they are purchased** (one by 171 days), 23 are
   delivered before they ship, and 6 cancelled orders carry a delivery time. The
   "no event precedes its order" invariant was true only of clean data.
2. **547 orders carry more than one review**, which broke the event grain
   outright. A key of `(order_id, event_name)` would have had to drop a buyer's
   second review to stay unique.
3. **The incremental window was 2.3× too small.** Real lifecycles run to 528 days
   against the generated 116, and `verify_incremental.py` failed until the window
   moved from 120 days to 270 — surfacing that full correctness would cost 93% of
   the table.
4. **Two analytical conclusions were wrong.** Retention is not flat, it decays
   threefold; and the least *reliable* region is the Northeast, not the slowest
   one. Both are recorded in [ANALYSIS.md](ANALYSIS.md#a-note-on-what-changed-when-the-real-data-arrived)
   rather than quietly corrected.

Synthetic data reproduces the structure you thought to model. It cannot reproduce
the findings you did not — which is the argument for running against real data
before believing anything, and for building the flagging machinery that made
absorbing all four a matter of hours rather than a rewrite.
