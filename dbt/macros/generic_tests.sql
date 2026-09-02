{#
    Generic tests this project uses in place of dbt_utils / dbt_expectations.
    Written locally so the project has no package dependencies (see
    generate_surrogate_key.sql for the rationale).
#}

{% test expression_is_true(model, column_name=none, expression='') %}
{#- Fails on any row where `expression` is not true. If applied to a column,
    the column name is prefixed automatically so the YAML stays terse. -#}
select *
from {{ model }}
where not (
    {%- if column_name %} {{ column_name }} {{ expression }} {%- else %} {{ expression }} {%- endif %}
)
{% endtest %}


{% test accepted_range(model, column_name, min_value=none, max_value=none, inclusive=true) %}
{#- Numeric bounds check. NULLs pass; pair it with not_null when that matters. -#}
select *
from {{ model }}
where {{ column_name }} is not null
  and (
    false
    {%- if min_value is not none %}
    or {{ column_name }} {{ '<' if inclusive else '<=' }} {{ min_value }}
    {%- endif %}
    {%- if max_value is not none %}
    or {{ column_name }} {{ '>' if inclusive else '>=' }} {{ max_value }}
    {%- endif %}
  )
{% endtest %}


{% test unique_combination_of_columns(model, combination_of_columns) %}
{#- Compound uniqueness, i.e. the grain of the model. -#}
select
    {{ combination_of_columns | join(', ') }},
    count(*) as n_records
from {{ model }}
group by {{ range(1, combination_of_columns | length + 1) | join(', ') }}
having count(*) > 1
{% endtest %}


{% test not_null_where(model, column_name, condition) %}
{#- Conditional not_null: the column is only required to be populated for the
    subset of rows that satisfy `condition`. Lifecycle timestamps need this --
    order_delivered_customer_date must be present for delivered orders and
    must be absent for everything else. -#}
select *
from {{ model }}
where ({{ condition }})
  and {{ column_name }} is null
{% endtest %}
