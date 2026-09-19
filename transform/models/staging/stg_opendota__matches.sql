-- Renommage et typage, rien d'autre : aucune jointure, aucune règle métier.
-- Le vocabulaire propre à Dota 2 (radiant, dire) laisse place à un vocabulaire
-- neutre — camp 1 et camp 2 — pour qu'un match Counter-Strike puisse un jour
-- entrer dans la même table sans la tordre.

with source as (

    select * from {{ source('raw', 'opendota_pro_matches') }}

),

renamed as (

    select
        match_id,
        'opendota' as source_name,
        'dota2' as discipline,

        start_time as started_at,
        duration_seconds,

        radiant_team_id as side_1_team_id,
        radiant_name as side_1_team_name,
        dire_team_id as side_2_team_id,
        dire_name as side_2_team_name,
        radiant_score as side_1_score,
        dire_score as side_2_score,

        -- `radiant_win` ne doit pas être nul, mais un `case` sans garde ferait
        -- silencieusement gagner le camp 2 si ça arrivait.
        case
            when radiant_win is null then null
            when radiant_win then 1
            else 2
        end as winning_side,
        case
            when radiant_win is null then null
            when radiant_win then radiant_team_id
            else dire_team_id
        end as winner_team_id,

        league_id,
        nullif(trim(league_name), '') as league_name,
        series_id,
        refreshed_at

    from source

)

select * from renamed
