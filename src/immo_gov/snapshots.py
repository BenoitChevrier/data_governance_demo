import csv
import hashlib
from io import StringIO

# Number of columns in the CSV export, measured against the live endpoint on
# 2026-08-28. The catalog advertises 29 fields, but point_geo is a computed geo
# field that is not exported, so the file carries 28.
EXPECTED_COLUMNS = 28

COLUMNS = (
    "code_chorus",
    "id",
    "designation_site",
    "designation_batiment_terrain",
    "type",
    "fonction",
    "adresse",
    "ville",
    "dept",
    "code_postal",
    "code_insee",
    "libelle_nouvelle_region",
    "pays",
    "latitude",
    "longitude",
    "ministere",
    "libelle_gestionnaire",
    "type_gestionnaire",
    "libelle_proprietaire",
    "type_proprietaire",
    "surface_m2",
    "consommation_kwh_ef",
    "type_de_chauffage",
    "etat_de_sante",
    "annee_de_construction",
    "tri_des_dechets",
    "erp",
    "date_de_reference",
)

# The published vintages hold roughly 17,000 rows each. The floor is set well
# below that to tolerate a change of scope at the source, but high enough to
# catch a truncated body served with HTTP 200.
MIN_DATA_ROWS = 2_000


def compute_sha256(data: bytes) -> str:
    """Return the SHA-256 digest of the given bytes."""
    return hashlib.sha256(data).hexdigest()


def describe_csv(csv_bytes: bytes) -> tuple[int, int]:
    """Return (column count, data row count) for a semicolon-delimited CSV.

    Decoded with utf-8-sig: the provider prefixes the file with a UTF-8 BOM,
    and without it the first column would be named "﻿code_chorus".

    Rows are counted through the csv reader rather than by counting lines, so
    that a quoted field containing a newline counts as one record, not two.
    """
    f = StringIO(csv_bytes.decode("utf-8-sig"))
    reader = csv.reader(f, delimiter=";")
    header = next(reader)
    return len(header), sum(1 for _ in reader)


def check_csv_content(csv_bytes: bytes) -> bool:
    """Reject a payload that cannot be a valid snapshot of this dataset.

    This is the data contract with the provider: it does not check that our
    code is correct, it checks that the source has not changed shape under us.
    """
    if not csv_bytes:
        print("The CSV is empty.")
        return False
    try:
        columns_count, rows_count = describe_csv(csv_bytes)
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
