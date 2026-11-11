-- Une manche par ligne : qui la gagne, de quel camp, comment, et qui l'a ouverte.

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
        unnest(cast(maps.map_document -> 'rounds' as json[])) as manche

    from maps

),

typed as (

    select
        map_key || '#' || (manche ->> 'number') as round_key,
        map_key,
        series_key,
        discipline,
        played_at,
        map_number,
        map_name,

        cast(manche ->> 'number' as integer) as round_number,
        cast(manche ->> 'winner' as integer) as winning_side,
        -- Attaque ou défense : le rôle du vainqueur sur cette manche.
        manche ->> 'winner_side' as winning_role,
        manche ->> 'outcome' as outcome,
        manche ->> 'first_kill' as first_kill_player,
        manche ->> 'clutch' as clutch_player

    from unnested

)

select * from typed
