/*
    Every event stamped before its own order was placed must be FLAGGED as such.

    This test used to assert the stronger claim -- that no event ever precedes
    its order at all -- and that claim was simply false. The published Olist
    dataset contains 166 orders whose carrier-handoff timestamp is earlier than
    their purchase timestamp, most by minutes but one by 171 days. The invariant
    was true only of clean data, which is not the data.

    So the assertion is now the one that is actually defensible, and is the same
    policy the rest of the event layer follows: the source is allowed to be
    wrong, the model is not allowed to be wrong ABOUT the source. A negative
    offset is fine as long as it is named, because a named anomaly can be
    excluded in one predicate; an unnamed one turns up inside an average months
    later, as a ship time that is mysteriously too fast.

    Two failure modes are caught here:

      1. An event precedes its order but is_before_order_placed is false. The
         flag has stopped tracking the condition it is supposed to describe, so
         every consumer filtering on it is now silently including bad rows.

      2. hours_since_order_placed disagrees in sign with the flag. These are two
         representations of the same fact and they must not drift apart.
         The comparison is non-strict on purpose: five real events precede
         their order by less than a minute, and the offset is rounded to three
         decimal places of an hour, so they legitimately present as 0.0 while
         being genuinely early. Requiring a strictly negative offset there
         would fail the build over a rounding artefact.

    The RATE is bounded separately, in the staging tests on stg_orders, which
    warn on any occurrence and error if the anomaly count roughly doubles.
    Splitting it this way keeps the two concerns apart: how much bad data
    arrived is a question about the source, whereas whether the model described
    it correctly is a question about this project, and only the second one is a
    bug in the repository.
*/

select
    e.event_id,
    e.order_id,
    e.event_name,
    e.event_ts,
    e.order_placed_at,
    e.is_before_order_placed,
    e.hours_since_order_placed,
    case
        when e.event_ts < e.order_placed_at and not e.is_before_order_placed
            then 'event precedes its order but is not flagged'
        else 'flag and signed offset disagree'
    end as failure_reason
from {{ ref('int_events') }} as e
where
    -- 1. the condition holds but the flag does not
    (e.event_ts < e.order_placed_at and not e.is_before_order_placed)
    -- 2. the flag and the signed offset contradict each other
    or (e.is_before_order_placed and e.hours_since_order_placed > 0)
    or (not e.is_before_order_placed and e.hours_since_order_placed < 0)
