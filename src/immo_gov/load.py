import argparse
from datetime import datetime
from pathlib import Path
import json
import csv
from io import StringIO
import sys
import psycopg
from psycopg import sql


from immo_gov.snapshots import COLUMNS, compute_sha256, describe_csv

def _get_csv_columns(csv_bytes: bytes) -> tuple[str, ...]:
    """Return the list of column names from a semicolon-delimited CSV.

    Decoded with utf-8-sig: the provider prefixes the file with a UTF-8 BOM,
    and without it the first column would be named "﻿code_chorus".
    """
    f = StringIO(csv_bytes.decode("utf-8-sig"))
    reader = csv.reader(f, delimiter=";")
    header = next(reader)
    return tuple(header)


def verify_csv_columns(csv_bytes: bytes) -> bool:
    """Check that the CSV has the expected columns."""
    try:
        columns = _get_csv_columns(csv_bytes)
        if columns != COLUMNS:
            print(f"CSV columns do not match expected columns. Found: {columns}")
            return False
        return True
    except Exception as e:
        print("Error while reading the CSV:", e)
        return False


def _read_manifest(manifest_path: Path) -> dict:
    """Read the shared manifest; raises if it does not exist."""
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def verify_manifest(path):
    """Check that the manifest is well-formed and self-consistent."""
    manifest = _read_manifest(path)
    snapshots = manifest["snapshots"]
    validated_entries = []
    for name, entry in snapshots.items():
        if not verify_manifest_entry(entry):
            raise ValueError(f"manifest entry {name!r} is malformed")
        if entry["dataset_id"] != name:
            raise ValueError(f"manifest key {name!r} does not match its dataset_id")
        validated_entries.append(entry)
    if not validated_entries:
        raise ValueError(f"manifest {path} lists no snapshot")
    return validated_entries



def verify_manifest_entry(entry) -> bool:
    """Check that the manifest entry is well-formed and self-consistent."""
    required_keys = {
        "dataset_id", "file", "source_url", "extracted_at",
        "sha256", "bytes", "columns", "rows", "source"
    }
    if not required_keys.issubset(entry.keys()):
        print(f"Manifest entry is missing required keys: {required_keys - set(entry.keys())}")
        return False
    if not isinstance(entry["columns"], int) or entry["columns"] <= 0:
        print("Manifest entry has invalid 'columns' value.")
        return False
    if not isinstance(entry["rows"], int) or entry["rows"] < 0:
        print("Manifest entry has invalid 'rows' value.")
        return False
    return True


def _build_create_table(schema, table, columns):
    # Builds the column definitions for the CREATE TABLE statement
    col_defs = []
    for col in columns:
        col_defs.append(
            sql.SQL("{} TEXT").format(sql.Identifier(col))
        )
    # Builds CREATE TABLE query
    METADATA_COLUMNS = sql.SQL(
        "millesime date, _source_file text, _source_sha256 text, _loaded_at timestamptz DEFAULT now()"
    )
    query = sql.SQL("CREATE TABLE IF NOT EXISTS {} ({}, {})").format(
            sql.Identifier(schema, table),
            sql.SQL(", ").join(col_defs),
            METADATA_COLUMNS)
    return query

def _build_staging_table(table, columns):
    # Builds the column definitions for the CREATE TABLE statement
    col_defs = []
    for col in columns:
        col_defs.append(
            sql.SQL("{} TEXT").format(sql.Identifier(col))
        )
    # Builds CREATE TABLE query
    query = sql.SQL("CREATE TEMP TABLE {} ({}) ON COMMIT DROP").format(
        sql.Identifier(table),
        sql.SQL(", ").join(col_defs)
    )
    return query


def _millesime_from_dataset_id (dataset_id: str) -> datetime.date:
    """Extract the millesime (YYYY-MM-DD) from the dataset_id."""
    # Assuming the dataset_id is in the format "parc_immobilier_YYYY-MM-DD"
    parts = dataset_id.split("_")
    if len(parts) < 3:
        raise ValueError(f"Invalid dataset_id format: {dataset_id}")
    millesime = parts[-1]
    return datetime.strptime(millesime, "%Y%m%d").date()


def load_snapshot(entry, snapshots_dir, conninfo) -> int:
    """Load a snapshot into the database, returning the number of rows inserted."""

    # Sets csv_bytes
    csv_path = snapshots_dir / entry["file"]
    if not csv_path.exists():
        raise FileNotFoundError(f"Snapshot file does not exist: {csv_path}")
    csv_bytes = csv_path.read_bytes()

    # Verify snapshot content against the manifest entry
    if compute_sha256(csv_bytes) != entry["sha256"]:
        raise ValueError(f"{csv_path}: digest does not match the manifest, not the published snapshot")
    # Verify that the CSV has the expected columns and row count
    if not verify_csv_columns(csv_bytes):
        raise ValueError(f"{csv_path}: columns do not match the contract")
    columns, rows = describe_csv(csv_bytes)
    if columns != entry["columns"] or rows != entry["rows"]:
        raise ValueError(
            f"{csv_path}: {columns} columns / {rows} rows, "
            f"manifest says {entry['columns']} / {entry['rows']}"
        )

    table = sql.Identifier("bronze", "parc_immobilier")
    millesime = _millesime_from_dataset_id(entry["dataset_id"])

    with psycopg.connect(conninfo) as conn, conn.cursor() as cur:
        cur.execute(_build_create_table("bronze", "parc_immobilier", COLUMNS))

        # Same file already loaded: nothing to do.
        cur.execute(
            sql.SQL("SELECT 1 FROM {} WHERE _source_sha256 = %s LIMIT 1").format(table),
            (entry["sha256"],),
        )
        if cur.fetchone():
            return 0

        # Another version of this vintage: replace it, inside the same transaction.
        cur.execute(sql.SQL("DELETE FROM {} WHERE millesime = %s").format(table), (millesime,))

        cur.execute(_build_staging_table("staging", COLUMNS))
        copy_sql = "COPY staging FROM STDIN WITH (FORMAT csv, DELIMITER ';', HEADER true)"
        with cur.copy(copy_sql) as copy:
            copy.write(csv_bytes)

        cols = sql.SQL(", ").join(map(sql.Identifier, COLUMNS))
        cur.execute(
            sql.SQL(
                "INSERT INTO {} ({}, millesime, _source_file, _source_sha256) "
                "SELECT {}, %s, %s, %s FROM staging"
            ).format(table, cols, cols),
            (millesime, entry["file"], entry["sha256"]),
        )
        if cur.rowcount != entry["rows"]:
            raise RuntimeError(
                f"{csv_path}: inserted {cur.rowcount} rows, expected {entry['rows']}"
            )
        return cur.rowcount

def main() -> int:
    parser = argparse.ArgumentParser(description="Load versioned snapshots into bronze.")
    parser.add_argument("manifest", type=Path, help="Path to the manifest JSON file.")
    parser.add_argument("snapshots_dir", type=Path, help="Directory containing snapshot CSV files.")
    parser.add_argument("conninfo", type=str, help="PostgreSQL connection string.")
    args = parser.parse_args()

    try:
        for entry in verify_manifest(args.manifest):
            inserted = load_snapshot(entry, args.snapshots_dir, args.conninfo)
            status = "already loaded, skipped" if inserted == 0 else f"{inserted} rows inserted"
            print(f"{entry['dataset_id']}: {status}")
    except Exception as e:
        print(f"Error: {e}")
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())

