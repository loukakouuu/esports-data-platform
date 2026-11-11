-- La ligne d'un joueur sur une carte.
--
-- Un joueur s'identifie par le titre de sa page Liquipedia, pas par son pseudo
-- affiché : « kiNgg » et « KiNgg » sont la même personne, et seul le titre de
-- page ne change pas d'une feuille de match à l'autre.

with maps as (

    select * from {{ ref('stg_liquipedia__match_maps') }}

),

unnested as (

    select
        maps.map_key,
        maps.series_key,
        maps.discipline,
        maps.played_at,
        maps.map_number,
        maps.map_name,
        maps.side_1_team,
        maps.side_2_team,
        maps.side_1_rounds,
        maps.side_2_rounds,
        unnest(cast(maps.map_document -> 'players' as json[])) as joueur

    from maps

),

typed as (

    select
        map_key,
        series_key,
        discipline,
        played_at,
        map_number,
        map_name,

        cast(joueur ->> 'side' as integer) as side,
        case cast(joueur ->> 'side' as integer)
            when 1 then side_1_team
            when 2 then side_2_team
        end as team,
        joueur ->> 'page' as player,
        joueur ->> 'name' as player_name,
        joueur ->> 'agent' as agent,

        side_1_rounds + side_2_rounds as rounds_played,
        case cast(joueur ->> 'side' as integer)
            when 1 then side_1_rounds > side_2_rounds
            when 2 then side_2_rounds > side_1_rounds
        end as won_map,

        cast(joueur ->> 'acs' as double) as acs,
        cast(joueur ->> 'kills' as integer) as kills,
        cast(joueur ->> 'deaths' as integer) as deaths,
        cast(joueur ->> 'assists' as integer) as assists,
        cast(joueur ->> 'kast' as double) as kast_pct,
        cast(joueur ->> 'adr' as double) as adr,
        cast(joueur ->> 'headshot_pct' as double) as headshot_pct,
        cast(joueur ->> 'first_kills' as integer) as first_kills,
        cast(joueur ->> 'first_deaths' as integer) as first_deaths

    from unnested

)

select * from typed
where player is not null
