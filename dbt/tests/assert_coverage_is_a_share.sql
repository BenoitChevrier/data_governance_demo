-- An invariant, not a data-quality rule: whatever the provider publishes, a
-- coverage rate is a share of a whole. If measured assets ever outnumber the
-- assets they are drawn from, or the rate falls outside [0, 1], the join in the
-- gold model is wrong — the figures would be arithmetically impossible rather
-- than merely surprising.
--
-- Severity is left at error, unlike the construction-year test: this one fails
-- only if our own model is broken, never because of a defect we inherited.

select
    ministry_vintage_key,
    assets_total,
    assets_measured,
    coverage_rate

from {{ ref('gold_intensite_energetique') }}

where assets_measured > assets_total
   or coverage_rate < 0
   or coverage_rate > 1
   or assets_above_threshold > assets_measured
