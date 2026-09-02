-- Products, joined to the English category names at the staging layer so no
-- downstream model has to remember the translation table exists.
with source as (
    select * from {{ source('olist', 'olist_products_dataset') }}
),

translation as (
    select * from {{ ref('stg_product_category_translation') }}
),

renamed as (
    select
        cast(product_id as varchar)                    as product_id,
        nullif(trim(cast(product_category_name as varchar)), '') as product_category_name_pt,
        cast(product_name_lenght as integer)           as product_name_length,
        cast(product_description_lenght as integer)    as product_description_length,
        cast(product_photos_qty as integer)            as product_photos_qty,
        cast(product_weight_g as integer)              as product_weight_g,
        cast(product_length_cm as integer)             as product_length_cm,
        cast(product_height_cm as integer)             as product_height_cm,
        cast(product_width_cm as integer)              as product_width_cm
    from source
)

select
    renamed.product_id,
    renamed.product_category_name_pt,
    -- ~2% of products carry no category. 'uncategorized' beats NULL here: it
    -- keeps category breakdowns from quietly dropping those rows.
    coalesce(translation.product_category_name_en, 'uncategorized') as product_category,
    renamed.product_name_length,
    renamed.product_description_length,
    renamed.product_photos_qty,
    renamed.product_weight_g,
    renamed.product_length_cm,
    renamed.product_height_cm,
    renamed.product_width_cm,
    renamed.product_length_cm * renamed.product_height_cm * renamed.product_width_cm as product_volume_cm3
from renamed
left join translation
    on renamed.product_category_name_pt = translation.product_category_name_pt
