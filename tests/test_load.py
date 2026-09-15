"""Tests for the bronze loader.

Two families, run differently:

A. Offline tests — everything that happens before a database connection is
   opened: manifest reading and validation, the column contract, SQL
   composition, and the loader's own refusals. No Docker needed; these run by
   default, in CI, and carry the coverage threshold.

B. Integration tests — marked `integration`, excluded by default, run against a
   disposable database created on the postgres-immo container. They prove what
   only a real PostgreSQL can: idempotence, replacement of a vintage, and
   transactional rollback. Run them with:  pytest -m integration --no-cov

The test snapshots mimic the provider's file: UTF-8 BOM and CRLF line endings,
so the loader is exercised on the same byte-level quirks as the real data.
"""

import json
import os
import sys
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from immo_gov.load import (
    _build_create_table,
    _build_staging_table,
    _get_csv_columns,
    _millesime_from_dataset_id,
    _read_manifest,
    load_snapshot,
    main,
    verify_csv_columns,
    verify_manifest,
    verify_manifest_entry,
)
from immo_gov.snapshots import COLUMNS, compute_sha256

SNAPSHOTS = Path(__file__).resolve().parents[1] / "data" / "snapshots"
DATASET_2023 = "parc_immobilier_etat_20231231"
VINTAGE_2023 = date(2023, 12, 31)

# The refusal tests pass a conninfo that cannot connect. If a refusal came too
# late and the loader reached the database, the test would fail with a
# connection error instead of the expected exception.
UNREACHABLE = "host=invalid.invalid connect_timeout=1"

# Administrative connection used to create and drop the disposable database.
ADMIN_DSN = os.environ.get(
    "IMMO_PG_DSN", "host=localhost port=5432 dbname=immo user=immo_user password=immo_password"
)
TEST_DB = "immo_test"


# =============================================================================
# Helpers
# =============================================================================


def _csv_bytes(
    header: tuple[str, ...], rows: int, *, seed: str = "v", extra_field_on: int | None = None
) -> bytes:
    """Build a snapshot payload the way the provider serves it: BOM, `;`, CRLF.

    `seed` changes the cell values, hence the digest, without changing the shape.
    `extra_field_on` appends one unexpected field to that data row: the header
    and the record count stay valid, so only COPY itself can reject it.
    """
    lines = [";".join(header)]
    for r in range(rows):
        fields = [f"{seed}{r}_{i}" for i in range(len(header))]
        if r == extra_field_on:
            fields.append("unexpected")
        lines.append(";".join(fields))
    return b"\xef\xbb\xbf" + ("\r\n".join(lines) + "\r\n").encode("utf-8")


def _make_snapshot(
    directory: Path,
    rows: int = 3,
    dataset_id: str = DATASET_2023,
    *,
    header: tuple[str, ...] = COLUMNS,
    seed: str = "v",
    extra_field_on: int | None = None,
) -> dict:
    """Write a snapshot CSV into `directory` and return its manifest entry.

    The entry's sha256, columns and rows are computed from the bytes actually
    written, so every validation passes: each test then breaks exactly one thing.
    """
    payload = _csv_bytes(header, rows, seed=seed, extra_field_on=extra_field_on)
    filename = f"{dataset_id}.csv"
    (directory / filename).write_bytes(payload)
    return {
        "dataset_id": dataset_id,
        "file": filename,
        "source_url": f"https://example.invalid/{dataset_id}",
        "extracted_at": "2026-09-15T00:00:00+00:00",
        "sha256": compute_sha256(payload),
        "bytes": len(payload),
        "columns": len(header),
        "rows": rows,
        "source": {},
    }


def _write_manifest(directory: Path, entries: list[dict]) -> Path:
    """Write a manifest.json keyed by dataset_id into `directory`, return its path."""
    path = directory / "manifest.json"
    manifest = {"snapshots": {entry["dataset_id"]: entry for entry in entries}}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


# =============================================================================
# A. Offline tests
# =============================================================================

# --- _read_manifest ----------------------------------------------------------


def test_read_manifest_returns_the_parsed_json(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, [_make_snapshot(tmp_path)])
    assert _read_manifest(path) == json.loads(path.read_text(encoding="utf-8"))


