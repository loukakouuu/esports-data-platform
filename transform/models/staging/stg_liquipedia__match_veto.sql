-- Le veto d'une rencontre, une étape par ligne, dans l'ordre où il s'est joué.

with matches as (

    select * from {{ ref('stg_liquipedia__matches') }}

),

unnested as (

    select
        matches.series_key,
        matches.discipline,
        matches.played_at,
        matches.side_1_team,
        matches.side_2_team,
        unnest(cast(json_extract(matches.document, '$.veto') as json[])) as etape

    from matches

),

typed as (

    select
        series_key,
        discipline,
        played_at,

        cast(etape ->> 'order' as integer) as veto_order,
        etape ->> 'action' as action,
        etape ->> 'map' as map_name,
        -- Nul pour la carte décisive : personne ne la choisit.
        etape ->> 'team' as team

    from unnested

)

select * from typed
