# ===========================================================================
# NADDP — canonical task runner (GNU make).
#
# This is the source of truth for every developer/CI command. Windows machines
# have no GNU make, so they use ./make.ps1, which exposes the IDENTICAL target
# list and delegates to the same underlying commands. If you change a recipe
# here, change make.ps1 in the same commit — no logic drift.
#
# Ports: web 3000 · api 8000 · postgres 5433 (host) -> 5432 (container).
# ===========================================================================

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# --- Overridable tools and paths -------------------------------------------
DOCKER_COMPOSE ?= docker compose
UV             ?= uv
PNPM           ?= pnpm
API_DIR        ?= apps/api
WEB_DIR        ?= apps/web
CONTRACTS_DIR  ?= packages/contracts
SEED_SCRIPT    ?= data/demo-seed/seed.py
API_PORT       ?= 8000
WEB_PORT       ?= 3000
API_URL        ?= http://localhost:$(API_PORT)
DB_SERVICE     ?= db
DB_USER        ?= naddp
DB_NAME        ?= naddp
DB_WAIT_TRIES  ?= 60

.PHONY: help install db-up db-down migrate dev seed demo-reset test lint typecheck gen-client clean

help: ## Show this help
	@printf '\nNADDP — Nigeria-Australia Digital Diplomacy Platform (DEMO / SYNTHETIC DATA ONLY)\n\n'
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
	@printf '\nWindows without GNU make: use  %s <target>\n\n' '.\make.ps1'

install: ## Install JS + Python dependencies (pnpm workspace, uv project)
	$(PNPM) install
	cd $(API_DIR) && $(UV) sync

db-up: ## Start Postgres (pgvector) on host port 5433 and wait until healthy
	$(DOCKER_COMPOSE) up -d $(DB_SERVICE)
	@printf 'waiting for postgres to report healthy'; \
	for i in $$(seq 1 $(DB_WAIT_TRIES)); do \
	  status="$$($(DOCKER_COMPOSE) ps --format '{{.Health}}' $(DB_SERVICE) 2>/dev/null || true)"; \
	  if [ "$$status" = "healthy" ]; then printf ' ok\n'; exit 0; fi; \
	  printf '.'; sleep 1; \
	done; \
	printf '\npostgres was not healthy after $(DB_WAIT_TRIES)s\n' >&2; \
	$(DOCKER_COMPOSE) logs --tail=50 $(DB_SERVICE) >&2 || true; \
	exit 1

db-down: ## Stop the database container (the named volume is preserved)
	$(DOCKER_COMPOSE) down

migrate: ## Apply all Alembic migrations (alembic upgrade head)
	cd $(API_DIR) && $(UV) run alembic upgrade head

dev: db-up migrate ## Run db + api (:8000) + web (:3000) together; Ctrl+C stops all
	@printf 'api -> %s\nweb -> http://localhost:%s\n\n' '$(API_URL)' '$(WEB_PORT)'
	@( cd $(API_DIR) && $(UV) run uvicorn app.main:app --reload --host 0.0.0.0 --port $(API_PORT) ) & \
	api_pid=$$!; \
	( $(PNPM) --filter @naddp/web dev --port $(WEB_PORT) ) & \
	web_pid=$$!; \
	trap 'kill "$$api_pid" "$$web_pid" 2>/dev/null || true' INT TERM EXIT; \
	wait

seed: ## Load the synthetic demo dataset (hero thread + citation registry)
	$(UV) run --project $(API_DIR) python $(SEED_SCRIPT)

