/*
    Sessionization must partition the buyer event stream -- every qualifying
    event lands in exactly one session, and no session invents events.

    Gap-based sessionization is built from three stacked window functions, and
    the classic way to break it is a partition or ORDER BY that does not match
    between them: the sessions still look reasonable, but events get double
    counted across adjacent sessions or dropped at a boundary. Neither shows up
    in a uniqueness test on session_id.

    So this test reconciles both directions at once: total events across all
    sessions must equal the number of buyer events eligible for sessionization
    (actor_type = 'buyer' and not ts_is_derived -- the rule stated in
    int_sessions), and the set of buyers must match exactly.
*/

with eligible_events as (
    select
        count(*)                          as event_count,
        count(distinct customer_unique_id) as buyer_count
    from {{ ref('int_events') }}
    where actor_type = 'buyer'
      and not ts_is_derived
),

session_totals as (
    select
        sum(event_count)                   as event_count,
        count(distinct customer_unique_id) as buyer_count
    from {{ ref('int_sessions') }}
),

checks as (
    select
        'buyer events are conserved across sessionization' as assertion,
        e.event_count as expected,
        s.event_count as actual
    from eligible_events e cross join session_totals s

    union all

    select
        'every buyer with an eligible event has a session',
        e.buyer_count,
        s.buyer_count
    from eligible_events e cross join session_totals s
)

select
    assertion,
    expected,
    actual,
    actual - expected as difference
from checks
where expected is distinct from actual
