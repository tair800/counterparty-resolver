# Every target a person or CI actually runs. `make help` lists them.
#
# The split between `check` and `evidence` is the one that matters: `check` needs nothing but the
# repository and runs in seconds, `evidence` reaches GLEIF and rebuilds the corpus. CI runs the
# first on every push and the second on a schedule, because a suite that needs the network to pass
# is a suite that goes red for reasons nobody in the pull request caused.

.DEFAULT_GOAL := help
.PHONY: help install check test lint types fmt corpus holdout evaluate evaluate-holdout demo \
        artifacts screenshots run docker-build docker-run breaches

help: ## List the targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-18s %s\n", $$1, $$2}'

install: ## Sync the environment, dev dependencies included
	uv sync --dev

check: lint types test ## Everything that needs no network. This is the CI gate.

test: ## The full suite: kill criteria, guards, units, adversarial cases, store, console
	uv run python -m pytest

lint: ## ruff, as configured in pyproject.toml
	uv run ruff check .
	uv run ruff format --check .

types: ## mypy --strict over src, tests and scripts
	uv run mypy

fmt: ## Rewrite to the formatter's opinion
	uv run ruff format .
	uv run ruff check --fix .

# --------------------------------------------------------------------------------- the evidence

corpus: ## Fetch GLEIF adjudications and build the labelled corpus (~65 requests)
	uv run python scripts/build_corpus.py

holdout: ## Freeze the split. Commit this BEFORE scoring anything against it.
	uv run python scripts/freeze_holdout.py

evaluate: ## Score the development corpus. Never touches the hold-out.
	uv run python scripts/evaluate.py

evaluate-holdout: ## Spend the hold-out. One-way door: no rule may change afterwards.
	uv run python scripts/evaluate.py --score-holdout

demo: ## Build the console's dataset from the development split
	uv run python scripts/build_demo.py

artifacts: corpus holdout evaluate demo ## Rebuild everything the tests read

screenshots: ## Drive a headless browser over the four screens, light and dark
	uv run playwright install chromium
	uv run python scripts/screenshots.py

breaches: ## Plant a defect into each guard and prove the suite goes red
	uv run python scripts/plant_breaches.py

# ------------------------------------------------------------------------------------- running

run: ## The console, read-only (no approver token configured)
	uv run uvicorn counterparty_resolver.api.app:app --port 8000

docker-build: ## Build the image
	docker build -t counterparty-resolver .

docker-run: ## Run the image, read-only, on :8000
	docker run --rm -p 8000:8000 counterparty-resolver
