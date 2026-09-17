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
- **BALLDONTLIE** — nécessite une clé gratuite, non testée à ce stade.
- **GRID Open Access** — demande déposée le 2026-09-17, réponse en attente.
  **Purement additif** : l'interface de source doit permettre de le brancher
  plus tard sans rien retoucher. Ne pas attendre cette réponse pour avancer.

## État actuel

Structure et documentation posées. **Aucun code écrit à ce stade.**

Prochaine étape : socle Python (`uv`), puis ingestion OpenDota — la source
la plus riche immédiatement exploitable. BALLDONTLIE publie une spécification
OpenAPI (https://www.balldontlie.io/openapi/cs.yml) utile pour générer le
client. Liquipedia MediaWiki est ouverte mais renvoie du wikitexte à parser.

Environnement : Windows, PowerShell. Python restait à installer au moment
d'écrire ces lignes ; vérifier avant de lancer quoi que ce soit.
