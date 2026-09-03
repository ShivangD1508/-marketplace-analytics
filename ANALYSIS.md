# Analysis

Three questions, three charts, one paragraph each. Every number here comes from a
query in [`scripts/build_charts.py`](scripts/build_charts.py) run against the
built marts — nothing is transcribed by hand, so regenerating after a model
change either reproduces these figures or visibly contradicts the prose.

Scope: **the published Olist dataset** — 99,441 orders from 96,096 buyers between
2016-09-04 and 2018-10-17, R$15.8M of gross merchandise value, collapsed into
690,828 events.

---

## 1. Funnel drop-off

![Order lifecycle funnel](docs/img/funnel_drop_off.png)

The marketplace loses about 3.6% of orders across the whole lifecycle, and — the
useful finding — it loses them evenly. There is no single broken step to go fix:
approval sheds 1,239 orders, shipping 617, delivery 1,107, review 646. An
operations team hoping this chart would point at one queue will be disappointed,
and that is worth knowing before a quarter is spent on it. Payment method barely
moves approval either (98.8% for boleto and credit card, 99.2% for debit), with
one exception worth a second look: **vouchers approve at only 96.4%**, more than
two points below everything else, which is the one payment-side signal in the
data. The more interesting number is the one that is *not* in the funnel: 2,841
orders fired `review_submitted` without ever reaching `delivered` — the
marketplace surveys cancelled and stuck orders too. Counting raw event hits, more
orders "reach" review than reach delivery, and the funnel is not monotonic at
all. That is why `fct_funnel_steps` carries two columns, `is_reached` and
`is_reached_in_sequence`, and why the chart uses the second. Those buyers average
**1.74 stars** against 4.29 for an on-time delivery, so they are not a rounding
error to be filtered away; they are the angriest population in the dataset, and
modelling the funnel the naive way hides them twice — once by inflating the last
bar, and once by never naming them.

## 2. Retention curves

![Repeat-purchase rate by acquisition cohort](docs/img/cohort_retention.png)

Retention is negligible, and it decays. 0.48% of a cohort places another order in
month 1, falling to 0.24% by month 3 and 0.15% by month 12 — a **threefold
decline**, and almost all of it inside the first quarter. Only 3.12% of buyers
ever place a second order at all, with a mean lifetime value of R$165. The honest
conclusion is that this is a transactional marketplace with very little repeat
behaviour to retain, not a subscription business having a bad quarter — but the
shape still matters: whatever brings the 0.48% back is spent by month 3, so a
retention intervention has a narrow window to act in. Two modelling decisions had
to be right for the chart to say even this much. First, cohorts are keyed on
`customer_unique_id`, not `customer_id`: the raw file mints a fresh `customer_id`
per checkout, so keying on it would report 0.00% forever and the decay would
vanish entirely. Second, `agg_cohort_retention` materialises the full cohort ×
month grid including the zeros and flags `is_fully_observed` — a sparse table
would let a chart interpolate over months with no returning buyers, and every
cohort's final month is partial and would draw a fake cliff. The chart plots only
fully-observed months for exactly that reason.

## 3. Delivery time by region

![Delivery time by buyer region](docs/img/delivery_time_by_region.png)

Geography is the strongest signal in the dataset, and it lives in the tail rather
than the median. A Southeast buyer waits 9 days at the median; a North buyer
waits 20. But the 90th percentiles are 20 days against 37 — the gap widens from
11 days to 17 as you move into the tail, so the North's problem is not that
delivery is uniformly slower, it is that the bad cases are far worse. The
ordering by lateness is different from the ordering by speed, and that is the
finding: **the Northeast, not the North, is the least reliable region** — 14.3%
of its deliveries miss the date promised at checkout, against 9.8% in the slower
North and 7.5% in the Southeast. Being slow and being unreliable are not the same
failure, and only the promise-vs-actual comparison separates them. The cause is
structural rather than regional incompetence: sellers are overwhelmingly
concentrated in the Southeast, so `shipping_lane` explains delivery time better
than buyer region alone — intra-state orders arrive in 6.6 days at the median,
inter-region orders take 13.8. That has a direct revenue consequence through the
review scores: on-time deliveries average 4.29 stars, late ones 2.57. The lever
is not "make the carriers faster", it is seller distribution — every seller
onboarded outside the Southeast converts an inter-region lane into an intra-state
one for the buyers nearest to them.

---

Three charts, and no fourth. Adding a category-mix or a payment-method breakdown
would be easy and would not answer a question anyone asked.

### A note on what changed when the real data arrived

These figures were first produced against a schema-identical generated dataset,
because the real one needs Kaggle credentials. Most conclusions survived the swap
intact — the funnel shape, the ~3% repeat rate, the regional gradient. Two did
not, and they are recorded here rather than quietly corrected:

- **Retention was described as flat.** On generated data it sat near 0.35% for a
  year. The real curve decays threefold, and the decay is the interesting part.
- **The North was described as worst for late delivery.** It is the slowest, but
  the **Northeast** is the least reliable, by a wide margin.

Synthetic data reproduces the structure you thought to model. It cannot reproduce
the findings you did not.
