-- Renommage et typage. Le tier reste celui de la source : le traduire suppose
-- une correspondance entre disciplines, qui est une décision — elle vit dans
-- la graine `tournament_tier` et s'applique dans les marts.

with source as (

    select * from {{ source('raw', 'liquipedia_tournaments') }}

),

renamed as (

    select
        wiki,
        page_id,
        page_title,

        'liquipedia' as source_name,
        -- Un wiki Liquipedia couvre exactement une discipline.
        wiki as discipline,

        nullif(trim(name), '') as tournament_name,
        nullif(trim(series), '') as series_name,
        nullif(trim(organizer), '') as organizer,

        nullif(trim(tier), '') as tier_source,
        -- La casse varie d'une page à l'autre : « Showmatch », « showmatch »
        -- et « Show Match » désignent la même chose.
        lower(nullif(trim(tier_type), '')) as tier_type,
        nullif(trim(game), '') as game,
        lower(nullif(trim(setting), '')) as setting,

        nullif(trim(country), '') as country,
        nullif(trim(city), '') as city,

        start_date,
        end_date,
        prize_pool_usd,
        local_currency,
        team_count,

        revised_at,
        refreshed_at

    from source

)

select * from renamed
