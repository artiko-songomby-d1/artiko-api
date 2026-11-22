# Audit artiko-api : correctifs à faire

Audit en lecture seule du 2026-11-22. Code lu sur `main` (`43f13ab`) ; `develop` (`17e8e2a`) ne diffère que
par la CI, le README et `Taskfile.yml`, `app/` est identique. Chaque point cite `fichier:ligne`.
Non exécuté : `uv` absent de la session d'audit, donc ni `pytest` ni `ruff` ni `pip-audit` n'ont été lancés.

Points forts déjà en place : erreurs centralisées sans fuite de stack (`app/platform/errors.py:79`), uploads
vérifiés par magic bytes (`storage.py:57`), noms de fichiers en SHA-256 filtrés par regex (`storage.py:81`),
CSP `default-src 'none'` + `nosniff` sur `/media`, liens privés signés HMAC à durée courte, rate limit
par client + global, test d'architecture, gitleaks en CI (sur `develop`).

## 🔴 Sécurité / intégrité (priorité haute)

- [ ] **1. Le badge « human proven » est gratuit.** `ALLOW_IMPORTED_PROOFS_FOR_BADGE=true` par défaut
  (`config.py:64`, `works/service.py:131`) : deux fichiers importés quelconques suffisent, sans contrôle
  qu'ils diffèrent entre eux ni du fichier de l'œuvre. Or le badge conditionne le vote
  (`votes/service.py:23`) et le web envoie toujours des preuves importées. → Désactiver en prod, ou au
  minimum exiger des preuves distinctes (SHA-256 / pHash) et différentes de l'œuvre.
- [ ] **2. Vérification de l'attestation Android incomplète** (`devices/attestation.py:68`) :
  - aucune vérification que les certificats intermédiaires sont des CA (`BasicConstraints ca=True`,
    `keyCertSign`) : un attaquant qui possède une clé attestée légitime (son propre téléphone) pourrait
    s'en servir comme « CA » et signer un faux leaf avec n'importe quel niveau / challenge
    (plausible, à prouver par un test) ;
  - pas de contrôle de la liste de révocation Google des clés d'attestation ;
  - la présence de l'extension d'attestation sur un autre certificat que le leaf n'est pas refusée.
- [ ] **3. Rate limit inefficace derrière Render.** `render.yaml` ne déclare pas `CLIENT_IP_HEADER`
  (ni `PROOF_URL_SECRET`, `NONCE_SECRET`, `ATTESTATION_PROVIDER`, `ALLOW_IMPORTED_PROOFS_FOR_BADGE`).
  Sans en-tête, `client_key` (`rate_limit.py:78`) prend l'IP du proxy Render : tous les visiteurs
  partagent le même compteur (20 scans/min pour tout le monde). → Identifier l'en-tête fiable fourni par
  Render (non vérifié) et compléter `render.yaml`.
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

- [ ] **18. Détection pHash en scan complet** : chaque publication charge le pHash de toutes les œuvres
  et compare en Python (`integrity/service.py:49`). → Requête SQL / index dès que le volume grandit,
  comme prévu pour CLIP (`scan/index.py:3`).
- [ ] **19. `refresh` de l'index à chaque scan** (`scan/service.py:27`) : 2 requêtes DB par scan sous
  verrou global ; un TTL suffirait.
- [ ] **20. Listes non paginées** : `GET /expositions` et `GET /artists/{id}/museum` renvoient tout, avec
  les preuves.
- [ ] **21. CORS `allow_credentials=True`** (`main.py:32`) alors que l'API n'utilise que des bearer
  tokens : inutile, à retirer.
- [ ] **22. Détail de l'erreur JWT renvoyé au client** (`auth.py:88`, `f"Invalid token: {e}"`) : à garder
  dans les logs.
- [ ] **23. Rate limit en mémoire par processus** (`rate_limit.py:3`, assumé) : partager les compteurs
  (Redis) si plusieurs instances.
- [ ] **24. Dépendances déclarées mais non importées** : `celery`, `redis`, `sse-starlette`, `pyacoustid`
  et `pgvector` n'apparaissent dans aucun import de `app/` ni `scripts/` (`grep`). Les retirer allège
  l'image et la surface d'attaque.

## Ordre suggéré

1, 3, 5, 12 et 13 d'abord (intégrité du concours, disponibilité en prod), puis 2, 4 et 16.
