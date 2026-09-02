/*
    fct_funnel_steps -- one row per order per funnel step, reached or not.

    The unreached rows are the point. A funnel table that only stores steps that
    happened forces every consumer to reconstruct the denominator with a join,
    and they will each get it slightly wrong. Materialising the full
    order x step grid means drop-off is `count(*) filter (where is_reached)` and
    nothing else, at any slice.

    The steps are the buyer-visible promise, not every event in the taxonomy:
    payment_confirmed is excluded because it shares a borrowed timestamp with
    approval and would read as a phantom 100%-conversion step, and
    review_requested is excluded because it is a system action, not a step the
    order passes through.

    Built on int_events rather than on fct_orders, deliberately: the funnel is a
    question about the event stream, and going through int_events means the
    derived/unobserved provenance flags travel with each step.

    TWO NOTIONS OF "REACHED", and the difference between them is a finding:

      is_reached             -- the event happened at all.
      is_reached_in_sequence -- the event happened AND every prior step did too.

    These come apart because the marketplace sends a satisfaction survey on
    cancellation as well as on delivery, so thousands of orders are reviewed
    without ever being delivered. On `is_reached` alone the funnel is not
    monotonic -- more orders reach "reviewed" than reach "delivered" -- which
    would be nonsense in a chart labelled "funnel".

    So funnel arithmetic uses is_reached_in_sequence, which is monotonic by
    construction (and tested to be, in tests/assert_funnel_steps_monotonic.sql).
    is_reached is kept alongside it because the gap between the two is exactly
    the population of buyers who were asked to rate an order that never
    arrived -- the most negative review scores in the dataset, and invisible if
    you only model the sequenced version.
*/

{% set funnel_steps = [
    (1, 'placed',    'order_placed'),
    (2, 'approved',  'order_approved'),
    (3, 'shipped',   'order_shipped'),
    (4, 'delivered', 'order_delivered'),
    (5, 'reviewed',  'review_submitted')
] %}

with steps as (
    {% for step_number, step_name, event_name in funnel_steps %}
    select
        {{ step_number }} as step_number,
        '{{ step_name }}' as step_name,
        '{{ event_name }}' as event_name
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
),

orders as (
    select
        order_id,
        customer_unique_id,
        placed_at,
        placed_month,
        order_status,
        buyer_region,
        buyer_state,
        primary_payment_type,
        primary_product_category,
        order_value,
        item_count,
        seller_count,
        shipping_lane
    from {{ ref('fct_orders') }}
),

events as (
    select
        order_id,
        event_name,
        event_ts,
        ts_is_derived,
        is_unobserved_step
    from {{ ref('int_events') }}
),

grid as (
    select
        o.*,
        s.step_number,
        s.step_name,
        s.event_name
    from orders as o
    cross join steps as s
),

joined as (
    select
        g.*,
        e.event_ts,
        e.event_ts is not null                as is_reached,
        coalesce(e.ts_is_derived, false)      as ts_is_derived,
        coalesce(e.is_unobserved_step, false) as is_unobserved_step
    from grid as g
    left join events as e
        on g.order_id = e.order_id
       and g.event_name = e.event_name
),

sequenced as (
    select
        *,
        -- Monotonic by construction: true only if this step and every step
        -- before it were reached.
        min(case when is_reached then 1 else 0 end) over (
            partition by order_id
            order by step_number
            rows between unbounded preceding and current row
        ) = 1 as is_reached_in_sequence
    from joined
),

with_prev as (
    select
        *,
        lag(is_reached_in_sequence) over (partition by order_id order by step_number) as prev_step_reached,
        lag(event_ts)               over (partition by order_id order by step_number) as prev_step_ts,
        -- Furthest step this order got to without breaking the chain, so a
        -- "where did orders die" analysis needs no window of its own downstream.
        max(case when is_reached_in_sequence then step_number else 0 end)
            over (partition by order_id) as furthest_step_reached
    from sequenced
)

select
    {{ generate_surrogate_key(['order_id', 'step_number']) }} as funnel_step_key,
    order_id,
    customer_unique_id,
    step_number,
    step_name,
    event_name,

    is_reached,
    is_reached_in_sequence,
    -- The event fired even though the order never got here in sequence -- a
    -- review on an order that was cancelled rather than delivered.
    is_reached and not is_reached_in_sequence             as is_out_of_sequence_hit,
    -- The order got to the previous step and stopped here. At most one step per
    -- order is true; orders that complete the funnel have none.
    coalesce(prev_step_reached, true) and not is_reached_in_sequence as is_drop_off_step,
    furthest_step_reached,
    step_number = furthest_step_reached                  as is_furthest_step,

    event_ts                                             as reached_at,
    round(date_diff('minute', placed_at, event_ts) / 60.0, 3)      as hours_from_placed,
    round(date_diff('minute', prev_step_ts, event_ts) / 60.0, 3)   as hours_from_prev_step,

    -- Provenance travels with the step so timing analysis can drop borrowed
    -- timestamps without re-deriving which ones they are.
    ts_is_derived,
    is_unobserved_step,

    -- Slicing dimensions, denormalised on purpose: a funnel is only ever read
    -- with a group-by attached.
    placed_at,
    placed_month,
    order_status,
    buyer_region,
    buyer_state,
    shipping_lane,
    primary_payment_type,
    primary_product_category,
    order_value,
    item_count,
    seller_count
from with_prev