def test_read_manifest_raises_when_the_file_is_missing(tmp_path: Path) -> None:
    """Without a manifest there is no digest nor row count to check against."""
    with pytest.raises(FileNotFoundError):
        _read_manifest(tmp_path / "absent.json")


# --- verify_manifest ---------------------------------------------------------


def test_verify_manifest_returns_every_entry_of_the_real_manifest() -> None:
    entries = verify_manifest(SNAPSHOTS / "manifest.json")
    assert sorted(entry["dataset_id"] for entry in entries) == [
        "parc_immobilier_etat_20221231",
        "parc_immobilier_etat_20231231",
    ]


def test_verify_manifest_rejects_an_entry_missing_a_required_key(tmp_path: Path) -> None:
    entry = _make_snapshot(tmp_path)
    del entry["sha256"]
    with pytest.raises(ValueError, match="malformed"):
        verify_manifest(_write_manifest(tmp_path, [entry]))


def test_verify_manifest_rejects_a_key_that_differs_from_its_dataset_id(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"snapshots": {"another_id": _make_snapshot(tmp_path)}}))
    with pytest.raises(ValueError, match="does not match"):
        verify_manifest(path)


def test_verify_manifest_rejects_a_manifest_without_snapshots(tmp_path: Path) -> None:
    """A KeyError, not a silent empty list that would load nothing and succeed."""
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"entries": []}))
    with pytest.raises(KeyError):
        verify_manifest(path)


def test_verify_manifest_rejects_a_manifest_with_no_entry(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"snapshots": {}}))
    with pytest.raises(ValueError, match="lists no snapshot"):
        verify_manifest(path)


# --- verify_manifest_entry ---------------------------------------------------


def test_verify_manifest_entry_accepts_a_well_formed_entry(tmp_path: Path) -> None:
    assert verify_manifest_entry(_make_snapshot(tmp_path)) is True


def test_verify_manifest_entry_rejects_non_integer_rows(tmp_path: Path) -> None:
    assert verify_manifest_entry(_make_snapshot(tmp_path) | {"rows": "3"}) is False


# --- _millesime_from_dataset_id ----------------------------------------------


def test_millesime_is_the_date_at_the_end_of_the_dataset_id() -> None:
    """A date, not the string "20231231" that PostgreSQL would merely tolerate."""
    assert _millesime_from_dataset_id(DATASET_2023) == VINTAGE_2023


@pytest.mark.parametrize(
    "dataset_id",
    ["parc_immobilier_etat", "20231231", "parc_immobilier_etat_20231331"],
    ids=["no-date", "no-prefix", "month-13"],
)
def test_millesime_rejects_a_dataset_id_without_a_valid_date(dataset_id: str) -> None:
    with pytest.raises(ValueError):
        _millesime_from_dataset_id(dataset_id)


# --- column contract ---------------------------------------------------------


def test_csv_columns_are_read_as_a_tuple_without_the_bom() -> None:
    """A list never equals a tuple, and a BOM would rename the first column."""
    header = _get_csv_columns(_csv_bytes(COLUMNS, rows=1))
    assert isinstance(header, tuple)
    assert header[0] == "code_chorus"


def test_verify_csv_columns_accepts_the_contract_header() -> None:
    assert verify_csv_columns(_csv_bytes(COLUMNS, rows=1)) is True


def test_verify_csv_columns_rejects_a_renamed_column() -> None:
    renamed = ("code_chorus_renamed", *COLUMNS[1:])
    assert verify_csv_columns(_csv_bytes(renamed, rows=1)) is False


def test_verify_csv_columns_rejects_columns_in_another_order() -> None:
    """COPY maps columns by position: same names in another order must be refused."""
    swapped = (COLUMNS[1], COLUMNS[0], *COLUMNS[2:])
    assert verify_csv_columns(_csv_bytes(swapped, rows=1)) is False


@pytest.mark.parametrize(
    "csv_name", ["parc_immobilier_etat_20221231.csv", "parc_immobilier_etat_20231231.csv"]
)
def test_versioned_snapshot_headers_match_the_column_contract(csv_name: str) -> None:
    """Contract test: fails if the constant or a future snapshot drifts from the other."""
    assert _get_csv_columns((SNAPSHOTS / csv_name).read_bytes()) == COLUMNS


# --- SQL composition ---------------------------------------------------------


