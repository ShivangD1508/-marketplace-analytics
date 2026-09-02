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
15 models · 228 tests · 691,505 events · dbt build passes clean in ~25s
```

---

## Quickstart

```bash
make setup         # install dependencies
make sample-data   # write the nine raw CSVs (6 seconds, deterministic)
make build         # dbt build: 15 models, 228 tests
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

**The committed figures and row counts come from the generated dataset**, and the
repository is honest about that rather than quietly implying otherwise. The Olist
data is not redistributable and Kaggle needs credentials, so
[`scripts/generate_sample_data.py`](scripts/generate_sample_data.py) produces a
seeded, deterministic dataset with the real one's exact schema — and, more to the
point, with its exact *awkwardness*: nullable lifecycle timestamps, terminal
statuses that truncate the timestamp chain, ~2% of delivered orders whose
approval time was never recorded, ~2% of products with no category, orders
settled across two payment instruments, reviews stamped before the survey that
prompted them, and a repeat-purchase rate near 3%. Those are the cases the event
layer has to have an opinion about; a clean synthetic dataset would let every
hard decision below go unmade. Point `raw_data_dir` at a real download and
`make build` runs unchanged.

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

`int_events` is one row per `(order_id, event_name)`. Nine event types, 691,505
rows over 100,000 orders.

## The taxonomy

| Event | Rank | Actor | Timestamp | Source |
|---|---|---|---|---|
| `order_placed` | 1 | buyer | `order_purchase_timestamp` | orders |
| `payment_confirmed` | 2 | buyer | **derived** — borrowed from `order_approved_at` | payments |
| `order_approved` | 3 | system | `order_approved_at` | orders |
| `order_shipped` | 4 | seller | `order_delivered_carrier_date` | orders |
| `order_delivered` | 5 | system | `order_delivered_customer_date` | orders |
| `order_canceled` / `order_unavailable` | 5 | system | **derived** — last point the order was alive | orders |
| `review_requested` | 6 | system | `review_creation_date` | reviews |
| `review_submitted` | 7 | buyer | `review_answer_timestamp` | reviews |

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
borrowed timestamp and `is_unobserved_step = true` when the time is unknown. In
this dataset that is 1,977 approvals — 2% of the funnel's second step, which
would otherwise look like a real leak.

### Out-of-order events

Some events are stamped earlier than events that canonically precede them —
review answers logged before the survey that prompted them, from source clock
skew. The policy is: **retain the row with its raw timestamp, flag it, never
silently reorder or drop it.** `is_out_of_order` marks 623 such events.

Correspondingly, **event ordering is by `lifecycle_rank`, never by timestamp.**
Two reasons. `payment_confirmed` and `order_approved` deliberately share a
borrowed timestamp, so a timestamp sort would order them arbitrarily; and an
out-of-order arrival must not be allowed to rewrite an order's funnel position.

The one thing that is *not* tolerated is an event stamped before its order
existed. That would make `hours_since_order_placed` negative and quietly corrupt
every duration aggregate downstream, so it is a hard failure, asserted by
[`assert_no_event_precedes_order_placed.sql`](dbt/tests/assert_no_event_precedes_order_placed.sql).

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
    select max(order_placed_at) - interval '120' day from {{ this }}
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

Sizing the window is an empirical question, not a taste one. Measured on this
data, placement-to-last-event is 11 days at p50, 44 at p99, 66 at p99.9 and 116
at the maximum — so the 120-day default clears the observed worst case. Re-measure
before trusting it elsewhere:

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
  holding back 8,834 deliveries later than 2018-09-17
  [1] late-arriving deliveries captured: +8,834 (expected +8,834)
  [2] duplicate event_ids after incremental run: 0
  [3] restated orders present in output: 8,834
  [4] incremental rows 691,505 vs full-refresh rows 691,505
       every column matches row for row
  PASSED
```

This script earned its place: it is what caught the `max(event_ts)` watermark bug
described above, which passed all 228 dbt tests.

---

# Testing

228 tests: 224 schema tests across all 15 models, and 4 singular tests. Failures
are persisted (`store_failures: true`) into a `test_failures` schema so a red
build can be inspected rather than re-run.

Schema tests cover the usual keys, nullability, accepted values and referential
integrity, plus grain assertions (`unique_combination_of_columns`) on every model
whose key is compound, and range checks on every duration and money column.

The four singular tests each guard something no generic test can express:

| Test | What it guards |
|---|---|
| [`assert_no_event_precedes_order_placed`](dbt/tests/assert_no_event_precedes_order_placed.sql) | The event layer's load-bearing invariant: every event is measured from its order's placement, so a negative offset would corrupt every duration downstream. Relational — compares a row against a *different* row's timestamp. |
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

- **The published figures are from generated data.** Schema-identical to the
  real dataset and built to reproduce its awkward cases, but the marketplace
  dynamics are modelled, not measured. Swap in the Kaggle download and every
  command works unchanged; the numbers in ANALYSIS.md will move.
- **The generated geolocation table is smaller than the real one** (158K rows vs
  ~1M). `stg_geolocation` collapses it to one centroid per zip prefix either way,
  so nothing downstream changes.
- **Sessions are thin by construction.** With ~1.03 orders per buyer there is
  little to sessionize. The rules are written for a stream that also carries
  clickstream events, which is the point of matching the 30-minute convention.
- **`fct_orders` is built from staging, not from `int_events`.** The event table
  is the *narrative* of an order; the fact table is its *state*. Deriving state by
  pivoting the event stream back into columns would make every order metric
  depend on the event taxonomy staying frozen.
- **No fourth mart.** A category-mix or seller-performance mart would be easy and
  would answer nothing that was asked.
