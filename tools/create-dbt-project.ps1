<#
.SYNOPSIS
    Create the dbt project skeleton for data_governance_demo.

.DESCRIPTION
    Creates dbt/ with the three configuration files that are easy to get wrong,
    and the model folders. It deliberately does NOT create the model .sql files:
    those carry the business rules and are written by hand. An empty .sql file
    would also make `dbt build` fail.

    Written for Windows PowerShell 5.1. Deliberately ASCII only: 5.1 reads a
    .ps1 without a BOM as ANSI, and accented characters would be mangled.

    Files are written as UTF-8 without BOM, with LF line endings, to match
    .gitattributes. Out-File -Encoding utf8 would add a BOM on 5.1.

.PARAMETER Root
    Repository root. Defaults to the parent of this script's folder, so the
    script works from any working directory.

.PARAMETER Force
    Overwrite files that already exist. Without it, existing files are kept.

.EXAMPLE
    .\tools\create-dbt-project.ps1

.EXAMPLE
    .\tools\create-dbt-project.ps1 -Force
#>
[CmdletBinding()]
param(
    [string] $Root,
    [switch] $Force
)

$ErrorActionPreference = 'Stop'

if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
$dbtRoot = Join-Path $Root 'dbt'

$created = 0
$kept = 0

function New-ProjectDirectory {
    param([string] $Path)
    if (Test-Path $Path) { return }
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    Write-Host ("  + {0}\" -f (Resolve-Path -Relative $Path))
}

function New-ProjectFile {
    param([string] $Path, [string] $Content)

    if ((Test-Path $Path) -and -not $Force) {
        Write-Host ("  = {0} already exists, left untouched" -f (Split-Path -Leaf $Path))
        $script:kept++
        return
    }
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }

    # LF endings and UTF-8 without BOM, as required by .gitattributes.
    $normalised = $Content -replace "`r`n", "`n"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $normalised, $utf8NoBom)
    Write-Host ("  + {0}" -f (Split-Path -Leaf $Path))
    $script:created++
}

# --- File contents -----------------------------------------------------------

$dbtProject = @'
name: immo_gov
version: "1.0.0"
profile: immo_gov

model-paths: ["models"]
macro-paths: ["macros"]

# One folder per medallion layer, each materialised in its own schema.
# Tables rather than the default view: the OpenMetadata profiler and its
# quality tests work on stable relations, not on queries recomputed on read.
models:
  immo_gov:
    silver:
      +schema: silver
      +materialized: table
    gold:
      +schema: gold
      +materialized: table
'@

$profiles = @'
# Local demo credentials, identical to docker-compose.yml and versioned for the
# same reason: the stack binds to localhost and holds nothing but public open
# data. Hiding them behind a file to copy would break the one-command promise.
#
# dbt looks for profiles.yml in the current working directory first, so run
# every dbt command from the dbt/ folder.
immo_gov:
  target: dev
  outputs:
    dev:
      type: postgres
      host: localhost
      port: 5432
      user: immo_user
      password: immo_password
      dbname: immo
      schema: silver
      threads: 4
'@

$generateSchemaName = @'
{#
    Without this macro, dbt concatenates the profile schema and the schema
    declared on a model: a gold model would land in "silver_gold", not "gold".
    Verified on dbt 1.12.3.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
'@

$sources = @'
# Source declaration and column definitions for the bronze layer.
#
# The 28 source columns are documented by the provider itself, in the data
# dictionary attached to the dataset (DGFiP notice of 2024-12-19): each variable
# has a definition and a source system (Chorus RE-Fx, Referentiel Technique,
# OSFi, IGN). Copy them here rather than inventing them; keep the original
# wording in quotes and name the source system.
#
# The four metadata columns are ours: millesime, _source_file, _source_sha256
# and _loaded_at.
#
# TODO: complete the 32 column entries below.
version: 2

sources:
  - name: bronze
    schema: bronze
    tables:
      - name: parc_immobilier
        description: >
          Raw-layer copy of the French State real-estate portfolio (DGFiP open
          data, Open Licence 2.0), vintages 2022-12-31 and 2023-12-31, loaded
          as published with all defects kept measurable.
        columns:
          - name: id
            description: >
              Unique identifier of the asset. Provider definition: "Identifiant
              unique du bien". Source system: Chorus RE-Fx.
          - name: millesime
            description: >
              Vintage date of the snapshot, derived from the dataset identifier.
              Use this, not date_de_reference, which reads 2021-12-31 in every
              vintage.
'@

# --- Run ---------------------------------------------------------------------

Write-Host ""
Write-Host ("dbt project skeleton in {0}" -f $dbtRoot)
Write-Host ""

foreach ($relative in @('', 'macros', 'models', 'models\silver', 'models\gold')) {
    New-ProjectDirectory (Join-Path $dbtRoot $relative)
}

New-ProjectFile (Join-Path $dbtRoot 'dbt_project.yml') $dbtProject
New-ProjectFile (Join-Path $dbtRoot 'profiles.yml') $profiles
New-ProjectFile (Join-Path $dbtRoot 'macros\generate_schema_name.sql') $generateSchemaName
New-ProjectFile (Join-Path $dbtRoot 'models\sources.yml') $sources

Write-Host ""
Write-Host ("{0} file(s) written, {1} kept." -f $created, $kept)
Write-Host ""
Write-Host "Next steps, from the dbt folder:"
Write-Host "  cd dbt"
Write-Host "  dbt debug        # expects: All checks passed!"
Write-Host ""
Write-Host "Then write, by hand:"
Write-Host "  models\silver\silver_parc_immobilier.sql   typing, cleaning, asset_key"
Write-Host "  models\silver\schema.yml                   description and contract tests"
Write-Host "  models\gold\gold_intensite_energetique.sql final energy kWh per m2"
Write-Host "  models\gold\schema.yml                     description and contract tests"
Write-Host ""
