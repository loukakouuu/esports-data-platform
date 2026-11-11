# Esports Data Platform

> Chaîne de données complète sur l'esport professionnel — de l'ingestion
> multi-sources jusqu'à la modélisation prédictive.

**🚧 Projet en construction.** Ce README décrit la cible ; l'état réel est suivi
dans [Avancement](#avancement).

---

## L'idée

L'esport publie beaucoup de données, mais éclatées : chaque discipline a ses
sources, ses formats, ses conventions, et aucune ne parle à l'autre. Ce projet
les réconcilie dans un modèle commun, puis s'en sert pour analyser et prédire.

Trois couches, une seule chaîne :

| Couche | Contenu |
|---|---|
| **Ingestion** | Collecte incrémentale multi-sources, sous contrainte de quota |
| **Transformation** | Normalisation vers un modèle commun inter-disciplines |
| **Analyse & modélisation** | Exploration du circuit, puis prédiction de résultats |

## Les sources

Trois sources de natures volontairement différentes — schémas, authentification,
quotas et fraîcheurs distincts. Les réconcilier est le cœur du travail.

| Source | Couverture | Contraintes |
|---|---|---|
| [Liquipedia (API MediaWiki)](https://liquipedia.net/api-terms-of-use) | Tous titres | Accès libre · CC-BY-SA 3.0 · 1 req/2 s (30 s pour `action=parse`) · `User-Agent` identifiant avec contact · gzip requis |
| [OpenDota](https://www.opendota.com/api-keys) | Dota 2 | 50 000 appels/mois · 60 req/min |
| [BALLDONTLIE](https://cs.balldontlie.io/) | Counter-Strike 2 | Clé requise · **matchs réservés au palier payant** · [spécification OpenAPI](https://www.balldontlie.io/openapi/cs.yml) |

Les trois se ressemblent si peu que le noyau ne peut pas les supposer
semblables : JSON contre wikitexte, identifiant décroissant contre jeton de
continuation, sans clé contre clé obligatoire. C'est voulu.

Sur BALLDONTLIE, la [spécification](https://www.balldontlie.io/openapi/cs.yml)
réserve `/cs/v1/matches` au palier GOAT. Une clé gratuite ne donne que du
référentiel — équipes, joueurs, tournois — et aucun match. Une dépendance
payante se défendrait mal dans un projet qu'on doit pouvoir cloner et lancer :
l'effort est donc allé à Liquipedia, qui couvre les deux disciplines sans clé.

### Valorant, la discipline prioritaire

Le projet vise désormais d'abord Valorant, avec une ambition : des analyses du
niveau de [vlr.gg](https://www.vlr.gg), et au-delà.

vlr.gg lui-même n'est pas une source. Son `robots.txt` est permissif, mais ses
conditions d'utilisation interdisent en toutes lettres « spider, robot,
scraper » et « data mining » : on ne le collecte pas.

La matière vient de Liquipedia, et il a fallu la chercher. Le wikitexte d'un
match Valorant ne contient pas ses scores — seulement un identifiant de partie,
que le wiki résout auprès de l'API de Riot au moment d'afficher. Mesuré sur 250
pages : une seule carte portait son score en clair. Tout l'intérêt d'un match
n'existe que dans la page **rendue**, et là il est entier : score par carte et
par mi-temps, veto, statistiques par joueur (ACS, K/D/A, KAST, ADR, tirs à la
tête, premiers kills, agent), et le déroulé manche par manche.

Le rendu passe par `action=parse`, limité à **une requête toutes les trente
secondes**. Les tournois désignent 95 765 matchs : tout lire prendrait
trente-trois jours. La collecte suit donc un ordre — tournois majeurs d'abord,
récents en tête — et s'arrête par défaut au niveau A : 6 861 matchs,
cinquante-huit heures, reprise gratuite à chaque interruption.

Chaque page rendue est conservée, réduite de 370 ko à une dizaine une fois
compressée, à côté du document qu'on en tire. Si le parseur doit un jour lire
autre chose, il relira l'entrepôt, pas le wiki.

### Ce que ce projet ne fait pas

**Aucune récolte sur des sources qui l'interdisent.** HLTV répond 403 à tout
client automatisé : il n'y a donc pas de collecte automatisable, et la
contourner reviendrait à mentir sur ce que fait le code. Les instantanés HLTV
éventuellement versés au dépôt sont collectés manuellement, datés et
explicitement signalés comme tels.

L'accès à Liquipedia suit ses conditions d'utilisation : requêtes limitées,
`User-Agent` identifiant, attribution CC-BY-SA.

## Architecture

```
ingestion/
  core/             Noyau : contrat de source, HTTP cadencé, entrepôt, boucle
  sources/          Une implémentation par source, derrière ce contrat
    wikitext.py     Découpage des modèles MediaWiki, testable à part
  cli.py            Ligne de commande esports-ingest
transform/          Modèles dbt : brut → normalisé → tables d'analyse
  models/staging/   Renommage et typage, sans interprétation
  models/marts/     Dimensions et faits, communs aux disciplines
  seeds/            Correspondances assumées, versionnées et discutables
analysis/           Notebooks d'exploration (à venir)
models/             Modélisation prédictive et backtests (à venir)
warehouse/          Entrepôt DuckDB (hors dépôt)
tests/              Tests de code et tests de données
```

Le noyau ne connaît qu'une interface de source. Ajouter une discipline ou un
fournisseur ne doit rien changer au reste — même principe que l'abstraction par
discipline d'[esport-manager](https://github.com/loukakouuu/esport-manager).

### Comment la collecte reprend son travail

Deux façons de parcourir une source, selon ce qu'elle offre.

**Quand les identifiants sont ordonnés** — OpenDota rend ses matchs du plus
récent au plus ancien — le flux garde deux bornes : le **sommet** de ce qui est
connu, et la **frontière** sous laquelle il reste à creuser. Entre les deux,
l'intervalle a été parcouru de façon contiguë. `catchup` repart du sommet et
s'arrête dès qu'il retrouve du connu : le quota ne sert qu'à collecter ce qui
manque. `backfill` reprend sous la frontière pour descendre dans l'historique.

**Quand il n'y a pas d'ordre exploitable** — Liquipedia liste ses tournois par
ordre alphabétique, une nouveauté peut apparaître n'importe où — il n'y a pas
de « au-dessus du connu » à retrouver. Le flux balaie tout, et ne retient que
le jeton de continuation. `catchup` recommence le balayage, seul moyen de voir
ce qui a changé ; `backfill` poursuit celui en cours.

Dans les deux cas, chaque page est écrite avec l'état correspondant dans une
seule transaction : une coupure — quota, réseau, Ctrl-C — ne coûte au plus
qu'une page. Et comme l'écriture se fait par clé primaire, relancer une
ingestion ne duplique rien.

### Ce que la transformation réconcilie

Counter-Strike classe ses tournois en `S-Tier`, Dota 2 en `1`. Les deux échelles
disent la même chose et ne se rencontrent jamais. Une graine versionnée —
[`tournament_tier.csv`](transform/seeds/tournament_tier.csv) — les ramène à un
rang commun, et rend possible une question qui ne l'était pas sur le brut :

```sql
select tier_rank, count(*), median(prize_pool_usd)
from marts.dim_tournament
group by tier_rank;   -- les deux disciplines dans la même colonne
```

Sur les 25 222 tournois collectés :

| Rang | Tournois | Dotation médiane |
|---|---|---|
| 1 — Premier | 2 176 | 250 000 $ |
| 2 — Majeur | 1 777 | 50 000 $ |
| 3 — Intermédiaire | 4 684 | 15 000 $ |
| 4 — Mineur | 16 535 | 2 000 $ |

Cette correspondance est **une décision, pas une donnée**. Elle vit donc dans un
fichier qu'on peut discuter ligne à ligne, plutôt que dans du SQL enfoui au
milieu d'un modèle. Et un test refuse le silence : si Liquipedia ajoute demain
un tier que la graine ignore, le build échoue, au lieu de laisser les tournois
concernés glisser hors des analyses avec un rang nul.

Ce test a déjà servi. La première hypothèse — lettres chez Counter-Strike,
chiffres chez Dota 2 — tenait sur un échantillon de 250 pages ; la collecte
complète l'a défaite, en révélant 858 pages Counter-Strike classées en chiffres,
héritées d'une convention antérieure. Le build est devenu rouge, ce qui était
exactement son travail.

## Mise en route

Prérequis : Python 3.12 et [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env     # aucune clé n'est nécessaire pour OpenDota
```

```bash
uv run esports-ingest sources                                    # flux disponibles
uv run esports-ingest run opendota.dota2.pro_matches --pages 5   # collecter
uv run esports-ingest run liquipedia.counterstrike.tournaments --pages 5
uv run esports-ingest run liquipedia.valorant.tournaments --pages 200
uv run esports-ingest run liquipedia.valorant.tournament_matches --pages 200
uv run esports-ingest run liquipedia.valorant.match_pages --pages 30   # 4 matchs par page, 2 min
uv run esports-ingest run opendota.dota2.pro_matches --mode backfill --pages 20
uv run esports-ingest state                                      # où en est la collecte
uv run esports-ingest check                                      # vérifier les données
```

Puis la transformation, depuis `transform/` :

```bash
uv run dbt build      # graine, modèles et tests de données d'un seul coup
uv run dbt docs serve # le graphe de dépendances, si le cœur vous en dit
```

L'entrepôt atterrit dans `warehouse/esports.duckdb` :

| Table | Contenu |
|---|---|
| `raw.opendota_pro_matches` | Matchs professionnels Dota 2 |
| `raw.liquipedia_tournaments` | Tournois Counter-Strike, Dota 2 et Valorant, une ligne par page de wiki |
| `staging.stg_*` | Brut renommé et typé, sans interprétation |
| `marts.dim_tournament` | Un tournoi par ligne, toutes disciplines, tier ramené à un rang commun |
| `raw.liquipedia_tournament_matches` | Pour chaque tournoi Valorant, les pages de match qu'il désigne |
| `raw.liquipedia_match_pages` | Pages de match rendues : document extrait et page compressée |
| `marts.fct_match` | Une partie par ligne, toutes disciplines : match Dota 2, carte Valorant |
| `marts.fct_series` | Une rencontre Valorant (Bo1, Bo3, Bo5), reliée à son tournoi |
| `marts.fct_map` | Une carte jouée, avec son veto et ses statistiques d'équipe |
| `marts.fct_player_map` | La feuille d'un joueur sur une carte |
| `marts.fct_round` | Une manche : score avant, issue, premier kill et sa conversion |
| `marts.fct_veto` | Le veto, une étape par ligne |
| `meta.ingestion_state` | Où en est chaque flux |
| `meta.ingestion_runs` | Historique des exécutions, échecs compris |

Chaque table brute conserve la charge utile d'origine — JSON d'OpenDota,
infobox de Liquipedia — à côté des colonnes normalisées : le jour où un champ
devient intéressant, l'historique n'est pas à recollecter.

## Avancement

- [x] Cadrage, choix des sources, structure
- [x] Socle Python et intégration continue
- [x] Ingestion OpenDota — matchs professionnels Dota 2
- [x] Tests de données sur la couche brute
- [x] Ingestion Liquipedia — tournois Counter-Strike, Dota 2 et Valorant
- [x] Modèle commun inter-disciplines (dbt)
- [x] Valorant — lecture des pages de match, liens tournoi → match, file priorisée
- [x] Valorant — tables d'analyse : rencontre, carte, joueur, manche, veto
- [ ] Valorant — collecte des niveaux S et A (en cours, 58 heures au total)
- [ ] Valorant — analyses : joueurs, agents, cartes, veto, manches
- [ ] Valorant — classement des équipes et prédiction, avec backtest
- [ ] Demande GRID Open Access (CS2 et Dota 2, données officielles)
- [ ] Rapprochement des tournois OpenDota et Liquipedia

Ce qui n'est pas encore fait : les analyses elles-mêmes. Les tables sont là,
la collecte des matchs majeurs avance à son rythme de trente secondes la page,
mais aucun notebook n'existe encore et rien n'est prédit. Les niveaux B et en
dessous ne sont pas collectés — 88 000 matchs, un mois de requêtes. Côté
Counter-Strike, seuls les tournois sont couverts ; côté Dota 2, les matchs ne
sont pas reliés à leurs tournois, faute d'identifiant commun.

## Licence

[MIT](LICENSE). Les données restent soumises aux licences de leurs sources —
notamment CC-BY-SA 3.0 pour Liquipedia, qui impose l'attribution.
