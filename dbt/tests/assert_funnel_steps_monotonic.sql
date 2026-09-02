/*
    A funnel must not widen as it descends.

    fct_funnel_steps computes `is_reached_in_sequence` with a running window,
    which is *supposed* to make monotonicity structural. This test exists
    because "structural by construction" is a claim about the SQL, and the claim
    is worth checking: change the window frame, the partition, or the step
    ordering and the guarantee silently disappears while every row count still
    looks plausible.

    It matters here more than in a typical funnel because this dataset really
    does break naive monotonicity -- the marketplace surveys cancelled orders,
    so more orders fire `review_submitted` than ever reach `delivered`. That is
    precisely the bug this test would catch if the sequencing logic regressed to
    counting raw event hits.

    Fails with one row per step that has more orders than the step above it.
*/

with step_totals as (
    select
        step_number,
        step_name,
        count(*) filter (where is_reached_in_sequence) as orders_reaching_step
    from {{ ref('fct_funnel_steps') }}
    group by step_number, step_name
),

with_previous as (
    select
        *,
        lag(step_name)            over (order by step_number) as previous_step_name,
        lag(orders_reaching_step) over (order by step_number) as previous_step_orders
    from step_totals
)

select
    step_number,
    step_name,
    orders_reaching_step,
    previous_step_name,
    previous_step_orders,
    orders_reaching_step - previous_step_orders as excess_orders
from with_previous
where previous_step_orders is not null
  and orders_reaching_step > previous_step_orders
