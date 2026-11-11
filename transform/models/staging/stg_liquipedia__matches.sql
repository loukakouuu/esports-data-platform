-- Une rencontre par ligne, telle que sa page rendue la décrit.
--
-- Seuls les matchs terminés passent : une page rendue avant le match, une page
-- disparue ou une page sans bloc exploitable restent dans le brut, où elles
-- disent ce qui a été tenté, mais n'ont rien à faire dans une analyse.

with source as (

    select * from {{ source('raw', 'liquipedia_match_pages') }}

),

finished as (

    select
        'liquipedia:' || wiki || ':' || page_title as series_key,
        'liquipedia' as source_name,
        wiki as discipline,
        page_title,

        played_at,
        tournament_page,

        nullif(trim(team_1), '') as side_1_team,
        nullif(trim(team_2), '') as side_2_team,
        score_1 as side_1_maps,
        score_2 as side_2_maps,
        map_count,

        document,
        refreshed_at

    from source
    where status = 'finished'

)

select * from finished
