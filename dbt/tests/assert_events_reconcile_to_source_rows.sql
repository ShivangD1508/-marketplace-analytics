/*
    Every lifecycle timestamp in the source must produce exactly one event, and
    the event layer must not invent orders that the source does not have.

    The risk this guards is specific to how int_events is built: nine separate
    CTEs, each with its own WHERE clause, unioned together. A wrong predicate in
    any one of them loses events silently -- the model still builds, the tests on
    uniqueness and not-null still pass, and the funnel just quietly reports a
    smaller number. Row-count reconciliation against the raw tables is the only
    thing that catches it.

    Each branch below states an expected identity between the source and the
    event stream; the test fails with one row per identity that does not hold,
    naming the expected and actual counts.
*/

with source_counts as (
    select
        (select count(*) from {{ ref('stg_orders') }})                                          as orders,
        (select count(*) from {{ ref('stg_orders') }} where reached_approved)                   as approved_orders,
        (select count(*) from {{ ref('stg_orders') }} where reached_shipped)                    as shipped_orders,
        (select count(*) from {{ ref('stg_orders') }} where reached_delivered)                  as delivered_orders,
        (select count(*) from {{ ref('stg_orders') }} where is_terminal_failure)                as failed_orders,
        (select count(*) from {{ ref('stg_order_reviews') }})                                   as reviews,
        (select count(distinct order_id) from {{ ref('stg_order_payments') }})                  as paid_orders
),

event_counts as (
    select
        count(*) filter (where event_name = 'order_placed')                              as placed_events,
        count(*) filter (where event_name = 'order_approved')                            as approved_events,
        count(*) filter (where event_name = 'order_shipped')                             as shipped_events,
        count(*) filter (where event_name = 'order_delivered')                           as delivered_events,
        count(*) filter (where event_name in ('order_canceled', 'order_unavailable'))    as failed_events,
        count(*) filter (where event_name = 'review_requested')                          as review_requested_events,
        count(*) filter (where event_name = 'review_submitted')                          as review_submitted_events,
        count(*) filter (where event_name = 'payment_confirmed')                         as payment_events,
        count(distinct order_id)                                                         as distinct_orders
    from {{ ref('int_events') }}
),

checks as (
    select 'every order emits exactly one order_placed'    as assertion, s.orders           as expected, e.placed_events           as actual from source_counts s cross join event_counts e
    union all
    select 'every order appears in the event stream',              s.orders,           e.distinct_orders          from source_counts s cross join event_counts e
    union all
    select 'every approved order emits order_approved',            s.approved_orders,  e.approved_events          from source_counts s cross join event_counts e
    union all
    select 'every shipped order emits order_shipped',              s.shipped_orders,   e.shipped_events           from source_counts s cross join event_counts e
    union all
    select 'every delivered order emits order_delivered',          s.delivered_orders, e.delivered_events         from source_counts s cross join event_counts e
    union all
    select 'every terminal failure emits a failure event',         s.failed_orders,    e.failed_events            from source_counts s cross join event_counts e
    union all
    select 'every review emits review_requested',                  s.reviews,          e.review_requested_events  from source_counts s cross join event_counts e
    union all
    select 'every review emits review_submitted',                  s.reviews,          e.review_submitted_events  from source_counts s cross join event_counts e
    union all
    select 'every paid order emits payment_confirmed',             s.paid_orders,      e.payment_events           from source_counts s cross join event_counts e
)

select
    assertion,
    expected,
    actual,
    actual - expected as difference
from checks
where expected <> actual
