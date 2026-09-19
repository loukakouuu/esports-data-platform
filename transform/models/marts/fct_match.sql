-- Un match, quelle que soit sa discipline.
--
-- Une seule source l'alimente aujourd'hui — OpenDota, donc Dota 2. La table
-- est pourtant écrite au pluriel des disciplines : les colonnes sont neutres,
-- et brancher Counter-Strike reviendra à ajouter une union, pas à refondre le
-- modèle. Ce qui manque est dit dans le README plutôt que masqué ici.

with matches as (

    select * from {{ ref('stg_opendota__matches') }}

),

final as (

    select
        source_name || ':' || discipline || ':' || match_id as match_key,

        source_name,
        discipline,
        match_id as source_match_id,

        started_at,
        started_at::date as started_on,
        duration_seconds,

        side_1_team_id,
        side_1_team_name,
        side_1_score,
        side_2_team_id,
        side_2_team_name,
        side_2_score,

        winning_side,
        winner_team_id,
        abs(side_1_score - side_2_score) as score_gap,

        -- Le tournoi n'est pas relié à `dim_tournament` : OpenDota et
        -- Liquipedia n'ont aucun identifiant commun, et seul le nom permettrait
        -- de les rapprocher. On garde donc l'identifiant de la source.
        league_id as source_league_id,
        league_name as source_league_name,
        series_id as source_series_id

    from matches

)

select * from final
