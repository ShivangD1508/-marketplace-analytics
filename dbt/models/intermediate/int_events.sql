{{
    config(
        materialized = 'incremental',
        unique_key = 'event_id',
        incremental_strategy = 'delete+insert',
        on_schema_change = 'sync_all_columns'
    )
}}

/*
    int_events -- the long event table.

    The source is a set of wide lifecycle tables: one row per order carrying
    four timestamp columns, one row per review carrying two more, one row per
    payment carrying none. This model collapses all of that into one row per
    OCCURRENCE, which is the shape every product-analytics question wants.

    Occurrence, not (order, event). That distinction was forced by the real
    data: 547 orders carry more than one review, so a grain of
    (order_id, event_name) would have had to silently drop a buyer's second
    review to stay unique. An event table whose key cannot represent something
    happening twice is not an event table. `event_source_id` therefore carries
    whatever distinguishes repeat occurrences -- review_id for the review
    events, null for the lifecycle events, which genuinely happen at most once
    per order -- and the surrogate key includes it.

    The design decisions are all documented in README.md under "Event schema".
    The short version, because the reasons matter more than the SQL:

    1. NAMING. `object_pastTenseVerb`, lower snake case: order_placed,
       order_shipped, review_submitted. Past tense because an event is a fact
       that already happened; object-first because it sorts usefully and lets
       you glob a whole object's stream with `order_%`.

    2. COLUMNS vs PROPERTIES. A field is a column if it is meaningful for
       *every* event type -- ids, timestamps, actor, provenance. Everything
       else goes in the `properties` JSON blob. That keeps the table narrow and
       stable: adding an event type never adds a column, so the incremental
       model never has to migrate its schema for a new event.

    3. IDENTITY. The buyer actor is `customer_unique_id`, never `customer_id`.
       Olist mints a fresh customer_id per order, so keying on it would make
       every buyer look brand new and destroy retention analysis outright. The
       seller actor is `seller_id`, but only where the order has exactly one
       seller -- see order_shipped below.

    4. DERIVED TIMESTAMPS. Payments and terminal statuses carry no timestamp in
       the source. Rather than drop those events or invent a time, they borrow
       the nearest defensible one and set `ts_is_derived = true`, so any
       analysis can exclude them in one predicate.

    5. UNOBSERVED vs UNREACHED. An order with status='delivered' and a null
       approved_at was approved; we just cannot see when. Emitting no
       order_approved event there would understate approvals, so the event is
       emitted with a borrowed timestamp and flagged. See stg_orders.
*/

