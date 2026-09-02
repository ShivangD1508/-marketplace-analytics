{#
    A local stand-in for dbt_utils.generate_surrogate_key.

    This project deliberately has zero dbt package dependencies so that
    `dbt build` works offline and CI never depends on hub.getdbt.com being up.

    NULL-safe: every field is coalesced to a sentinel before hashing, so a NULL
    never collapses the whole key to NULL the way a bare concat would.
#}
{% macro generate_surrogate_key(field_list) -%}
    md5(cast(
        {%- for field in field_list %}
        coalesce(cast({{ field }} as varchar), '_dbt_null_')
        {%- if not loop.last %} || '-' || {% endif -%}
        {%- endfor %}
    as varchar))
{%- endmacro %}
