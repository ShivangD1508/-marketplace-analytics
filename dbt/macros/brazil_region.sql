{#
    Maps a Brazilian state (UF) to its IBGE macro-region.

    Delivery performance in this marketplace is far more a function of region
    than of state -- the North and Northeast are structurally slower because
    almost every seller is in the Southeast. Keeping the mapping in one macro
    means fct_orders and dim_customers cannot drift apart on it.
#}
{% macro brazil_region(state_column) -%}
    case {{ state_column }}
        when 'AC' then 'North'        when 'AP' then 'North'
        when 'AM' then 'North'        when 'PA' then 'North'
        when 'RO' then 'North'        when 'RR' then 'North'
        when 'TO' then 'North'
        when 'AL' then 'Northeast'    when 'BA' then 'Northeast'
        when 'CE' then 'Northeast'    when 'MA' then 'Northeast'
        when 'PB' then 'Northeast'    when 'PE' then 'Northeast'
        when 'PI' then 'Northeast'    when 'RN' then 'Northeast'
        when 'SE' then 'Northeast'
        when 'DF' then 'Central-West' when 'GO' then 'Central-West'
        when 'MT' then 'Central-West' when 'MS' then 'Central-West'
        when 'ES' then 'Southeast'    when 'MG' then 'Southeast'
        when 'RJ' then 'Southeast'    when 'SP' then 'Southeast'
        when 'PR' then 'South'        when 'RS' then 'South'
        when 'SC' then 'South'
        else 'Unknown'
    end
{%- endmacro %}
