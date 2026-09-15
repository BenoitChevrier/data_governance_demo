"""Snapshot extraction of the French State real-estate portfolio open data.

The extraction is deliberately a one-off, manual step: it writes a dated
snapshot into the repository rather than being called at startup. A demo that
depends on a third-party API being up is a demo that fails at the reader's
machine, not at ours (see ADR-001).

Each snapshot is written alongside the SHA-256 of its bytes, so that anyone can
re-download the source and check that the versioned file was not altered, and
recorded in a shared manifest.json carrying its provenance: source URL,
extraction date, licence, publisher, and both our row count and the provider's.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests

from immo_gov.snapshots import (
    check_csv_content,
    compute_sha256,
    describe_csv,
)

# Provenance document, shared by every snapshot in the directory. One file
# rather than one per dataset: provenance is easier to audit when it is in a
# single place, and the reader has one thing to open.
MANIFEST_NAME = "manifest.json"


def make_URL(dataset_id: str, query_params: str | None = None) -> str:
    """Build the CSV export URL for a dataset."""
    BASE_URL = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/"
    url_query = "exports/csv"
    if query_params is None:
        query_params = "?delimiter=%3B"
    return BASE_URL + dataset_id + url_query + query_params


def make_metadata_url(dataset_id: str) -> str:
    """Build the dataset metadata URL: licence, publisher, row count, modified date."""
    BASE_URL = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/"
    return BASE_URL + dataset_id.rstrip("/")


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


def write_hash(path: Path, sha256_hash: str) -> None:
    """Write the digest to its sidecar .sha256 file."""
    # newline="\n" pins LF on every platform. Without it, Windows text mode
    # writes CRLF, and since .sha256 files are stored byte for byte (-text),
    # re-running the extraction on another OS would show a spurious change.
    with open(path, "w", encoding="utf-8", newline="\n") as f:
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


def build_manifest_entry(
    dataset_id: str,
    filename: str,
    csv_bytes: bytes,
    source_metadata: dict | None = None,
    extracted_at: str | None = None,
) -> dict:
    """Describe one snapshot: what it is, where it came from, and how to verify it.

    `extracted_at` is a parameter rather than a call to the clock inside the
    function. A function that reads the current time cannot be asserted against
    an expected value; one that receives it can.
    """
    columns, rows = describe_csv(csv_bytes)
    return {
        "dataset_id": dataset_id,
        "file": filename,
        "source_url": make_URL(_normalize_dataset_id(dataset_id)),
        "extracted_at": extracted_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "sha256": compute_sha256(csv_bytes),
        "bytes": len(csv_bytes),
        "columns": columns,
        # Counted by us. The provider's own figure is kept under "source" so
        # that a divergence between the two is visible rather than resolved.
        "rows": rows,
        "source": source_metadata or {},
    }


def update_manifest(manifest_path: Path, entry: dict) -> None:
    """Insert or replace one entry in the shared manifest, keyed by dataset id."""
    manifest: dict = {"snapshots": {}}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError as e:
            print("Warning: the existing manifest is unreadable, rewriting it:", e)
    manifest.setdefault("snapshots", {})[entry["dataset_id"]] = entry
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def fetch_dataset_metadata(dataset_id: str) -> dict:
    """Fetch what the provider says about the dataset: licence, publisher, counts.

    Degrades to an empty dict rather than failing the extraction. Provenance is
    worth recording, but a snapshot whose bytes are hashed and verifiable is
    still useful without the provider's own description of it.
    """
    try:
        response = requests.get(make_metadata_url(dataset_id), timeout=30)
        response.raise_for_status()
        metas = response.json().get("metas", {}).get("default", {})
    except (requests.exceptions.RequestException, ValueError) as e:
        print("Warning: could not read the dataset metadata:", e)
        return {}
    return {
        "title": metas.get("title"),
        "publisher": metas.get("publisher"),
        "licence": metas.get("license"),
        "records_count": metas.get("records_count"),
        "modified": metas.get("modified"),
    }


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
        entry = build_manifest_entry(
            dataset_id=dataset_id,
            filename=csv_path.name,
            csv_bytes=csv_bytes,
            source_metadata=fetch_dataset_metadata(dataset_id),
        )
        update_manifest(dir_path / MANIFEST_NAME, entry)
        print(f"Manifest updated: {entry['rows']} rows, {entry['columns']} columns.")
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
