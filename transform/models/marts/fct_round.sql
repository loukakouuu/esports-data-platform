-- Une manche par ligne, replacée dans le fil de sa carte.
--
-- C'est le grain le plus fin que la source donne, et celui que vlr.gg ne
-- montre pas agrégé : le score au moment où la manche commence, le camp qui
-- prend le premier kill, et s'il le convertit.

with rounds as (

    select * from {{ ref('stg_liquipedia__match_rounds') }}

),

players as (

    select map_key, player, side
    from {{ ref('stg_liquipedia__match_players') }}

),

maps as (

    select map_key, tournament_key, tier_rank, side_1_team, side_2_team
    from {{ ref('fct_map') }}

),

joined as (

    select
        rounds.round_key,
        rounds.map_key,
        rounds.series_key,
        rounds.discipline,
        maps.tournament_key,
        maps.tier_rank,

        rounds.played_at,
        rounds.map_number,
        rounds.map_name,
        rounds.round_number,

        rounds.winning_side,
        rounds.winning_role,
        rounds.outcome,

        -- Le score avant la manche : ce qu'il faut pour parler de manches
        -- décisives, de remontées, et plus tard de probabilité de victoire.
        count(*) filter (where rounds.winning_side = 1) over previous_rounds
            as side_1_score_before,
        count(*) filter (where rounds.winning_side = 2) over previous_rounds
            as side_2_score_before,

        rounds.first_kill_player,
        openers.side as first_kill_side,
        openers.side = rounds.winning_side as first_kill_converted,

        rounds.clutch_player,
        rounds.clutch_player is not null as was_clutch

    from rounds
    left join maps on rounds.map_key = maps.map_key
    left join players as openers
        on rounds.map_key = openers.map_key
        and rounds.first_kill_player = openers.player

    window previous_rounds as (
        partition by rounds.map_key
        order by rounds.round_number
        rows between unbounded preceding and 1 preceding
    )

)

select * from joined
