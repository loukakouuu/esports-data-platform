-- Le score d'une carte et le décompte de ses manches viennent de deux endroits
-- différents de la page. S'ils divergent, c'est le parseur qui se trompe — ou
-- la page qui a changé de forme — et il vaut mieux l'apprendre ici que dans
-- une analyse manche par manche.

with recomptees as (

    select
        map_key,
        count(*) filter (where winning_side = 1) as side_1_recompte,
        count(*) filter (where winning_side = 2) as side_2_recompte

    from {{ ref('fct_round') }}
    group by map_key

)

select
    cartes.map_key,
    cartes.side_1_rounds,
    recomptees.side_1_recompte,
    cartes.side_2_rounds,
    recomptees.side_2_recompte

from {{ ref('fct_map') }} as cartes
inner join recomptees on cartes.map_key = recomptees.map_key

where cartes.side_1_rounds <> recomptees.side_1_recompte
    or cartes.side_2_rounds <> recomptees.side_2_recompte