def test_create_table_statement_lists_contract_columns_then_metadata() -> None:
    statement = _build_create_table("bronze", "parc_immobilier", COLUMNS).as_string()
    assert statement.startswith('CREATE TABLE IF NOT EXISTS "bronze"."parc_immobilier" (')
    first = statement.index('"code_chorus" TEXT')
    last = statement.index('"date_de_reference" TEXT')
    assert first < last < statement.index("millesime date")
    assert statement.count(" TEXT") == len(COLUMNS)
    assert "_loaded_at timestamptz DEFAULT now()" in statement


def test_staging_table_is_temporary_unqualified_and_dropped_on_commit() -> None:
    """A temporary table cannot live in the bronze schema, and must not outlive the load."""
    statement = _build_staging_table("staging", COLUMNS).as_string()
    assert statement.startswith('CREATE TEMP TABLE "staging" (')
    assert statement.endswith(") ON COMMIT DROP")
    assert statement.count(" TEXT") == len(COLUMNS)
    assert "millesime" not in statement


# --- load_snapshot: refusals before any connection ---------------------------


def test_load_snapshot_raises_when_the_snapshot_file_is_missing(tmp_path: Path) -> None:
    entry = _make_snapshot(tmp_path)
    (tmp_path / entry["file"]).unlink()
    with pytest.raises(FileNotFoundError):
        load_snapshot(entry, tmp_path, UNREACHABLE)


def test_load_snapshot_raises_when_the_digest_does_not_match(tmp_path: Path) -> None:
    entry = _make_snapshot(tmp_path) | {"sha256": "0" * 64}
    with pytest.raises(ValueError, match="digest"):
        load_snapshot(entry, tmp_path, UNREACHABLE)


def test_load_snapshot_raises_when_columns_break_the_contract(tmp_path: Path) -> None:
    entry = _make_snapshot(tmp_path, header=("renamed", *COLUMNS[1:]))
    with pytest.raises(ValueError, match="contract"):
        load_snapshot(entry, tmp_path, UNREACHABLE)


def test_load_snapshot_raises_when_row_count_differs_from_manifest(tmp_path: Path) -> None:
    entry = _make_snapshot(tmp_path, rows=3) | {"rows": 4}
    with pytest.raises(ValueError, match="manifest says"):
        load_snapshot(entry, tmp_path, UNREACHABLE)


# --- main --------------------------------------------------------------------


