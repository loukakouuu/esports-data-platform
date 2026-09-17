# Instantanés de test

Réponses réelles, capturées puis réduites, rejouées par les tests. La CI ne
touche jamais le réseau : ce qui n'est pas ici n'est pas testé.

## `opendota_pro_matches_p{1,2,3}.json`

- **Origine** : `GET https://api.opendota.com/api/proMatches`, capturé le
  2026-09-17 avec le `User-Agent` du projet.
- **Contenu** : 14 matchs sur les 200 renvoyés, découpés en trois pages qui
  s'enchaînent strictement par identifiant décroissant. La troisième est
  incomplète : c'est ainsi que l'API signale le bas du flux.
- **Choix des enregistrements** : la fenêtre a été prise autour de matchs
  portant des valeurs nulles (`radiant_team_id`, `radiant_name`, `series_id`,
  `series_type`). Un jeu d'essai trop propre ne prouve rien.
- **Données** : identifiants de matchs, d'équipes et de tournois publics.
  Aucune donnée personnelle, aucune clé d'API.
