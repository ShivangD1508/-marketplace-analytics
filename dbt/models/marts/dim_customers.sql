/*
    dim_customers -- one row per buyer, keyed on customer_unique_id.

    The key choice is the whole point of this model. The raw file offers
    customer_id, which is minted fresh for every order and therefore describes a
    checkout, not a person. Building the customer dimension on it would make
    every buyer look like a first-time buyer, silently reporting 0% repeat rate
    and flat retention curves. customer_unique_id is the person.

    Geography is taken from the buyer's *most recent* order rather than their
    first, on the grounds that a dimension should describe current state; the
    cohort's original region is preserved separately for cohort analysis, since
    a mover must not be allowed to retroactively change the region a cohort was
    acquired into.
*/

with orders as (
    select * from {{ ref('fct_orders') }}
),

sessions as (
    select
        customer_unique_id,
        count(*)                      as session_count,
        min(session_started_at)       as first_session_at,
        max(session_ended_at)         as last_session_at,
        sum(event_count)              as session_event_count
    from {{ ref('int_sessions') }}
    group by customer_unique_id
),

order_stats as (
    select
        customer_unique_id,

        count(*)                                                       as orders_placed,
        count(*) filter (where reached_delivered)                      as orders_delivered,
        count(*) filter (where is_terminal_failure)                    as orders_failed,
        count(*) filter (where is_late_delivery)                       as orders_delivered_late,

        min(placed_at)                                                 as first_order_at,
        max(placed_at)                                                 as last_order_at,
        date_trunc('month', min(placed_at))                            as cohort_month,

        sum(order_value)                                               as lifetime_value,
        avg(order_value)                                               as avg_order_value,
        max(order_value)                                               as max_order_value,
        sum(item_count)                                                as lifetime_items,

        count(*) filter (where has_review)                             as reviews_submitted,
        avg(review_score)                                              as avg_review_score,
        count(*) filter (where is_detractor)                           as detractor_reviews,

        avg(days_to_delivery)                                          as avg_days_to_delivery,

        -- Region at acquisition, frozen; and current region, from the latest order.
        arg_min(buyer_region, placed_at)                               as acquisition_region,
        arg_min(buyer_state, placed_at)                                as acquisition_state,
        arg_max(buyer_region, placed_at)                               as current_region,
        arg_max(buyer_state, placed_at)                                as current_state,
        arg_max(buyer_latitude, placed_at)                             as current_latitude,
        arg_max(buyer_longitude, placed_at)                            as current_longitude,
        arg_max(primary_payment_type, placed_at)                       as latest_payment_type,
        arg_max(primary_product_category, order_value)                 as top_spend_category
    from orders
    group by customer_unique_id
)

select
    o.customer_unique_id,

    -- Acquisition
    o.first_order_at,
    o.cohort_month,
    cast(o.first_order_at as date)                          as first_order_date,
    o.acquisition_region,
    o.acquisition_state,

    -- Current state
    o.last_order_at,
    o.current_region,
    o.current_state,
    o.current_latitude,
    o.current_longitude,
    o.latest_payment_type,
    o.top_spend_category,
    o.current_region <> o.acquisition_region                as has_changed_region,

    -- Volume and value
    o.orders_placed,
    o.orders_delivered,
    o.orders_failed,
    o.orders_delivered_late,
    o.lifetime_items,
    round(o.lifetime_value, 2)                              as lifetime_value,
    round(o.avg_order_value, 2)                             as avg_order_value,
    round(o.max_order_value, 2)                             as max_order_value,

    -- Repeat behaviour. The single most consequential number in this dataset:
    -- almost nobody comes back, and the dimension has to make that legible
    -- rather than bury it.
    o.orders_placed > 1                                     as is_repeat_buyer,
    case
        when o.orders_placed = 1 then 'one_time'
        when o.orders_placed between 2 and 3 then 'occasional'
        else 'frequent'
    end                                                     as buyer_segment,
    case
        when o.orders_placed > 1
        then round(date_diff('minute', o.first_order_at, o.last_order_at) / 1440.0, 2)
    end                                                     as days_active,
    case
        when o.orders_placed > 1
        then round(date_diff('minute', o.first_order_at, o.last_order_at) / 1440.0 / (o.orders_placed - 1), 2)
    end                                                     as avg_days_between_orders,

    -- Satisfaction
    o.reviews_submitted,
    round(o.avg_review_score, 3)                            as avg_review_score,
    o.detractor_reviews,
    o.detractor_reviews > 0                                 as has_ever_complained,
    round(o.avg_days_to_delivery, 2)                        as avg_days_to_delivery,

    -- Engagement, from the sessionized stream
    coalesce(s.session_count, 0)                            as session_count,
    s.first_session_at,
    s.last_session_at
from order_stats as o
left join sessions as s on o.customer_unique_id = s.customer_unique_id
