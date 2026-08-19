\
# Cartograph dev tasks. On Windows without `make`, use `.\make.ps1 <target>`
# instead — it mirrors every target here.

.PHONY: help setup dev test test-fast test-cov lint format typecheck check \
        run serve tree mcp clean clean-logs

TASK ?= convert unittest tests to pytest
REPO ?= tests/fixtures/sample_repo

help:
	@echo "Cartograph dev tasks:"
	@echo "  make setup       uv sync (install runtime deps into .venv)"
	@echo "  make dev         uv sync --extra dev (adds ruff/mypy/pytest-asyncio)"
	@echo "  make test        run the full pytest suite, verbose"
	@echo "  make test-fast   run the full pytest suite, quiet"
	@echo "  make test-cov    run pytest with coverage (requires 'make dev')"
	@echo "  make lint        ruff check ."
	@echo "  make format      ruff format ."
	@echo "  make typecheck   mypy src/cartograph"
	@echo "  make check       lint + typecheck + test-fast"
	@echo "  make demo        full pipeline on a throwaway copy of the sample repo"
	@echo "  make models      list Groq model ids your API key can use"
	@echo "  make run         cartograph run \$$(REPO) \"\$$(TASK)\""
	@echo "                   override with: make run REPO=... TASK=\"...\""
	@echo "  make serve       start the local web UI (http://127.0.0.1:8765)"
	@echo "  make tree        build a standalone dependency tree for \$$(REPO)"
	@echo "  make mcp         start the MCP server (stdio transport)"
	@echo "  make clean       remove __pycache__/.pytest_cache/.ruff_cache/.mypy_cache"
	@echo "  make clean-logs  remove logs/*.log"

setup:
	uv sync

dev:
	uv sync --extra dev

test:
	uv run pytest -v

test-fast:
	uv run pytest -q

test-cov:
	uv run pytest --cov=cartograph --cov-report=term-missing

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy src/cartograph

check: lint typecheck test-fast

demo:
	uv run cartograph demo

models:
	uv run cartograph models

run:
	uv run cartograph run $(REPO) "$(TASK)"

serve:
	uv run cartograph serve

tree:
	uv run cartograph tree $(REPO)

mcp:
	uv run python -m cartograph.mcp_server.server

clean:
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .mypy_cache

clean-logs:
	rm -f logs/*.log
