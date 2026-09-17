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
| [BALLDONTLIE](https://cs.balldontlie.io/) | Counter-Strike 2 | Clé gratuite · [spécification OpenAPI](https://www.balldontlie.io/openapi/cs.yml) |

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
  cli.py            Ligne de commande esports-ingest
transform/          Modèles dbt : brut → normalisé → tables d'analyse (à venir)
analysis/           Notebooks d'exploration (à venir)
models/             Modélisation prédictive et backtests (à venir)
warehouse/          Entrepôt DuckDB (hors dépôt)
tests/              Tests de code et tests de données
```

Le noyau ne connaît qu'une interface de source. Ajouter une discipline ou un
fournisseur ne doit rien changer au reste — même principe que l'abstraction par
discipline d'[esport-manager](https://github.com/loukakouuu/esport-manager).

### Comment la collecte reprend son travail

Chaque flux garde deux bornes : le **sommet** de ce qui est connu, et la
**frontière basse** sous laquelle il reste à creuser. Entre les deux, l'intervalle
a été parcouru de façon contiguë.

- `catchup` repart du sommet et s'arrête dès qu'il retrouve du connu. Le quota
  ne sert qu'à collecter ce qui manque.
- `backfill` reprend sous la frontière pour descendre dans l'historique.

Chaque page est écrite avec l'état correspondant dans une seule transaction :
une coupure — quota, réseau, Ctrl-C — ne coûte au plus qu'une page. Et comme
l'écriture se fait par clé primaire, relancer une ingestion ne duplique rien.

## Mise en route

Prérequis : Python 3.12 et [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env     # aucune clé n'est nécessaire pour OpenDota
```

```bash
uv run esports-ingest sources                             # flux disponibles
uv run esports-ingest run opendota.pro_matches --pages 5  # collecter
uv run esports-ingest run opendota.pro_matches --mode backfill --pages 20
uv run esports-ingest state                               # où en est la collecte
uv run esports-ingest check                               # vérifier les données
```

L'entrepôt atterrit dans `warehouse/esports.duckdb` : `raw.opendota_pro_matches`
pour les matchs, `meta.ingestion_state` pour l'avancement, `meta.ingestion_runs`
pour l'historique des exécutions.

## Avancement

- [x] Cadrage, choix des sources, structure
- [x] Socle Python et intégration continue
- [x] Ingestion OpenDota — matchs professionnels Dota 2
- [x] Tests de données sur la couche brute
- [ ] Demande GRID Open Access (CS2 et Dota 2, données officielles)
- [ ] Ingestion BALLDONTLIE
- [ ] Ingestion Liquipedia
- [ ] Modèle commun inter-disciplines (dbt)
- [ ] Analyses exploratoires
- [ ] Modèle de prédiction et backtest

Ce qui n'est pas encore fait : une seule source est branchée, et rien ne
normalise encore les disciplines entre elles. La couche `transform/` est vide,
les notebooks aussi.

## Licence

[MIT](LICENSE). Les données restent soumises aux licences de leurs sources —
notamment CC-BY-SA 3.0 pour Liquipedia, qui impose l'attribution.