with orders as (
    select
        o.order_id,
        o.customer_id,
        c.customer_unique_id,
        c.customer_state,
        c.customer_region,
        o.order_status,
        o.placed_at,
        o.approved_at,
        o.shipped_at,
        o.delivered_at,
        o.estimated_delivery_at,
        o.reached_approved,
        o.reached_shipped,
        o.reached_delivered,
        o.is_terminal_failure,
        o.has_unobserved_approval,
        o.has_unobserved_shipment,
        o.has_unobserved_delivery,
        o.has_shipment_before_purchase
    from {{ ref('stg_orders') }} as o
    inner join {{ ref('stg_customers') }} as c
        on o.customer_id = c.customer_id

    {% if is_incremental() %}
    /*
        RESTATEMENT WINDOW -- the part of an incremental model that is easy to
        get wrong and impossible to notice.

        The window is anchored on the *purchase frontier*, max(order_placed_at),
        and it selects orders by when they were placed. Not by event_ts, and
        this distinction is the whole design:

        An order's events keep arriving long after the order does. A purchase in
        March is approved in March, delivered in April, reviewed in May. So the
        newest event_ts in this table always belongs to an old order, and it runs
        months ahead of the orders still in flight -- in this dataset the last
        event is 2019-01-17 while the last purchase is 2018-10-17. A watermark of
        `max(event_ts) - lookback` therefore sits in the *future* relative to the
        orders that are still changing, and skips precisely the late-arriving
        deliveries the window exists to catch. That failure is silent: the model
        builds, every test passes, and the funnel just quietly under-reports
        delivery forever.

        Anchoring on max(order_placed_at) inverts the logic to something true:
        an order stops changing once it is older than the longest lifecycle the
        marketplace produces. Reprocess every order placed inside that span and
        nothing that can still move is missed.

        Sizing `events_lookback_days` is therefore an empirical question, and on
        real data it is also a trade-off with no free answer. Measured on the
        published dataset the placement-to-last-event span is 13 days at p50,
        56 at p99, 185 at p99.9 -- and 528 at the maximum. A window wide enough
        to be strictly correct (550 days) reprocesses 93% of the table, at
        which point the model is incremental in name only.

        So the window is set to 270 days, which leaves 33 orders (0.03%) whose
        lifecycles outrun it, and those are swept up by the weekly full refresh
        the Dagster DAG schedules. That is the honest shape of the problem: with
        no source-side updated_at column, a time-windowed incremental strategy
        can be cheap or exhaustive but not both, and the gap has to be closed by
        something else rather than wished away. Re-measure before trusting these
        numbers on other data; the query is in README "Incrementality".

        `delete+insert` on the deterministic event_id makes the rewrite
        idempotent, which is what scripts/verify_incremental.py checks by
        asserting an incremental build matches a full refresh row for row.
    */
    where o.placed_at >= (
        select coalesce(max(order_placed_at), timestamp '1900-01-01')
               - interval '{{ var("events_lookback_days") }}' day
        from {{ this }}
    )
    {% endif %}
),

order_items_rollup as (
    select
        order_id,
        count(*)                        as item_count,
        count(distinct seller_id)       as seller_count,
        min(seller_id)                  as any_seller_id,
        count(distinct product_id)      as product_count,
        sum(item_price)                 as items_value,
        sum(item_freight_value)         as freight_value,
        min(shipping_limit_at)          as earliest_shipping_limit_at
    from {{ ref('stg_order_items') }}
    where order_id in (select order_id from orders)
    group by order_id
),

order_payments_rollup as (
    select
        order_id,
        count(*)                                   as payment_count,
        sum(payment_value)                         as payment_value,
        max(payment_installments)                  as max_installments,
        -- The instrument that settled the largest share is the one worth
        -- reporting as "the" payment type on a split payment.
        arg_max(payment_type, payment_value)       as primary_payment_type
    from {{ ref('stg_order_payments') }}
    where order_id in (select order_id from orders)
    group by order_id
),

reviews as (
    select
        r.review_id,
        r.order_id,
        r.review_score,
        r.survey_sent_at,
        r.survey_answered_at,
        r.has_written_comment,
        r.is_detractor,
        r.has_negative_answer_lag
    from {{ ref('stg_order_reviews') }} as r
    where r.order_id in (select order_id from orders)
),

/* ------------------------------------------------------------------ events */

evt_order_placed as (
    select
        o.order_id,
        'order_placed'      as event_name,
        cast(null as varchar) as event_source_id,
        1                   as lifecycle_rank,
        o.placed_at         as event_ts,
        false               as ts_is_derived,
        false               as is_unobserved_step,
        'buyer'             as actor_type,
        o.customer_unique_id as actor_id,
        cast(null as varchar) as seller_id,
        'olist_orders_dataset' as source_table,
        to_json({
            'order_status':        o.order_status,
            'item_count':          coalesce(i.item_count, 0),
            'seller_count':        coalesce(i.seller_count, 0),
            'product_count':       coalesce(i.product_count, 0),
            'items_value':         round(coalesce(i.items_value, 0), 2),
            'freight_value':       round(coalesce(i.freight_value, 0), 2),
            'order_value':         round(coalesce(i.items_value, 0) + coalesce(i.freight_value, 0), 2),
            'buyer_state':         o.customer_state,
            'buyer_region':        o.customer_region,
            'promised_delivery_at': cast(o.estimated_delivery_at as varchar),
            'promised_lead_days':  round(date_diff('hour', o.placed_at, o.estimated_delivery_at) / 24.0, 2)
        }) as properties
    from orders as o
    left join order_items_rollup as i on o.order_id = i.order_id
),

