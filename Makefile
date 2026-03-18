.PHONY: setup setup-backend setup-frontend dev backend frontend test test-backend test-frontend check lint build build-wheel clean

# --- Setup ---

setup: setup-backend setup-frontend ## Full project setup

setup-backend: ## Create venv and install backend with all extras
	python3 -m venv .venv
	.venv/bin/pip install -e ".[server,test,dev]"

setup-frontend: ## Install frontend dependencies
	npm --prefix web install

# --- Development ---

dev: ## Start backend + frontend (run in foreground)
	@echo "Starting backend on :8080 and frontend on :3000..."
	@trap 'kill 0' EXIT; \
		.venv/bin/overdrive server & \
		npm --prefix web run dev & \
		wait

backend: ## Start backend only
	.venv/bin/overdrive server

frontend: ## Start frontend dev server only
	npm --prefix web run dev

# --- Testing ---

test: test-backend test-frontend ## Run all tests

test-backend: ## Run backend tests
	.venv/bin/pytest -n auto -q

test-frontend: ## Run frontend tests
	npm --prefix web run test

# --- Quality ---

check: ## Full CI check (lint + test + build)
	.venv/bin/ruff check src/
	.venv/bin/pytest -n auto -q
	npm --prefix web run check

lint: ## Lint backend
	.venv/bin/ruff check src/

build: ## Build frontend for production
	npm --prefix web run build

build-wheel: build ## Build Python wheel with bundled frontend
	rm -rf src/overdrive/web_dist
	cp -r web/dist src/overdrive/web_dist
	.venv/bin/python -m build

# --- Utilities ---

clean: ## Remove build artifacts and caches
	rm -rf .venv/
	rm -rf web/node_modules/
	rm -rf web/dist/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf src/overdrive.egg-info/

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
