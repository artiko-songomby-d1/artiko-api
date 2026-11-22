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

Correctifs suivants, même jour : 2, 4, 5, 6, 7, 10 à 15 et 17 corrigés et retirés de cette liste. Suite
complète lancée sur l'ensemble (Postgres et disque locaux) : 266 tests réussis, 2 ignorés. Non vérifié en
production : bucket, pooler Neon, Render en non-root, vrai téléphone, Safari iOS.

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
- [ ] **3. Rate limit inefficace derrière Render** — *corrigé, hypothèse à confirmer.*
  - Fait : `render.yaml` déclare `CLIENT_IP_HEADER=CF-Connecting-IP`, `PROOF_URL_SECRET`, `NONCE_SECRET`,
    `ATTESTATION_PROVIDER` et `ALLOW_IMPORTED_PROOFS_FOR_BADGE`. Une valeur d'en-tête qui n'est pas une IP
    retombe sur l'IP du socket (`rate_limit.py`, `client_key`).
  - Reste : que Render place Cloudflare devant chaque service, et que Cloudflare réécrive toujours
    `CF-Connecting-IP`, n'est pas documenté par Render (non vérifié, seulement des posts de la communauté).
    → Confirmer en prod, par exemple en envoyant un `CF-Connecting-IP` forgé et en vérifiant qu'il n'est
    pas pris comme clé.
- [ ] **8. Vote sans email vérifié** : `CurrentUser` ne lit aucun claim `email_verified` (`auth.py:89`) ;
  avec l'inscription libre côté web, comptes jetables = votes multiples.
  → En cours : PR #18 (`feat/verified-email-tokens`, ouverte, non mergée) refuse les jetons dont le claim
  `emailVerified` n'est pas `true`. Elle part d'un ancien `develop` : à rebaser, conflit attendu avec le
  point 22 dans `auth.py`.
- [ ] **9. Rôle `reviewer` lu depuis le JWT** (`auth.py:69`) alors que le web n'active pas de plugin de
  rôles Better Auth (non vérifié) : soit le rôle est inatteignable, soit il faut garantir qu'aucun
  utilisateur ne peut l'écrire lui-même.

## 🟠 Bugs

- [ ] **16. Les cas de revue n'ont aucun endpoint** : `ReviewCase` est créé (`review/service.py:9`) mais
  aucun routeur `review` n'est monté (`main.py:23`). Une œuvre signalée « possible copy » reste bloquée,
  sans recours.

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

La fin du 1 (intégrité du concours) et la confirmation du 3 en prod. 8 et 9 demandent une décision côté web
(comptes à e-mail vérifié, rôle relecteur) ; 16 dépend de 9.
