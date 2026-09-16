{#
    Convert a bronze text column to a number, whatever the provider published.

    The bronze layer is text by design: the file is loaded as published so that
    its defects stay measurable. Three of them show up as soon as a cast is
    attempted, all measured on the two snapshots:

      - consommation_kwh_ef is written the French way in the 2022 vintage
        (comma decimal, non-breaking space for thousands: "11 128,45") and the
        English way in the 2023 one ("68893.15"). The same column, two formats,
        two files.
      - latitude and longitude carry a literal "-" where a ministry withheld
        the position: 29 rows in 2022, 943 in 2023. It is a redaction marker,
        not a number.
      - surface_m2 uses a dot in both vintages, so it needs none of this — but
        it goes through the same macro, because a column that is clean today is
        not a column that stays clean.

    A plain cast would fail on the first "-" and on every thousands separator,
    and the run would stop. Here the text is normalised first, then cast only
    when it actually is a number: a withheld value becomes NULL, which the
    completeness tests can then count.
#}
{% macro to_numeric(column) -%}
    {%- set cleaned -%}
        replace(replace(replace(trim({{ column }}), chr(160), ''), ' ', ''), ',', '.')
    {%- endset -%}
    case
        when {{ cleaned }} ~ '^-?[0-9]+(\.[0-9]+)?$' then ({{ cleaned }})::numeric
    end
{%- endmacro %}
