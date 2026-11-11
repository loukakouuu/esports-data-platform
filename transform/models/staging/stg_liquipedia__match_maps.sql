-- Une carte jouée par ligne : le document de la rencontre, déplié d'un cran.
--
-- Le brut garde chaque rencontre d'un seul tenant, en JSON ; c'est ici que la
-- forme tabulaire apparaît. Les mi-temps arrivent en liste — deux en temps
-- réglementaire, davantage en prolongation — d'où les sommes filtrées par camp.

with matches as (

    select * from {{ ref('stg_liquipedia__matches') }}

),

unnested as (

    select
        matches.series_key,
        matches.discipline,
        matches.played_at,
        matches.tournament_page,
        matches.side_1_team,
        matches.side_2_team,
        unnest(cast(json_extract(matches.document, '$.maps') as json[])) as carte

    from matches

),

typed as (

    select
        series_key || '#' || (carte ->> 'number') as map_key,
        series_key,
        discipline,
        played_at,
        tournament_page,
        side_1_team,
        side_2_team,

        cast(carte ->> 'number' as integer) as map_number,
        carte ->> 'map' as map_name,
        cast(carte -> 'score' ->> 0 as integer) as side_1_rounds,
        cast(carte -> 'score' ->> 1 as integer) as side_2_rounds,

        -- « 63:00 » : minutes puis secondes, les minutes dépassant l'heure.
        cast(split_part(carte ->> 'length', ':', 1) as integer) * 60
            + cast(split_part(carte ->> 'length', ':', 2) as integer) as duration_seconds,

        -- Le camp tenu par le camp 1 en première mi-temps : l'autre a l'inverse.
        carte -> 'halves' -> 0 -> 0 ->> 'side' as side_1_starting_side,

        {% for side in [1, 2] %}
        {% for role in ['atk', 'def'] %}
        -- `lambda x:` et non `x ->` : la flèche se confondrait avec celle du JSON.
        coalesce(list_sum(list_transform(
            list_filter(
                cast(carte -> 'halves' -> {{ side - 1 }} as json[]),
                lambda half: (half ->> 'side') = '{{ role }}'
            ),
            lambda half: cast(half ->> 'rounds' as integer)
        )), 0) as side_{{ side }}_{{ role }}_rounds,
        {% endfor %}
        {% endfor %}

        {% for side in [1, 2] %}
        cast(carte -> 'team_stats' -> 'first_kills' ->> {{ side - 1 }} as integer)
            as side_{{ side }}_first_kills,
        cast(carte -> 'team_stats' -> 'clutches' ->> {{ side - 1 }} as integer)
            as side_{{ side }}_clutches,
        cast(carte -> 'team_stats' -> 'thrifties' ->> {{ side - 1 }} as integer)
            as side_{{ side }}_thrifties,
        cast(carte -> 'team_stats' -> 'flawless' ->> {{ side - 1 }} as integer)
            as side_{{ side }}_flawless,
        -- « 5/8 » : manches gagnées après la pose, sur manches où le spike est posé.
        cast(split_part(carte -> 'team_stats' -> 'post_plant' ->> {{ side - 1 }}, '/', 1)
            as integer) as side_{{ side }}_post_plant_won,
        cast(split_part(carte -> 'team_stats' -> 'post_plant' ->> {{ side - 1 }}, '/', 2)
            as integer) as side_{{ side }}_post_plant_played,
        {% endfor %}

        carte as map_document

    from unnested

)

select * from typed
