"""Generate the dbt sources.yml from a TSV of column definitions.

Why a generator rather than hand-written YAML: 32 column descriptions typed
into indented YAML is a typo waiting to happen, and nobody can tell afterwards
whether the file still matches the column contract. Here the TSV is the input a
human edits, the YAML is an artefact, and the script refuses to write it unless
the TSV covers exactly the contract — every column of immo_gov.snapshots.COLUMNS
plus the four metadata columns added by the loader, no more, no less.

Step 3 of a three-step pipeline: tools/fetch_column_definitions.py refreshes the
TSV from the provider's API, a human writes the English descriptions, this
script renders the YAML.

TSV columns (tab-separated, header row required), as written by the fetch script:

    name            column name, must match the contract
    label_fr        the provider's own label, carried for the reader of the TSV
    type            the provider's declared type, same
    definition_fr   optional, the provider's wording, transcribed and quoted as-is
    source_system   optional, e.g. Chorus RE-Fx, Référentiel Technique
    description_en  the definition published in the catalog, written by hand
    tags            optional, comma- or semicolon-separated, closed vocabulary

Only description_en is required: a column nobody has described has no business
being documented in the catalog, and a blank line would be worse than a refusal.

Lines starting with # are ignored, so the TSV can carry its own notes.

Tags are the classification of the demo, declared as code rather than clicked
in the catalog UI: the TSV is reviewable in a diff and regenerable. They are
validated against ALLOWED_TAGS below, because a free-text vocabulary drifts
into pii / PII / personal_data within weeks, and an unenforced classification
is exactly the PowerPoint governance this project argues against.

Usage, from the repository root:

    python tools/generate_sources_yml.py                 # write dbt/models/sources.yml
    python tools/generate_sources_yml.py --check         # verify it is up to date
    python tools/generate_sources_yml.py --force         # overwrite an existing file

The TSV is read as utf-8-sig: a spreadsheet export carries a BOM, and without
it the first column would be named "﻿name".
"""

import argparse
import csv
import re
import sys
import textwrap
from pathlib import Path

from immo_gov.snapshots import COLUMNS

# Added by the loader, documented by us: the provider knows nothing about them.
METADATA_COLUMNS = ("millesime", "_source_file", "_source_sha256", "_loaded_at")
EXPECTED_COLUMNS = COLUMNS + METADATA_COLUMNS

# Closed classification vocabulary, two axes kept deliberately short.
#
# Confidentiality: everything published under the Open Licence is public, except
# the owner label, which names a natural person on part of the rows — hence a
# tag of its own rather than a blanket "public".
#
# Subject: what the column is about, so that a reader of the catalog can filter
# without reading 32 descriptions.
ALLOWED_TAGS = (
    # confidentiality
    "public",
    "personal-data",
    # subject
    "identifier",
    "location",
    "organisation",
    "building",
    "energy",
    "environment",
    # provenance
    "technical",
)

_TAG_SEPARATORS = re.compile(r"[;,]")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEFINITIONS = ROOT / "dbt" / "column_definitions.tsv"
DEFAULT_OUTPUT = ROOT / "dbt" / "models" / "sources.yml"

WIDTH = 96


