{#
    Without this macro, dbt concatenates the profile schema and the schema
    declared on a model: a gold model would land in "silver_gold", not "gold".
    Verified on dbt 1.12.3.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}