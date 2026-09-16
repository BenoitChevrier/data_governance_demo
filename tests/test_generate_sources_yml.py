"""Tests for the sources.yml generator.

The point of the generator is that nobody has to read the YAML it produces to
trust it. That only holds if the tests check two different things:

A. the refusals — a column missing from the TSV, a column that is not in the
   contract, a tag outside the vocabulary, a description left blank. Each one
   must fail the run and name what is wrong. A generator that writes a half
   documented file on a malformed input is worse than no generator.

B. the output, parsed as YAML rather than compared as text. Asserting on
   strings would prove the renderer is stable, not that it is correct; parsing
   proves the indentation, the folded scalars and the accented characters
   survive a round trip through a YAML reader — which is what dbt does.
"""

import generate_sources_yml as gen
import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML comes with the dbt extra")

HEADER = ("name", "label_fr", "type", "definition_fr", "source_system", "description_en", "tags")


def tsv_line(**values: str) -> str:
    """Build one TSV row from keyword arguments, blanks for the rest."""
    return "\t".join(values.get(column, "") for column in HEADER)


def write_tsv(path, rows, header=HEADER, encoding="utf-8"):
    """Write a TSV file the generator can read."""
    lines = ["\t".join(header), *rows]
    path.write_text("\n".join(lines) + "\n", encoding=encoding, newline="\n")
    return path


def full_rows(**overrides: str) -> list[str]:
    """One row per contract column, described, with per-column overrides.

    Most tests need a TSV that passes the coverage check so that they can
    exercise something else; building it from the contract itself keeps them
    from drifting when a column is added.
    """
    rows = []
    for name in gen.EXPECTED_COLUMNS:
        values = {"name": name, "description_en": f"Definition of {name}"}
        if name in overrides:
            values.update(overrides[name])
        rows.append(tsv_line(**values))
    return rows


def render(tmp_path, rows, argv=()):
    """Run the generator end to end and return the parsed YAML and its file."""
    definitions = write_tsv(tmp_path / "column_definitions.tsv", rows)
    output = tmp_path / "sources.yml"
    assert gen.main(["--definitions", str(definitions), "--output", str(output), *argv]) == 0
    return yaml.safe_load(output.read_text(encoding="utf-8")), output


# --- what the generator refuses -----------------------------------------------


def test_missing_column_is_named(tmp_path, capsys):
    """A column of the contract absent from the TSV fails the run."""
    rows = [row for row in full_rows() if not row.startswith("erp\t")]
    definitions = write_tsv(tmp_path / "defs.tsv", rows)

    code = gen.main(["--definitions", str(definitions), "--output", str(tmp_path / "out.yml")])

    assert code == 1
    message = capsys.readouterr().out
    assert "erp" in message
    assert "missing (1)" in message
    assert not (tmp_path / "out.yml").exists()


def test_column_outside_the_contract_is_refused(tmp_path, capsys):
    """A column the loader does not carry has no place in the documentation."""
    rows = [*full_rows(), tsv_line(name="surface_utile", description_en="Invented column")]
    definitions = write_tsv(tmp_path / "defs.tsv", rows)

    assert gen.main(["--definitions", str(definitions), "--output", str(tmp_path / "out.yml")]) == 1
    assert "not in the contract: surface_utile" in capsys.readouterr().out


def test_duplicate_column_is_refused(tmp_path, capsys):
    """Two rows for one column would silently keep only one description."""
    rows = [*full_rows(), tsv_line(name="erp", description_en="Second definition")]
    definitions = write_tsv(tmp_path / "defs.tsv", rows)

    assert gen.main(["--definitions", str(definitions), "--output", str(tmp_path / "out.yml")]) == 1
    assert "defined twice: erp" in capsys.readouterr().out


def test_blank_description_is_refused_and_counted(tmp_path, capsys):
    """An undescribed column fails the run rather than producing an empty entry."""
    rows = [row for row in full_rows() if not row.startswith("erp\t")]
    rows.append(tsv_line(name="erp"))
    definitions = write_tsv(tmp_path / "defs.tsv", rows)

    assert gen.main(["--definitions", str(definitions), "--output", str(tmp_path / "out.yml")]) == 1
    out = capsys.readouterr().out
    assert "no description_en for 1 column(s)" in out
    assert "erp" in out


