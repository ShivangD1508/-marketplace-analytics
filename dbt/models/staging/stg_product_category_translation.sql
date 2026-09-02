with source as (
    select * from {{ source('olist', 'product_category_name_translation') }}
)

select
    trim(cast(product_category_name as varchar))         as product_category_name_pt,
    trim(cast(product_category_name_english as varchar)) as product_category_name_en
from source
