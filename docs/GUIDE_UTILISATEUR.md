# CIVITAS-RAG — Guide d'utilisation complet
> Du zéro absolu à la production, avec des cas réels.

---

## Table des matières

1. [Prérequis et installation](#1-prérequis-et-installation)
2. [Cas 1 — L'équipe DevOps qui indexe son infrastructure](#2-cas-1--léquipe-devops-qui-indexe-son-infrastructure)
3. [Cas 2 — Ajout de nouveaux fichiers sans tout réingérer](#3-cas-2--ajout-de-nouveaux-fichiers-sans-tout-réingérer)
4. [Cas 3 — Un fichier a été modifié, resync automatique](#4-cas-3--un-fichier-a-été-modifié-resync-automatique)
5. [Cas 4 — Recherche sémantique avancée](#5-cas-4--recherche-sémantique-avancée)
6. [Cas 5 — Audit et vérification du système](#6-cas-5--audit-et-vérification-du-système)
7. [Cas 6 — Réorganiser ses collections (purge + réingestion)](#7-cas-6--réorganiser-ses-collections-purge--réingestion)
8. [Cas 7 — Export et reporting](#8-cas-7--export-et-reporting)
9. [Cas 8 — Intégration dans un pipeline CI/CD](#9-cas-8--intégration-dans-un-pipeline-cicd)
10. [Référence rapide des commandes](#10-référence-rapide-des-commandes)

---

## 1. Prérequis et installation

### Ce dont vous avez besoin

```
Python 3.10+
Docker (pour lancer Qdrant)
Git
```

### Étape 1 — Cloner le projet

```bash
git clone https://github.com/amourgit/CIVITAS-RAG.git
cd CIVITAS-RAG
```

### Étape 2 — Installer les dépendances Python

```bash
pip install -e ".[dev]"
# Ou manuellement :
pip install qdrant-client sentence-transformers scikit-learn \
            rich tqdm pyyaml python-dotenv chardet
```

### Étape 3 — Lancer Qdrant (base vectorielle)

```bash
# Via Docker (recommandé)
docker run -d \
  --name qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  -v $(pwd)/qdrant_storage:/qdrant/storage \
  qdrant/qdrant

# Vérifier que ça tourne
docker ps | grep qdrant
# → qdrant    qdrant/qdrant   "..."   Up X seconds
```

### Étape 4 — Tester la connexion

```bash
python scripts/qdrant_ingest.py ping
```

Résultat attendu :
```
══════════════════════════════════════════════════════════════════
  CIVITAS · Ping Qdrant
══════════════════════════════════════════════════════════════════
✓ Qdrant accessible · 0 collection(s)
```

### Étape 5 — Créer votre fichier .env (optionnel)

```bash
cat > .env << 'ENVEOF'
QDRANT_HOST=localhost
QDRANT_PORT=6333
# QDRANT_URL=https://xxxx.cloud.qdrant.io   # Qdrant Cloud
# QDRANT_API_KEY=votre-clé-cloud
CIVITAS_TRACKER_DB=.civitas_ingestion_tracker.db
# OPENAI_API_KEY=sk-...   # Si vous utilisez OpenAI pour les embeddings
ENVEOF
```

---

## 2. Cas 1 — L'équipe DevOps qui indexe son infrastructure

### Contexte

Vous avez une arborescence de fichiers IaC, configs SSH, pipelines CI/CD.
Vous voulez les rendre **cherchables sémantiquement** ("retrouver comment
on a configuré nginx", "quel playbook installe postgres", etc.).

### Structure de vos fichiers

```
data/documents/
├── ansible/
│   ├── webservers/nginx-install.yml
│   ├── databases/postgres-install.yml
│   ├── databases/postgres-backup.yml
│   └── monitoring/prometheus-deploy.yml
├── terraform/
│   ├── aws/prod/main.tf
│   └── modules/vpc/main.tf
├── cicd/
│   ├── jenkins/Jenkinsfile
│   ├── github/deploy.yml
│   └── docker/Dockerfile
├── iam/
│   └── aws/s3-policy.json
└── ssh/
    └── prod/ssh-config-prod.conf
```

### Étape 1 — Visualiser ce qui sera ingéré (sans rien toucher)

```bash
python scripts/qdrant_ingest.py tree --path data/documents
```

→ Vous voyez l'arborescence complète avec icônes, tailles,
  et le compte par extension. Rien n'est modifié.

### Étape 2 — Dry-run (simulation complète)

```bash
python scripts/qdrant_ingest.py \
  ingest \
  --path data/documents \
  --collection all_docs \
  --dry-run
```

Résultat :
```
CIVITAS INGESTION [DRY RUN] REPORT
  Collection : all_docs
  Discovered : 11 files
  ✓  New / Indexed    11
  ⬡  Chunks produced  11
  ✔  Success rate    100%
```
Zéro écrit dans Qdrant. Juste la simulation.

### Étape 3 — Ingestion réelle par domaine (recommandé)

Chaque domaine dans sa propre collection — plus facile à maintenir :

```bash
# Ansible
python scripts/qdrant_ingest.py ingest \
  --path data/documents/ansible \
  --collection ansible_docs \
  --domain devops \
  --tags ansible iac automation

# Terraform
python scripts/qdrant_ingest.py ingest \
  --path data/documents/terraform \
  --collection terraform_docs \
  --domain devops \
  --tags terraform iac cloud

# CI/CD
python scripts/qdrant_ingest.py ingest \
  --path data/documents/cicd \
  --collection cicd_docs \
  --domain devops \
  --tags cicd docker jenkins

# IAM / Sécurité
python scripts/qdrant_ingest.py ingest \
  --path data/documents/iam \
  --collection iam_docs \
  --domain security \
  --tags iam aws policies

# SSH
python scripts/qdrant_ingest.py ingest \
  --path data/documents/ssh \
  --collection ssh_docs \
  --domain infrastructure \
  --tags ssh security bastion
```

Résultat pour ansible :
```
CIVITAS INGESTION REPORT
  Collection : ansible_docs
  Source     : data/documents/ansible
  Duration   : 4.2s
  Discovered : 4 files

  ✓  New / Indexed               4
  ⬡  Chunks produced            12
  ⬡  Points in Qdrant           12
  ✔  Success rate             100%
```

### Étape 4 — Ou tout ingérer d'un coup via la config YAML

```bash
# Voir la config
python scripts/qdrant_ingest.py -c config/qdrant_ingestion.yaml config

# Ingérer tous les scans définis dans le YAML
python scripts/qdrant_ingest.py \
  -c config/qdrant_ingestion.yaml \
  ingest --all
```

→ Lance ansible_scan + terraform_scan + ssh_scan + iam_scan + cicd_scan
  en séquence, chacun dans sa collection.

### Étape 5 — Vérifier que tout est en place

```bash
python scripts/qdrant_ingest.py status
```

```
📊 Tracker SQLite
  Fichiers totaux  : 11
  Succès           : 11
  Chunks totaux    : 35
  Volume total     : 10.1 KB

📁 Collections trackées (5)
  ansible_docs       4 fichiers    12 chunks
  terraform_docs     2 fichiers     6 chunks
  cicd_docs          3 fichiers    10 chunks
  iam_docs           1 fichier      3 chunks
  ssh_docs           1 fichier      4 chunks

⬡  Qdrant
  Collections : 5   (ansible_docs, terraform_docs, cicd_docs, iam_docs, ssh_docs)
```

---

## 3. Cas 2 — Ajout de nouveaux fichiers sans tout réingérer

### Contexte

Vous avez ajouté de nouveaux playbooks Ansible cette semaine.
Vous voulez les indexer **sans retoucher** les fichiers déjà ingérés.

### Ce qui se passe si vous relancez naïvement

```bash
python scripts/qdrant_ingest.py ingest \
  --path data/documents/ansible \
  --collection ansible_docs
```

```
CIVITAS INGESTION REPORT
  ✓  New / Indexed               2   ← seulement les 2 nouveaux fichiers
  ⏭  Skipped (unchanged)         4   ← les 4 anciens ignorés automatiquement
  ✗  Failed                      0
  ⬡  Chunks produced             6   ← chunks des nouveaux seulement
```

**La déduplication est automatique.** Le système compare le SHA-256
de chaque fichier. Aucun doublon possible dans Qdrant.

### Voir ce qui sera traité avant de lancer

```bash
python scripts/qdrant_ingest.py diff \
  --path data/documents/ansible \
  --collection ansible_docs
```

```
CIVITAS · Diff : data/documents/ansible → ansible_docs

  Fichiers sur disque : 6
  Fichiers trackés   : 4

  ✚  Nouveaux     : 2      ← seront ingérés
  ↻  Modifiés     : 0
  ✗  En erreur    : 0
  ✘  Supprimés    : 0
  ⏭  Inchangés    : 4      ← seront skippés

✚ Nouveaux (seront ingérés)
  · roles/common/tasks/main.yml
  · roles/docker/tasks/main.yml
```

### Lancer l'ingestion des nouveaux seulement

```bash
python scripts/qdrant_ingest.py ingest \
  --path data/documents/ansible \
  --collection ansible_docs \
  --domain devops --tags ansible
```

C'est tout. Les fichiers existants sont détectés comme inchangés et skippés.

---

## 4. Cas 3 — Un fichier a été modifié, resync automatique

### Contexte

Vous avez mis à jour votre `postgres-install.yml` avec de nouvelles tasks.
Il faut que Qdrant reflète la version actuelle.

### Le système détecte automatiquement les modifications

```bash
# Simuler une modification
echo "\n    - name: Configure pg_hba.conf\n      template: src=pg_hba.j2 dest=/etc/postgresql/pg_hba.conf" \
  >> data/documents/ansible/databases/postgres-install.yml

# Voir ce qui a changé
python scripts/qdrant_ingest.py diff \
  --path data/documents/ansible \
  --collection ansible_docs
```

```
  ✚  Nouveaux     : 0
  ↻  Modifiés     : 1      ← postgres-install.yml détecté comme modifié
  ⏭  Inchangés    : 3

↻ Modifiés (seront réingérés)
  · databases/postgres-install.yml
```

### Lancer le resync

```bash
python scripts/qdrant_ingest.py ingest \
  --path data/documents/ansible \
  --collection ansible_docs
```

```
CIVITAS INGESTION REPORT
  ↻  Modified / Re-indexed    1   ← ancien contenu supprimé de Qdrant,
  ⏭  Skipped (unchanged)      3      nouveau contenu indexé à la place
```

Le système a automatiquement :
1. Supprimé les anciens points Qdrant de ce fichier
2. Rechunké + réembeddé le nouveau contenu
3. Inséré les nouveaux points
4. Mis à jour le tracker SQLite

### Forcer la réingestion d'UN fichier spécifique

Si vous voulez forcer sans modifier le fichier :

```bash
# Supprimer ce fichier du tracker → sera vu comme "nouveau" au prochain ingest
python scripts/qdrant_ingest.py delete-file \
  --file data/documents/ansible/databases/postgres-install.yml \
  --collection ansible_docs \
  --yes

# Relancer
python scripts/qdrant_ingest.py ingest \
  --path data/documents/ansible \
  --collection ansible_docs
```

---

## 5. Cas 4 — Recherche sémantique avancée

### Recherche de base

```bash
python scripts/qdrant_ingest.py search \
  --query "comment installer postgresql sur ubuntu" \
  --collection ansible_docs \
  --top-k 5
```

```
CIVITAS · Recherche : « comment installer postgresql sur ubuntu »

   1.  0.8934  ████████████████████░░░
       databases/postgres-install.yml  #0
       collection: ansible_docs  domain: devops

   2.  0.7821  ████████████████░░░░░░░
       databases/postgres-backup.yml  #1
       collection: ansible_docs  domain: devops

   3.  0.6103  ████████████░░░░░░░░░░░
       databases/postgres-install.yml  #2
       collection: ansible_docs  domain: devops
```

### Voir l'extrait de texte du chunk

```bash
python scripts/qdrant_ingest.py search \
  --query "install postgresql database" \
  --collection ansible_docs \
  --top-k 3 \
  --show-text
```

```
   1.  0.8934  ████████████████████░░░
       databases/postgres-install.yml  #0
       collection: ansible_docs  domain: devops

       File: postgres-install.yml  - name: Install PostgreSQL database server
       hosts: databases  vars: postgres_version: "14" postgres_db: "civitas_db"
       tasks: - name: Install PostgreSQL  apt: name: - postgresql-14...
```

### Recherche dans toutes les collections à la fois

```bash
python scripts/qdrant_ingest.py search \
  --query "deploy docker container kubernetes" \
  --all-collections \
  --top-k 10 \
  --min-score 0.5
```

→ Retourne les 10 meilleurs résultats tous domaines confondus,
  triés par score de similarité décroissant.

### Filtrer par domaine ou extension

```bash
# Seulement les fichiers de sécurité
python scripts/qdrant_ingest.py search \
  --query "accès s3 bucket permissions" \
  --all-collections \
  --filter-domain security

# Seulement les fichiers Terraform
python scripts/qdrant_ingest.py search \
  --query "create vpc subnet aws" \
  --collection terraform_docs \
  --filter-extension .tf

# Seulement les fichiers avec le tag "jenkins"
python scripts/qdrant_ingest.py search \
  --query "pipeline build test deploy" \
  --collection cicd_docs \
  --filter-tags jenkins
```

### Sortie JSON (pour l'intégrer dans un autre système)

```bash
python scripts/qdrant_ingest.py search \
  --query "nginx configuration ssl" \
  --all-collections \
  --top-k 5 \
  --json > results.json

cat results.json
```

```json
[
  {
    "rank": 1,
    "score": 0.8934,
    "file": "/data/documents/ansible/webservers/nginx-install.yml",
    "relative_path": "webservers/nginx-install.yml",
    "collection": "ansible_docs",
    "chunk_index": 0,
    "domain": "devops",
    "chunk_text": "..."
  }
]
```

---

## 6. Cas 5 — Audit et vérification du système

### Inspecter une collection en détail

```bash
python scripts/qdrant_ingest.py inspect \
  --collection ansible_docs \
  --limit 20 \
  --sample 3
```

Vous voyez :
- Info Qdrant (nombre de points, dimension, statut)
- Info Tracker (fichiers ingérés, chunks, volume)
- Liste des fichiers avec leur statut (✓ / ✗)
- Sample de 3 points Qdrant avec leur payload complet

### Détecter les problèmes

```bash
python scripts/qdrant_ingest.py verify
```

Vérifie pour chaque collection :
```
📁 ansible_docs
  ✓ Collection présente dans Qdrant
  ✓ Aucun fichier en erreur
  ✓ Tous les fichiers trackés présents sur disque
  Tracker : 4 fichiers, 12 chunks
  Qdrant  : 12 points

📁 cicd_docs
  ✓ Collection présente dans Qdrant
  ✗ 2 fichier(s) en statut 'failed'    ← à corriger
    · cicd/jenkins/Jenkinsfile
    · cicd/docker/Dockerfile
  ✓ Tous les fichiers trackés présents sur disque

⚠  Des anomalies ont été détectées.
```

### Corriger les fichiers en erreur

```bash
# Voir les fichiers en erreur
python scripts/qdrant_ingest.py list-files \
  --collection cicd_docs \
  --status failed

# Réinitialiser uniquement les enregistrements en erreur
python scripts/qdrant_ingest.py reset --failed-only

# Relancer l'ingestion → seulement les fichiers failed seront retraités
python scripts/qdrant_ingest.py ingest \
  --path data/documents/cicd \
  --collection cicd_docs \
  --domain devops --tags cicd
```

### Voir les fichiers supprimés du disque

```bash
python scripts/qdrant_ingest.py diff \
  --path data/documents/ansible \
  --collection ansible_docs \
  --show-unchanged
```

```
  ✘  Supprimés    : 1      ← fichier trackéé mais absent du disque
✘ Supprimés du disque
  · /data/documents/ansible/webservers/old-nginx.yml
```

Ces fichiers restent dans Qdrant jusqu'à ce que vous purgiez
ou supprimiez manuellement leur collection.

---

## 7. Cas 6 — Réorganiser ses collections (purge + réingestion)

### Contexte

Vous étiez partis avec une collection `all_docs` fourre-tout.
Vous voulez maintenant séparer par domaine proprement.

### Étape 1 — Purger l'ancienne collection

```bash
# Voir ce qu'elle contient
python scripts/qdrant_ingest.py inspect --collection all_docs

# Purger Qdrant + tracker
python scripts/qdrant_ingest.py purge \
  --collection all_docs \
  --yes
```

```
  Qdrant  : 45 points seront supprimés
  Tracker : 11 enregistrements seront supprimés
✓ Collection Qdrant 'all_docs' supprimée.
✓ Tracker 'all_docs' purgé (11 enregistrements).
```

### Étape 2 — Réingérer par domaine

```bash
python scripts/qdrant_ingest.py \
  -c config/qdrant_ingestion.yaml \
  ingest --all
```

### Garder Qdrant mais vider le tracker (pour tester la réingestion)

```bash
# Purge tracker seul — Qdrant intact
python scripts/qdrant_ingest.py purge \
  --collection ansible_docs \
  --tracker-only \
  --yes

# La prochaine ingestion détecte tout comme "nouveau"
# et écrase les points existants dans Qdrant
python scripts/qdrant_ingest.py ingest \
  --path data/documents/ansible \
  --collection ansible_docs \
  --force
```

### Changer le modèle d'embedding (migration)

Si vous changez de `all-MiniLM-L6-v2` (384 dims) vers `all-mpnet-base-v2`
(768 dims), les collections existantes sont incompatibles (dimension différente).
Il faut tout recréer :

```bash
# 1. Purger TOUTES les collections Qdrant (pas le tracker)
for col in ansible_docs terraform_docs cicd_docs iam_docs ssh_docs; do
  python scripts/qdrant_ingest.py purge --collection $col --qdrant-only --yes
done

# 2. Réingérer avec le nouveau modèle
python scripts/qdrant_ingest.py \
  --embedding-model all-mpnet-base-v2 \
  --embedding-dim 768 \
  -c config/qdrant_ingestion.yaml \
  ingest --all --force
```

---

## 8. Cas 7 — Export et reporting

### Export JSON pour un audit

```bash
python scripts/qdrant_ingest.py export \
  --collection ansible_docs \
  --format json \
  --output rapport_ansible_$(date +%Y%m%d).json

# Contenu :
# [{ "file_path", "status", "chunks", "file_size",
#    "file_hash", "ingested_at", "point_ids", "error_msg" }]
```

### Export CSV pour Excel/Sheets

```bash
python scripts/qdrant_ingest.py export \
  --collection ansible_docs \
  --format csv \
  --output rapport_ansible.csv
```

### Rapport global de toutes les collections

```bash
# Statut complet en une commande
python scripts/qdrant_ingest.py status

# Ou avec détail Qdrant sur une collection
python scripts/qdrant_ingest.py status --collection ansible_docs
```

### Lister tous les fichiers en erreur toutes collections confondues

```bash
for col in ansible_docs terraform_docs cicd_docs iam_docs ssh_docs; do
  echo "=== $col ==="
  python scripts/qdrant_ingest.py list-files --collection $col --status failed
done
```

---

## 9. Cas 8 — Intégration dans un pipeline CI/CD

### Contexte

Vous voulez que chaque push sur votre repo de configs IaC
déclenche automatiquement la réingestion des fichiers modifiés.

### Script d'ingestion automatique (CI)

```bash
#!/bin/bash
# scripts/ci_ingest.sh
set -e

echo "=== CIVITAS-RAG · Ingestion CI ==="

# 1. Ping Qdrant
python scripts/qdrant_ingest.py ping
if [ $? -ne 0 ]; then
  echo "❌ Qdrant inaccessible"
  exit 1
fi

# 2. Diff pour voir ce qui a changé
echo "--- Changements détectés ---"
python scripts/qdrant_ingest.py diff \
  --path data/documents \
  --collection all_docs

# 3. Ingérer les nouveaux/modifiés uniquement
python scripts/qdrant_ingest.py \
  -c config/qdrant_ingestion.yaml \
  ingest --all

# 4. Vérifier la cohérence
python scripts/qdrant_ingest.py verify
if [ $? -ne 0 ]; then
  echo "⚠ Des anomalies détectées — voir logs"
  exit 1
fi

echo "✓ Ingestion CI terminée"
```

### GitHub Actions

```yaml
# .github/workflows/rag-ingest.yml
name: CIVITAS-RAG Ingestion

on:
  push:
    paths:
      - 'data/documents/**'
      - 'config/qdrant_ingestion.yaml'

jobs:
  ingest:
    runs-on: ubuntu-latest
    services:
      qdrant:
        image: qdrant/qdrant
        ports:
          - 6333:6333

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -e ".[dev]"

      - name: Ping Qdrant
        run: python scripts/qdrant_ingest.py ping

      - name: Ingest documents
        run: |
          python scripts/qdrant_ingest.py \
            -c config/qdrant_ingestion.yaml \
            ingest --all
        env:
          QDRANT_HOST: localhost
          QDRANT_PORT: 6333

      - name: Verify consistency
        run: python scripts/qdrant_ingest.py verify

      - name: Export report
        run: |
          for col in ansible_docs terraform_docs cicd_docs iam_docs ssh_docs; do
            python scripts/qdrant_ingest.py export \
              --collection $col \
              --format json \
              --output reports/${col}.json
          done

      - name: Upload reports
        uses: actions/upload-artifact@v4
        with:
          name: ingestion-reports
          path: reports/
```

### Utiliser tfidf-local en CI (pas de téléchargement HuggingFace)

En CI/CD vous n'avez pas forcément accès à HuggingFace pour télécharger
le modèle. Utilisez `tfidf-local` pour l'environnement CI, et
`sentence-transformers` en production :

```bash
# CI (rapide, offline, pas de GPU)
python scripts/qdrant_ingest.py \
  --embedding-provider tfidf-local \
  --embedding-dim 128 \
  -c config/qdrant_ingestion.yaml \
  ingest --all

# Production (qualité maximale)
python scripts/qdrant_ingest.py \
  --embedding-provider sentence-transformers \
  --embedding-model all-MiniLM-L6-v2 \
  --embedding-dim 384 \
  -c config/qdrant_ingestion.yaml \
  ingest --all
```

---

## 10. Référence rapide des commandes

```bash
# ── DÉMARRAGE RAPIDE ──────────────────────────────────────────
python scripts/qdrant_ingest.py ping                        # Tester Qdrant
python scripts/qdrant_ingest.py config                      # Voir la config active
python scripts/qdrant_ingest.py tree --path <dossier>       # Voir les fichiers

# ── INGESTION ─────────────────────────────────────────────────
python scripts/qdrant_ingest.py ingest \
  --path <dossier> --collection <nom>                       # Ingestion directe
  [--dry-run]          # Simuler sans écrire
  [--force]            # Réingérer même les inchangés
  [--domain <str>]     # Métadonnée domaine
  [--tags a b c]       # Métadonnées tags
  [--chunk-size 512]   # Taille des chunks
  [--extensions .yml .tf]  # Filtrer les extensions

python scripts/qdrant_ingest.py ingest \
  -c config/qdrant_ingestion.yaml --scan ansible_scan       # Scan nommé

python scripts/qdrant_ingest.py ingest \
  -c config/qdrant_ingestion.yaml --all                     # Tous les scans

# ── RECHERCHE ─────────────────────────────────────────────────
python scripts/qdrant_ingest.py search \
  --query "<texte>" --collection <nom>                      # Recherche simple
  [--all-collections]   # Toutes les collections
  [--top-k 10]          # Nombre de résultats
  [--min-score 0.5]     # Score minimum
  [--show-text]         # Afficher l'extrait
  [--json]              # Sortie JSON stdout
  [--filter-domain devops]
  [--filter-extension .yml]
  [--filter-tags ansible terraform]

# ── INSPECTION ────────────────────────────────────────────────
python scripts/qdrant_ingest.py status                      # Vue globale
python scripts/qdrant_ingest.py status --collection <nom>   # Vue collection
python scripts/qdrant_ingest.py inspect --collection <nom>  # Détail complet
python scripts/qdrant_ingest.py list-files --collection <nom>  # Fichiers trackés
  [--status failed]     # Filtrer par statut
  [--json]              # Sortie JSON
python scripts/qdrant_ingest.py diff \
  --path <dossier> --collection <nom>                       # Diff disque vs tracker
  [--show-unchanged]    # Inclure les inchangés
python scripts/qdrant_ingest.py verify                      # Cohérence globale
python scripts/qdrant_ingest.py verify --collection <nom>   # Cohérence une collection
python scripts/qdrant_ingest.py collections                 # Lister collections Qdrant

# ── MAINTENANCE ───────────────────────────────────────────────
python scripts/qdrant_ingest.py reset --collection <nom> [--yes]  # Reset tracker
python scripts/qdrant_ingest.py reset --all [--yes]               # Reset total
python scripts/qdrant_ingest.py reset --failed-only               # Reset erreurs
python scripts/qdrant_ingest.py purge --collection <nom> [--yes]  # Purge Qdrant+tracker
  [--tracker-only]      # Seulement le tracker
  [--qdrant-only]       # Seulement Qdrant
python scripts/qdrant_ingest.py delete-file \
  --file <chemin> --collection <nom> [--yes]               # Supprimer un fichier
python scripts/qdrant_ingest.py export \
  --collection <nom> --format json|csv                     # Export
  [--output <fichier>]  # Vers fichier (défaut: stdout)

# ── OPTIONS GLOBALES (valables pour toutes les commandes) ─────
  -c / --config <yaml>           # Fichier de configuration
  --qdrant-host <host>           # Override hôte Qdrant
  --qdrant-port <port>           # Override port Qdrant
  --qdrant-url  <url>            # Override URL (Qdrant Cloud)
  --embedding-provider <p>       # sentence-transformers | openai | tfidf-local
  --embedding-model <m>          # Nom du modèle
  --embedding-dim <n>            # Dimension des vecteurs
  -v / --verbose                 # Logs DEBUG
  --log-file <fichier>           # Écrire les logs dans un fichier
```

---

## Conseils pratiques

**Organisation des collections :** Une collection par domaine (ansible, terraform,
cicd...) plutôt qu'une collection fourre-tout. Vous pouvez toujours chercher dans
toutes les collections avec `--all-collections`.

**Modèle d'embedding :** `all-MiniLM-L6-v2` est le meilleur ratio
vitesse/qualité pour commencer. Pour du français, utilisez
`paraphrase-multilingual-mpnet-base-v2` (768 dims). Ne changez pas
de modèle après avoir indexé sans faire une purge complète — les
dimensions doivent être cohérentes dans chaque collection.

**Déduplication :** Relancez `ingest` aussi souvent que vous voulez.
Seuls les fichiers nouveaux ou modifiés sont traités. C'est idempotent.

**Qdrant Cloud vs local :** Pour la production, Qdrant Cloud évite
de gérer l'infra. Définissez `QDRANT_URL` et `QDRANT_API_KEY` dans
votre `.env` et tout fonctionne sans changer une ligne de code.

**Chunk size :** Les fichiers courts (configs SSH, policies IAM) → chunk_size 200-300.
Les fichiers longs (playbooks complexes, gros modules Terraform) → chunk_size 400-600.
La valeur par défaut (512) convient à 90% des cas.
