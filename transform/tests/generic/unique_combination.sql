{#
    Unicité sur plusieurs colonnes. dbt n'en fournit pas en standard, et
    `dbt_utils` ne vaut pas une dépendance réseau pour huit lignes de SQL.

    Une clé composite n'est pas une exception ici : un identifiant de page
    Liquipedia n'est unique qu'au sein de son wiki.
#}
{% test unique_combination(model, combination_of_columns) %}

with compte as (

    select
        {{ combination_of_columns | join(', ') }},
        count(*) as occurrences
    from {{ model }}
    group by {{ combination_of_columns | join(', ') }}
    having count(*) > 1

)

select * from compte

{% endtest %}