evt_payment_confirmed as (
    -- No payment timestamp exists anywhere in the source. Approval *is* the
    -- payment authorisation clearing, so approved_at is the closest honest
    -- proxy; orders that never reached approval fall back to placed_at. Both
    -- cases are marked ts_is_derived so funnel timing analysis can exclude them.
    select
        o.order_id,
        'payment_confirmed' as event_name,
        cast(null as varchar) as event_source_id,
        2                   as lifecycle_rank,
        coalesce(o.approved_at, o.placed_at) as event_ts,
        true                as ts_is_derived,
        false               as is_unobserved_step,
        'buyer'             as actor_type,
        o.customer_unique_id as actor_id,
        cast(null as varchar) as seller_id,
        'olist_order_payments_dataset' as source_table,
        to_json({
            'payment_type':         p.primary_payment_type,
            'payment_count':        p.payment_count,
            'is_split_payment':     p.payment_count > 1,
            'payment_installments': p.max_installments,
            'payment_value':        round(p.payment_value, 2),
            'ts_borrowed_from':     case when o.approved_at is not null then 'order_approved_at' else 'order_purchase_timestamp' end
        }) as properties
    from orders as o
    inner join order_payments_rollup as p on o.order_id = p.order_id
),

evt_order_approved as (
    select
        o.order_id,
        'order_approved'    as event_name,
        cast(null as varchar) as event_source_id,
        3                   as lifecycle_rank,
        coalesce(o.approved_at, o.placed_at) as event_ts,
        o.approved_at is null as ts_is_derived,
        o.has_unobserved_approval as is_unobserved_step,
        -- Approval is an automated payment-gateway decision, not something a
        -- buyer or a seller does. It gets actor_type 'system' and no actor_id.
        'system'            as actor_type,
        cast(null as varchar) as actor_id,
        cast(null as varchar) as seller_id,
        'olist_orders_dataset' as source_table,
        to_json({
            'approval_lag_hours': case
                when o.approved_at is null then null
                else round(date_diff('minute', o.placed_at, o.approved_at) / 60.0, 3)
            end,
            'is_unobserved': o.has_unobserved_approval
        }) as properties
    from orders as o
    where o.reached_approved
),

evt_order_shipped as (
    -- Handoff to the carrier: the seller's side of the contract. A multi-seller
    -- order has no single seller actor, so actor_id is left null rather than
    -- arbitrarily picking one, and seller_count in properties says why.
    select
        o.order_id,
        'order_shipped'     as event_name,
        cast(null as varchar) as event_source_id,
        4                   as lifecycle_rank,
        coalesce(o.shipped_at, o.approved_at, o.placed_at) as event_ts,
        o.shipped_at is null as ts_is_derived,
        o.has_unobserved_shipment as is_unobserved_step,
        'seller'            as actor_type,
        case when i.seller_count = 1 then i.any_seller_id end as actor_id,
        case when i.seller_count = 1 then i.any_seller_id end as seller_id,
        'olist_orders_dataset' as source_table,
        to_json({
            'seller_count': coalesce(i.seller_count, 0),
            'ship_lag_days': case
                when o.shipped_at is null then null
                else round(date_diff('hour', o.placed_at, o.shipped_at) / 24.0, 3)
            end,
            'shipping_limit_at': cast(i.earliest_shipping_limit_at as varchar),
            'missed_shipping_limit': o.shipped_at > i.earliest_shipping_limit_at,
            'is_unobserved': o.has_unobserved_shipment
        }) as properties
    from orders as o
    left join order_items_rollup as i on o.order_id = i.order_id
    where o.reached_shipped
),

