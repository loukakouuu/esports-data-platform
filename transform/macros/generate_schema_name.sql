{#
    Par défaut, dbt préfixe les schémas personnalisés par celui de la cible :
    `staging` deviendrait `main_staging`. On veut lire `staging.` et `marts.`
    dans l'entrepôt, tels qu'annoncés dans le README.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
