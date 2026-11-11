-- Une carte jouée par ligne, avec ce que le veto en disait.
--
-- Savoir qui a choisi la carte change la lecture du résultat : gagner sur son
-- choix est attendu, gagner sur celui de l'adversaire ne l'est pas.

with maps as (

    select * from {{ ref('stg_liquipedia__match_maps') }}

),

series as (

    select series_key, tournament_key, tier_rank from {{ ref('fct_series') }}

),

picks as (

    -- Une carte n'est choisie qu'une fois par rencontre ; on s'en assure
    -- plutôt que de le supposer, un veto mal saisi dupliquerait la carte.
    select
        series_key,
        map_name,
        min(action) as action,
        min(team) as team
    from {{ ref('stg_liquipedia__match_veto') }}
    where action in ('pick', 'decider')
    group by series_key, map_name

),

final as (

    select
        maps.map_key,
        maps.series_key,
        maps.discipline,
        series.tournament_key,
        series.tier_rank,

        maps.played_at,
        maps.map_number,
        maps.map_name,
        maps.duration_seconds,

        maps.side_1_team,
        maps.side_2_team,
        maps.side_1_rounds,
        maps.side_2_rounds,
        maps.side_1_rounds + maps.side_2_rounds as rounds_played,
        -- Une carte se gagne à 13 : au-delà de 24 manches, c'est la prolongation.
        maps.side_1_rounds + maps.side_2_rounds > 24 as went_to_overtime,

        case
            when maps.side_1_rounds > maps.side_2_rounds then 1
            when maps.side_2_rounds > maps.side_1_rounds then 2
        end as winning_side,
        case
            when maps.side_1_rounds > maps.side_2_rounds then maps.side_1_team
            when maps.side_2_rounds > maps.side_1_rounds then maps.side_2_team
        end as winner_team,

        picks.action = 'decider' as is_decider,
        case when picks.action = 'pick' then picks.team end as picked_by,
        case
            when picks.action = 'pick' and picks.team = maps.side_1_team then 1
            when picks.action = 'pick' and picks.team = maps.side_2_team then 2
        end as picking_side,

        maps.side_1_starting_side,
        maps.side_1_atk_rounds,
        maps.side_1_def_rounds,
        maps.side_2_atk_rounds,
        maps.side_2_def_rounds,

        maps.side_1_first_kills,
        maps.side_2_first_kills,
        maps.side_1_clutches,
        maps.side_2_clutches,
        maps.side_1_thrifties,
        maps.side_2_thrifties,
        maps.side_1_flawless,
        maps.side_2_flawless,
        maps.side_1_post_plant_won,
        maps.side_1_post_plant_played,
        maps.side_2_post_plant_won,
        maps.side_2_post_plant_played

    from maps
    left join series on maps.series_key = series.series_key
    left join picks
        on maps.series_key = picks.series_key
        and maps.map_name = picks.map_name

)

select * from final
