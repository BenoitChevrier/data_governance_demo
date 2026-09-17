-- Energy intensity of the State portfolio, in kWh of final energy per square
-- metre, by occupying ministry and vintage.
--
-- The KPI is published three times on purpose, and the reason is the whole
-- point of this model. Measured over the two vintages:
--
--   weighted over every measured asset   123.2  ->  229.5   (+86 %)
--   median asset                          80.3  ->   74.2
--   weighted, implausible rows excluded  100.5  ->  100.4
--
-- The headline doubling is produced by 57 rows out of 6,642, each carrying a
-- consumption far too large for the surface it is divided by. A single figure
-- would have sent a steering committee chasing an energy crisis that the data
-- does not show. Publishing the three side by side, with the count of excluded
-- rows and the share of the portfolio actually measured, is what makes the
-- number usable rather than merely available.
--
-- Nothing is hidden: the implausible rows are counted in a column of their own,
-- and the threshold is declared in dbt_project.yml so it can be argued with.

{% set implausible = var('implausible_intensity_kwh_m2') %}

with measured as (

    select
        ministere,
        millesime,
        surface_m2,
        consommation_kwh_ef,
        consommation_kwh_ef / surface_m2 as intensity_kwh_m2

    from {{ ref('silver_parc_immobilier') }}

    -- A surface of 0 was already read as missing upstream, so this join of the
    -- two conditions is also what keeps the division safe.
    where surface_m2 is not null
      and consommation_kwh_ef is not null

),

portfolio as (

    -- Every asset, measured or not. The denominator of the coverage rate has to
    -- come from here: counting only measured assets would report 100 % coverage
    -- of the assets we happen to know about.
    select
        ministere,
        millesime,
        count(*) as assets_total

    from {{ ref('silver_parc_immobilier') }}
    group by 1, 2

),

aggregated as (

    select
        ministere,
        millesime,
        count(*) as assets_measured,
        sum(surface_m2) as surface_measured_m2,
        sum(consommation_kwh_ef) as consumption_measured_kwh,
        sum(consommation_kwh_ef) / nullif(sum(surface_m2), 0) as intensity_kwh_m2,
        sum(consommation_kwh_ef) filter (where intensity_kwh_m2 <= {{ implausible }})
            / nullif(sum(surface_m2) filter (where intensity_kwh_m2 <= {{ implausible }}), 0)
            as intensity_kwh_m2_plausible_only,
        percentile_cont(0.5) within group (order by intensity_kwh_m2)
            as median_intensity_kwh_m2,
        count(*) filter (where intensity_kwh_m2 > {{ implausible }}) as assets_above_threshold

    from measured
    group by 1, 2

)

select
    portfolio.ministere || ' - ' || portfolio.millesime::text  as ministry_vintage_key,
    portfolio.ministere,
    portfolio.millesime,

    -- Coverage first, deliberately: it qualifies every figure that follows.
    portfolio.assets_total,
    coalesce(aggregated.assets_measured, 0)                    as assets_measured,
    round(
        coalesce(aggregated.assets_measured, 0)::numeric / portfolio.assets_total, 4
    )                                                          as coverage_rate,

    round(aggregated.surface_measured_m2, 0)                   as surface_measured_m2,
    round(aggregated.consumption_measured_kwh, 0)              as consumption_measured_kwh,

    round(aggregated.intensity_kwh_m2, 1)                      as intensity_kwh_m2,
    round(aggregated.intensity_kwh_m2_plausible_only, 1)       as intensity_kwh_m2_plausible_only,
    round(aggregated.median_intensity_kwh_m2::numeric, 1)      as median_intensity_kwh_m2,
    coalesce(aggregated.assets_above_threshold, 0)             as assets_above_threshold

from portfolio

-- Left join, not inner: a ministry whose assets carry no consumption figure at
-- all must appear with a coverage rate of 0, not disappear from the report.
left join aggregated
       on aggregated.ministere = portfolio.ministere
      and aggregated.millesime = portfolio.millesime

order by portfolio.millesime, portfolio.ministere
