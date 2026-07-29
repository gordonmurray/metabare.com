# MetaBare
#
# Targets reveal the command they run rather than hiding it behind a wrapper.
# If a target is doing something you would not want to run by hand, that is a
# bug in the target.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

UV ?= uv
COMPOSE ?= docker compose
API_URL ?= http://localhost:8080
ENV ?= dev
TF_DIR := infra/environments/$(ENV)

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ---- local development ------------------------------------------------------

.PHONY: install
install: ## Create the virtualenv and install all extras
	$(UV) sync --all-extras

.PHONY: up
up: ## Start the local stack (MinIO, Firn, API)
	$(COMPOSE) up -d --build

.PHONY: down
down: ## Stop the local stack and delete its volumes
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Follow local stack logs
	$(COMPOSE) logs -f

.PHONY: smoke
smoke: ## Ingest a note and search for it against the running local stack
	API_URL=$(API_URL) ./scripts/smoke.sh

# ---- quality ----------------------------------------------------------------

.PHONY: fmt
fmt: ## Format Python and Terraform
	$(UV) run ruff format .
	$(UV) run ruff check . --fix
	@command -v terraform >/dev/null && terraform fmt -recursive infra/ || echo "terraform not installed, skipping"

.PHONY: lint
lint: ## Lint Python, shell and Terraform
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	@command -v shellcheck >/dev/null && shellcheck scripts/*.sh || echo "shellcheck not installed, skipping"
	@command -v terraform >/dev/null && terraform fmt -check -recursive infra/ || echo "terraform not installed, skipping"

.PHONY: typecheck
typecheck: ## Static type check
	$(UV) run mypy

.PHONY: test
test: ## Run the unit test suite
	$(UV) run pytest tests/ -m "not integration"

.PHONY: test-integration
test-integration: ## Run tests that need the local stack running
	$(UV) run pytest tests/ -m integration

.PHONY: check
check: lint typecheck test ## Everything CI runs

# ---- evaluation -------------------------------------------------------------

.PHONY: eval-embeddings
eval-embeddings: ## Compare candidate text embedding models (writes to benchmarks/results/)
	$(UV) run python benchmarks/runners/embedding_model_eval.py

# ---- infrastructure ---------------------------------------------------------
#
# Every target below can spend money. `plan` is safe; `apply` is not.
# Read the cost table in the README before `apply`.

.PHONY: init
init: ## terraform init for ENV (default: dev). Needs $(TF_DIR)/backend.hcl
	@test -f $(TF_DIR)/backend.hcl || { \
		echo "Missing $(TF_DIR)/backend.hcl."; \
		echo "Copy $(TF_DIR)/backend.hcl.example and edit it."; \
		exit 1; \
	}
	$(TF_ENV) terraform -chdir=$(TF_DIR) init -backend-config=backend.hcl

# Serialised on purpose. The AWS provider's concurrent connection pool produced
# 302s on iam:CreateRole and signature mismatches on plain reads in this
# environment, while the same calls through the CLI were reliable. Serialising
# fixed it. Roughly doubles apply wall-clock; a half-created cluster costs more.
TF_PARALLELISM ?= 1
TF_ENV := AWS_MAX_ATTEMPTS=10 AWS_RETRY_MODE=adaptive

.PHONY: plan
plan: ## terraform plan for ENV
	$(TF_ENV) terraform -chdir=$(TF_DIR) plan -parallelism=$(TF_PARALLELISM)

.PHONY: apply
apply: ## terraform apply for ENV. COSTS MONEY. Read the README cost table first
	$(TF_ENV) terraform -chdir=$(TF_DIR) apply -parallelism=$(TF_PARALLELISM)

.PHONY: destroy
destroy: ## Destroy ENV entirely
	$(TF_ENV) terraform -chdir=$(TF_DIR) destroy -parallelism=$(TF_PARALLELISM)

.PHONY: deploy
deploy: ## Build, push and deploy the application to ENV
	./scripts/deploy-eks.sh $(ENV)

.PHONY: cost
cost: ## Show the fixed-cost resources this environment creates
	terraform -chdir=$(TF_DIR) output -json fixed_cost_resources 2>/dev/null | jq . \
		|| echo "no fixed_cost_resources output; has $(TF_DIR) been applied?"