demo-reset: ## Drop the schema, re-migrate and re-seed — restores a clean demo state
	$(MAKE) db-up
	$(DOCKER_COMPOSE) exec -T $(DB_SERVICE) psql -v ON_ERROR_STOP=1 -U $(DB_USER) -d $(DB_NAME) \
	  -c 'DROP SCHEMA IF EXISTS public CASCADE;' \
	  -c 'CREATE SCHEMA public;' \
	  -c 'GRANT ALL ON SCHEMA public TO $(DB_USER);' \
	  -c 'GRANT ALL ON SCHEMA public TO public;'
	$(DOCKER_COMPOSE) exec -T $(DB_SERVICE) psql -v ON_ERROR_STOP=1 -U $(DB_USER) -d $(DB_NAME) \
	  -f /docker-entrypoint-initdb.d/001_extensions.sql
	$(MAKE) migrate
	$(MAKE) seed
	@# The AI Gateway memoises snapshots AND misses, and the citation registry with them.
	@# A reset that left either warm serves the previous seed's answer against the new
	@# seed's evidence ids, which stage 8 then refuses — on stage. The caches are
	@# process-local: this clears them here and prints the restart a running API needs.
	cd $(API_DIR) && $(UV) run python -m app.ai.reset
	@printf '\ndemo-reset complete — clean seeded state restored\n'

test: ## Run the API test suite plus web lint + typecheck
	cd $(API_DIR) && $(UV) run pytest
	$(PNPM) --filter @naddp/web lint
	$(PNPM) --filter @naddp/web typecheck

lint: ## ruff check + ruff format --check + mypy (api); eslint (web)
	cd $(API_DIR) && $(UV) run ruff check .
	cd $(API_DIR) && $(UV) run ruff format --check .
	cd $(API_DIR) && $(UV) run mypy app
	$(PNPM) --filter @naddp/web lint

typecheck: ## mypy (api); tsc --noEmit (web)
	cd $(API_DIR) && $(UV) run mypy app
	$(PNPM) --filter @naddp/web typecheck

gen-client: ## Generate the typed OpenAPI client into packages/contracts (starts api if needed)
	@started=0; \
	if ! curl -fsS -o /dev/null '$(API_URL)/openapi.json'; then \
	  printf 'api not reachable — starting a temporary instance on :%s\n' '$(API_PORT)'; \
	  ( cd $(API_DIR) && $(UV) run uvicorn app.main:app --host 127.0.0.1 --port $(API_PORT) >/dev/null 2>&1 ) & \
	  started=$$!; \
	  for i in $$(seq 1 40); do \
	    if curl -fsS -o /dev/null '$(API_URL)/openapi.json'; then break; fi; \
	    sleep 1; \
	  done; \
	fi; \
	rc=0; \
	mkdir -p '$(CONTRACTS_DIR)/src/generated'; \
	if curl -fsS '$(API_URL)/openapi.json' -o '$(CONTRACTS_DIR)/openapi.json'; then \
	  $(PNPM) --filter @naddp/contracts generate || rc=$$?; \
	else \
	  printf 'could not fetch %s/openapi.json\n' '$(API_URL)' >&2; rc=1; \
	fi; \
	if [ "$$started" != "0" ]; then kill "$$started" 2>/dev/null || true; fi; \
	if [ "$$rc" -ne 0 ]; then exit "$$rc"; fi; \
	printf 'wrote %s/src/generated/openapi.d.ts\n' '$(CONTRACTS_DIR)'

clean: ## Remove build output, caches and generated client artefacts
	rm -rf '$(WEB_DIR)/.next' '$(WEB_DIR)/out' '$(WEB_DIR)/coverage' '$(WEB_DIR)/tsconfig.tsbuildinfo'
	# NOT '$(CONTRACTS_DIR)/src/generated' as a directory: it holds the committed
	# openapi.d.ts placeholder the workspace typechecks against before gen-client has
	# ever run. Deleting it dead-ends `pnpm typecheck` and `next build`.
	rm -rf '$(CONTRACTS_DIR)/dist' '$(CONTRACTS_DIR)/openapi.json'
	rm -rf .turbo node_modules/.cache
	rm -rf '$(API_DIR)/.pytest_cache' '$(API_DIR)/.mypy_cache' '$(API_DIR)/.ruff_cache' '$(API_DIR)/htmlcov' '$(API_DIR)/.coverage'
	find . -type d -name __pycache__ -not -path './node_modules/*' -prune -exec rm -rf {} + 2>/dev/null || true
	@printf 'clean\n'
