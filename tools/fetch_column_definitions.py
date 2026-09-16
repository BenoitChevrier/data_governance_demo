"""Fetch the provider's column metadata and refresh the definitions TSV.

Step 1 of a three-step pipeline:

    1. this script        fetch the field list from the API, snapshot the data
                          dictionary, refresh dbt/column_definitions.tsv
    2. a human            transcribe definition_fr and source_system from the
                          dictionary, write description_en, set tags
    3. generate_sources_yml.py
                          render dbt/models/sources.yml from the TSV

What the provider actually exposes, measured on 2026-09-16: the catalog API
gives the field list (name, label, type) but every field description is null.
The definitions live in a PDF attached to the dataset, and that PDF has no text
layer — the dictionary is a picture of a table. So the transcription cannot be
automated without OCR, which is not worth a system dependency for 27 rows done
once.

What is automated is the part that decays: the field list comes from the API
rather than from memory, and the dictionary is snapshotted with its SHA-256
next to the CSV snapshots. If the provider republishes a different dictionary,
the digest changes and this script says so — the transcriptions are then known
to need a review, instead of silently describing the wrong thing.

The TSV columns are ordered by who owns them:

    name, label_fr, type        fetched, overwritten on every run
    definition_fr, source_system
                                transcribed from the dictionary PDF, preserved
    description_en, tags        authored here, preserved

Usage, from the repository root:

    python tools/fetch_column_definitions.py
    python tools/fetch_column_definitions.py --dry-run
"""

import argparse
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests

from immo_gov.extract import make_metadata_url, update_manifest, write_hash
from immo_gov.snapshots import COLUMNS, compute_sha256

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = "parc_immobilier_etat_20231231"
DEFAULT_OUTPUT = ROOT / "dbt" / "column_definitions.tsv"
DEFAULT_SNAPSHOT_DIR = ROOT / "data" / "snapshots"
MANIFEST_NAME = "manifest.json"

TSV_HEADER = (
    "name",
    "label_fr",
    "type",
    "definition_fr",
    "source_system",
    "description_en",
    "tags",
)

# Columns added by the loader: the provider knows nothing about them, so they
# are appended to the fetched field list rather than expected in the API.
METADATA_ROWS = (
    {"name": "millesime", "type": "date", "source_system": "immo_gov loader"},
    {"name": "_source_file", "type": "text", "source_system": "immo_gov loader"},
    {"name": "_source_sha256", "type": "text", "source_system": "immo_gov loader"},
    {"name": "_loaded_at", "type": "timestamp", "source_system": "immo_gov loader"},
)

# Fields the human owns. A refresh must never overwrite them.
PRESERVED_FIELDS = ("definition_fr", "source_system", "description_en", "tags")


def fetch_catalog(dataset_id: str) -> dict:
    """Return the full catalog entry: fields, attachments and metadata."""
    response = requests.get(make_metadata_url(dataset_id), timeout=30)
    response.raise_for_status()
    return response.json()


def extract_fields(catalog: dict) -> list[dict[str, str]]:
    """Return one row per published field, in the provider's own order."""
    fields = catalog.get("fields") or []
    if not fields:
        raise ValueError("the catalog entry carries no field list")
    return [
        {
            "name": (field.get("name") or "").strip(),
            # The provider's first label carries a stray BOM, visible in the
            # CSV header too. Stripped here so it does not travel any further.
            "label_fr": (field.get("label") or "").replace("﻿", "").strip(),
            "type": (field.get("type") or "").strip(),
        }
        for field in fields
    ]


def check_contract(fetched: list[dict[str, str]]) -> None:
    """Refuse to refresh when the published fields no longer match COLUMNS.

    A column appearing or disappearing at the source breaks the loader before
    it breaks the documentation. Failing here makes that visible at the moment
    it happens, rather than at the next load.
    """
    names = tuple(row["name"] for row in fetched)
    if names == COLUMNS:
        return
    added = [name for name in names if name not in COLUMNS]
    removed = [name for name in COLUMNS if name not in names]
    detail = []
    if added:
        detail.append(f"added at the source: {', '.join(added)}")
    if removed:
        detail.append(f"no longer published: {', '.join(removed)}")
    if not detail:
        detail.append("same names, different order")
    raise ValueError(
        "the published fields no longer match immo_gov.snapshots.COLUMNS — "
        + " ; ".join(detail)
        + " ; update the contract and the loader first"
    )


