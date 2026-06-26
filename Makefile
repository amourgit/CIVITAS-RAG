# ╔══════════════════════════════════════════════════════════════════════╗
# ║  CIVITAS-RAG — Makefile                                             ║
# ║  Interface unifiée : Docker · Ingestion · Recherche · Maintenance  ║
# ╚══════════════════════════════════════════════════════════════════════╝
#
# Usage : make <cible> [VARIABLE=valeur ...]
# Aide  : make help
#
# Exemples rapides :
#   make up                              — démarrer la stack
#   make ingest path=data/documents/ansible col=ansible_docs
#   make search q="install postgresql" col=ansible_docs
#   make status
#   make diff path=data/documents/ansible col=ansible_docs
#   make verify

# ══════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════════════

# ── Shell strict ──────────────────────────────────────────────────────
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

# ── Couleurs terminal ─────────────────────────────────────────────────
BOLD   := \033[1m
DIM    := \033[2m
CYAN   := \033[36m
GREEN  := \033[32m
YELLOW := \033[33m
RED    := \033[31m
RESET  := \033[0m

# ── Python / CLI ──────────────────────────────────────────────────────
PYTHON      := python
CLI         := $(PYTHON) scripts/qdrant_ingest.py
PYTEST      := $(PYTHON) -m pytest
PIP         := $(PYTHON) -m pip

# ── Docker ────────────────────────────────────────────────────────────
DC          := docker compose
DC_FILE     := docker-compose.yml

# ── Variables avec defaults (passables en argument CLI) ───────────────

# Ingestion
path        ?= data/documents         # Répertoire source
col         ?=                        # Collection Qdrant cible
domain      ?=                        # Domaine métier (devops, security...)
tags        ?=                        # Tags (espace-séparés)
extensions  ?=                        # Extensions (.yml .tf ...)
chunk_size  ?=                        # Taille chunk en mots
chunk_overlap ?=                      # Chevauchement en mots
max_size_mb ?=                        # Taille max fichier en MB
scan        ?=                        # Nom d'un scan YAML nommé
config      ?= config/qdrant_ingestion.yaml   # Fichier YAML de config

# Embedding
emb_provider ?=                       # sentence-transformers | openai | tfidf-local
emb_model    ?=                       # Nom du modèle
emb_dim      ?=                       # Dimension vectorielle

# Recherche
q           ?=                        # Requête de recherche (obligatoire pour search)
top_k       ?= 5                      # Nombre de résultats
min_score   ?= 0.0                    # Score minimum de similarité
filter_domain   ?=                    # Filtre domaine
filter_ext      ?=                    # Filtre extension
filter_tags     ?=                    # Filtre tags

# Inspection
limit       ?= 20                     # Nb fichiers à lister
sample      ?= 3                      # Nb points Qdrant à afficher
file        ?=                        # Chemin d'un fichier (delete-file)

# Export
format      ?= json                   # json | csv
output      ?=                        # Fichier de sortie (défaut: stdout)

# Outils
status_filter ?=                      # success | failed (list-files)
check_points  ?=                      # true = vérifier les points Qdrant

# Tests
test_path   ?= tests/
test_cov    ?= civitas

# Docker
service     ?=                        # Service Docker spécifique
tail        ?= 100                    # Nb lignes de logs

# ── Constructeur des flags CLI optionnels ─────────────────────────────
_COL          = $(if $(col),--collection $(col))
_DOMAIN       = $(if $(domain),--domain $(domain))
_TAGS         = $(if $(tags),--tags $(tags))
_EXTENSIONS   = $(if $(extensions),--extensions $(extensions))
_CHUNK_SIZE   = $(if $(chunk_size),--chunk-size $(chunk_size))
_CHUNK_OVERLAP= $(if $(chunk_overlap),--chunk-overlap $(chunk_overlap))
_MAX_SIZE     = $(if $(max_size_mb),--max-size-mb $(max_size_mb))
_TOP_K        = --top-k $(top_k)
_MIN_SCORE    = $(if $(filter $(min_score),0.0),,--min-score $(min_score))
_FILTER_DOM   = $(if $(filter_domain),--filter-domain $(filter_domain))
_FILTER_EXT   = $(if $(filter_ext),--filter-extension $(filter_ext))
_FILTER_TAGS  = $(if $(filter_tags),--filter-tags $(filter_tags))
_EMB_PROVIDER = $(if $(emb_provider),--embedding-provider $(emb_provider))
_EMB_MODEL    = $(if $(emb_model),--embedding-model $(emb_model))
_EMB_DIM      = $(if $(emb_dim),--embedding-dim $(emb_dim))
_OUTPUT       = $(if $(output),--output $(output))
_LIMIT        = --limit $(limit)
_SAMPLE       = --sample $(sample)
_CONFIG       = $(if $(config),-c $(config))
_STATUS_F     = $(if $(status_filter),--status $(status_filter))
_CHECK_PTS    = $(if $(filter $(check_points),true),--check-points)

# Flags globaux embedding
_EMB_FLAGS    = $(_EMB_PROVIDER) $(_EMB_MODEL) $(_EMB_DIM)

# ── .PHONY complet ────────────────────────────────────────────────────
.PHONY: help \
        up up-infra up-worker up-tools down restart rebuild \
        logs logs-qdrant logs-postgres logs-app ps \
        shell shell-qdrant shell-postgres shell-app \
        ping config-show tree status inspect \
        list-files list-files-json list-failed \
        diff diff-full verify collections \
        ingest ingest-all ingest-scan ingest-dry ingest-force ingest-dev ingest-ci \
        search search-text search-all search-json search-all-json \
        reset reset-all reset-failed \
        purge purge-tracker purge-qdrant \
        delete-file export export-all \
        db-shell db-migrate db-reset db-dump \
        install install-dev lint format type-check \
        test test-unit test-watch \
        build build-no-cache push \
        clean clean-docker clean-tracker nuke \
        env-check

# ══════════════════════════════════════════════════════════════════════
#  AIDE
# ══════════════════════════════════════════════════════════════════════

## help : Afficher cette aide
help:
	@printf "\n$(BOLD)$(CYAN)╔══════════════════════════════════════════════════════════╗$(RESET)\n"
	@printf "$(BOLD)$(CYAN)║  CIVITAS-RAG — Makefile                                  ║$(RESET)\n"
	@printf "$(BOLD)$(CYAN)╚══════════════════════════════════════════════════════════╝$(RESET)\n\n"
	@printf "$(BOLD)Usage :$(RESET) make $(CYAN)<cible>$(RESET) [$(YELLOW)VAR=valeur$(RESET) ...]\n\n"
	@printf "$(BOLD)$(CYAN)▶ DOCKER$(RESET)\n"
	@printf "  $(CYAN)up$(RESET)                   Démarrer Qdrant + Postgres\n"
	@printf "  $(CYAN)up-worker$(RESET)             Démarrer avec le worker d'ingestion\n"
	@printf "  $(CYAN)up-tools$(RESET)              Démarrer avec PgAdmin (http://localhost:5050)\n"
	@printf "  $(CYAN)down$(RESET)                  Arrêter tous les services\n"
	@printf "  $(CYAN)restart$(RESET)               Redémarrer tous les services\n"
	@printf "  $(CYAN)rebuild$(RESET)               Rebuild + restart\n"
	@printf "  $(CYAN)logs$(RESET)                  Logs en temps réel (tail=$(YELLOW)$(tail)$(RESET))\n"
	@printf "  $(CYAN)logs service=qdrant$(RESET)   Logs d'un service spécifique\n"
	@printf "  $(CYAN)ps$(RESET)                    État des containers\n"
	@printf "  $(CYAN)shell service=app$(RESET)     Shell interactif dans un container\n"
	@printf "\n$(BOLD)$(CYAN)▶ INGESTION$(RESET)\n"
	@printf "  $(CYAN)ping$(RESET)                  Tester la connexion Qdrant\n"
	@printf "  $(CYAN)ingest$(RESET)                Ingérer $(YELLOW)path=$(RESET) dans $(YELLOW)col=$(RESET)\n"
	@printf "  $(CYAN)ingest-all$(RESET)            Tous les scans du YAML\n"
	@printf "  $(CYAN)ingest-scan$(RESET)           Scan nommé $(YELLOW)scan=$(RESET)\n"
	@printf "  $(CYAN)ingest-dry$(RESET)            Simulation (dry-run)\n"
	@printf "  $(CYAN)ingest-force$(RESET)          Forcer la réingestion complète\n"
	@printf "  $(CYAN)ingest-dev$(RESET)            Ingestion rapide (tfidf-local, dim 128)\n"
	@printf "  $(CYAN)ingest-ci$(RESET)             Mode CI/CD (tfidf-local, pas de UI)\n"
	@printf "\n$(BOLD)$(CYAN)▶ RECHERCHE$(RESET)\n"
	@printf "  $(CYAN)search$(RESET)                Chercher $(YELLOW)q=$(RESET) dans $(YELLOW)col=$(RESET)\n"
	@printf "  $(CYAN)search-all$(RESET)            Chercher dans toutes les collections\n"
	@printf "  $(CYAN)search-json$(RESET)           Résultats en JSON\n"
	@printf "\n$(BOLD)$(CYAN)▶ INSPECTION$(RESET)\n"
	@printf "  $(CYAN)status$(RESET)                Statut global du système\n"
	@printf "  $(CYAN)inspect$(RESET)               Détail d'une collection ($(YELLOW)col=$(RESET))\n"
	@printf "  $(CYAN)list-files$(RESET)            Fichiers trackés ($(YELLOW)col=$(RESET))\n"
	@printf "  $(CYAN)diff$(RESET)                  Diff disque vs tracker\n"
	@printf "  $(CYAN)verify$(RESET)                Vérifier la cohérence système\n"
	@printf "  $(CYAN)tree$(RESET)                  Arborescence fichiers ($(YELLOW)path=$(RESET))\n"
	@printf "  $(CYAN)config-show$(RESET)           Afficher la config active\n"
	@printf "  $(CYAN)collections$(RESET)           Lister les collections Qdrant\n"
	@printf "\n$(BOLD)$(CYAN)▶ MAINTENANCE$(RESET)\n"
	@printf "  $(CYAN)reset$(RESET)                 Reset tracker ($(YELLOW)col=$(RESET))\n"
	@printf "  $(CYAN)reset-all$(RESET)             Reset tout le tracker\n"
	@printf "  $(CYAN)reset-failed$(RESET)          Reset uniquement les erreurs\n"
	@printf "  $(CYAN)purge$(RESET)                 Purge Qdrant + tracker ($(YELLOW)col=$(RESET))\n"
	@printf "  $(CYAN)delete-file$(RESET)           Supprimer un fichier ($(YELLOW)file=, col=$(RESET))\n"
	@printf "  $(CYAN)export$(RESET)                Exporter les métadonnées ($(YELLOW)col=$(RESET))\n"
	@printf "\n$(BOLD)$(CYAN)▶ BASE DE DONNÉES$(RESET)\n"
	@printf "  $(CYAN)db-shell$(RESET)              Shell psql interactif\n"
	@printf "  $(CYAN)db-migrate$(RESET)            Lancer les migrations SQL\n"
	@printf "  $(CYAN)db-reset$(RESET)              Réinitialiser la base\n"
	@printf "\n$(BOLD)$(CYAN)▶ DÉVELOPPEMENT$(RESET)\n"
	@printf "  $(CYAN)install$(RESET)               Installer les dépendances\n"
	@printf "  $(CYAN)install-dev$(RESET)           Installer avec outils dev\n"
	@printf "  $(CYAN)lint$(RESET)                  Linter le code\n"
	@printf "  $(CYAN)format$(RESET)                Formater le code\n"
	@printf "  $(CYAN)type-check$(RESET)            Vérification des types\n"
	@printf "  $(CYAN)test$(RESET)                  Lancer les tests\n"
	@printf "  $(CYAN)test-unit$(RESET)             Tests unitaires uniquement\n"
	@printf "\n$(BOLD)$(CYAN)▶ BUILD / CLEAN$(RESET)\n"
	@printf "  $(CYAN)build$(RESET)                 Build l'image Docker\n"
	@printf "  $(CYAN)clean$(RESET)                 Nettoyer fichiers temporaires\n"
	@printf "  $(CYAN)clean-docker$(RESET)          Supprimer les volumes Docker\n"
	@printf "  $(CYAN)clean-tracker$(RESET)         Supprimer le tracker SQLite\n"
	@printf "  $(CYAN)nuke$(RESET)                  Tout supprimer (IRRÉVERSIBLE)\n"
	@printf "\n$(BOLD)Variables disponibles :$(RESET)\n"
	@printf "  $(YELLOW)path$(RESET)=$(DIM)$(path)$(RESET)   $(YELLOW)col$(RESET)=$(DIM)$(if $(col),$(col),(non défini))$(RESET)   $(YELLOW)domain$(RESET)=$(DIM)$(if $(domain),$(domain),(non défini))$(RESET)\n"
	@printf "  $(YELLOW)tags$(RESET)=$(DIM)$(if $(tags),$(tags),(non défini))$(RESET)   $(YELLOW)q$(RESET)=$(DIM)$(if $(q),$(q),(non défini))$(RESET)   $(YELLOW)top_k$(RESET)=$(DIM)$(top_k)$(RESET)   $(YELLOW)format$(RESET)=$(DIM)$(format)$(RESET)\n"
	@printf "  $(YELLOW)emb_provider$(RESET)=$(DIM)$(if $(emb_provider),$(emb_provider),(config))$(RESET)   $(YELLOW)config$(RESET)=$(DIM)$(config)$(RESET)\n"
	@printf "\n"

# ══════════════════════════════════════════════════════════════════════
#  DOCKER — CYCLE DE VIE
# ══════════════════════════════════════════════════════════════════════

## up : Démarrer Qdrant + PostgreSQL (services de base)
up: env-check
	@printf "$(BOLD)$(GREEN)▶ Démarrage de la stack CIVITAS...$(RESET)\n"
	$(DC) -f $(DC_FILE) up -d qdrant postgres
	@printf "$(GREEN)✓ Stack démarrée$(RESET)\n"
	@printf "  Qdrant   : $(CYAN)http://localhost:$(or $(QDRANT_PORT),6333)$(RESET)\n"
	@printf "  Postgres : $(CYAN)localhost:$(or $(POSTGRES_PORT),5432)$(RESET)\n"
	@make --no-print-directory ps

## up-infra : Démarrer seulement Qdrant (sans Postgres)
up-infra: env-check
	$(DC) -f $(DC_FILE) up -d qdrant
	@printf "$(GREEN)✓ Qdrant démarré : $(CYAN)http://localhost:$(or $(QDRANT_PORT),6333)$(RESET)\n"

## up-worker : Démarrer la stack + le worker d'ingestion en arrière-plan
up-worker: env-check
	$(DC) -f $(DC_FILE) --profile worker up -d
	@printf "$(GREEN)✓ Stack + worker démarrés$(RESET)\n"

## up-tools : Démarrer la stack + PgAdmin
up-tools: env-check
	$(DC) -f $(DC_FILE) --profile tools up -d qdrant postgres pgadmin
	@printf "$(GREEN)✓ PgAdmin : $(CYAN)http://localhost:$(or $(PGADMIN_PORT),5050)$(RESET)\n"
	@printf "  Email : $(or $(PGADMIN_EMAIL),admin@civitas.local)\n"

## down : Arrêter tous les services (garde les volumes)
down:
	@printf "$(BOLD)$(YELLOW)▶ Arrêt de la stack...$(RESET)\n"
	$(DC) -f $(DC_FILE) --profile worker --profile tools down
	@printf "$(GREEN)✓ Stack arrêtée$(RESET)\n"

## restart : Redémarrer tous les services
restart: down up

## rebuild : Rebuild l'image + restart
rebuild:
	@printf "$(BOLD)$(CYAN)▶ Rebuild + restart...$(RESET)\n"
	$(DC) -f $(DC_FILE) build --no-cache
	$(MAKE) --no-print-directory restart

## ps : État des containers
ps:
	@printf "\n$(BOLD)État des services :$(RESET)\n"
	$(DC) -f $(DC_FILE) --profile worker --profile tools ps

## logs : Logs en temps réel
logs:
	$(DC) -f $(DC_FILE) --profile worker --profile tools logs \
		--follow --tail=$(tail) $(service)

## logs-qdrant : Logs Qdrant uniquement
logs-qdrant:
	$(DC) -f $(DC_FILE) logs --follow --tail=$(tail) qdrant

## logs-postgres : Logs PostgreSQL uniquement
logs-postgres:
	$(DC) -f $(DC_FILE) logs --follow --tail=$(tail) postgres

## logs-app : Logs de l'application
logs-app:
	$(DC) -f $(DC_FILE) logs --follow --tail=$(tail) app

## shell : Shell interactif dans un container (service=app par défaut)
shell:
	$(DC) -f $(DC_FILE) exec $(or $(service),app) /bin/bash

## shell-qdrant : Shell dans le container Qdrant
shell-qdrant:
	$(DC) -f $(DC_FILE) exec qdrant /bin/sh

## shell-postgres : Shell psql dans le container Postgres
shell-postgres:
	$(DC) -f $(DC_FILE) exec postgres \
		psql -U $${POSTGRES_USER:-civitas} -d $${POSTGRES_DB:-civitas_knowledge}

## shell-app : Shell dans le container app
shell-app:
	$(DC) -f $(DC_FILE) exec app /bin/bash

# ══════════════════════════════════════════════════════════════════════
#  CONNEXION / CONFIG
# ══════════════════════════════════════════════════════════════════════

## ping : Tester la connexion Qdrant
ping:
	$(CLI) $(_CONFIG) $(_EMB_FLAGS) ping

## config-show : Afficher la configuration active complète
config-show:
	$(CLI) $(_CONFIG) $(_EMB_FLAGS) config

# ══════════════════════════════════════════════════════════════════════
#  INGESTION
# ══════════════════════════════════════════════════════════════════════

## ingest : Ingérer path= dans col= (avec options domaine/tags/chunks)
## Exemple : make ingest path=data/documents/ansible col=ansible_docs domain=devops tags="ansible iac"
ingest:
	@test -n "$(col)" || (printf "$(RED)✗ Erreur : col= requis. Exemple : make ingest path=data/documents/ansible col=ansible_docs$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) $(_EMB_FLAGS) ingest \
		--path $(path) \
		$(_COL) \
		$(_DOMAIN) \
		$(_TAGS) \
		$(_EXTENSIONS) \
		$(_CHUNK_SIZE) \
		$(_CHUNK_OVERLAP) \
		$(_MAX_SIZE)

## ingest-all : Lancer tous les scans définis dans le fichier YAML
## Exemple : make ingest-all config=config/qdrant_ingestion.yaml
ingest-all:
	$(CLI) $(_CONFIG) $(_EMB_FLAGS) ingest --all

## ingest-scan : Lancer un scan nommé depuis le YAML
## Exemple : make ingest-scan scan=ansible_scan
ingest-scan:
	@test -n "$(scan)" || (printf "$(RED)✗ Erreur : scan= requis. Exemple : make ingest-scan scan=ansible_scan$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) $(_EMB_FLAGS) ingest --scan $(scan)

## ingest-dry : Simuler l'ingestion (dry-run, rien n'est écrit dans Qdrant)
## Exemple : make ingest-dry path=data/documents/ansible col=ansible_docs
ingest-dry:
	@test -n "$(col)" || (printf "$(RED)✗ Erreur : col= requis$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) $(_EMB_FLAGS) ingest \
		--path $(path) \
		$(_COL) \
		$(_DOMAIN) $(_TAGS) \
		--dry-run

## ingest-force : Réingérer TOUS les fichiers (ignore la déduplication)
## Exemple : make ingest-force path=data/documents/ansible col=ansible_docs
ingest-force:
	@test -n "$(col)" || (printf "$(RED)✗ Erreur : col= requis$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) $(_EMB_FLAGS) ingest \
		--path $(path) \
		$(_COL) \
		$(_DOMAIN) $(_TAGS) \
		--force

## ingest-dev : Ingestion rapide en mode dev (tfidf-local, dim=128, offline)
## Exemple : make ingest-dev path=data/documents/ansible col=ansible_docs
ingest-dev:
	@test -n "$(col)" || (printf "$(RED)✗ Erreur : col= requis$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) \
		--embedding-provider tfidf-local \
		--embedding-dim 128 \
		ingest --path $(path) $(_COL) $(_DOMAIN) $(_TAGS)

## ingest-ci : Mode CI/CD (tfidf-local, tous les scans, logs minimaux)
ingest-ci:
	$(CLI) $(_CONFIG) \
		--embedding-provider tfidf-local \
		--embedding-dim 128 \
		ingest --all

# ══════════════════════════════════════════════════════════════════════
#  RECHERCHE
# ══════════════════════════════════════════════════════════════════════

## search : Recherche sémantique dans col=
## Exemple : make search q="install postgresql" col=ansible_docs top_k=5
search:
	@test -n "$(q)" || (printf "$(RED)✗ Erreur : q= requis. Exemple : make search q=\"install postgresql\" col=ansible_docs$(RESET)\n" && exit 1)
	@test -n "$(col)" || (printf "$(RED)✗ Erreur : col= requis (ou utilisez search-all)$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) $(_EMB_FLAGS) search \
		--query "$(q)" \
		$(_COL) \
		$(_TOP_K) \
		$(_MIN_SCORE) \
		$(_FILTER_DOM) \
		$(_FILTER_EXT) \
		$(_FILTER_TAGS)

## search-text : Recherche avec affichage du texte des chunks
## Exemple : make search-text q="nginx ssl config" col=ansible_docs
search-text:
	@test -n "$(q)" || (printf "$(RED)✗ Erreur : q= requis$(RESET)\n" && exit 1)
	@test -n "$(col)" || (printf "$(RED)✗ Erreur : col= requis$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) $(_EMB_FLAGS) search \
		--query "$(q)" \
		$(_COL) \
		$(_TOP_K) \
		$(_MIN_SCORE) \
		--show-text \
		$(_FILTER_DOM) $(_FILTER_EXT) $(_FILTER_TAGS)

## search-all : Recherche dans toutes les collections
## Exemple : make search-all q="deploy docker kubernetes" top_k=10
search-all:
	@test -n "$(q)" || (printf "$(RED)✗ Erreur : q= requis. Exemple : make search-all q=\"deploy docker\"$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) $(_EMB_FLAGS) search \
		--query "$(q)" \
		--all-collections \
		$(_TOP_K) \
		$(_MIN_SCORE) \
		$(_FILTER_DOM) $(_FILTER_EXT) $(_FILTER_TAGS)

## search-json : Recherche avec sortie JSON (stdout, pipelable)
## Exemple : make search-json q="terraform vpc" col=terraform_docs | jq '.[0]'
search-json:
	@test -n "$(q)" || (printf "$(RED)✗ Erreur : q= requis$(RESET)\n" && exit 1)
	@test -n "$(col)" || (printf "$(RED)✗ Erreur : col= requis$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) $(_EMB_FLAGS) search \
		--query "$(q)" \
		$(_COL) \
		$(_TOP_K) \
		$(_MIN_SCORE) \
		--json \
		$(_FILTER_DOM) $(_FILTER_EXT) $(_FILTER_TAGS)

## search-all-json : Recherche multi-collection en JSON
## Exemple : make search-all-json q="iam policy s3" | jq '.[] | {score, file}'
search-all-json:
	@test -n "$(q)" || (printf "$(RED)✗ Erreur : q= requis$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) $(_EMB_FLAGS) search \
		--query "$(q)" \
		--all-collections \
		$(_TOP_K) \
		$(_MIN_SCORE) \
		--json \
		$(_FILTER_DOM) $(_FILTER_EXT) $(_FILTER_TAGS)

# ══════════════════════════════════════════════════════════════════════
#  INSPECTION / DIAGNOSTIC
# ══════════════════════════════════════════════════════════════════════

## tree : Visualiser l'arborescence des fichiers scannables
## Exemple : make tree path=data/documents/ansible
## Exemple : make tree path=data/documents extensions=".yml .tf"
tree:
	$(CLI) $(_CONFIG) tree \
		--path $(path) \
		$(_EXTENSIONS) \
		$(_MAX_SIZE)

## status : Statut global (tracker + Qdrant)
## Exemple : make status
## Exemple : make status col=ansible_docs
status:
	$(CLI) $(_CONFIG) status $(_COL)

## inspect : Inspection détaillée d'une collection
## Exemple : make inspect col=ansible_docs
## Exemple : make inspect col=ansible_docs limit=50 sample=5
inspect:
	@test -n "$(col)" || (printf "$(RED)✗ Erreur : col= requis. Exemple : make inspect col=ansible_docs$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) inspect \
		$(_COL) \
		$(_LIMIT) \
		$(_SAMPLE)

## list-files : Lister les fichiers trackés
## Exemple : make list-files col=ansible_docs
## Exemple : make list-files col=ansible_docs status_filter=failed
list-files:
	@test -n "$(col)" || (printf "$(RED)✗ Erreur : col= requis$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) list-files \
		$(_COL) \
		$(_STATUS_F)

## list-files-json : Lister les fichiers en JSON
## Exemple : make list-files-json col=ansible_docs | jq '.[].file_path'
list-files-json:
	@test -n "$(col)" || (printf "$(RED)✗ Erreur : col= requis$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) list-files $(_COL) $(_STATUS_F) --json

## list-failed : Lister uniquement les fichiers en erreur
## Exemple : make list-failed col=ansible_docs
list-failed:
	@test -n "$(col)" || (printf "$(RED)✗ Erreur : col= requis$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) list-files $(_COL) --status failed

## diff : Comparer disque vs tracker (voir ce qui va changer)
## Exemple : make diff path=data/documents/ansible col=ansible_docs
diff:
	@test -n "$(col)" || (printf "$(RED)✗ Erreur : col= requis. Exemple : make diff path=data/documents/ansible col=ansible_docs$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) diff \
		--path $(path) \
		$(_COL)

## diff-full : Diff avec affichage des fichiers inchangés
diff-full:
	@test -n "$(col)" || (printf "$(RED)✗ Erreur : col= requis$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) diff \
		--path $(path) \
		$(_COL) \
		--show-unchanged

## verify : Vérifier la cohérence tracker ↔ Qdrant
## Exemple : make verify
## Exemple : make verify col=ansible_docs
verify:
	$(CLI) $(_CONFIG) verify \
		$(_COL) \
		$(_CHECK_PTS)

## collections : Lister toutes les collections Qdrant
collections:
	$(CLI) $(_CONFIG) collections

# ══════════════════════════════════════════════════════════════════════
#  MAINTENANCE
# ══════════════════════════════════════════════════════════════════════

## reset : Réinitialiser le tracker d'une collection (force réingestion)
## Exemple : make reset col=ansible_docs
reset:
	@test -n "$(col)" || (printf "$(RED)✗ Erreur : col= requis. Exemple : make reset col=ansible_docs$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) reset --collection $(col) --yes

## reset-all : Réinitialiser tout le tracker (réingestion totale)
reset-all:
	@printf "$(YELLOW)⚠  Réinitialisation complète du tracker. Continuer ? [y/N] $(RESET)" && \
	read ans && [ "$${ans:-N}" = "y" ] || (printf "Annulé.\n" && exit 0)
	$(CLI) $(_CONFIG) reset --all --yes

## reset-failed : Réinitialiser uniquement les enregistrements en erreur
reset-failed:
	$(CLI) $(_CONFIG) reset --failed-only

## purge : Supprimer une collection Qdrant + son tracker
## Exemple : make purge col=ansible_docs
## Exemple : make purge col=ansible_docs purge_mode=tracker-only
purge_mode ?=
_PURGE_MODE = $(if $(filter tracker-only,$(purge_mode)),--tracker-only,$(if $(filter qdrant-only,$(purge_mode)),--qdrant-only))
purge:
	@test -n "$(col)" || (printf "$(RED)✗ Erreur : col= requis. Exemple : make purge col=ansible_docs$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) purge \
		--collection $(col) \
		$(_PURGE_MODE) \
		--yes

## purge-tracker : Purger uniquement le tracker d'une collection
## Exemple : make purge-tracker col=ansible_docs
purge-tracker:
	@test -n "$(col)" || (printf "$(RED)✗ Erreur : col= requis$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) purge --collection $(col) --tracker-only --yes

## purge-qdrant : Purger uniquement la collection Qdrant (garder le tracker)
## Exemple : make purge-qdrant col=ansible_docs
purge-qdrant:
	@test -n "$(col)" || (printf "$(RED)✗ Erreur : col= requis$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) purge --collection $(col) --qdrant-only --yes

## delete-file : Supprimer un fichier spécifique du tracker + ses points Qdrant
## Exemple : make delete-file file=data/documents/ansible/webservers/nginx-install.yml col=ansible_docs
delete-file:
	@test -n "$(file)" || (printf "$(RED)✗ Erreur : file= requis$(RESET)\n" && exit 1)
	@test -n "$(col)"  || (printf "$(RED)✗ Erreur : col= requis$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) delete-file \
		--file $(file) \
		--collection $(col) \
		--yes

## export : Exporter les métadonnées d'une collection
## Exemple : make export col=ansible_docs
## Exemple : make export col=ansible_docs format=csv output=/tmp/export.csv
export:
	@test -n "$(col)" || (printf "$(RED)✗ Erreur : col= requis. Exemple : make export col=ansible_docs$(RESET)\n" && exit 1)
	$(CLI) $(_CONFIG) export \
		--collection $(col) \
		--format $(format) \
		$(_OUTPUT)

## export-all : Exporter toutes les collections connues du tracker
export-all:
	@mkdir -p exports
	@printf "$(CYAN)▶ Export de toutes les collections...$(RESET)\n"
	@$(PYTHON) -c " \
import sys; sys.path.insert(0,''); \
from civitas.ingestion.qdrant import IngestionTracker; \
t = IngestionTracker('$${CIVITAS_TRACKER_DB:-.civitas_ingestion_tracker.db}'); \
[print(c['collection']) for c in t.list_collections()]" 2>/dev/null | \
	while read col; do \
		printf "  Exporting $$col...\n"; \
		$(CLI) $(_CONFIG) export --collection $$col --format $(format) \
			--output exports/$${col}_$$(date +%Y%m%d).$(format); \
	done
	@printf "$(GREEN)✓ Exports dans ./exports/$(RESET)\n"

# ══════════════════════════════════════════════════════════════════════
#  BASE DE DONNÉES
# ══════════════════════════════════════════════════════════════════════

## db-shell : Shell psql interactif
db-shell:
	$(DC) -f $(DC_FILE) exec postgres \
		psql -U $${POSTGRES_USER:-civitas} -d $${POSTGRES_DB:-civitas_knowledge}

## db-migrate : Lancer les migrations SQL
db-migrate:
	@printf "$(CYAN)▶ Exécution des migrations...$(RESET)\n"
	$(DC) -f $(DC_FILE) exec -T postgres \
		psql -U $${POSTGRES_USER:-civitas} -d $${POSTGRES_DB:-civitas_knowledge} \
		-f /docker-entrypoint-initdb.d/001_init.sql
	@printf "$(GREEN)✓ Migrations appliquées$(RESET)\n"

## db-reset : Réinitialiser la base PostgreSQL (IRRÉVERSIBLE)
db-reset:
	@printf "$(RED)⚠  Réinitialisation complète de la base. Continuer ? [yes/N] $(RESET)" && \
	read ans && [ "$$ans" = "yes" ] || (printf "Annulé.\n" && exit 0)
	$(DC) -f $(DC_FILE) exec -T postgres \
		psql -U $${POSTGRES_USER:-civitas} -c \
		"DROP SCHEMA IF EXISTS civitas CASCADE; CREATE SCHEMA civitas;"
	$(MAKE) --no-print-directory db-migrate
	@printf "$(GREEN)✓ Base réinitialisée$(RESET)\n"

## db-dump : Sauvegarder la base PostgreSQL
db-dump:
	@mkdir -p backups
	$(DC) -f $(DC_FILE) exec -T postgres \
		pg_dump -U $${POSTGRES_USER:-civitas} $${POSTGRES_DB:-civitas_knowledge} \
		> backups/civitas_$$(date +%Y%m%d_%H%M%S).sql
	@printf "$(GREEN)✓ Backup dans ./backups/$(RESET)\n"

# ══════════════════════════════════════════════════════════════════════
#  DÉVELOPPEMENT
# ══════════════════════════════════════════════════════════════════════

## install : Installer les dépendances de production
install:
	$(PIP) install --upgrade pip
	$(PIP) install \
		qdrant-client \
		sentence-transformers \
		scikit-learn \
		rich \
		pyyaml \
		python-dotenv \
		chardet \
		pypdf \
		python-docx

## install-dev : Installer toutes les dépendances (prod + dev)
install-dev:
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]" 2>/dev/null || \
	$(PIP) install \
		qdrant-client \
		sentence-transformers \
		scikit-learn \
		rich \
		pyyaml \
		python-dotenv \
		chardet \
		pypdf \
		python-docx \
		pytest \
		pytest-asyncio \
		ruff \
		mypy
	@printf "$(GREEN)✓ Dépendances installées$(RESET)\n"

## lint : Analyser le code (ruff)
lint:
	@printf "$(CYAN)▶ Lint...$(RESET)\n"
	ruff check civitas/ scripts/ tests/ || true
	@printf "$(GREEN)✓ Lint terminé$(RESET)\n"

## format : Formater le code (ruff)
format:
	@printf "$(CYAN)▶ Format...$(RESET)\n"
	ruff format civitas/ scripts/ tests/
	ruff check --fix civitas/ scripts/ tests/ || true
	@printf "$(GREEN)✓ Format terminé$(RESET)\n"

## type-check : Vérification des types (mypy)
type-check:
	@printf "$(CYAN)▶ Type check...$(RESET)\n"
	mypy civitas/ || true
	@printf "$(GREEN)✓ Type check terminé$(RESET)\n"

## test : Lancer tous les tests
## Exemple : make test
## Exemple : make test test_path=tests/unit/test_qdrant_ingestion.py
test:
	$(PYTEST) $(test_path) \
		-v --tb=short --no-header \
		-p no:cacheprovider \
		--override-ini="addopts="

## test-unit : Tests unitaires uniquement
test-unit:
	$(PYTEST) tests/unit/ \
		-v --tb=short --no-header \
		-p no:cacheprovider \
		--override-ini="addopts="

## test-watch : Relancer les tests à chaque modification (nécessite pytest-watch)
test-watch:
	ptw tests/ -- -v --tb=short --override-ini="addopts="

# ══════════════════════════════════════════════════════════════════════
#  BUILD / IMAGE DOCKER
# ══════════════════════════════════════════════════════════════════════

## build : Builder l'image Docker de l'application
build:
	@printf "$(CYAN)▶ Build de l'image civitas-rag...$(RESET)\n"
	$(DC) -f $(DC_FILE) build \
		--build-arg BUILD_DATE=$$(date -u +%Y-%m-%dT%H:%M:%SZ) \
		--build-arg GIT_COMMIT=$$(git rev-parse --short HEAD 2>/dev/null || echo "unknown") \
		app
	@printf "$(GREEN)✓ Image buildée$(RESET)\n"

## build-no-cache : Build sans cache Docker
build-no-cache:
	$(DC) -f $(DC_FILE) build --no-cache \
		--build-arg BUILD_DATE=$$(date -u +%Y-%m-%dT%H:%M:%SZ) \
		--build-arg GIT_COMMIT=$$(git rev-parse --short HEAD 2>/dev/null || echo "unknown") \
		app

## push : Pousser l'image vers le registry
push: build
	@test -n "$(REGISTRY)" || (printf "$(RED)✗ REGISTRY= requis. Exemple : make push REGISTRY=ghcr.io/amourgit$(RESET)\n" && exit 1)
	docker tag civitas-rag:$${APP_VERSION:-latest} $(REGISTRY)/civitas-rag:$${APP_VERSION:-latest}
	docker push $(REGISTRY)/civitas-rag:$${APP_VERSION:-latest}
	@printf "$(GREEN)✓ Image poussée vers $(REGISTRY)$(RESET)\n"

# ══════════════════════════════════════════════════════════════════════
#  NETTOYAGE
# ══════════════════════════════════════════════════════════════════════

## clean : Nettoyer les fichiers temporaires Python
clean:
	@printf "$(CYAN)▶ Nettoyage fichiers temporaires...$(RESET)\n"
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -f .coverage coverage.xml
	@printf "$(GREEN)✓ Nettoyage terminé$(RESET)\n"

## clean-docker : Supprimer les volumes Docker (PERD LES DONNÉES Qdrant + Postgres)
clean-docker:
	@printf "$(RED)⚠  Suppression des volumes Docker. Toutes les données seront perdues. Continuer ? [yes/N] $(RESET)" && \
	read ans && [ "$$ans" = "yes" ] || (printf "Annulé.\n" && exit 0)
	$(MAKE) --no-print-directory down
	docker volume rm civitas_qdrant_storage civitas_postgres_data \
		civitas_tracker_data civitas_pgadmin_data 2>/dev/null || true
	@printf "$(GREEN)✓ Volumes supprimés$(RESET)\n"

## clean-tracker : Supprimer le tracker SQLite local
clean-tracker:
	@printf "$(YELLOW)⚠  Suppression du tracker SQLite local. Continuer ? [y/N] $(RESET)" && \
	read ans && [ "$${ans:-N}" = "y" ] || (printf "Annulé.\n" && exit 0)
	rm -f $${CIVITAS_TRACKER_DB:-.civitas_ingestion_tracker.db}
	@printf "$(GREEN)✓ Tracker supprimé$(RESET)\n"

## nuke : Tout supprimer (Docker + tracker + cache) — IRRÉVERSIBLE
nuke:
	@printf "$(RED)$(BOLD)⚠⚠  NUKE : Suppression TOTALE de toutes les données. Taper 'NUKE' pour confirmer : $(RESET)" && \
	read ans && [ "$$ans" = "NUKE" ] || (printf "Annulé.\n" && exit 0)
	$(MAKE) --no-print-directory down
	docker volume rm civitas_qdrant_storage civitas_postgres_data \
		civitas_tracker_data civitas_pgadmin_data 2>/dev/null || true
	rm -f $${CIVITAS_TRACKER_DB:-.civitas_ingestion_tracker.db}
	$(MAKE) --no-print-directory clean
	@printf "$(GREEN)✓ Tout supprimé.$(RESET)\n"

# ══════════════════════════════════════════════════════════════════════
#  UTILITAIRES INTERNES
# ══════════════════════════════════════════════════════════════════════

## env-check : Vérifier que .env existe (crée depuis .env.example si absent)
env-check:
	@if [ ! -f .env ]; then \
		printf "$(YELLOW)⚠  .env absent — création depuis .env.example$(RESET)\n"; \
		cp .env.example .env; \
		printf "$(GREEN)✓ .env créé. Éditez-le selon votre environnement.$(RESET)\n"; \
	fi
	@if [ -f .env ]; then export $$(grep -v '^#' .env | grep -v '^$$' | xargs) 2>/dev/null || true; fi

