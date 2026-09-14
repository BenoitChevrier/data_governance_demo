# `data_governance_demo`

Foundations of an **operational Data Office** on the French State's real-estate
portfolio: metadata catalog, business glossary, classification, ownership,
quality tests and column-level lineage.

The goal is to show governance that **runs**, not governance described in
slides: every object visible in the interface is produced by the pipeline, not
typed in for the demo.

---

## Requirements

Measured on 2026-08-27, Windows 11 with Docker Desktop on the WSL2 backend,
Docker constrained to 6 GB and 4 vCPUs. Figures are observed, not quoted from
vendor documentation.

| | |
|---|---|
| **Docker memory** | **6 GB minimum.** The stack settles at **4.6 GiB**, leaving ~1.2 GiB of headroom. |
| **vCPUs** | 4 |
| **Disk** | **~11.7 GB of images**, of which 7.4 GB is the ingestion image alone |
| **Startup** | **~70 s** from `docker compose up` to both UIs answering |

```
docker compose up -d
```

- Catalog UI — http://localhost:8585
- Airflow — http://localhost:8080

**If it does not fit in 6 GB**, the first lever is the Elasticsearch heap:
lower `ES_JAVA_OPTS` from `-Xms1024m -Xmx1024m` to `512m` in
[docker-compose.yml](docker-compose.yml). Elasticsearch is the second heaviest
service; the ingestion container is the heaviest, and it is not tunable the
same way.

**On Windows**, Docker memory is set in `%USERPROFILE%\.wslconfig`, not in the
Docker Desktop settings panel — the slider is read-only on the WSL2 backend.
After changing it and running `wsl --shutdown`, published ports can be left
bound by a stale relay: `docker compose down && docker compose up -d` clears it.

## Status

**Under construction — Sprint 0 complete.** Version 1.0 is due on **16 September 2026**.

This repository has been public since day one, construction included. The
commit history is part of what is being shown.

| Sprint | Window | Focus | Status |
|---|---|---|---|
| 0 | 26 → 30 Aug | Docker foundation, proof of startup | ✅ done |
| 1 | 31 Aug → 6 Sep | Data extraction, PostgreSQL, dbt, tests | upcoming |
| 2 | 7 → 13 Sep | Governance and lineage in the catalog | upcoming |
| 3 | 14 → 16 Sep | CI, documentation, release | upcoming |

## Stack

- **OpenMetadata 1.13.4** — catalog, glossary, classification, quality
  ([ADR-001](docs/adr/ADR-001-catalog-choice.md): why it, and not DataHub)
- **PostgreSQL** — *bronze* layer
- **dbt-core** — *bronze → silver → gold*, and the **source of the lineage**
- **Airflow**, embedded in OpenMetadata — ingestion and quality tests
- **Python 3.11+**, pytest, ruff, GitHub Actions

The data foundation — real open data rather than a synthetic dataset — is
documented in [ADR-002](docs/adr/ADR-002-data-foundation.md).

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
