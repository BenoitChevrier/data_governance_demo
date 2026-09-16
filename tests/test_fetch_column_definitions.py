"""Tests for the column definitions fetcher.

No test here touches the network. The two HTTP calls are replaced by doubles
built from a real response, captured on 2026-09-16: 28 fields whose first label
carries a stray BOM, and a single PDF attachment. A test suite that needed the
provider to be up would fail on the reader's machine for reasons that have
nothing to do with the code.

Three behaviours carry the value of this script and are tested hardest:

A. it refuses to refresh when the published fields no longer match the column
   contract, because a source that gains or loses a column breaks the loader
   before it breaks the documentation;
B. it never overwrites what a human wrote — the transcriptions, the English
   descriptions, the tags — whatever the API returns;
C. it notices when the data dictionary itself changed, which is the only signal
   that the transcriptions need re-reading.
"""

import json

import fetch_column_definitions as fetch
import pytest

from immo_gov.snapshots import COLUMNS

ATTACHMENT = {
    "id": "descriptif_donnees_parc_immobilier_etat_20231231_pdf",
    "title": "Descriptif_Données_Parc_Immobilier_Etat_20231231.pdf",
    "mimetype": "application/pdf",
    "url": "https://example.invalid/attachments/descriptif_pdf",
}

PDF_BYTES = b"%PDF-1.7 fake dictionary"


def catalog(fields=None, attachments=None):
    """Build a catalog response shaped like the provider's."""
    if fields is None:
        fields = [
            {
                "name": name,
                # The provider's first label really does carry a BOM.
                "label": ("﻿Code Chorus" if name == "code_chorus" else name.title()),
                "type": "text",
                "description": None,
            }
            for name in COLUMNS
        ]
    return {
        "fields": fields,
        "attachments": [ATTACHMENT] if attachments is None else attachments,
        "metas": {
            "default": {
                "title": "Parc de l'immobilier de l'Etat au 31/12/2023",
                "publisher": "DGFIP",
                "license": "Licence Ouverte v2.0 (Etalab)",
                "modified": "2024-12-20T10:33:56.463000+00:00",
            }
        },
    }


class FakeResponse:
    def __init__(self, content=b"", payload=None):
        self.content = content
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.fixture
def no_network(monkeypatch):
    """Serve the PDF bytes to any GET, and fail loudly on an unexpected call."""
    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        return FakeResponse(content=PDF_BYTES)

    monkeypatch.setattr(fetch.requests, "get", fake_get)
    return calls


# --- the field list -----------------------------------------------------------


def test_fields_are_read_in_the_provider_order_with_the_bom_stripped():
    fields = fetch.extract_fields(catalog())

    assert [field["name"] for field in fields] == list(COLUMNS)
    assert fields[0]["label_fr"] == "Code Chorus"
    assert "﻿" not in fields[0]["label_fr"]


def test_an_empty_field_list_is_refused():
    """An HTTP 200 carrying no fields must not silently empty the TSV."""
    with pytest.raises(ValueError, match="no field list"):
        fetch.extract_fields(catalog(fields=[]))


def test_contract_match_passes():
    fetch.check_contract(fetch.extract_fields(catalog()))


def test_a_new_column_at_the_source_stops_the_refresh():
    fields = [
        *fetch.extract_fields(catalog()),
        {"name": "surface_utile", "label_fr": "", "type": ""},
    ]

    with pytest.raises(ValueError, match="added at the source: surface_utile"):
        fetch.check_contract(fields)


def test_a_withdrawn_column_stops_the_refresh():
    fields = [f for f in fetch.extract_fields(catalog()) if f["name"] != "erp"]

    with pytest.raises(ValueError, match="no longer published: erp"):
        fetch.check_contract(fields)


def test_a_reordered_field_list_stops_the_refresh():
    """Order matters: the loader COPYs positionally."""
    fields = fetch.extract_fields(catalog())
    fields[0], fields[1] = fields[1], fields[0]

    with pytest.raises(ValueError, match="same names, different order"):
        fetch.check_contract(fields)


# --- the dictionary attachment ------------------------------------------------


def test_the_single_pdf_attachment_is_selected():
    assert fetch.select_dictionary(catalog())["id"] == ATTACHMENT["id"]


def test_no_pdf_attachment_is_an_error():
    with pytest.raises(ValueError, match="no PDF attachment"):
        fetch.select_dictionary(catalog(attachments=[]))


def test_several_pdf_attachments_are_not_guessed_between():
    """Picking the first one silently would hash the wrong document."""
    second = {**ATTACHMENT, "id": "autre_pdf", "title": "Autre.pdf"}

    with pytest.raises(ValueError, match="several PDF attachments"):
        fetch.select_dictionary(catalog(attachments=[ATTACHMENT, second]))


def test_the_dictionary_is_written_hashed_and_recorded(tmp_path, no_network):
    path, digest, changed = fetch.snapshot_dictionary(ATTACHMENT, tmp_path, {})

    assert path.name == "descriptif_donnees_parc_immobilier_etat_20231231.pdf"
    assert path.read_bytes() == PDF_BYTES
    assert path.with_suffix(".sha256").read_text(encoding="utf-8").strip() == digest
    assert changed is False

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["snapshots"][ATTACHMENT["id"]]
    assert entry["sha256"] == digest
    assert entry["bytes"] == len(PDF_BYTES)
    assert entry["source_url"] == ATTACHMENT["url"]


