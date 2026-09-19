# Contexte du projet

Ce fichier porte le contexte d'une session à l'autre. À lire avant d'intervenir.

## Ce qu'est ce projet

Une chaîne de données sur l'esport professionnel, couvrant les trois métiers :
ingénierie (pipeline), analyse (modèles et exploration), science (prédiction).

**C'est un projet de portfolio, et l'objectif de carrière en découle.** Son
auteur est alternant data analyst en master EISI dev, et vise data scientist ou
data engineer. Chaque décision technique doit servir ce signal : la couche
engineering est celle qui manque le plus à son profil, c'est donc celle qui doit
être la plus soignée.

Il est également l'auteur d'[esport-manager](https://github.com/loukakouuu/esport-manager),
un jeu de gestion esport en Godot. **Les deux projets sont indépendants** — pas
de dépendance, pas de code partagé. Le rapprochement est narratif uniquement.

## Décisions déjà prises

Ne pas les rouvrir sans raison ; elles ont été arbitrées.

- **Un seul dépôt**, pas un par couche. Une chaîne complète raconte mieux qu'un
  empilement de notebooks.
- **Multi-jeux**, pas mono-discipline. Le noyau ne connaît qu'une interface de
  source ; ajouter un fournisseur ou une discipline ne change rien au reste.
- **Trois sources hétérogènes** (Liquipedia MediaWiki, OpenDota, BALLDONTLIE) choisies
  pour leurs différences — schémas, auth, quotas, fraîcheurs. Les réconcilier
  est l'intérêt technique du projet, pas un obstacle à contourner.
- **Public depuis le premier commit.** L'historique fait partie de la
  démonstration : il doit rester propre et lisible.
- **Licence MIT.**

## Règles non négociables

- **Aucune récolte sur une source qui l'interdit.** HLTV répond 403 aux clients
  automatisés : pas de contournement, pas de faux user-agent, pas de scraping
  déguisé. Les instantanés manuels sont datés et signalés comme tels.
- **Liquipedia — API MediaWiki, accès libre, aucune demande à déposer.**
  Respecter 1 req/2 s, et 1 req/30 s pour `action=parse`. Supporter gzip.
  Envoyer un `User-Agent` descriptif incluant un contact — un agent générique
  comme `python-requests` se fait bloquer. **Ce contact ne doit pas être en dur
  dans le dépôt** : il vit dans `.env` (`LIQUIPEDIA_USER_AGENT`). Attribuer en
  CC-BY-SA 3.0. L'API LiquipediaDB, elle, reste sur demande approuvée et donne
  des données structurées plutôt que du wikitexte : piste à garder en réserve.
- **Aucune donnée personnelle** dans le dépôt, les README ou les commits.
  L'adresse de commit est `221532583+loukakouuu@users.noreply.github.com` —
  vérifier `git config user.email` avant de committer.
- **Aucune clé d'API committée.** Variables d'environnement, `.env` ignoré,
  `.env.example` documenté.
- **Pas de mention d'assistance IA** dans les messages de commit.

## Attentes de qualité

Le dépôt est une vitrine : du code jamais exécuté n'y a pas sa place.

- Toute ingestion est **incrémentale et idempotente** — la relancer ne duplique
  rien et reprend où elle s'est arrêtée.
- Les quotas se respectent par construction : backoff, cache, suivi d'état.
- Les tests de données comptent autant que les tests de code.
- La CI doit être verte.
- Le README reflète l'état réel, y compris ce qui n'est pas fait.

## Sources vérifiées le 2026-09-17

Testées en direct, inutile de refaire ces vérifications :

- **OpenDota** — `GET https://api.opendota.com/api/proMatches` répond 200 **sans
  aucune clé**, 100 matchs par page : `match_id`, `start_time`, ids et noms
  d'équipes, `leagueid`/`league_name`, `series_id`, scores, `radiant_win`.
  Pagination par curseur descendant (`less_than_match_id`) — idéale pour une
  ingestion incrémentale : on garde le plus petit `match_id` vu.
- **Liquipedia MediaWiki** — `GET https://liquipedia.net/counterstrike/api.php`
  répond 200 avec le `User-Agent`
  `esports-data-platform/0.1 (https://github.com/loukakouuu/esports-data-platform)`
  et `--compressed`. Renvoie un jeton `continue.cmcontinue` pour la pagination.
  Le contenu des pages est du **wikitexte à parser** — prévoir cet effort.
- **BALLDONTLIE** — **les matchs sont derrière le palier payant GOAT.** Vérifié
  dans la spécification OpenAPI le 2026-09-17 : `/cs/v1/matches`,
  `/cs/v1/match_maps` et toutes les statistiques l'exigent ; `rankings` et
  `team_map_pool` demandent le palier ALL-STAR. Une clé gratuite ne donne que
  `teams`, `players`, `tournaments` et `tournament_teams`. Sans clé, tout
  répond `401 Unauthorized` en texte brut, y compris les points d'accès
  gratuits. Pagination par jeton `cursor` entier rendu dans `meta.next_cursor`,
  `per_page` plafonné à 100, clé dans l'en-tête `Authorization`.
  **Ne pas replanifier cette source pour les matchs sans décider d'abord de payer.**
- **GRID Open Access** — demande déposée le 2026-09-17, réponse en attente.
  **Purement additif** : l'interface de source doit permettre de le brancher
  plus tard sans rien retoucher. Ne pas attendre cette réponse pour avancer.

## État actuel

Deux sources, trois flux, et une couche de transformation : la chaîne va du
réseau aux tables d'analyse.

- **Socle** : `uv`, ruff, mypy strict, pytest, CI GitHub Actions. Tout est vert.
- **Noyau** (`ingestion/core/`) : contrat de source, client HTTP cadencé et
  réessayant, entrepôt DuckDB, boucle incrémentale, tests de données.
- **Sources** : `opendota.py` (matchs Dota 2) et `liquipedia.py` (tournois
  Counter-Strike et Dota 2), plus `wikitext.py` pour le découpage MediaWiki.
- **Ligne de commande** : `esports-ingest run|state|check|sources`.

Un flux se nomme `fournisseur.discipline.ressource` — les trois comptent, sans
quoi les deux wikis Liquipedia se confondraient.

Le contrat de source a tenu l'épreuve d'une source qui ne lui ressemble pas.
Trois ajustements, aucun démenti :

- clés composites, un identifiant de page n'étant unique qu'au sein d'un wiki ;
- la discipline entre dans l'identité d'un flux ;
- l'agrégation d'un parcours reçoit la page, le jeton de continuation ne vivant
  pas dans les lignes.

Deux parcours coexistent désormais : `DescendingIdSource` (identifiant
décroissant, OpenDota) et `TokenScanSource` (balayage par jeton, Liquipedia).
Le lanceur, l'entrepôt et la ligne de commande ignorent la différence.

Vérifié en conditions réelles : 400 matchs et 250 tournois collectés, aucun
doublon, un rattrapage qui s'arrête après une page, un backfill qui reprend
sous la frontière, un balayage repris au jeton.

**Transformation** (`transform/`, dbt-duckdb) : staging qui renomme sans
interpréter, puis `marts.dim_tournament` et `marts.fct_match`. Les échelles de
tiers se rejoignent via la graine `tournament_tier`, et le test
`assert_tiers_tous_traduits` fait échouer le build si un tier apparaît sans
traduction. Deux tests génériques maison (`unique_combination`,
`accepted_range`) évitent la dépendance `dbt_utils`. `dbt build` rend 41 nœuds
au vert, et la suite pytest le rejoue sur un entrepôt bâti depuis les
instantanés — donc en CI, sans réseau.

Pistes suivantes, par intérêt décroissant :

1. **Rapprocher les tournois entre sources.** OpenDota et Liquipedia n'ont
   aucun identifiant commun ; seul le nom permettrait de les relier
   (`league_name` contre `tournament_name`). C'est le vrai chaînon manquant
   entre `fct_match` et `dim_tournament`, et un exercice de rapprochement
   approximatif honnête — avec un taux de correspondance mesuré, pas promis.
2. **Matchs Counter-Strike.** Ni BALLDONTLIE gratuit ni HLTV ne les donnent ;
   seule Liquipedia les porte, dans des pages de match à parser.
3. **Analyses puis prédiction**, une fois la matière assemblée.

L'entrepôt local ne contient qu'un échantillon (250 tournois sur ~25 000, 400
matchs). Une collecte complète tiendrait en une trentaine de minutes à la
cadence imposée — à lancer quand les analyses en auront besoin.

## Ce que l'environnement a appris

- **Windows, PowerShell.** Python 3.12.10 et uv 0.12.15 installés.
- **Encodage** : la sortie standard est en cp1252. Un script qui affiche `→` ou
  `✓` lève `UnicodeEncodeError` ; la ligne de commande s'en tient à l'ASCII pour
  ses marqueurs. Toujours passer `encoding="utf-8"` en lisant ou écrivant un
  fichier.
- **`pytz` est une dépendance réelle** : sans elle, DuckDB échoue à rendre un
  `TIMESTAMPTZ` en `datetime` Python.
- **Liquipedia : `rvsection=0` change tout.** `action=query&prop=revisions` avec
  `rvsection=0` ne rapporte que la section d'en-tête — celle qui porte
  l'infobox — et accepte 50 titres par requête : 1,5 ko au lieu de 123 ko,
  mesuré sur des pages réelles. `action=parse` donnerait un texte mieux découpé
  mais reste limité à une requête toutes les 30 s, inutilisable à cette échelle.
  Catégorie `Tournaments` : 19 561 pages côté counterstrike, 5 694 côté dota2,
  `cmlimit=500` accepté, des brouillons d'utilisateurs à écarter par
  `cmnamespace=0`. Les deux wikis partagent le même modèle `Infobox league`.
- **MediaWiki répond 200 même quand il refuse** : l'erreur est dans le corps,
  sous la clé `error`. Un code HTTP ne suffit pas à valider une réponse.
- **Lier un paramètre coûte environ 1 ms** sur cette machine (mesuré identique
  sur DuckDB 1.1, 1.3, 1.4 et 1.5 — ce n'est pas une régression). Une page de
  100 matchs met donc ~2 s à s'écrire, contre 7 ms en SQL littéral. Le code
  garde l'écriture paramétrée, qui est la bonne : ni injection, ni quoting à la
  main. Si le volume devient gênant, la sortie est le passage par Arrow, pas la
  construction de SQL à la ficelle.
