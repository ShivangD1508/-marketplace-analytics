-- Order lifecycle spine. Renaming and typing only, plus one derived flag set
-- that every downstream model would otherwise re-derive.
with source as (
    select * from {{ source('olist', 'olist_orders_dataset') }}
),

renamed as (
    select
        cast(order_id as varchar)                          as order_id,
        cast(customer_id as varchar)                       as customer_id,
        lower(trim(cast(order_status as varchar)))         as order_status,

        cast(order_purchase_timestamp as timestamp)        as placed_at,
        cast(order_approved_at as timestamp)               as approved_at,
        cast(order_delivered_carrier_date as timestamp)    as shipped_at,
        cast(order_delivered_customer_date as timestamp)   as delivered_at,
        cast(order_estimated_delivery_date as timestamp)   as estimated_delivery_at
    from source
),

flagged as (
    select
        *,

        -- "Reached the step" is a statement about the order's status, not about
        -- whether a timestamp happens to be populated. ~2% of delivered orders
        -- have a null approved_at: those orders certainly were approved, we just
        -- cannot observe when. Keeping the two ideas in separate columns is what
        -- stops the funnel from silently under-counting approvals.
        order_status in ('approved', 'invoiced', 'processing', 'shipped', 'delivered') as reached_approved,
        order_status in ('shipped', 'delivered')                                        as reached_shipped,
        order_status = 'delivered'                                                      as reached_delivered,
        order_status in ('canceled', 'unavailable')                                     as is_terminal_failure,

        approved_at  is null and order_status in ('approved', 'invoiced', 'processing', 'shipped', 'delivered') as has_unobserved_approval,
        shipped_at   is null and order_status in ('shipped', 'delivered')                                       as has_unobserved_shipment,
        delivered_at is null and order_status = 'delivered'                                                     as has_unobserved_delivery
    from renamed
)

select * from flagged
