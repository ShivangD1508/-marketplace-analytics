/*
    No event may be stamped before the order it belongs to was placed.

    This is the load-bearing invariant of the whole event layer. order_placed is
    the anchor every other event is measured from: hours_since_order_placed,
    the funnel's step timings, and sessionization all assume it. If a lifecycle
    timestamp were ever earlier than the purchase, those numbers would go
    negative and every duration aggregate downstream would be quietly wrong
    rather than obviously wrong.

    It is written as a singular test rather than a column range check because
    the failure mode it guards is *relational* -- an event compared against a
    different row's timestamp -- which no generic test can express.

    Note the deliberate scope: this does not assert that events are internally
    well ordered. int_events flags that separately as `is_out_of_order` and
    keeps the rows, because a review answered before its survey was sent is real
    source clock skew that analysts should be able to see. Being stamped before
    the order existed at all is a different thing: it is not skew, it is broken.
*/

select
    e.event_id,
    e.order_id,
    e.event_name,
    e.event_ts,
    e.order_placed_at,
    date_diff('second', e.order_placed_at, e.event_ts) as seconds_before_placement
from {{ ref('int_events') }} as e
where e.event_ts < e.order_placed_at
