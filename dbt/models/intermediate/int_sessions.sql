/*
    int_sessions -- buyer sessions, with the rules stated rather than assumed.

    Sessionizing a marketplace lifecycle stream is where most of the judgement
    lives, because the stream contains two very different kinds of event mixed
    together. Three rules, all deliberate:

    RULE 1 -- Only buyer-initiated events open or extend a session.
        order_shipped, order_delivered and order_approved are things that happen
        *to* a buyer while they are asleep. Feeding fulfilment events into
        sessionization would glue a January purchase to its February delivery
        into one 40-day "session", which is not a session in any useful sense.
        So the input is actor_type = 'buyer': order_placed and review_submitted.

    RULE 2 -- Events with a derived timestamp are excluded from the input.
        payment_confirmed is buyer-initiated but borrows its timestamp from
        order_approved_at, typically hours after the purchase. Admitting it
        would systematically inflate session duration with a number that is an
        artefact of the payment gateway, not of buyer behaviour. It is excluded
        from *defining* session boundaries; it is still an event in int_events.

    RULE 3 -- A gap longer than `session_inactivity_minutes` (default 30) starts
        a new session, per customer_unique_id.
        30 minutes is the web-analytics convention, and the point of matching it
        is comparability: a session here means the same thing it means in the
        clickstream this event layer is designed to eventually merge with.

    The honest consequence, spelled out because it is the interesting finding
    rather than a defect: this marketplace's buyers overwhelmingly place exactly
    one order and never come back, so most sessions contain exactly one event.
    A session table over transaction data is not a busy object -- but defining
    it explicitly is what lets you say that with confidence instead of guessing.
*/

with buyer_events as (
    select
        customer_unique_id,
        order_id,
        event_id,
        event_name,
        event_ts
    from {{ ref('int_events') }}
    where actor_type = 'buyer'
      and not ts_is_derived          -- Rule 2
),

with_gaps as (
    select
        *,
        lag(event_ts) over (
            partition by customer_unique_id order by event_ts, event_name
        ) as prev_event_ts
    from buyer_events
),

flagged as (
    select
        *,
        case
            when prev_event_ts is null then 1
            when date_diff('second', prev_event_ts, event_ts)
                 > {{ var('session_inactivity_minutes') }} * 60 then 1
            else 0
        end as is_session_start
    from with_gaps
),

numbered as (
    select
        *,
        sum(is_session_start) over (
            partition by customer_unique_id
            order by event_ts, event_name
            rows between unbounded preceding and current row
        ) as session_seq
    from flagged
),

order_value as (
    select
        order_id,
        sum(item_price) + sum(item_freight_value) as order_value
    from {{ ref('stg_order_items') }}
    group by order_id
),

aggregated as (
    select
        n.customer_unique_id,
        n.session_seq,
        min(n.event_ts)                                             as session_started_at,
        max(n.event_ts)                                             as session_ended_at,
        count(*)                                                    as event_count,
        count(distinct n.order_id)                                  as order_count,
        sum(case when n.event_name = 'order_placed' then 1 else 0 end)     as orders_placed,
        sum(case when n.event_name = 'review_submitted' then 1 else 0 end) as reviews_submitted,
        sum(case when n.event_name = 'order_placed' then coalesce(v.order_value, 0) else 0 end) as session_gross_value
    from numbered as n
    left join order_value as v on n.order_id = v.order_id
    group by n.customer_unique_id, n.session_seq
)

select
    {{ generate_surrogate_key(['customer_unique_id', 'session_seq']) }} as session_id,
    customer_unique_id,
    session_seq                                                  as session_number_for_buyer,
    session_seq = 1                                              as is_first_session,
    session_started_at,
    session_ended_at,
    cast(session_started_at as date)                             as session_date,
    round(date_diff('second', session_started_at, session_ended_at) / 60.0, 3) as session_duration_minutes,
    event_count,
    order_count,
    orders_placed,
    reviews_submitted,
    round(session_gross_value, 2)                                as session_gross_value,
    -- What the buyer actually did, so a funnel can be built on session intent
    -- rather than on raw event counts.
    case
        when orders_placed > 0 and reviews_submitted > 0 then 'mixed'
        when orders_placed > 0 then 'purchase'
        else 'review'
    end                                                          as session_type,
    event_count = 1                                              as is_single_event_session
from aggregated
