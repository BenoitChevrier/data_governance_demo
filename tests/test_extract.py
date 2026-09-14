"""Unit tests for the open-data extraction module.

These tests touch neither the network nor the filesystem: they cover the pure
functions only. That is deliberate — a test suite that needs the internet is a
test suite nobody runs, and a CI that depends on a third-party API is a CI that
goes red for reasons that have nothing to do with the code.

The network path is worth testing too, but as a separately marked integration
test, excluded from the default run.
"""

import hashlib
import json
from unittest.mock import patch

from immo_gov.extract import (
    _ensure_directory,
    _normalize_dataset_id,
    build_manifest_entry,
    check_csv_content,
    compute_sha256,
    describe_csv,
    extract_snapshot,
    make_URL,
    update_manifest,
    write_csv,
    write_hash,
)

# Column count of the CSV export, measured against the live endpoint on
# 2026-08-28. The catalog metadata advertises 29 fields, but point_geo is a
# computed geo field and is not exported, so the file carries 28.
EXPORTED_COLUMNS = 28


def _fake_csv(columns: int = EXPORTED_COLUMNS, rows: int = 0) -> bytes:
    """Build a semicolon-delimited CSV in memory: one header plus `rows` records.

    Building the payload rather than downloading one keeps these tests offline
    and instant, and lets each case isolate a single failure mode.
    """
    header = ";".join(f"col_{i}" for i in range(columns))
    body = "\n".join(";".join(f"v{i}_{r}" for i in range(columns)) for r in range(rows))
    text = header if rows == 0 else f"{header}\n{body}"
    return (text + "\n").encode("utf-8")


def test_normalize_dataset_id_adds_a_trailing_slash() -> None:
    assert _normalize_dataset_id("parc_immobilier_etat_20231231") == (
        "parc_immobilier_etat_20231231/"
    )


def test_normalize_dataset_id_is_idempotent() -> None:
    """Normalising twice must not add a second slash.

    Idempotence matters here because the caller cannot always know whether an
    id has already been normalised. A function that is safe to apply twice
    removes a whole class of bugs.
    """
    once = _normalize_dataset_id("parc_immobilier_etat_20231231")
    assert _normalize_dataset_id(once) == once


def test_make_url_builds_the_expected_endpoint() -> None:
    """The URL is asserted in full, on purpose.

    Asserting only that the result "contains the dataset id" would still pass
    on a malformed URL. The exact string is the contract with the data
    provider, so it is worth writing out.
    """
    url = make_URL("parc_immobilier_etat_20231231/")
    assert url == (
        "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/"
        "parc_immobilier_etat_20231231/exports/csv?delimiter=%3B"
    )


def test_compute_sha256_matches_the_reference_implementation() -> None:
    """Checked against hashlib rather than a hardcoded digest.

    A hardcoded digest would prove the function returns one particular string;
    comparing against the standard library proves it computes SHA-256 of the
    bytes it was given, which is what the snapshot manifest actually claims.
    """
    payload = b"code_chorus;id;designation_site\n"
    assert compute_sha256(payload) == hashlib.sha256(payload).hexdigest()


# --- check_csv_content -------------------------------------------------------
# This is the data contract: what the extraction refuses to accept as a valid
# snapshot. Each case isolates one reason to reject.


def test_empty_payload_is_rejected() -> None:
    assert check_csv_content(b"") is False


def test_a_header_with_too_few_columns_is_rejected() -> None:
    """A truncated or restructured export must not pass as valid."""
    assert check_csv_content(_fake_csv(columns=3, rows=5000)) is False


def test_a_file_with_too_few_rows_is_rejected() -> None:
    """Guards against an HTTP 200 carrying a truncated body."""
    assert check_csv_content(_fake_csv(rows=10)) is False


def test_a_well_formed_export_is_accepted() -> None:
    assert check_csv_content(_fake_csv(rows=2_001)) is True


def test_the_row_threshold_is_exclusive() -> None:
    """Pins the boundary, because `<=` and `<` are one keystroke apart.

    The production check reads `if rows_count <= 2000`, so exactly 2000 data
    rows are refused and 2001 are accepted. Writing the boundary down means a
    later edit to that operator fails a test instead of passing unnoticed.
    """
    assert check_csv_content(_fake_csv(rows=2_000)) is False
    assert check_csv_content(_fake_csv(rows=2_001)) is True


