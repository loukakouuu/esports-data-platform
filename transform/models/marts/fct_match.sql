-- Une partie jouée, quelle que soit sa discipline.
--
-- Le grain est celui de la partie, pas de la rencontre : un match Dota 2 chez
-- OpenDota et une carte Valorant chez Liquipedia sont la même chose — deux
-- camps, un score, un vainqueur. Le vocabulaire neutre choisi dès le départ
-- (camp 1, camp 2) permet à Valorant d'entrer ici par une simple union.
--
-- Ce que chaque discipline a en propre vit dans ses tables de détail
-- (`fct_map`, `fct_player_map`, `fct_round`) ; ici, seulement le commun.

with dota as (

    select * from {{ ref('stg_opendota__matches') }}

),

valorant as (

    select * from {{ ref('fct_map') }}

),

unioned as (

    select
        source_name || ':' || discipline || ':' || match_id as match_key,
        source_name,
        discipline,

        'opendota:dota2:serie:' || series_id as series_key,
        cast(null as varchar) as tournament_key,

        started_at,
        duration_seconds,

        side_1_team_name as side_1_team,
        side_2_team_name as side_2_team,
        side_1_score,
        side_2_score,
        winning_side,

        -- OpenDota et Liquipedia n'ont aucun identifiant de tournoi commun :
        -- côté Dota 2, on garde le nom de ligue de la source.
        league_name as source_tournament_name

    from dota

    union all

    select
        map_key as match_key,
        'liquipedia' as source_name,
        discipline,

        series_key,
        tournament_key,

        -- La page ne date que la rencontre : toutes ses cartes en héritent.
        played_at as started_at,
        duration_seconds,

        side_1_team,
        side_2_team,
        side_1_rounds as side_1_score,
        side_2_rounds as side_2_score,
        winning_side,

        cast(null as varchar) as source_tournament_name

    from valorant

),

final as (

    select
        *,
        cast(started_at as date) as started_on,
        abs(side_1_score - side_2_score) as score_gap

    from unioned

)

select * from final