def test_main_returns_1_when_the_manifest_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An exit code of 0 here would let a pipeline carry on with nothing loaded."""
    argv = ["load", str(tmp_path / "absent.json"), str(tmp_path), UNREACHABLE]
    monkeypatch.setattr(sys, "argv", argv)
    assert main() == 1
    assert "Manifest not found" in capsys.readouterr().out


def test_main_returns_1_when_a_snapshot_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = _make_snapshot(tmp_path) | {"sha256": "0" * 64}
    manifest = _write_manifest(tmp_path, [entry])
    monkeypatch.setattr(sys, "argv", ["load", str(manifest), str(tmp_path), UNREACHABLE])
    assert main() == 1
    assert "digest" in capsys.readouterr().out


# =============================================================================
# B. Integration tests — disposable database on postgres-immo
# =============================================================================


@pytest.fixture(scope="session")
def test_db() -> Iterator[str]:
    """Create immo_test with a bronze schema, yield its conninfo, then drop it.

    The init script only runs on the immo database, so the bronze schema is
    created here. Skips, rather than errors, when the container is not running.
    """
    try:
        admin = psycopg.connect(ADMIN_DSN, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as e:
        pytest.skip(f"postgres-immo is not reachable: {e}")
    drop = sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(TEST_DB))
    with admin:
        admin.execute(drop)
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB)))

    test_dsn = make_conninfo(ADMIN_DSN, dbname=TEST_DB)
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA bronze")

    yield test_dsn

    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(drop)


@pytest.fixture
def empty_bronze(test_db: str) -> str:
    """Drop bronze.parc_immobilier before each test, so each one starts from nothing."""
    with psycopg.connect(test_db, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS bronze.parc_immobilier")
    return test_db


def _rows_per_vintage(conninfo: str) -> dict[date, tuple[int, set[str]]]:
    """Map each vintage to (row count, set of source digests) in bronze.parc_immobilier."""
    with psycopg.connect(conninfo) as conn:
        found = conn.execute(
            "SELECT millesime, count(*), array_agg(DISTINCT _source_sha256) "
            "FROM bronze.parc_immobilier GROUP BY millesime"
        ).fetchall()
    return {vintage: (count, set(digests)) for vintage, count, digests in found}


@pytest.mark.integration
def test_first_load_inserts_every_row(tmp_path: Path, empty_bronze: str) -> None:
    entry = _make_snapshot(tmp_path, rows=5)
    assert load_snapshot(entry, tmp_path, empty_bronze) == 5
    assert _rows_per_vintage(empty_bronze) == {VINTAGE_2023: (5, {entry["sha256"]})}


@pytest.mark.integration
def test_reloading_the_same_file_inserts_nothing(tmp_path: Path, empty_bronze: str) -> None:
    entry = _make_snapshot(tmp_path, rows=5)
    load_snapshot(entry, tmp_path, empty_bronze)
    assert load_snapshot(entry, tmp_path, empty_bronze) == 0
    assert _rows_per_vintage(empty_bronze) == {VINTAGE_2023: (5, {entry["sha256"]})}


@pytest.mark.integration
def test_a_new_version_of_a_vintage_replaces_the_previous_one(
    tmp_path: Path, empty_bronze: str
) -> None:
    """Five rows, then a new version of four: appending would leave nine."""
    v1, v2 = tmp_path / "v1", tmp_path / "v2"
    v1.mkdir()
    v2.mkdir()
    old = _make_snapshot(v1, rows=5, seed="old")
    new = _make_snapshot(v2, rows=4, seed="new")

    load_snapshot(old, v1, empty_bronze)
    assert load_snapshot(new, v2, empty_bronze) == 4
    assert _rows_per_vintage(empty_bronze) == {VINTAGE_2023: (4, {new["sha256"]})}


@pytest.mark.integration
def test_metadata_columns_record_vintage_file_and_digest(tmp_path: Path, empty_bronze: str) -> None:
    entry = _make_snapshot(tmp_path, rows=2)
    load_snapshot(entry, tmp_path, empty_bronze)
    with psycopg.connect(empty_bronze) as conn:
        found = conn.execute(
            "SELECT DISTINCT millesime, _source_file, _source_sha256, _loaded_at IS NOT NULL "
            "FROM bronze.parc_immobilier"
        ).fetchall()
    assert found == [(VINTAGE_2023, entry["file"], entry["sha256"], True)]


@pytest.mark.integration
def test_a_failed_load_leaves_the_table_unchanged(tmp_path: Path, empty_bronze: str) -> None:
    """The delete of the previous version and the failed insert are undone together.

    The broken file targets the same vintage as the loaded one, with one data
    row carrying an extra field: every check before the database passes, the
    loader deletes the existing vintage, then COPY fails. If the transaction did
    not hold, the vintage would be gone.
    """
    v1, v2 = tmp_path / "v1", tmp_path / "v2"
    v1.mkdir()
    v2.mkdir()
    good = _make_snapshot(v1, rows=5, seed="good")
    broken = _make_snapshot(v2, rows=5, seed="broken", extra_field_on=2)

    load_snapshot(good, v1, empty_bronze)
    with pytest.raises(psycopg.errors.BadCopyFileFormat):
        load_snapshot(broken, v2, empty_bronze)
    assert _rows_per_vintage(empty_bronze) == {VINTAGE_2023: (5, {good["sha256"]})}


@pytest.mark.integration
def test_main_loads_every_entry_of_the_manifest(
    tmp_path: Path,
    empty_bronze: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    entries = [
        _make_snapshot(tmp_path, rows=3, dataset_id="parc_immobilier_etat_20221231"),
        _make_snapshot(tmp_path, rows=4, dataset_id=DATASET_2023),
    ]
    manifest = _write_manifest(tmp_path, entries)
    monkeypatch.setattr(sys, "argv", ["load", str(manifest), str(tmp_path), empty_bronze])

    assert main() == 0
    counts = {vintage: count for vintage, (count, _) in _rows_per_vintage(empty_bronze).items()}
    assert counts == {date(2022, 12, 31): 3, VINTAGE_2023: 4}
    assert capsys.readouterr().out.count("rows inserted") == 2
