-- Un tier que la graine ne connaît pas doit se voir.
--
-- Sans ce test, l'arrivée d'un « E-Tier » ou d'un tier 6 chez Liquipedia
-- passerait inaperçue : la jointure rendrait simplement un rang nul, et les
-- tournois concernés disparaîtraient silencieusement de toute analyse classée
-- par niveau. Mieux vaut un build rouge qu'une moyenne fausse.

select
    discipline,
    tier_source,
    count(*) as tournois_concernes

from {{ ref('dim_tournament') }}

where tier_source is not null
    and tier_rank is null

group by discipline, tier_source
