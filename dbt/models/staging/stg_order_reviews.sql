-- Post-purchase survey. Two genuinely distinct timestamps live here:
-- review_creation_date is when the survey was *sent*, review_answer_timestamp
-- is when the buyer *answered*. They become two separate events downstream.
with source as (
    select * from {{ source('olist', 'olist_order_reviews_dataset') }}
),

renamed as (
    select
        cast(review_id as varchar)                     as review_id,
        cast(order_id as varchar)                      as order_id,
        cast(review_score as integer)                  as review_score,
        nullif(trim(cast(review_comment_title as varchar)), '')   as review_comment_title,
        nullif(trim(cast(review_comment_message as varchar)), '') as review_comment_message,
        cast(review_creation_date as timestamp)        as survey_sent_at,
        cast(review_answer_timestamp as timestamp)     as survey_answered_at
    from source
)

select
    *,
    review_comment_message is not null as has_written_comment,
    review_score <= 2                  as is_detractor,
    review_score = 5                   as is_promoter,
    -- Source clock skew: a small share of answers are stamped before the survey
    -- they answer. Flagged here, never silently reordered. See README
    -- "Out-of-order events".
    survey_answered_at < survey_sent_at as has_negative_answer_lag
from renamed