def select_dictionary(catalog: dict) -> dict[str, str]:
    """Return the PDF attachment holding the data dictionary."""
    attachments = [
        attachment
        for attachment in catalog.get("attachments") or []
        if attachment.get("mimetype") == "application/pdf"
    ]
    if not attachments:
        raise ValueError("no PDF attachment on this dataset")
    if len(attachments) > 1:
        titles = ", ".join(a.get("title", "?") for a in attachments)
        raise ValueError(f"several PDF attachments, cannot choose: {titles}")
    return attachments[0]


def snapshot_dictionary(
    attachment: dict[str, str],
    directory: Path,
    metas: dict,
    dry_run: bool = False,
) -> tuple[Path, str, bool]:
    """Download the dictionary, hash it, and record it in the manifest.

    Returns the file path, its digest, and whether the digest differs from the
    one already on disk — that flag is what tells the reader to re-check the
    transcriptions.
    """
    response = requests.get(attachment["url"], timeout=60)
    response.raise_for_status()
    content = response.content
    digest = compute_sha256(content)

    # The attachment id is ASCII and stable, unlike the title, which carries
    # accents and spaces that have no business in a versioned filename.
    filename = attachment["id"].removesuffix("_pdf") + ".pdf"
    path = directory / filename
    sidecar = path.with_suffix(".sha256")
    previous = sidecar.read_text(encoding="utf-8").strip() if sidecar.exists() else ""
    changed = bool(previous) and previous != digest

    if dry_run:
        return path, digest, changed

    directory.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    write_hash(sidecar, digest)
    update_manifest(
        directory / MANIFEST_NAME,
        {
            "dataset_id": attachment["id"],
            "file": filename,
            "source_url": attachment["url"],
            "extracted_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "sha256": digest,
            "bytes": len(content),
            "source": {
                "title": attachment.get("title"),
                "publisher": metas.get("publisher"),
                "licence": metas.get("license"),
                "modified": metas.get("modified"),
            },
        },
    )
    return path, digest, changed


def read_existing(path: Path) -> dict[str, dict[str, str]]:
    """Read the current TSV, keyed by column name. Missing file means empty."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {
            row["name"].strip(): {k: (v or "").strip() for k, v in row.items() if k}
            for row in csv.DictReader(handle, delimiter="\t")
            if row.get("name") and not row["name"].lstrip().startswith("#")
        }


def merge_rows(
    fetched: list[dict[str, str]], existing: dict[str, dict[str, str]]
) -> list[dict[str, str]]:
    """Refresh the fetched fields, carry over everything a human wrote."""
    rows = []
    for row in [*fetched, *METADATA_ROWS]:
        merged = {key: "" for key in TSV_HEADER}
        merged.update({k: v for k, v in row.items() if k in TSV_HEADER})
        for key in PRESERVED_FIELDS:
            kept = existing.get(row["name"], {}).get(key, "")
            if kept:
                merged[key] = kept
        rows.append(merged)
    return rows


def write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write the TSV with LF endings and no BOM, as .gitattributes requires."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        writer = csv.DictWriter(handle, fieldnames=TSV_HEADER, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def report(
    rows: list[dict[str, str]],
    existing: dict[str, dict[str, str]],
    dictionary_path: Path,
    changed: bool,
) -> None:
    """Say what moved and what is left to write by hand."""
    names = {row["name"] for row in rows}
    added = sorted(names - set(existing)) if existing else []
    removed = sorted(set(existing) - names)
    todo = [row["name"] for row in rows if not row["description_en"]]

    print(f"Dictionary: {dictionary_path.name}")
    if changed:
        print("  ! the dictionary changed since the last run — re-check the transcriptions")
    if added:
        print(f"  + new columns: {', '.join(added)}")
    if removed:
        print(f"  - columns gone from the TSV: {', '.join(removed)}")
    print(f"Columns: {len(rows)} ({len(rows) - len(METADATA_ROWS)} published + metadata)")
    if todo:
        print(f"  {len(todo)} still without description_en: {', '.join(todo)}")
    else:
        print("  every column has an English description")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh the column definitions TSV.")
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and report, write nothing.",
    )
    args = parser.parse_args(argv)

    try:
        catalog = fetch_catalog(args.dataset)
        fetched = extract_fields(catalog)
        check_contract(fetched)

        metas = catalog.get("metas", {}).get("default", {})
        attachment = select_dictionary(catalog)
        dictionary_path, _, changed = snapshot_dictionary(
            attachment, args.snapshot_dir, metas, dry_run=args.dry_run
        )

        existing = read_existing(args.output)
        rows = merge_rows(fetched, existing)
        if not args.dry_run:
            write_tsv(args.output, rows)

        report(rows, existing, dictionary_path, changed)
        print(f"{'Would write' if args.dry_run else 'Written'}: {args.output}")
        return 0

    except (OSError, ValueError, requests.exceptions.RequestException) as error:
        print(f"Error: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