def test_old_header_points_to_the_fetch_script(tmp_path, capsys):
    """A TSV predating the pipeline must not fail obscurely."""
    definitions = tmp_path / "defs.tsv"
    definitions.write_text("name\tdescription\nerp\tSomething\n", encoding="utf-8", newline="\n")

    assert gen.main(["--definitions", str(definitions), "--output", str(tmp_path / "out.yml")]) == 1
    out = capsys.readouterr().out
    assert "no 'description_en' column" in out
    assert "fetch_column_definitions.py" in out


def test_unknown_tag_is_refused_with_the_vocabulary(tmp_path, capsys):
    """The closed vocabulary is enforced, not merely documented."""
    rows = full_rows(erp={"name": "erp", "description_en": "Public venue", "tags": "PII"})
    definitions = write_tsv(tmp_path / "defs.tsv", rows)

    assert gen.main(["--definitions", str(definitions), "--output", str(tmp_path / "out.yml")]) == 1
    out = capsys.readouterr().out
    assert "erp: PII" in out
    assert "personal-data" in out  # the allowed list is printed alongside


def test_existing_output_is_not_overwritten_without_force(tmp_path, capsys):
    """Generated or not, an existing file is not destroyed by accident."""
    definitions = write_tsv(tmp_path / "defs.tsv", full_rows())
    output = tmp_path / "sources.yml"
    output.write_text("hand written\n", encoding="utf-8")

    assert gen.main(["--definitions", str(definitions), "--output", str(output)]) == 1
    assert "use --force" in capsys.readouterr().out
    assert output.read_text(encoding="utf-8") == "hand written\n"


def test_force_overwrites(tmp_path):
    definitions = write_tsv(tmp_path / "defs.tsv", full_rows())
    output = tmp_path / "sources.yml"
    output.write_text("hand written\n", encoding="utf-8")

    assert gen.main(["--definitions", str(definitions), "--output", str(output), "--force"]) == 0
    assert "hand written" not in output.read_text(encoding="utf-8")


# --- what the generator produces ----------------------------------------------


def test_output_is_valid_yaml_with_every_column(tmp_path):
    parsed, _ = render(tmp_path, full_rows())

    table = parsed["sources"][0]["tables"][0]
    assert parsed["version"] == 2
    assert parsed["sources"][0]["name"] == "bronze"
    assert parsed["sources"][0]["schema"] == "bronze"
    assert table["name"] == "parc_immobilier"
    assert len(table["columns"]) == len(gen.EXPECTED_COLUMNS)


def test_columns_follow_the_contract_order_not_the_tsv_order(tmp_path):
    """The TSV can be sorted or reordered in a spreadsheet without moving the output."""
    parsed, _ = render(tmp_path, list(reversed(full_rows())))

    names = [column["name"] for column in parsed["sources"][0]["tables"][0]["columns"]]
    assert names == list(gen.EXPECTED_COLUMNS)


def test_description_carries_the_provider_wording_and_source_system(tmp_path):
    rows = full_rows(
        ministere={
            "name": "ministere",
            "description_en": "Ministry occupying the asset",
            "definition_fr": "Ministère occupant le bien",
            "source_system": "Chorus RE-Fx",
        }
    )
    parsed, _ = render(tmp_path, rows)

    column = next(
        c for c in parsed["sources"][0]["tables"][0]["columns"] if c["name"] == "ministere"
    )
    assert column["description"] == (
        "Ministry occupying the asset. "
        "Provider definition: « Ministère occupant le bien ». "
        "Source system: Chorus RE-Fx."
    )


def test_a_long_description_survives_the_folded_scalar(tmp_path):
    """Folding is a rendering detail; the text read back must be unchanged.

    This is the test that would catch a wrapping bug: a line broken at the
    wrong place, or an indentation that turns the fold into a literal newline.
    """
    long_text = (
        "Final energy consumption of the asset in kWh, as published, including the rows "
        "left empty by ministries that chose to withhold the figure, so that completeness "
        "can be measured on the silver layer rather than assumed"
    )
    rows = full_rows(
        consommation_kwh_ef={
            "name": "consommation_kwh_ef",
            "description_en": long_text,
            "definition_fr": "Consommation énergétique du bien (kWh d'énergie finale)",
            "source_system": "Référentiel Technique, OSFi",
        }
    )
    parsed, output = render(tmp_path, rows)

    column = next(
        c
        for c in parsed["sources"][0]["tables"][0]["columns"]
        if c["name"] == "consommation_kwh_ef"
    )
    assert column["description"].startswith(long_text + ".")
    assert "« Consommation énergétique du bien (kWh d'énergie finale) »" in column["description"]
    assert "\n" not in column["description"]
    # Header comments quote the input path, whose length we do not control.
    body = [
        line for line in output.read_text(encoding="utf-8").splitlines() if not line.startswith("#")
    ]
    assert max(len(line) for line in body) <= gen.WIDTH


