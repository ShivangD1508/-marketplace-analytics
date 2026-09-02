/*
    agg_cohort_retention -- one row per (acquisition cohort month, month offset).

    Cohorts are keyed on customer_unique_id, for the reason set out in
    dim_customers: on customer_id every cohort would show 0% retention forever.

    Two decisions worth stating:

    1. RETENTION MEANS "PLACED AN ORDER IN THAT MONTH", not "was reachable" or
       "logged in". This dataset only observes purchases, so a softer definition
       would be unmeasurable. Month 0 is therefore 100% by construction and is
       kept in the output as the denominator row rather than dropped.

    2. THE GRID IS FULLY MATERIALISED, including months with zero returning
       buyers. Rows that are absent get read as "no data" and rows that are zero
       get read as "no retention"; those are different claims, and a retention
       curve drawn from a sparse table quietly interpolates over the gap.
       `is_fully_observed` marks the offsets that had a complete month of
       observation window available, so a curve can be truncated honestly
       instead of showing a fake cliff at the end of the data.
*/

with buyers as (
    select
        customer_unique_id,
        cohort_month,
        acquisition_region
    from {{ ref('dim_customers') }}
),

orders as (
    select
        o.customer_unique_id,
        o.placed_month,
        o.order_value
    from {{ ref('fct_orders') }} as o
),

bounds as (
    select
        max(placed_month) as last_observed_month
    from orders
),

cohort_sizes as (
    select
        cohort_month,
        count(*) as cohort_size
    from buyers
    group by cohort_month
),

activity as (
    select
        b.cohort_month,
        date_diff('month', b.cohort_month, o.placed_month) as months_since_first_order,
        count(distinct o.customer_unique_id)               as active_buyers,
        count(*)                                           as orders,
        sum(o.order_value)                                 as revenue
    from buyers as b
    inner join orders as o on b.customer_unique_id = o.customer_unique_id
    group by 1, 2
),

-- Every (cohort, offset) pair that could exist within the observation window.
grid as (
    select
        c.cohort_month,
        c.cohort_size,
        offsets.months_since_first_order
    from cohort_sizes as c
    cross join bounds
    cross join (
        select unnest(generate_series(0, 36)) as months_since_first_order
    ) as offsets
    where offsets.months_since_first_order
          <= date_diff('month', c.cohort_month, bounds.last_observed_month)
)

select
    {{ generate_surrogate_key(['grid.cohort_month', 'grid.months_since_first_order']) }} as cohort_period_key,
    grid.cohort_month,
    cast(grid.cohort_month as date)                as cohort_month_date,
    grid.months_since_first_order,
    grid.cohort_size,

    coalesce(a.active_buyers, 0)                   as active_buyers,
    coalesce(a.orders, 0)                          as orders,
    round(coalesce(a.revenue, 0), 2)               as revenue,

    round(coalesce(a.active_buyers, 0) * 1.0 / grid.cohort_size, 6)   as retention_rate,
    round(coalesce(a.orders, 0) * 1.0 / grid.cohort_size, 6)          as orders_per_cohort_buyer,
    round(coalesce(a.revenue, 0) / grid.cohort_size, 4)               as revenue_per_cohort_buyer,

    -- True when a full calendar month of observation was available for this
    -- offset. The final offset of every cohort is partial and will understate
    -- retention; this flag is what lets a chart cut it off honestly.
    grid.months_since_first_order
        < date_diff('month', grid.cohort_month, bounds.last_observed_month) as is_fully_observed
from grid
cross join bounds
left join activity as a
    on grid.cohort_month = a.cohort_month
   and grid.months_since_first_order = a.months_since_first_order
