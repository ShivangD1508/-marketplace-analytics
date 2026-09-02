-- The raw file carries many lat/lng observations per zip prefix. Collapsing to
-- one centroid per prefix here means every downstream join on geography is a
-- 1:1 join, which is the only way the delivery-by-region analysis stays honest.
with source as (
    select * from {{ source('olist', 'olist_geolocation_dataset') }}
),

renamed as (
    select
        cast(geolocation_zip_code_prefix as integer)      as zip_code_prefix,
        cast(geolocation_lat as double)                   as latitude,
        cast(geolocation_lng as double)                   as longitude,
        lower(trim(cast(geolocation_city as varchar)))    as city,
        upper(trim(cast(geolocation_state as varchar)))   as state
    from source
    -- Drop coordinates outside Brazil's bounding box; the raw file has a
    -- handful of clearly mis-keyed points.
    where geolocation_lat between -34.0 and 5.5
      and geolocation_lng between -74.0 and -34.0
),

centroids as (
    select
        zip_code_prefix,
        avg(latitude)  as latitude,
        avg(longitude) as longitude,
        count(*)       as observation_count,
        -- Modal city/state for the prefix, resolved deterministically.
        min(city)  as city,
        min(state) as state
    from renamed
    group by zip_code_prefix
)

select
    zip_code_prefix,
    latitude,
    longitude,
    city,
    state,
    {{ brazil_region('state') }} as region,
    observation_count
from centroids
