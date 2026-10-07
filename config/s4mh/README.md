Configuration de s4mh (montée en lecture seule dans le conteneur).

Pour personnaliser les niches, seuils, etc. :

    docker compose run --rm --no-deps --entrypoint cat s4mh config.example.yaml > config/s4mh/config.yaml

puis modifier `config/s4mh/config.yaml` et `docker compose restart s4mh`.
Sans ce fichier, s4mh utilise sa configuration d'exemple.
