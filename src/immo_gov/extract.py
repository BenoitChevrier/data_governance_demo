"""Snapshot extraction of the French State real-estate portfolio open data.

The extraction is deliberately a one-off, manual step: it writes a dated
snapshot into the repository rather than being called at startup. A demo that
depends on a third-party API being up is a demo that fails at the reader's
machine, not at ours (see ADR-001).

Each snapshot is written alongside the SHA-256 of its bytes, so that anyone can
re-download the source and check that the versioned file was not altered.
"""

import argparse
import csv
import hashlib
import sys
from io import StringIO
from pathlib import Path

import requests

# Number of columns in the CSV export, measured against the live endpoint on
# 2026-08-28. The catalog advertises 29 fields, but point_geo is a computed geo
# field that is not exported, so the file carries 28.
EXPECTED_COLUMNS = 28

# The published vintages hold roughly 17,000 rows each. The floor is set well
# below that to tolerate a change of scope at the source, but high enough to
# catch a truncated body served with HTTP 200.
MIN_DATA_ROWS = 2_000


def make_URL(dataset_id: str, query_params: str | None = None) -> str:
    """Build the CSV export URL for a dataset."""
    BASE_URL = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/"
    url_query = "exports/csv"
    if query_params is None:
        query_params = "?delimiter=%3B"
    return BASE_URL + dataset_id + url_query + query_params


def _normalize_dataset_id(dataset_id: str) -> str:
    """Append the trailing slash the URL builder expects, if it is missing."""
    if not dataset_id.endswith("/"):
        dataset_id = dataset_id + "/"
    return dataset_id


def _get_project_root() -> Path:
    """Walk up from this file until a project marker is found."""
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
            return parent
    return here.parent  # fallback


ROOT = _get_project_root()


def build_response(dataset_id: str) -> requests.Response | None:
    """Fetch the CSV export, returning None on any network or HTTP failure."""
    dataset_id = _normalize_dataset_id(dataset_id)
    url = make_URL(dataset_id)
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response
    except requests.exceptions.HTTPError as e:
        print("HTTP error:", e)
    except requests.exceptions.Timeout:
        print("Error: the request timed out.")
    except requests.exceptions.ConnectionError:
        print("Error: could not connect to the server.")
    except requests.exceptions.InvalidURL:
        print("Error: invalid URL:", url)
    except requests.exceptions.RequestException as e:
        # Catches every other requests-level failure.
        print("Request error:", e)
    return None


def get_csv_from_api(dataset_id: str) -> bytes | None:
    """Return the CSV body as bytes.

    The bytes are taken from `response.content`, which requests has already
    decompressed: the server answers with Content-Encoding: gzip on some calls
    and plain text on others. Hashing the decoded content rather than the wire
    bytes is what keeps the digest stable across those two cases.
    """
    response = build_response(dataset_id)
    if response is not None:
        csv_text = response.content
        return csv_text
    else:
        print("Could not retrieve the CSV export.")
        return None


def write_csv(path: Path, data: bytes) -> None:
    """Write the CSV bytes to disk, unmodified."""
    with open(path, "wb") as f:
        f.write(data)


def compute_sha256(data: bytes) -> str:
    """Return the SHA-256 digest of the given bytes."""
    return hashlib.sha256(data).hexdigest()


def write_hash(path: Path, sha256_hash: str) -> None:
    """Write the digest to its sidecar .sha256 file."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(sha256_hash + "\n")


def _ensure_directory(path: str | Path) -> Path | None:
    """Return the resolved path if it is an existing directory, else None."""
    p = Path(path).resolve()
    try:
        if not p.exists():
            raise FileNotFoundError(f"Directory does not exist: {p}")
        if not p.is_dir():
            raise NotADirectoryError(f"Not a directory: {p}")
        return p
    except FileNotFoundError as e:
        print("[FileNotFoundError]", e)
        return None
    except NotADirectoryError as e:
        print("[NotADirectoryError]", e)
        return None


def check_csv_content(csv_bytes: bytes) -> bool:
    """Reject a payload that cannot be a valid snapshot of this dataset.

    This is the data contract with the provider: it does not check that our
    code is correct, it checks that the source has not changed shape under us.
    """
    if not csv_bytes:
        print("The CSV is empty.")
        return False
    try:
        f = StringIO(csv_bytes.decode("utf-8"))
        reader = csv.reader(f, delimiter=";")
        first_row = next(reader)
        columns_count = len(first_row)
        rows_count = sum(1 for line in f)
        if columns_count < EXPECTED_COLUMNS:
            print(f"The CSV has {columns_count} columns, expected at least {EXPECTED_COLUMNS}.")
            return False
        if rows_count <= MIN_DATA_ROWS:
            print(f"The CSV has {rows_count} data rows, expected more than {MIN_DATA_ROWS}.")
            return False
        return True
    except Exception as e:
        print("Error while reading the CSV:", e)
        return False


def extract_snapshot(dataset_id: str, dir_path: Path) -> int:
    """Download, validate and write one snapshot. Returns a process exit code."""
    filename = dataset_id.replace("/", "_")
    csv_path = dir_path / f"{filename}.csv"
    hash_path = dir_path / f"{filename}.sha256"
    csv_bytes = get_csv_from_api(dataset_id)
    if csv_bytes:
        if not check_csv_content(csv_bytes):
            print("The retrieved CSV is not a valid snapshot.")
            return 1
        hash_value = compute_sha256(csv_bytes)
        print("SHA-256:", hash_value)
        write_csv(csv_path, csv_bytes)
        write_hash(hash_path, hash_value)
    else:
        print("No CSV retrieved.")
        return 1
    return 0


def main() -> int:
    """Parse the command line and run one extraction."""
    parser = argparse.ArgumentParser(
        description="Download a dataset CSV export and write it with its SHA-256."
    )
    parser.add_argument("--dataset-id", required=True, help="Dataset id to fetch")
    parser.add_argument(
        "--dir",
        default=ROOT / "data/snapshots",
        help="Output directory (absolute, or relative to the current directory)",
    )

    args = parser.parse_args()
    dir_path = _ensure_directory(args.dir)
    if dir_path is None:
        # Failing here is deliberate. Silently writing to a fallback directory
        # would put a snapshot somewhere the caller did not ask for, and the
        # caller would have no way to notice.
        print("Error: the output directory does not exist or is not a directory.")
        return 1

    return extract_snapshot(args.dataset_id, dir_path)


if __name__ == "__main__":
    sys.exit(main())
