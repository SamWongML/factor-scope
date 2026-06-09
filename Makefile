# factor-scope — developer entrypoints. Uses `uv` as the package manager.
.DEFAULT_GOAL := help
.PHONY: help setup test unit system lint typecheck check run clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Create the uv venv and install the project (+dev, +store extras)
	uv venv
	uv pip install -e ".[dev,store]"

test: ## Run the full test suite (unit + integration + system), offline
	uv run pytest

unit: ## Run only the fast unit tests
	uv run pytest -m unit

system: ## Run the end-to-end system test (the "nothing is broken" gate)
	uv run pytest -m system

lint: ## Lint with ruff
	uv run ruff check factor_scope tests

typecheck: ## Type-check with mypy
	uv run mypy factor_scope

check: lint typecheck test ## Everything CI runs

run: ## Run the engine over bundled fixtures (offline) and print the morning artifact
	uv run factor-scope run --offline

clean: ## Remove build/cache artifacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache dist build out *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
