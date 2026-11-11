-- La feuille de statistiques d'un joueur sur une carte.
--
-- Les taux par manche sont calculés ici une fois pour toutes : comparer des
-- kills bruts entre un 13-2 et un 13-15 n'a pas de sens.

with players as (

    select * from {{ ref('stg_liquipedia__match_players') }}

),

maps as (

    select map_key, tournament_key, tier_rank from {{ ref('fct_map') }}

),

final as (

    select
        players.map_key || '#' || players.player as player_map_key,
        players.map_key,
        players.series_key,
        players.discipline,
        maps.tournament_key,
        maps.tier_rank,

        players.played_at,
        players.map_number,
        players.map_name,

        players.player,
        players.player_name,
        players.team,
        players.side,
        players.agent,
        players.won_map,
        players.rounds_played,

        players.acs,
        players.kills,
        players.deaths,
        players.assists,
        players.kast_pct,
        players.adr,
        players.headshot_pct,
        players.first_kills,
        players.first_deaths,

        players.kills - players.deaths as kill_diff,
        players.first_kills - players.first_deaths as first_kill_diff,
        players.kills / nullif(players.deaths, 0) as kd_ratio,
        players.kills / nullif(players.rounds_played, 0) as kills_per_round,
        players.first_kills / nullif(players.rounds_played, 0) as first_kills_per_round

    from players
    left join maps on players.map_key = maps.map_key

)

select * from final
