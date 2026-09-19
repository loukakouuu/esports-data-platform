{#
    Borne une valeur numérique. Les nuls ne sont pas des fautes ici : une
    dotation inconnue reste inconnue, et c'est `not_null` qui tranche cela
    quand il le faut.
#}
{% test accepted_range(model, column_name, min_value=none, max_value=none) %}

{%- if min_value is none and max_value is none -%}
    {{ exceptions.raise_compiler_error("accepted_range attend au moins une borne") }}
{%- endif -%}

select {{ column_name }} as valeur
from {{ model }}
where {{ column_name }} is not null
    and (
        {%- if min_value is not none %} {{ column_name }} < {{ min_value }}{% endif -%}
        {%- if min_value is not none and max_value is not none %} or{% endif -%}
        {%- if max_value is not none %} {{ column_name }} > {{ max_value }}{% endif %}
    )

{% endtest %}
