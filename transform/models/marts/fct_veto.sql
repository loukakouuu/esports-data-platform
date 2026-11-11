-- Le veto, une étape par ligne : qui écarte quoi, qui choisit quoi.

with veto as (

    select * from {{ ref('stg_liquipedia__match_veto') }}

),

series as (

    select series_key, tournament_key, tier_rank, side_1_team, side_2_team
    from {{ ref('fct_series') }}

),

final as (

    select
        veto.series_key || '#' || veto.veto_order as veto_key,
        veto.series_key,
        veto.discipline,
        series.tournament_key,
        series.tier_rank,
        veto.played_at,

        veto.veto_order,
        veto.action,
        veto.map_name,
        veto.team,
        case
            when veto.team = series.side_1_team then 1
            when veto.team = series.side_2_team then 2
        end as side

    from veto
    left join series on veto.series_key = series.series_key

)

select * from final