def read_definitions(path: Path) -> list[dict[str, str]]:
    """Read the TSV, dropping comment lines and blank rows."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        header = reader.fieldnames or []
        rows = [
            {key: (value or "").strip() for key, value in row.items() if key}
            for row in reader
            if row.get("name") and not row["name"].lstrip().startswith("#")
        ]
    for required in ("name", "description_en"):
        if required not in header:
            raise ValueError(
                f"{path}: no '{required}' column — "
                "refresh the file with tools/fetch_column_definitions.py"
            )
    if not rows:
        raise ValueError(f"{path}: no definition found")
    undescribed = [r["name"] for r in rows if not r.get("description_en")]
    if undescribed:
        raise ValueError(
            f"{path}: no description_en for {len(undescribed)} column(s): " + ", ".join(undescribed)
        )
    return rows


def check_coverage(definitions: list[dict[str, str]]) -> None:
    """Refuse anything but an exact match with the column contract."""
    names = [row["name"] for row in definitions]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    missing = [name for name in EXPECTED_COLUMNS if name not in names]
    unknown = [name for name in names if name not in EXPECTED_COLUMNS]

    problems = []
    if duplicates:
        problems.append(f"defined twice: {', '.join(duplicates)}")
    if missing:
        problems.append(f"missing ({len(missing)}): {', '.join(missing)}")
    if unknown:
        problems.append(f"not in the contract: {', '.join(unknown)}")
    if problems:
        raise ValueError("TSV does not match the column contract — " + " ; ".join(problems))


def parse_tags(row: dict[str, str]) -> list[str]:
    """Split the tags cell, keeping the order written in the TSV."""
    raw = row.get("tags", "")
    return [tag.strip() for tag in _TAG_SEPARATORS.split(raw) if tag.strip()]


def check_tags(definitions: list[dict[str, str]]) -> None:
    """Refuse a tag outside the declared vocabulary.

    Failing the generation is the whole point: a classification nobody enforces
    is a classification nobody can trust.
    """
    unknown = {
        f"{row['name']}: {tag}"
        for row in definitions
        for tag in parse_tags(row)
        if tag not in ALLOWED_TAGS
    }
    if unknown:
        raise ValueError(
            "tag outside the vocabulary — "
            + " ; ".join(sorted(unknown))
            + f" ; allowed: {', '.join(ALLOWED_TAGS)}"
        )


def compose_description(row: dict[str, str]) -> str:
    """Assemble one description: definition, provider wording, source system.

    Composing here rather than in the TSV keeps the 32 entries in the same shape:
    the wording of a sentence cannot drift from one column to the next.
    """
    parts = [row["description_en"].rstrip(".") + "."]
    if row.get("definition_fr"):
        parts.append(f"Provider definition: « {row['definition_fr'].strip()} ».")
    if row.get("source_system"):
        parts.append(f"Source system: {row['source_system']}.")
    return " ".join(parts)


def _folded_scalar(text: str, indent: int) -> str:
    """Render text as a YAML folded block scalar, wrapped and indented.

    A block scalar needs no quoting or escaping, which matters for descriptions
    holding quotes, colons and accented characters.
    """
    pad = " " * indent
    wrapped = textwrap.fill(" ".join(text.split()), width=WIDTH - indent)
    return "\n".join(pad + line for line in wrapped.splitlines())


def render_sources_yaml(
    definitions: list[dict[str, str]],
    table_description: str,
    source_name: str,
    schema: str,
    table: str,
    definitions_path: Path,
) -> str:
    """Render the whole file, columns in contract order rather than TSV order."""
    by_name = {row["name"]: row for row in definitions}
    # The header names the input so the file can be traced back to it. A path
    # outside the repository — a scratch file, a test — has no relative form,
    # and that must not fail the generation.
    try:
        relative = definitions_path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        relative = definitions_path.as_posix()

    lines = [
        "# GENERATED FILE — do not edit by hand.",
        "#",
        f"# Source of truth: {relative}",
        "# Regenerate:      python tools/generate_sources_yml.py",
        "# Verify:          python tools/generate_sources_yml.py --check",
        "#",
        "# Field list fetched from the provider's catalog API by",
        "# tools/fetch_column_definitions.py. Provider definitions transcribed from the",
        "# data dictionary attached to the dataset, snapshotted with its SHA-256 under",
        "# data/snapshots/. The four metadata columns are added by the loader and",
        "# documented by this project.",
        "#",
        "# Column tags are the classification of this demo, declared as code and",
        "# validated against a closed vocabulary by the generator.",
        "version: 2",
        "",
        "sources:",
        f"  - name: {source_name}",
        f"    schema: {schema}",
        "    tables:",
        f"      - name: {table}",
        "        description: >-",
        _folded_scalar(table_description, 10),
        "        columns:",
    ]
    for name in EXPECTED_COLUMNS:
        row = by_name[name]
        lines.append(f"          - name: {name}")
        lines.append("            description: >-")
        lines.append(_folded_scalar(compose_description(row), 14))
        tags = parse_tags(row)
        if tags:
            lines.append(f"            tags: [{', '.join(tags)}]")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the dbt sources.yml from a TSV.")
    parser.add_argument("--definitions", type=Path, default=DEFAULT_DEFINITIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--table-description",
        type=Path,
        default=None,
        help="Optional text file holding the table description.",
    )
    parser.add_argument("--source-name", default="bronze")
    parser.add_argument("--schema", default="bronze")
    parser.add_argument("--table", default="parc_immobilier")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing output file.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare the existing output with what the TSV would produce; write nothing.",
    )
    args = parser.parse_args(argv)

    try:
        definitions = read_definitions(args.definitions)
        check_coverage(definitions)
        check_tags(definitions)

        if args.table_description:
            table_description = args.table_description.read_text(encoding="utf-8-sig")
        else:
            table_description = (
                "Raw-layer copy of the French State real-estate portfolio (DGFiP open data, "
                "Open Licence 2.0), vintages 2022-12-31 and 2023-12-31: one row per asset per "
                "vintage. Loaded as published, defects included, so that completeness and "
                "validity can be measured rather than lost at load time. Not meant for direct "
                "use: consume the silver and gold layers."
            )

        rendered = render_sources_yaml(
            definitions,
            table_description,
            args.source_name,
            args.schema,
            args.table,
            args.definitions,
        )

        if args.check:
            if not args.output.exists():
                print(f"{args.output}: missing, run the generator")
                return 1
            current = args.output.read_text(encoding="utf-8")
            if current != rendered:
                print(f"{args.output}: out of date, regenerate it")
                return 1
            print(f"{args.output}: up to date, {len(definitions)} columns")
            return 0

        if args.output.exists() and not args.force:
            print(f"{args.output}: already exists, use --force to overwrite")
            return 1

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
        print(f"{args.output}: written, {len(definitions)} columns")
        return 0

    except (OSError, ValueError) as error:
        print(f"Error: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