evt_order_delivered as (
    select
        o.order_id,
        'order_delivered'   as event_name,
        cast(null as varchar) as event_source_id,
        5                   as lifecycle_rank,
        coalesce(o.delivered_at, o.shipped_at, o.placed_at) as event_ts,
        o.delivered_at is null as ts_is_derived,
        o.has_unobserved_delivery as is_unobserved_step,
        -- The carrier delivers; neither marketplace party acts. 'system' again.
        'system'            as actor_type,
        cast(null as varchar) as actor_id,
        case when i.seller_count = 1 then i.any_seller_id end as seller_id,
        'olist_orders_dataset' as source_table,
        to_json({
            'delivery_days': case
                when o.delivered_at is null then null
                else round(date_diff('hour', o.placed_at, o.delivered_at) / 24.0, 3)
            end,
            'transit_days': case
                when o.delivered_at is null or o.shipped_at is null then null
                else round(date_diff('hour', o.shipped_at, o.delivered_at) / 24.0, 3)
            end,
            'days_vs_promise': case
                when o.delivered_at is null then null
                else round(date_diff('hour', o.delivered_at, o.estimated_delivery_at) / 24.0, 3)
            end,
            'is_late': o.delivered_at > o.estimated_delivery_at,
            'buyer_region': o.customer_region,
            'is_unobserved': o.has_unobserved_delivery
        }) as properties
    from orders as o
    left join order_items_rollup as i on o.order_id = i.order_id
    where o.reached_delivered
),

evt_order_failed as (
    -- Cancellation and unavailability have no timestamp of their own. They are
    -- stamped at the last point the order was demonstrably alive, and marked
    -- derived. The event name keeps the two apart in properties rather than
    -- multiplying event types: both are "the order stopped, for a bad reason".
    select
        o.order_id,
        case o.order_status
            when 'canceled' then 'order_canceled'
            else 'order_unavailable'
        end                 as event_name,
        cast(null as varchar) as event_source_id,
        -- Rank 5, alongside order_delivered rather than after everything: a
        -- terminal failure is the *alternative* to delivery, not a step past
        -- it. Ranking it last would flag almost every cancellation as
        -- out-of-order, which would bury the ~600 genuine clock-skew rows in
        -- noise. The two are mutually exclusive within an order, so the tie
        -- never actually occurs.
        5                   as lifecycle_rank,
        coalesce(o.shipped_at, o.approved_at, o.placed_at) as event_ts,
        true                as ts_is_derived,
        false               as is_unobserved_step,
        'system'            as actor_type,
        cast(null as varchar) as actor_id,
        cast(null as varchar) as seller_id,
        'olist_orders_dataset' as source_table,
        to_json({
            'order_status': o.order_status,
            'reached_approved': o.reached_approved,
            'reached_shipped': o.reached_shipped,
            'days_alive': round(date_diff('hour', o.placed_at, coalesce(o.shipped_at, o.approved_at, o.placed_at)) / 24.0, 3),
            'ts_borrowed_from': case
                when o.shipped_at is not null then 'order_delivered_carrier_date'
                when o.approved_at is not null then 'order_approved_at'
                else 'order_purchase_timestamp'
            end
        }) as properties
    from orders as o
    where o.is_terminal_failure
),

evt_review_requested as (
    -- The survey being sent is a real, separately-timestamped occurrence and is
    -- kept as its own event: without it the review funnel has no denominator.
    select
        r.order_id,
        'review_requested'  as event_name,
        r.review_id         as event_source_id,
        6                   as lifecycle_rank,
        r.survey_sent_at    as event_ts,
        false               as ts_is_derived,
        false               as is_unobserved_step,
        'system'            as actor_type,
        cast(null as varchar) as actor_id,
        cast(null as varchar) as seller_id,
        'olist_order_reviews_dataset' as source_table,
        to_json({'review_id': r.review_id}) as properties
    from reviews as r
),

