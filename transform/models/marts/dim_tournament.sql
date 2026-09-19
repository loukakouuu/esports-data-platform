-- Un tournoi, quelle que soit sa discipline.
--
-- C'est ici que les deux vocabulaires se rejoignent : « S-Tier » côté
-- Counter-Strike et « 1 » côté Dota 2 deviennent un même rang comparable. La
-- traduction vient de la graine `tournament_tier` ; un tier absent de celle-ci
-- laisse `tier_rank` nul, et le test `assert_tiers_tous_traduits` le dit.

with tournaments as (

    select * from {{ ref('stg_liquipedia__tournaments') }}

),

tiers as (

    select * from {{ ref('tournament_tier') }}

),

joined as (

    select
        -- Clé lisible plutôt qu'un condensé : on veut pouvoir la déchiffrer
        -- en lisant une ligne.
        tournaments.source_name
            || ':' || tournaments.discipline
            || ':' || tournaments.page_id as tournament_key,

        tournaments.source_name,
        tournaments.discipline,
        tournaments.page_id,
        tournaments.page_title,

        tournaments.tournament_name,
        tournaments.series_name,
        tournaments.organizer,

        tournaments.tier_source,
        tiers.tier_rank,
        tiers.tier_label,
        tournaments.tier_key,
        tournaments.tier_type,
        tournaments.game,
        tournaments.setting,

        tournaments.country,
        tournaments.city,

        tournaments.start_date,
        -- Trois pages sur 25 000 se terminent avant d'avoir commencé : une
        -- faute de saisie chez Liquipedia. On ne corrige pas la source — on
        -- refuse d'en tirer une durée négative, et on signale le conflit pour
        -- que personne n'ait à le redécouvrir.
        case
            when tournaments.end_date >= tournaments.start_date then tournaments.end_date
            when tournaments.start_date is null then tournaments.end_date
        end as end_date,
        coalesce(tournaments.end_date < tournaments.start_date, false) as has_date_conflict,
        case
            when tournaments.end_date >= tournaments.start_date
                then date_diff('day', tournaments.start_date, tournaments.end_date) + 1
        end as duration_days,

        tournaments.prize_pool_usd,
        tournaments.team_count,
        tournaments.revised_at

    from tournaments
    left join tiers
        on tournaments.discipline = tiers.discipline
        and tournaments.tier_key = tiers.tier_key

)

select * from joined
