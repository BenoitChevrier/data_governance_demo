"""Tests for the bronze loader.

Two families, run differently:

A. Offline tests — everything that happens before a database connection is
   opened: manifest reading and validation, the column contract, SQL
   composition, and the loader's own refusals. No Docker needed; these run in
   CI and carry the coverage threshold.

B. Integration tests — marked `integration`, run against a disposable database
   created on the postgres-immo container. They prove what only a real
   PostgreSQL can: idempotence, replacement of a vintage, transactional rollback.
   Run them with:  pytest -m integration --no-cov

Every prototype below skips until written. An empty body would pass and show
green while checking nothing; a skip keeps the remaining work visible.

Tests marked "Fails with the current load.py" describe a behaviour the loader
does not have yet. Write them first, watch them fail, then fix the code.

Imports to add as tests get written:
    import json, sys
    from immo_gov.snapshots import COLUMNS, compute_sha256
    from immo_gov.load import (
        _build_create_table, _build_staging_table, _get_csv_columns,
        _millesime_from_dataset_id, _read_manifest, load_snapshot, main,
        verify_csv_columns, verify_manifest, verify_manifest_entry,
    )
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

# =============================================================================
# Helpers
# =============================================================================


def _make_snapshot(
    directory: Path, rows: int = 3, dataset_id: str = "parc_immobilier_etat_20231231"
) -> dict:
    """Write a tiny valid snapshot CSV into `directory` and return its manifest entry.

    Header = COLUMNS, `rows` data lines, semicolon-delimited. The entry's
    sha256, columns and rows are computed from the bytes actually written, so
    every validation passes: each test then breaks exactly one field.
    """
    raise NotImplementedError


def _write_manifest(directory: Path, entries: list[dict]) -> Path:
    """Write a manifest.json keyed by dataset_id into `directory`, return its path."""
    raise NotImplementedError


# =============================================================================
# A. Offline tests
# =============================================================================

# --- _read_manifest ----------------------------------------------------------


def test_read_manifest_returns_the_parsed_json(tmp_path: Path) -> None:
    pytest.skip("to write")


def test_read_manifest_raises_when_the_file_is_missing(tmp_path: Path) -> None:
    """Fails with the current load.py: a missing manifest returns an empty one."""
    pytest.skip("to write")


# --- verify_manifest ---------------------------------------------------------


def test_verify_manifest_returns_every_entry_of_the_real_manifest() -> None:
    """Reads data/snapshots/manifest.json, versioned in the repository: 2 entries."""
    pytest.skip("to write")


def test_verify_manifest_rejects_an_entry_missing_a_required_key(tmp_path: Path) -> None:
    pytest.skip("to write")


def test_verify_manifest_rejects_a_key_that_differs_from_its_dataset_id(tmp_path: Path) -> None:
    pytest.skip("to write")


def test_verify_manifest_rejects_a_manifest_without_snapshots(tmp_path: Path) -> None:
    """Fails with the current load.py: .get("snapshots", {}) turns it into an empty list."""
    pytest.skip("to write")


def test_verify_manifest_rejects_a_manifest_with_no_entry(tmp_path: Path) -> None:
    """Fails with the current load.py: an empty result is returned as a success."""
    pytest.skip("to write")


# --- verify_manifest_entry ---------------------------------------------------


def test_verify_manifest_entry_accepts_a_well_formed_entry() -> None:
    pytest.skip("to write")


def test_verify_manifest_entry_rejects_non_integer_rows() -> None:
    pytest.skip("to write")


# --- _millesime_from_dataset_id ----------------------------------------------


def test_millesime_is_the_date_at_the_end_of_the_dataset_id() -> None:
    """parc_immobilier_etat_20231231 -> datetime.date(2023, 12, 31), a date, not a string."""
    pytest.skip("to write")


def test_millesime_rejects_a_dataset_id_without_a_date() -> None:
    pytest.skip("to write")


# --- column contract ---------------------------------------------------------


def test_csv_columns_are_read_as_a_tuple_without_the_bom() -> None:
    """A list never equals a tuple, and a BOM would rename the first column."""
    pytest.skip("to write")


def test_verify_csv_columns_accepts_the_contract_header() -> None:
    pytest.skip("to write")


def test_verify_csv_columns_rejects_a_renamed_column() -> None:
    pytest.skip("to write")


def test_verify_csv_columns_rejects_columns_in_another_order() -> None:
    """COPY maps columns by position: same names in another order must be refused."""
    pytest.skip("to write")


def test_versioned_snapshot_headers_match_the_column_contract() -> None:
    """Contract test: reads the CSVs versioned in data/snapshots and compares to COLUMNS."""
    pytest.skip("to write")


# --- SQL composition ---------------------------------------------------------


def test_create_table_statement_lists_contract_columns_then_metadata() -> None:
    """Assert on .as_string(), which needs no connection with psycopg 3."""
    pytest.skip("to write")


def test_staging_table_is_temporary_unqualified_and_dropped_on_commit() -> None:
    pytest.skip("to write")


# --- load_snapshot: refusals before any connection ---------------------------
# Pass a conninfo that cannot connect (e.g. "host=invalid"): if a refusal came
# too late and the loader reached the database, the test would fail with a
# connection error instead of the expected exception.


def test_load_snapshot_raises_when_the_snapshot_file_is_missing(tmp_path: Path) -> None:
    pytest.skip("to write")


def test_load_snapshot_raises_when_the_digest_does_not_match(tmp_path: Path) -> None:
    pytest.skip("to write")


def test_load_snapshot_raises_when_columns_break_the_contract(tmp_path: Path) -> None:
    pytest.skip("to write")


def test_load_snapshot_raises_when_row_count_differs_from_manifest(tmp_path: Path) -> None:
    pytest.skip("to write")


# --- main --------------------------------------------------------------------


def test_main_returns_1_when_the_manifest_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails with the current load.py: prints nothing and returns 0.

    Set sys.argv with monkeypatch.setattr(sys, "argv", [...]).
    """
    pytest.skip("to write")


