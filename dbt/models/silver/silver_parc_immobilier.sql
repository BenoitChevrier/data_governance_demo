-- Typed and cleaned portfolio: one row per asset per vintage.
--
-- This layer converts and documents, it does not judge: no row is filtered,
-- no value is corrected. What cannot be read as a number or a boolean becomes
-- NULL, so that completeness is measurable on the gold layer instead of being
-- silently repaired here.
--
-- Column names are kept exactly as published. A renaming would read better and
-- make the lineage in the catalog harder to follow.

with bronze as (

    select * from {{ source('bronze', 'parc_immobilier') }}

)

select
    -- Grain. The provider has no single-column key across vintages: id is
    -- unique within a file (17,007 distinct over 17,007 rows in 2023) and
    -- 16,198 ids are shared by the two vintages, so the pair identifies a row.
    nullif(trim(id), '') || '-' || millesime::text          as asset_vintage_key,
    nullif(trim(id), '')                                    as id,
    millesime,

    -- Identity of the asset
    nullif(trim(code_chorus), '')                           as code_chorus,
    nullif(trim(designation_site), '')                      as designation_site,
    nullif(trim(designation_batiment_terrain), '')          as designation_batiment_terrain,
    nullif(trim(type), '')                                  as type,
    nullif(trim(fonction), '')                              as fonction,

    -- Location. Latitude and longitude are missing on about half the rows,
    -- either left empty or published as "-" when a ministry withheld them;
    -- to_numeric turns both into NULL.
    nullif(trim(adresse), '')                               as adresse,
    nullif(trim(ville), '')                                 as ville,
    nullif(trim(dept), '')                                  as dept,
    nullif(trim(code_postal), '')                           as code_postal,
    nullif(trim(code_insee), '')                            as code_insee,
    nullif(trim(libelle_nouvelle_region), '')               as libelle_nouvelle_region,
    nullif(trim(pays), '')                                  as pays,
    {{ to_numeric('latitude') }}                            as latitude,
    {{ to_numeric('longitude') }}                           as longitude,

    -- Occupancy and ownership
    nullif(trim(ministere), '')                             as ministere,
    nullif(trim(libelle_gestionnaire), '')                  as libelle_gestionnaire,
    nullif(trim(type_gestionnaire), '')                     as type_gestionnaire,
    nullif(trim(libelle_proprietaire), '')                  as libelle_proprietaire,
    nullif(trim(type_proprietaire), '')                     as type_proprietaire,

    -- Measurements. A surface of 0 m2 is read as missing rather than as a
    -- measurement: it is the only value rewritten by this model, and it keeps
    -- the gold layer from dividing by zero. Outliers are left untouched — the
    -- largest surface published in 2023 is 72.9 billion m2.
    nullif({{ to_numeric('surface_m2') }}, 0)               as surface_m2,
    {{ to_numeric('consommation_kwh_ef') }}                 as consommation_kwh_ef,
    nullif(trim(type_de_chauffage), '')                     as type_de_chauffage,
    nullif(trim(etat_de_sante), '')                         as etat_de_sante,
    {{ to_numeric('annee_de_construction') }}::int          as annee_de_construction,

    -- Flags. NULL where the provider said nothing, never false: three quarters
    -- of tri_des_dechets is unfilled, and reading that as "does not sort" would
    -- invent a fact.
    case trim(tri_des_dechets) when 'Oui' then true when 'Non' then false end
                                                            as tri_des_dechets,
    case trim(erp) when 'Oui' then true when 'Non' then false end
                                                            as erp,

    -- Published reference date. Identical on every row of both vintages
    -- (2021-12-31), which is why millesime, derived from the dataset id by the
    -- loader, is the column to date a row by.
    nullif(trim(date_de_reference), '')::date               as date_de_reference,

    -- Provenance, carried forward so that a row in the catalog can be traced
    -- to the signed file it came from.
    _source_file,
    _source_sha256,
    _loaded_at

from bronze
