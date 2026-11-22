# Audit artiko-api : correctifs à faire

Audit en lecture seule du 2026-11-22. Code lu sur `main` (`43f13ab`) ; `develop` (`17e8e2a`) ne diffère que
par la CI, le README et `Taskfile.yml`, `app/` est identique. Chaque point cite `fichier:ligne`.
Non exécuté : `uv` absent de la session d'audit, donc ni `pytest` ni `ruff` ni `pip-audit` n'ont été lancés.

Points forts déjà en place : erreurs centralisées sans fuite de stack (`app/platform/errors.py:79`), uploads
vérifiés par magic bytes (`storage.py:57`), noms de fichiers en SHA-256 filtrés par regex (`storage.py:81`),
CSP `default-src 'none'` + `nosniff` sur `/media`, liens privés signés HMAC à durée courte, rate limit
par client + global, test d'architecture, gitleaks en CI (sur `develop`).

Relecture des correctifs le 2026-11-22 sur `develop` (`af790c5`, PR #24 et #25) : 18 à 24 corrigés, 1 et 3
partiellement, les autres inchangés. Relecture du diff seulement : `pytest` non lancé (`uv` absent).

## 🔴 Sécurité / intégrité (priorité haute)

- [ ] **1. Le badge « human proven » est gratuit** — *partiellement corrigé.*
  - Fait : une preuve ne compte que si elle diffère de l'œuvre (fichier principal, GLB détaillé, poster)
    et des autres preuves, par SHA-256 et, pour les images, par pHash (`works/service.py`,
    `_distinct_evidence`).
  - Reste : `ALLOW_IMPORTED_PROOFS_FOR_BADGE` vaut toujours `true` par défaut (`config.py`) et
    `render.yaml` le force à `"true"` en prod. Deux images importées quelconques mais différentes donnent
    encore le badge, donc le droit de vote (`votes/service.py:23`). Les preuves non-image (vidéo…) ne
    sont comparées que par SHA-256 : modifier un octet suffit. → Passer à `false` en prod dès que la
    capture signée de l'app Android est disponible.
- [ ] **2. Vérification de l'attestation Android incomplète** (`devices/attestation.py:68`) :
  - aucune vérification que les certificats intermédiaires sont des CA (`BasicConstraints ca=True`,
    `keyCertSign`) : un attaquant qui possède une clé attestée légitime (son propre téléphone) pourrait
    s'en servir comme « CA » et signer un faux leaf avec n'importe quel niveau / challenge
    (plausible, à prouver par un test) ;
  - pas de contrôle de la liste de révocation Google des clés d'attestation ;
  - la présence de l'extension d'attestation sur un autre certificat que le leaf n'est pas refusée.
- [ ] **3. Rate limit inefficace derrière Render** — *corrigé, hypothèse à confirmer.*
  - Fait : `render.yaml` déclare `CLIENT_IP_HEADER=CF-Connecting-IP`, `PROOF_URL_SECRET`, `NONCE_SECRET`,
    `ATTESTATION_PROVIDER` et `ALLOW_IMPORTED_PROOFS_FOR_BADGE`. Une valeur d'en-tête qui n'est pas une IP
    retombe sur l'IP du socket (`rate_limit.py`, `client_key`).
  - Reste : que Render place Cloudflare devant chaque service, et que Cloudflare réécrive toujours
    `CF-Connecting-IP`, n'est pas documenté par Render (non vérifié, seulement des posts de la communauté).
    → Confirmer en prod, par exemple en envoyant un `CF-Connecting-IP` forgé et en vérifiant qu'il n'est
    pas pris comme clé.
- [ ] **4. Endpoints coûteux sans rate limit** : `POST /works` (jusqu'à 6 × 20 Mo en mémoire + CLIP),
  `POST /votes`, `PUT /me/artist`, `POST /expositions`. Chaque `POST /works` refusé pour copie crée en
  plus un `ReviewCase` en base (`integrity/service.py:65`) → la table peut être spammée.
- [ ] **5. Bombe de décompression d'image.** Aucune limite `Image.MAX_IMAGE_PIXELS` ni contrôle des
  dimensions (`storage.py:43`, `embed.py:30`, `phash.py:13`). Un PNG de 5 Mo peut décoder ~89 M pixels
  (≈ 270 Mo en RGB) : OOM probable sur l'instance Render free. → Plafonner les pixels avant
  `convert()` / `resize()`.
- [ ] **6. Format d'image non vérifié contre le type déclaré** : `is_image` accepte tout ce que Pillow
  sait ouvrir (GIF, BMP, TIFF…) puis le stocke en `.png` / `.jpg` (`storage.py:67`). → Comparer
  `Image.format` au content-type.
- [ ] **7. Rejeu des captures signées** : le nonce est optionnel dans le message signé
  (`devices/signatures.py:13`), jamais revalidé ni marqué utilisé ; la même preuve signée peut servir
  à plusieurs œuvres pendant 10 min.
- [ ] **8. Vote sans email vérifié** : `CurrentUser` ne lit aucun claim `email_verified` (`auth.py:89`) ;
  avec l'inscription libre côté web, comptes jetables = votes multiples.
- [ ] **9. Rôle `reviewer` lu depuis le JWT** (`auth.py:69`) alors que le web n'active pas de plugin de
  rôles Better Auth (non vérifié) : soit le rôle est inatteignable, soit il faut garantir qu'aucun
  utilisateur ne peut l'écrire lui-même.
- [ ] **10. CI** (`.github/workflows/ci.yml`) : `push` seulement sur `main` (pas `develop`) ; les jobs
  `test` et `docker` n'ont pas de bloc `permissions:` ; actions épinglées par tag et non par SHA.
- [ ] **11. Dockerfile** : tourne en root, image de base non épinglée par digest ; `alembic upgrade head`
  au démarrage de chaque instance (course si plusieurs instances).

## 🟠 Bugs

- [ ] **12. Appels bloquants dans l'event loop** : `storage.save` / `storage.load` (boto3 synchrone) sont
  appelés depuis des `async def` (`works/service.py:81`, `:101`, `works/router.py:114`), de même que
  `kind_of` (décodage Pillow) et la recherche numpy de l'index. Un upload S3 lent fige toute l'API.
  → `run_in_threadpool` ou client S3 asynchrone.
- [ ] **13. Pas de requêtes `Range` sur `/media`** (`media/router.py:10`) : le fichier entier est chargé
  en mémoire et renvoyé en 200. Safari iOS exige des réponses 206 pour lire une vidéo MP4 → vidéos
  probablement illisibles sur iPhone (à confirmer). → Redirection vers une URL S3 présignée, ou réponse
  streamée avec support des ranges.
- [ ] **14. Fichiers orphelins** : l'œuvre et ses preuves sont écrites dans le stockage avant le `commit`
  (`works/service.py:81`, `:101`) ; un `IntegrityError` laisse les fichiers.
- [ ] **15. Course sur `upsert_artist_of_user`** (`artists/service.py:28`) : deux premiers `PUT` simultanés
  → `IntegrityError` sur `user_id` unique → 500 au lieu d'un upsert.
- [ ] **16. Les cas de revue n'ont aucun endpoint** : `ReviewCase` est créé (`review/service.py:9`) mais
  aucun routeur `review` n'est monté (`main.py:23`). Une œuvre signalée « possible copy » reste bloquée,
  sans recours.
- [ ] **17. Index de similarité jamais purgé** (`scan/index.py:52`) : il ne fait qu'ajouter, et chaque
  processus garde sa propre copie (incohérence entre instances).

## 🟡 Architecture / dette

- [x] **18. Détection pHash en scan complet** : PostgreSQL calcule la distance exacte et renvoie
  les meilleures correspondances par groupe. Quatre index filtrent les candidats pour les seuils
  jusqu'à 11 bits, dont le seuil par défaut de 8. Les seuils supérieurs gardent le calcul SQL exact.
  `bit_count` exige PostgreSQL 14 ou plus (version Neon non vérifiée).
- [x] **19. `refresh` de l'index à chaque scan** : délai par processus configurable via
  `SCAN_INDEX_REFRESH_SECONDS` (15 s par défaut). Une publication invalide l'index local après validation
  de la transaction. Le contrôle des doublons continue d'actualiser l'index avant la comparaison.
  L'invalidation reste locale : les autres instances voient une nouvelle œuvre au plus 15 s plus tard.
- [x] **20. Listes non paginées** : `GET /expositions` et `GET /artists/{id}/museum` utilisent
  `limit` et `offset`. Le musée charge les preuves et calcule la salle pour les œuvres de la page.
- [x] **21. CORS `allow_credentials=True`** : option retirée. Les origines configurées peuvent
  toujours envoyer l'en-tête `Authorization` pour l'authentification bearer.
- [x] **22. Détail de l'erreur JWT renvoyé au client** : réponse 401 générique. Le type d'erreur est
  journalisé au niveau debug sans le jeton, les claims ou le détail fourni par le client.
- [x] **23. Rate limit en mémoire par processus** : les instances partagent les compteurs lorsque
  `RATE_LIMIT_REDIS_URL` est configurée. Sans cette variable, les compteurs restent locaux au processus.
  Redis configuré mais injoignable : les endpoints limités répondent 503 (choix fail-closed).
  `RATE_LIMIT_REDIS_URL` n'est pas dans `render.yaml` : compteurs locaux en prod (une seule instance).
- [x] **24. Dépendances déclarées mais non importées** : retrait de `celery`, `sse-starlette`, `pyacoustid`
  et `pgvector` de `pyproject.toml`, avec régénération de `uv.lock`. `redis` est conservé : le point 23
  l'utilise pour les compteurs partagés.

## Ordre suggéré

5, 12 et 13 d'abord (disponibilité en prod), puis la fin du 1 (intégrité du concours), puis 2, 4 et 16.