def test_write_csv(tmp_path):
    csv_path = tmp_path / "test.csv"
    write_csv(csv_path, _fake_csv(columns=3, rows=5000))
    assert csv_path.exists()
    assert csv_path.read_bytes() == _fake_csv(columns=3, rows=5000)


def test_write_hash_file(tmp_path):
    csv_path = tmp_path / "test.csv"
    hash_path = tmp_path / "test.sha256"
    write_csv(csv_path, _fake_csv(columns=3, rows=5000))
    expected_hash = compute_sha256(_fake_csv(columns=3, rows=5000))
    write_hash(hash_path, expected_hash)
    assert hash_path.exists()
    assert hash_path.read_text(encoding="utf-8").strip() == expected_hash


def test_ensure_directory(tmp_path):
    # Test with an existing directory
    dir_path = tmp_path / "existing_dir"
    dir_path.mkdir()
    assert _ensure_directory(dir_path) == dir_path.resolve()
    # Test with a non-existing directory
    non_existing_dir = tmp_path / "non_existing_dir"
    assert _ensure_directory(non_existing_dir) is None
    # Test with a file instead of a directory
    file_path = tmp_path / "file.txt"
    file_path.write_text("This is a test file.")
    assert _ensure_directory(file_path) is None


def test_extract_snapshot_success(tmp_path):
    """End to end, offline: only the two network calls are replaced.

    Validation, hashing, both writes and the manifest all run for real against
    tmp_path. Mocking them as well would assert that extract_snapshot calls the
    right functions while proving that none of them does the right thing.
    """
    dataset_id = "parc/immobilier"
    csv_bytes = _fake_csv(rows=2_001)

    with (
        patch("immo_gov.extract.get_csv_from_api", return_value=csv_bytes) as mock_get,
        patch(
            "immo_gov.extract.fetch_dataset_metadata",
            return_value={"licence": "Open Licence 2.0"},
        ),
    ):
        result = extract_snapshot(dataset_id, tmp_path)

    assert result == 0
    mock_get.assert_called_once_with(dataset_id)

    # The slash in the dataset id must not create a subdirectory.
    csv_path = tmp_path / "parc_immobilier.csv"
    hash_path = tmp_path / "parc_immobilier.sha256"
    manifest_path = tmp_path / "manifest.json"
    assert csv_path.read_bytes() == csv_bytes
    assert hash_path.read_text(encoding="utf-8").strip() == hashlib.sha256(csv_bytes).hexdigest()

    entry = json.loads(manifest_path.read_text(encoding="utf-8"))["snapshots"][dataset_id]
    assert entry["file"] == "parc_immobilier.csv"
    assert entry["sha256"] == hashlib.sha256(csv_bytes).hexdigest()
    assert entry["rows"] == 2_001
    assert entry["source"]["licence"] == "Open Licence 2.0"


def test_extract_snapshot_invalid_csv(tmp_path):
    dataset_id = "parc"
    csv_bytes = b"bad;csv"
    with (
        patch("immo_gov.extract.get_csv_from_api", return_value=csv_bytes),
        patch("immo_gov.extract.check_csv_content", return_value=False),
    ):
        result = extract_snapshot(dataset_id, tmp_path)
        assert result == 1


def test_extract_snapshot_no_csv(tmp_path):
    dataset_id = "parc"
    with patch("immo_gov.extract.get_csv_from_api", return_value=None):
        result = extract_snapshot(dataset_id, tmp_path)
        assert result == 1


# --- provenance manifest -----------------------------------------------------
# The digest proves a file was not altered. The manifest says what the file is,
# where it came from and when — which is what makes the digest worth anything.


def test_describe_csv_counts_columns_and_data_rows() -> None:
    assert describe_csv(_fake_csv(columns=5, rows=42)) == (5, 42)