evt_review_submitted as (
    select
        r.order_id,
        'review_submitted'  as event_name,
        r.review_id         as event_source_id,
        7                   as lifecycle_rank,
        r.survey_answered_at as event_ts,
        false               as ts_is_derived,
        false               as is_unobserved_step,
        'buyer'             as actor_type,
        o.customer_unique_id as actor_id,
        cast(null as varchar) as seller_id,
        'olist_order_reviews_dataset' as source_table,
        to_json({
            'review_id':        r.review_id,
            'review_score':     r.review_score,
            'is_detractor':     r.is_detractor,
            'has_comment':      r.has_written_comment,
            'answer_lag_hours': round(date_diff('minute', r.survey_sent_at, r.survey_answered_at) / 60.0, 3),
            -- Source clock skew: the answer is stamped before the survey.
            -- Retained as-is and flagged; see README "Out-of-order events".
            'has_negative_lag': r.has_negative_answer_lag
        }) as properties
    from reviews as r
    inner join orders as o on r.order_id = o.order_id
),

unioned as (
    select * from evt_order_placed
    union all select * from evt_payment_confirmed
    union all select * from evt_order_approved
    union all select * from evt_order_shipped
    union all select * from evt_order_delivered
    union all select * from evt_order_failed
    union all select * from evt_review_requested
    union all select * from evt_review_submitted
),

with_order_context as (
    select
        u.*,
        o.customer_id,
        o.customer_unique_id,
        o.customer_state,
        o.customer_region,
        o.order_status,
        o.placed_at as order_placed_at
    from unioned as u
    inner join orders as o on u.order_id = o.order_id
),

sequenced as (
    select
        *,
        -- Ordering is by canonical lifecycle rank, never by timestamp.
        -- payment_confirmed and order_approved deliberately share a borrowed
        -- timestamp, so a timestamp sort would order them arbitrarily; and an
        -- out-of-order arrival must not be allowed to rewrite the funnel.
        row_number() over (
            partition by order_id
            order by lifecycle_rank, event_name, coalesce(event_source_id, '')
        ) as event_seq_in_order,

        -- True when this event is stamped earlier than something that
        -- canonically precedes it. Flagged, never corrected.
        event_ts < max(event_ts) over (
            partition by order_id
            order by lifecycle_rank, event_name, coalesce(event_source_id, '')
            rows between unbounded preceding and 1 preceding
        ) as is_out_of_order
    from with_order_context
)

select
    {{ generate_surrogate_key(['order_id', 'event_name', 'event_source_id']) }} as event_id,
    event_name,
    event_source_id,
    event_ts,
    cast(event_ts as date)                as event_date,
    date_trunc('month', event_ts)         as event_month,
    lifecycle_rank,
    event_seq_in_order,

    actor_type,
    actor_id,
    customer_unique_id,
    customer_id,
    seller_id,
    order_id,
    order_status,
    order_placed_at,
    customer_state,
    customer_region,

    ts_is_derived,
    is_unobserved_step,
    coalesce(is_out_of_order, false)      as is_out_of_order,

    -- The sharp version of is_out_of_order: this event is stamped before the
    -- order it belongs to existed. On the real dataset 166 order_shipped events
    -- are, because the source says so. The raw timestamp is kept -- see
    -- stg_orders -- and the anomaly is named here so that a consumer can
    -- exclude it in one predicate rather than discovering it as a negative
    -- number inside an average.
    coalesce(event_ts < order_placed_at, false) as is_before_order_placed,

    -- Signed, and therefore honest: negative for the events above. Anything
    -- aggregating durations must filter on is_before_order_placed first; the
    -- alternative is clamping to zero, which would silently understate ship
    -- times rather than admit the source is wrong.
    round(date_diff('minute', order_placed_at, event_ts) / 60.0, 3) as hours_since_order_placed,

    cast(properties as varchar)           as properties,
    source_table,
    cast('{{ run_started_at }}' as timestamp) as dbt_ingested_at
from sequenced
