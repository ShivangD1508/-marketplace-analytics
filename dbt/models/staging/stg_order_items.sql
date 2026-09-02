-- One row per item line. order_item_id is a within-order sequence, not a key,
-- so a real surrogate key is minted here rather than downstream.
with source as (
    select * from {{ source('olist', 'olist_order_items_dataset') }}
)

select
    {{ generate_surrogate_key(['order_id', 'order_item_id']) }} as order_item_key,
    cast(order_id as varchar)                  as order_id,
    cast(order_item_id as integer)             as order_item_seq,
    cast(product_id as varchar)                as product_id,
    cast(seller_id as varchar)                 as seller_id,
    cast(shipping_limit_date as timestamp)     as shipping_limit_at,
    cast(price as double)                      as item_price,
    cast(freight_value as double)              as item_freight_value,
    cast(price as double) + cast(freight_value as double) as item_total_value
from source
