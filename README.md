# `data_governance_demo`

Foundations of an **operational Data Office** on the French State's real-estate
portfolio: metadata catalog, business glossary, classification, ownership,
quality tests and column-level lineage.

The goal is to show governance that **runs**, not governance described in
slides: every object visible in the interface is produced by the pipeline, not
typed in for the demo.

---

## Status

**Under construction — Sprint 0.** Version 1.0 is due on **16 September 2026**.

This repository has been public since day one, construction included. The
commit history is part of what is being shown.

| Sprint | Window | Focus | Status |
|---|---|---|---|
| 0 | 26 → 30 Aug | Docker foundation, proof of startup | 🚧 in progress |
| 1 | 31 Aug → 6 Sep | Data extraction, PostgreSQL, dbt, tests | upcoming |
| 2 | 7 → 13 Sep | Governance and lineage in the catalog | upcoming |
| 3 | 14 → 16 Sep | CI, documentation, release | upcoming |

## Stack

- **OpenMetadata 1.12.x** — catalog, glossary, classification, quality
  ([ADR-001](docs/adr/ADR-001-catalog-choice.md): why it, and not DataHub)
- **PostgreSQL** — *bronze* layer
- **dbt-core** — *bronze → silver → gold*, and the **source of the lineage**
- **Airflow**, embedded in OpenMetadata — ingestion and quality tests
- **Python 3.11+**, pytest, ruff, GitHub Actions

The data foundation — real open data rather than a synthetic dataset — is
documented in [ADR-002](docs/adr/ADR-002-data-foundation.md).

## Requirements

To be measured and recorded here at the end of Sprint 0: required Docker memory
allocation, observed cold-start time.

## Data

**Real public data, captured as versioned snapshots.** No network call is needed
at startup: the extracts live in the repository, with their extraction date and
the script that produced them.

| Source | Content | Size |
|---|---|---|
| **French State real-estate portfolio** — DGFiP | 33,900 assets, 29 columns, 2022 and 2023 vintages | 12.7 MB |
| **Géorisques** — BRGM | Exposure to natural hazards, by INSEE municipality code | tens of KB |

Only the **contact fields** (steward, email) are synthetic: open data is
de-identified by construction and carries none. They are flagged as fabricated
in the catalog itself — the provenance of each column is part of what is
governed.

This data is **incomplete**, and that is deliberate: 78 % of assets have no
energy consumption figure, 14 % carry an aberrant construction year, and
Ministry of Defence assets are redacted by the publisher. The project does not
paper over these defects — **it measures them, the tests catch them, and the
catalog reports on them.** A clean dataset would demonstrate no governance at
all.

Full reasoning, the seven datasets evaluated and the quantified reasons for
rejecting each: [ADR-002](docs/adr/ADR-002-data-foundation.md).

### Attribution

Data published under the **Open Licence / Etalab 2.0**:

> *French State real-estate portfolio — DGFiP, vintages 2022-12-31 and
> 2023-12-31, extracted on 2026-08-26.
> Natural hazard data — BRGM / Géorisques, extracted on 2026-08-26.*

## Licence

[MIT](LICENSE)
