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
| [Liquipedia](https://liquipedia.net/api-terms-of-use) | Tous titres | Accès sur demande · CC-BY-SA 3.0 · 60 req/h · `User-Agent` identifiant |
| [OpenDota](https://www.opendota.com/api-keys) | Dota 2 | 50 000 appels/mois · 60 req/min |
| [BALLDONTLIE](https://cs.balldontlie.io/) | Counter-Strike 2 | Clé gratuite |

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
ingestion/          Collecte par source, incrémentale et idempotente
  sources/          Une implémentation par source, derrière une interface commune
transform/          Modèles dbt : brut → normalisé → tables d'analyse
analysis/           Notebooks d'exploration
models/             Modélisation prédictive et backtests
warehouse/          DuckDB
tests/              Tests de code et tests de données
```

Le noyau ne connaît qu'une interface de source. Ajouter une discipline ou un
fournisseur ne doit rien changer au reste — même principe que l'abstraction par
discipline d'[esport-manager](https://github.com/loukakouuu/esport-manager).

## Avancement

- [x] Cadrage, choix des sources, structure
- [ ] Demandes d'accès (Liquipedia, GRID, clé production Riot)
- [ ] Socle Python et intégration continue
- [ ] Ingestion OpenDota
- [ ] Ingestion BALLDONTLIE
- [ ] Ingestion Liquipedia
- [ ] Modèle commun inter-disciplines (dbt)
- [ ] Tests de données
- [ ] Analyses exploratoires
- [ ] Modèle de prédiction et backtest

## Licence

[MIT](LICENSE). Les données restent soumises aux licences de leurs sources —
notamment CC-BY-SA 3.0 pour Liquipedia, qui impose l'attribution.