def test_tags_are_rendered_only_when_present(tmp_path):
    rows = full_rows(
        libelle_proprietaire={
            "name": "libelle_proprietaire",
            "description_en": "Owner label",
            "tags": "organisation;personal-data",
        }
    )
    parsed, _ = render(tmp_path, rows)

    columns = {c["name"]: c for c in parsed["sources"][0]["tables"][0]["columns"]}
    assert columns["libelle_proprietaire"]["tags"] == ["organisation", "personal-data"]
    assert "tags" not in columns["erp"]


def test_generated_file_announces_itself(tmp_path):
    """A generated file that does not say so gets edited by hand once, then diverges."""
    _, output = render(tmp_path, full_rows())
    header = output.read_text(encoding="utf-8").splitlines()[0]

    assert "GENERATED FILE" in header
    assert "generate_sources_yml.py" in output.read_text(encoding="utf-8")


def test_written_bytes_are_utf8_without_bom_and_lf(tmp_path):
    """.gitattributes stores these files verbatim; a CRLF would show as a change."""
    rows = full_rows(
        ministere={
            "name": "ministere",
            "description_en": "Ministry occupying the asset",
            "definition_fr": "Ministère occupant le bien",
        }
    )
    _, output = render(tmp_path, rows)
    raw = output.read_bytes()

    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw
    # Accents are written as UTF-8 bytes, not escaped and not transliterated.
    assert "Ministère occupant le bien".encode() in raw


# --- the drift guard ----------------------------------------------------------


def test_check_passes_on_an_up_to_date_file(tmp_path, capsys):
    definitions = write_tsv(tmp_path / "defs.tsv", full_rows())
    output = tmp_path / "sources.yml"
    assert gen.main(["--definitions", str(definitions), "--output", str(output)]) == 0

    code = gen.main(["--definitions", str(definitions), "--output", str(output), "--check"])

    assert code == 0
    assert "up to date" in capsys.readouterr().out


def test_check_fails_when_the_tsv_moved_on(tmp_path, capsys):
    """The guard CI will run: the YAML must never lag behind the TSV."""
    definitions = tmp_path / "defs.tsv"
    write_tsv(definitions, full_rows())
    output = tmp_path / "sources.yml"
    assert gen.main(["--definitions", str(definitions), "--output", str(output)]) == 0

    write_tsv(definitions, full_rows(erp={"name": "erp", "description_en": "Reworded"}))
    code = gen.main(["--definitions", str(definitions), "--output", str(output), "--check"])

    assert code == 1
    assert "out of date" in capsys.readouterr().out


def test_check_reports_a_missing_output(tmp_path, capsys):
    definitions = write_tsv(tmp_path / "defs.tsv", full_rows())

    code = gen.main(
        ["--definitions", str(definitions), "--output", str(tmp_path / "gone.yml"), "--check"]
    )

    assert code == 1
    assert "missing" in capsys.readouterr().out


# --- reading the TSV ----------------------------------------------------------


def test_comment_rows_and_a_bom_are_tolerated(tmp_path):
    """The TSV is edited in a spreadsheet, which adds a BOM on export."""
    rows = ["# the four metadata columns are ours", *full_rows()]
    definitions = write_tsv(tmp_path / "defs.tsv", rows, encoding="utf-8-sig")

    definitions_read = gen.read_definitions(definitions)

    assert len(definitions_read) == len(gen.EXPECTED_COLUMNS)
    assert definitions_read[0]["name"] == gen.EXPECTED_COLUMNS[0]


def test_an_empty_tsv_is_refused(tmp_path):
    definitions = write_tsv(tmp_path / "defs.tsv", [])

    with pytest.raises(ValueError, match="no definition found"):
        gen.read_definitions(definitions)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        ("public", ["public"]),
        ("public;technical", ["public", "technical"]),
        ("public, technical", ["public", "technical"]),
        ("  public ;; technical  ", ["public", "technical"]),
    ],
)
def test_tags_are_split_on_either_separator(raw, expected):
    """Comma or semicolon: a spreadsheet user should not have to remember which."""
    assert gen.parse_tags({"tags": raw}) == expected
