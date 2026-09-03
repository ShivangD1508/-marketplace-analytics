/*
    fct_orders -- one row per order, with the item and payment rollups folded in.

    Deliberately built from the staging layer rather than from int_events. The
    event table is the *narrative* of an order; this is its *state*. Deriving
    state by pivoting the event stream back into columns would make every order
    metric depend on the event taxonomy staying frozen, which is exactly the
    coupling the two-model split is there to avoid.
*/

with orders as (
    select * from {{ ref('stg_orders') }}
),

customers as (
    select * from {{ ref('stg_customers') }}
),

items as (
    select
        i.order_id,
        count(*)                     as item_count,
        count(distinct i.product_id) as product_count,
        count(distinct i.seller_id)  as seller_count,
        min(i.seller_id)             as first_seller_id,
        sum(i.item_price)            as items_value,
        sum(i.item_freight_value)    as freight_value,
        min(i.shipping_limit_at)     as earliest_shipping_limit_at,
        max(i.shipping_limit_at)     as latest_shipping_limit_at
    from {{ ref('stg_order_items') }} as i
    group by i.order_id
),

item_categories as (
    -- The category that accounts for the largest share of order value. One
    -- label per order, chosen deterministically rather than arbitrarily.
    select
        i.order_id,
        arg_max(p.product_category, i.item_price) as primary_product_category
    from {{ ref('stg_order_items') }} as i
    inner join {{ ref('stg_products') }} as p on i.product_id = p.product_id
    group by i.order_id
),

payments as (
    select
        order_id,
        count(*)                             as payment_count,
        sum(payment_value)                   as payment_value,
        max(payment_installments)            as max_installments,
        arg_max(payment_type, payment_value) as primary_payment_type
    from {{ ref('stg_order_payments') }}
    group by order_id
),

reviews as (
    -- An order can carry more than one review row in the raw file. The latest
    -- answered survey is the buyer's final word, so that is the one kept.
    select
        order_id,
        arg_max(review_score, survey_answered_at)          as review_score,
        arg_max(has_written_comment, survey_answered_at)   as review_has_comment,
        max(survey_answered_at)                            as review_submitted_at,
        min(survey_sent_at)                                as review_requested_at,
        count(*)                                           as review_count
    from {{ ref('stg_order_reviews') }}
    group by order_id
),

seller_geo as (
    select
        s.seller_id,
        s.seller_state,
        s.seller_region
    from {{ ref('stg_sellers') }} as s
),

final as (
    select
        o.order_id,
        o.customer_id,
        c.customer_unique_id,

        o.order_status,
        o.reached_approved,
        o.reached_shipped,
        o.reached_delivered,
        o.is_terminal_failure,

        -- Timeline
        o.placed_at,
        o.approved_at,
        o.shipped_at,
        o.delivered_at,
        o.estimated_delivery_at,
        cast(o.placed_at as date)           as placed_date,
        date_trunc('month', o.placed_at)    as placed_month,

        -- Durations, in days, null wherever the endpoint was not observed.
        round(date_diff('minute', o.placed_at, o.approved_at) / 1440.0, 3)   as days_to_approval,
        round(date_diff('minute', o.approved_at, o.shipped_at) / 1440.0, 3)  as days_approval_to_ship,
        round(date_diff('minute', o.shipped_at, o.delivered_at) / 1440.0, 3) as days_in_transit,
        round(date_diff('minute', o.placed_at, o.delivered_at) / 1440.0, 3)  as days_to_delivery,
        round(date_diff('minute', o.placed_at, o.estimated_delivery_at) / 1440.0, 3) as promised_lead_days,
        -- Positive means early against the promise made at purchase.
        round(date_diff('minute', o.delivered_at, o.estimated_delivery_at) / 1440.0, 3) as days_vs_promise,
        o.delivered_at > o.estimated_delivery_at as is_late_delivery,

        -- Geography. Buyer side is the delivery destination and is the one that
        -- drives the regional analysis; seller side is only meaningful for
        -- single-seller orders.
        c.customer_state                    as buyer_state,
        c.customer_region                   as buyer_region,
        bg.latitude                         as buyer_latitude,
        bg.longitude                        as buyer_longitude,
        case when i.seller_count = 1 then sg.seller_state end  as seller_state,
        case when i.seller_count = 1 then sg.seller_region end as seller_region,
        case
            when i.seller_count <> 1 then 'multi_seller'
            when sg.seller_state = c.customer_state then 'intra_state'
            when sg.seller_region = c.customer_region then 'intra_region'
            else 'inter_region'
        end                                 as shipping_lane,

        -- Basket
        coalesce(i.item_count, 0)           as item_count,
        coalesce(i.product_count, 0)        as product_count,
        coalesce(i.seller_count, 0)         as seller_count,
        ic.primary_product_category,
        round(coalesce(i.items_value, 0), 2)    as items_value,
        round(coalesce(i.freight_value, 0), 2)  as freight_value,
        round(coalesce(i.items_value, 0) + coalesce(i.freight_value, 0), 2) as order_value,
        case
            when coalesce(i.items_value, 0) > 0
            then round(coalesce(i.freight_value, 0) / i.items_value, 4)
        end                                 as freight_ratio,
        i.earliest_shipping_limit_at,
        o.shipped_at > i.earliest_shipping_limit_at as missed_shipping_limit,

        -- Payment
        p.primary_payment_type,
        coalesce(p.payment_count, 0)        as payment_count,
        p.payment_count > 1                 as is_split_payment,
        p.max_installments,
        round(p.payment_value, 2)           as payment_value,

        -- Review
        r.review_score,
        r.review_has_comment,
        r.review_requested_at,
        r.review_submitted_at,
        r.review_score is not null          as has_review,
        r.review_score <= 2                 as is_detractor,

        -- Data-quality provenance, carried through from staging so a consumer
        -- of this mart can reproduce the funnel's treatment of missing steps
        -- and exclude the source's self-contradicting rows.
        o.has_unobserved_approval,
        o.has_unobserved_shipment,
        o.has_unobserved_delivery,
        o.has_shipment_before_purchase,
        o.has_delivery_before_shipment,
        o.has_delivery_without_delivered_status

    from orders as o
    inner join customers as c on o.customer_id = c.customer_id
    left join items as i on o.order_id = i.order_id
    left join item_categories as ic on o.order_id = ic.order_id
    left join payments as p on o.order_id = p.order_id
    left join reviews as r on o.order_id = r.order_id
    left join seller_geo as sg on i.first_seller_id = sg.seller_id
    left join {{ ref('stg_geolocation') }} as bg on c.customer_zip_code_prefix = bg.zip_code_prefix
)

select * from final
