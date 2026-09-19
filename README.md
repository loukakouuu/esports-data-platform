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

Cette correspondance est **une décision, pas une donnée**. Elle vit donc dans un
fichier qu'on peut discuter ligne à ligne, plutôt que dans du SQL enfoui au
milieu d'un modèle. Et un test refuse le silence : si Liquipedia ajoute demain
un tier que la graine ignore, le build échoue, au lieu de laisser les tournois
concernés glisser hors des analyses avec un rang nul.

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
| `raw.liquipedia_tournaments` | Tournois Counter-Strike et Dota 2, une ligne par page de wiki |
| `staging.stg_*` | Brut renommé et typé, sans interprétation |
| `marts.dim_tournament` | Un tournoi par ligne, toutes disciplines, tier ramené à un rang commun |
| `marts.fct_match` | Un match par ligne, vocabulaire neutre entre disciplines |
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
- [x] Ingestion Liquipedia — tournois Counter-Strike et Dota 2
- [x] Modèle commun inter-disciplines (dbt)
- [ ] Demande GRID Open Access (CS2 et Dota 2, données officielles)
- [ ] Ingestion BALLDONTLIE — référentiel seulement, les matchs étant payants
- [ ] Rapprochement des tournois OpenDota et Liquipedia
- [ ] Analyses exploratoires
- [ ] Modèle de prédiction et backtest

Ce qui n'est pas encore fait : les tournois et les matchs ne se parlent pas
encore. OpenDota et Liquipedia n'ont aucun identifiant commun, et seul le nom
permettrait de les rapprocher — `fct_match` garde donc l'identifiant de ligue
de sa source, sans jointure vers `dim_tournament`. Côté matchs, seul Dota 2 est
couvert : Counter-Strike n'a que ses tournois. Les notebooks sont vides.

## Licence

[MIT](LICENSE). Les données restent soumises aux licences de leurs sources —
notamment CC-BY-SA 3.0 pour Liquipedia, qui impose l'attribution.