def test_main_returns_1_when_a_snapshot_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.skip("to write")


# =============================================================================
# B. Integration tests — disposable database on postgres-immo
# =============================================================================
# The `integration` marker is registered in pyproject.toml. These tests still
# run by default: add "-m", "not integration" to addopts to exclude them once
# they have bodies, since CI has no PostgreSQL.


@pytest.fixture(scope="session")
def test_db() -> Iterator[str]:
    """Create immo_test with a bronze schema, yield its conninfo, then drop it.

    Connect to immo in autocommit, CREATE DATABASE immo_test, connect to it and
    CREATE SCHEMA bronze (the init script does not run on this database),
    yield the conninfo, then DROP DATABASE immo_test WITH (FORCE).
    """
    pytest.skip("fixture to write")


@pytest.fixture
def empty_bronze(test_db: str) -> str:
    """Drop bronze.parc_immobilier before each test, so each one starts empty."""
    pytest.skip("fixture to write")


@pytest.mark.integration
def test_first_load_inserts_every_row(tmp_path: Path, empty_bronze: str) -> None:
    pytest.skip("to write")


@pytest.mark.integration
def test_reloading_the_same_file_inserts_nothing(tmp_path: Path, empty_bronze: str) -> None:
    """Second call returns 0 and the row count does not change."""
    pytest.skip("to write")


@pytest.mark.integration
def test_a_new_version_of_a_vintage_replaces_the_previous_one(
    tmp_path: Path, empty_bronze: str
) -> None:
    """Fails with the current load.py: without the DELETE, rows are appended.

    No second file needed: load once, overwrite _source_sha256 in the table,
    load again, and expect the vintage to still hold `rows` rows, not twice that.
    """
    pytest.skip("to write")


@pytest.mark.integration
def test_metadata_columns_record_vintage_file_and_digest(tmp_path: Path, empty_bronze: str) -> None:
    """millesime is a date, _source_file and _source_sha256 match the entry, _loaded_at is set."""
    pytest.skip("to write")


@pytest.mark.integration
def test_a_failed_load_leaves_the_table_unchanged(tmp_path: Path, empty_bronze: str) -> None:
    """Transactional guarantee: nothing partial survives an error.

    Load a valid snapshot, then a second vintage whose CSV has one data line
    with an extra field. Header and row count still pass the checks, so COPY
    itself fails inside the transaction: the first vintage must be intact and
    the second absent.
    """
    pytest.skip("to write")