def test_the_sidecar_is_written_with_lf(tmp_path, no_network):
    """Sidecars are stored byte for byte; a CRLF would look like a change."""
    path, _, _ = fetch.snapshot_dictionary(ATTACHMENT, tmp_path, {})

    assert b"\r\n" not in path.with_suffix(".sha256").read_bytes()


def test_a_changed_dictionary_is_reported(tmp_path, no_network):
    """The one signal that the transcriptions are stale."""
    sidecar = tmp_path / "descriptif_donnees_parc_immobilier_etat_20231231.sha256"
    sidecar.write_text("0" * 64 + "\n", encoding="utf-8")

    _, _, changed = fetch.snapshot_dictionary(ATTACHMENT, tmp_path, {})

    assert changed is True


def test_an_unchanged_dictionary_is_not_reported(tmp_path, no_network):
    fetch.snapshot_dictionary(ATTACHMENT, tmp_path, {})

    _, _, changed = fetch.snapshot_dictionary(ATTACHMENT, tmp_path, {})

    assert changed is False


def test_dry_run_writes_nothing(tmp_path, no_network):
    fetch.snapshot_dictionary(ATTACHMENT, tmp_path, {}, dry_run=True)

    assert list(tmp_path.iterdir()) == []


# --- the merge, which must never lose human work ------------------------------


def test_human_columns_survive_a_refresh(tmp_path):
    """B: the whole point of merging rather than rewriting."""
    existing = {
        "ministere": {
            "name": "ministere",
            "label_fr": "stale label",
            "type": "stale type",
            "definition_fr": "Ministère occupant le bien",
            "source_system": "Chorus RE-Fx",
            "description_en": "Ministry occupying the asset",
            "tags": "organisation;public",
        }
    }
    fields = fetch.extract_fields(catalog())

    rows = fetch.merge_rows(fields, existing)

    ministere = next(row for row in rows if row["name"] == "ministere")
    assert ministere["definition_fr"] == "Ministère occupant le bien"
    assert ministere["description_en"] == "Ministry occupying the asset"
    assert ministere["tags"] == "organisation;public"
    # Fetched columns are refreshed from the API, not kept from the file.
    assert ministere["label_fr"] == "Ministere"
    assert ministere["type"] == "text"


def test_the_metadata_columns_are_appended(tmp_path):
    """The loader's own columns are not published by the provider."""
    rows = fetch.merge_rows(fetch.extract_fields(catalog()), {})

    names = [row["name"] for row in rows]
    assert names[: len(COLUMNS)] == list(COLUMNS)
    assert names[len(COLUMNS) :] == ["millesime", "_source_file", "_source_sha256", "_loaded_at"]
    assert rows[-1]["source_system"] == "immo_gov loader"


def test_every_row_carries_the_full_header(tmp_path):
    rows = fetch.merge_rows(fetch.extract_fields(catalog()), {})

    assert all(set(row) == set(fetch.TSV_HEADER) for row in rows)


def test_an_existing_file_is_read_ignoring_comments(tmp_path):
    path = tmp_path / "defs.tsv"
    path.write_text(
        "name\tdescription_en\n# a note\terp\nerp\tPublic venue\n",
        encoding="utf-8-sig",
        newline="\n",
    )

    existing = fetch.read_existing(path)

    assert set(existing) == {"erp"}
    assert existing["erp"]["description_en"] == "Public venue"


def test_a_missing_file_is_not_an_error(tmp_path):
    assert fetch.read_existing(tmp_path / "absent.tsv") == {}


def test_the_tsv_is_written_with_the_declared_header_lf_and_no_bom(tmp_path):
    path = tmp_path / "defs.tsv"

    fetch.write_tsv(path, fetch.merge_rows(fetch.extract_fields(catalog()), {}))

    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "\t".join(fetch.TSV_HEADER)
    assert len(lines) == len(COLUMNS) + len(fetch.METADATA_ROWS) + 1


# --- the command ---------------------------------------------------------------


def test_the_run_reports_what_is_left_to_write(tmp_path, monkeypatch, capsys, no_network):
    monkeypatch.setattr(fetch, "fetch_catalog", lambda dataset_id: catalog())
    output = tmp_path / "defs.tsv"

    code = fetch.main(["--output", str(output), "--snapshot-dir", str(tmp_path / "snapshots")])

    assert code == 0
    out = capsys.readouterr().out
    assert "32 still without description_en" in out
    assert output.exists()


def test_a_contract_drift_fails_the_command_before_writing(tmp_path, monkeypatch, capsys):
    broken = catalog(fields=[{"name": "id", "label": "ID", "type": "text"}])
    monkeypatch.setattr(fetch, "fetch_catalog", lambda dataset_id: broken)
    output = tmp_path / "defs.tsv"

    code = fetch.main(["--output", str(output), "--snapshot-dir", str(tmp_path / "snapshots")])

    assert code == 1
    assert "no longer match" in capsys.readouterr().out
    assert not output.exists()


def test_a_network_failure_is_reported_not_raised(tmp_path, monkeypatch, capsys):
    def explode(dataset_id):
        raise fetch.requests.exceptions.ConnectionError("name resolution failed")

    monkeypatch.setattr(fetch, "fetch_catalog", explode)

    code = fetch.main(["--output", str(tmp_path / "defs.tsv")])

    assert code == 1
    assert "name resolution failed" in capsys.readouterr().out
