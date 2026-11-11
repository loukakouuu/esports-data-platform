-- Une rencontre par ligne — un « Bo3 » ou un « Bo5 », cartes non dépliées.
--
-- Le tournoi est relié par identifiant : la page de match nomme la page de son
-- tournoi. C'est le lien qui manquait entre les matchs OpenDota et leurs
-- tournois, où seul un rapprochement de noms était possible.

with series as (

    select * from {{ ref('stg_liquipedia__matches') }}

),

tournaments as (

    select * from {{ ref('dim_tournament') }}

),

final as (

    select
        series.series_key,
        series.source_name,
        series.discipline,
        series.page_title,

        series.played_at,
        cast(series.played_at as date) as played_on,

        tournaments.tournament_key,
        tournaments.tier_rank,
        series.tournament_page,

        series.side_1_team,
        series.side_2_team,
        series.side_1_maps,
        series.side_2_maps,
        series.map_count,

        case
            when series.side_1_maps > series.side_2_maps then 1
            when series.side_2_maps > series.side_1_maps then 2
        end as winning_side,
        case
            when series.side_1_maps > series.side_2_maps then series.side_1_team
            when series.side_2_maps > series.side_1_maps then series.side_2_team
        end as winner_team

    from series
    left join tournaments
        on series.discipline = tournaments.discipline
        and series.tournament_page = tournaments.page_title

)

select * from final
