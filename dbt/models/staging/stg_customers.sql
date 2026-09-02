-- One row per order-side customer record. The important thing this model does
-- is make the two-tier identity explicit in the names: customer_id is a
-- per-order handle, customer_unique_id is the person.
with source as (
    select * from {{ source('olist', 'olist_customers_dataset') }}
)

select
    cast(customer_id as varchar)                        as customer_id,
    cast(customer_unique_id as varchar)                 as customer_unique_id,
    cast(customer_zip_code_prefix as integer)           as customer_zip_code_prefix,
    lower(trim(cast(customer_city as varchar)))         as customer_city,
    upper(trim(cast(customer_state as varchar)))        as customer_state,
    {{ brazil_region('upper(trim(cast(customer_state as varchar)))') }} as customer_region
from source
