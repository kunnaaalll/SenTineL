.PHONY: setup lock test test-one fmt lint typecheck check hooks run serve clean

# Spec targets Python 3.11 (infra/Dockerfile.backend uses python:3.11-slim)
PY ?= python3.11
VENV := .venv
PIP := $(VENV)/bin/pip

setup:
	$(PY) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r backend/requirements-dev.txt

# Local-only API server (no auth in v1 — never expose publicly).
# backend/ is the import root, so uvicorn runs with it on PYTHONPATH.
run:
	cd backend && ../$(VENV)/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000

serve: run

# Regenerate backend/requirements-lock.txt from the current venv.
lock:
	$(PIP) freeze | grep -vE '^(pip|setuptools|wheel)==' > backend/requirements-lock.txt

test:
	$(VENV)/bin/python -m pytest

# Example: make test-one T="backend/tests/test_retrieval.py::test_add_search_round_trip"
test-one:
	$(VENV)/bin/python -m pytest $(T)

fmt:
	$(VENV)/bin/ruff format .
	$(VENV)/bin/ruff check --fix .

lint:
	$(VENV)/bin/ruff format --check .
	$(VENV)/bin/ruff check .

typecheck:
	$(VENV)/bin/mypy .

check: lint typecheck test

hooks:
	$(VENV)/bin/pre-commit install

clean:
	rm -rf $(VENV) .pytest_cache backend/.pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