def test_describe_csv_strips_the_byte_order_mark() -> None:
    """The provider prefixes its export with a UTF-8 BOM.

    Decoded as plain utf-8 the first column would be named "﻿col_0", and
    every lookup by name on it would silently miss.
    """
    payload = b"\xef\xbb\xbf" + _fake_csv(columns=3, rows=1)
    columns, rows = describe_csv(payload)
    assert (columns, rows) == (3, 1)
    header = payload.decode("utf-8-sig").splitlines()[0]
    assert header.split(";")[0] == "col_0"


def test_manifest_entry_carries_what_a_third_party_needs_to_verify() -> None:
    payload = _fake_csv(columns=EXPORTED_COLUMNS, rows=17)
    entry = build_manifest_entry(
        dataset_id="parc_immobilier_etat_20231231",
        filename="parc_immobilier_etat_20231231.csv",
        csv_bytes=payload,
        source_metadata={"licence": "Open Licence 2.0", "records_count": 17_007},
        extracted_at="2026-08-29T09:00:00+00:00",
    )
    assert entry["sha256"] == hashlib.sha256(payload).hexdigest()
    assert entry["bytes"] == len(payload)
    assert entry["columns"] == EXPORTED_COLUMNS
    assert entry["rows"] == 17
    assert entry["extracted_at"] == "2026-08-29T09:00:00+00:00"
    assert entry["source_url"].endswith("parc_immobilier_etat_20231231/exports/csv?delimiter=%3B")
    assert entry["source"]["licence"] == "Open Licence 2.0"


def test_manifest_entry_keeps_both_row_counts_apart() -> None:
    """Ours and the provider's are recorded separately, never reconciled.

    A divergence between the two is a governance signal — a scope change at the
    source, or a truncated download. Collapsing them into one number would hide
    exactly the thing worth seeing.
    """
    entry = build_manifest_entry(
        dataset_id="d",
        filename="d.csv",
        csv_bytes=_fake_csv(rows=10),
        source_metadata={"records_count": 17_007},
        extracted_at="2026-08-29T09:00:00+00:00",
    )
    assert entry["rows"] == 10
    assert entry["source"]["records_count"] == 17_007


def test_update_manifest_creates_the_file_when_absent(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    update_manifest(manifest_path, {"dataset_id": "a", "sha256": "aaa"})
    written = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert written["snapshots"]["a"]["sha256"] == "aaa"


def test_update_manifest_adds_a_second_entry_without_losing_the_first(tmp_path) -> None:
    """Each vintage is extracted by its own command, so the manifest accumulates."""
    manifest_path = tmp_path / "manifest.json"
    update_manifest(manifest_path, {"dataset_id": "a", "sha256": "aaa"})
    update_manifest(manifest_path, {"dataset_id": "b", "sha256": "bbb"})
    written = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(written["snapshots"]) == {"a", "b"}


def test_update_manifest_replaces_an_entry_on_re_extraction(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    update_manifest(manifest_path, {"dataset_id": "a", "sha256": "old"})
    update_manifest(manifest_path, {"dataset_id": "a", "sha256": "new"})
    written = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert written["snapshots"]["a"]["sha256"] == "new"
    assert len(written["snapshots"]) == 1


def test_update_manifest_survives_a_corrupted_file(tmp_path) -> None:
    """A half-written manifest must not block the next extraction."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{ this is not json", encoding="utf-8")
    update_manifest(manifest_path, {"dataset_id": "a", "sha256": "aaa"})
    written = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert written["snapshots"]["a"]["sha256"] == "aaa"


# --- cross-OS line endings ---------------------------------------------------
# Windows text mode writes CRLF, macOS and Linux write LF. The .sha256 sidecars
# are stored byte for byte, so without pinned line endings the same extraction
# would produce a different file depending on who ran it.


def test_hash_sidecar_is_written_with_lf_on_every_platform(tmp_path) -> None:
    hash_path = tmp_path / "x.sha256"
    write_hash(hash_path, "abc")
    assert hash_path.read_bytes() == b"abc\n"


def test_manifest_is_written_with_lf_on_every_platform(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    update_manifest(manifest_path, {"dataset_id": "a", "sha256": "aaa"})
    assert b"\r" not in manifest_path.read_bytes()
