.PHONY: install install-dev setup-db migrate lint format test test-unit test-integration clean

# ─────────────────────────────────────────────
#  Setup
# ─────────────────────────────────────────────
install:
	pip install -e .

install-dev:
	pip install -e ".[dev]"
	pre-commit install

setup-db:
	python scripts/setup_database.py

migrate:
	python scripts/setup_database.py --run-migrations

# ─────────────────────────────────────────────
#  Code Quality
# ─────────────────────────────────────────────
lint:
	ruff check civitas/ tests/
	mypy civitas/

format:
	ruff format civitas/ tests/
	ruff check --fix civitas/ tests/

# ─────────────────────────────────────────────
#  Testing
# ─────────────────────────────────────────────
test:
	pytest tests/ -v

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v --timeout=120

# ─────────────────────────────────────────────
#  Ingestion
# ─────────────────────────────────────────────
ingest:
	python scripts/ingest.py --path $(path) --space $(space)

# ─────────────────────────────────────────────
#  Cleanup
# ─────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -name "*.pyc" -delete
	find . -name ".coverage" -delete
	rm -rf .pytest_cache/ .mypy_cache/ htmlcov/ dist/ build/
