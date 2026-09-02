-- One row per payment instrument. Note there is no timestamp on this table at
-- all -- that absence is what forces the payment_confirmed event in int_events
-- to borrow a timestamp, which is documented as a derived-timestamp decision.
with source as (
    select * from {{ source('olist', 'olist_order_payments_dataset') }}
)

select
    {{ generate_surrogate_key(['order_id', 'payment_sequential']) }} as order_payment_key,
    cast(order_id as varchar)                        as order_id,
    cast(payment_sequential as integer)              as payment_seq,
    lower(trim(cast(payment_type as varchar)))       as payment_type,
    cast(payment_installments as integer)            as payment_installments,
    cast(payment_value as double)                    as payment_value
from source
