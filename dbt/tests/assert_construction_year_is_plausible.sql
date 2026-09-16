{{ config(severity = 'warn') }}

-- A singular test: every row it returns is a failure. Unlike the tests declared
-- in schema.yml, it is a query of its own, which is what a rule about a range
-- needs as long as the project carries no dbt_utils.
--
-- Severity is 'warn' deliberately. 1,668 rows over the two vintages carry a
-- construction year below 1000 — values such as 1, 19, 50 or 100, most likely
-- truncated entries. The defect belongs to the published data and is not ours
-- to correct. Failing the build on it would leave two options, both bad:
-- silence the test, or rewrite the provider's data. Warning keeps the count
-- visible on every run, which is what a data office actually does with a known
-- defect it does not own.

select
    asset_vintage_key,
    annee_de_construction

from {{ ref('silver_parc_immobilier') }}

where annee_de_construction is not null
  and (
      annee_de_construction < 1000
      or annee_de_construction > extract(year from current_date)
  )
