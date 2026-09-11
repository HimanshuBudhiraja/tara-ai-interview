# Development entry points. Nothing here needs Docker.
VENV ?= .venv
PY   := $(VENV)/bin/python

.PHONY: help setup api candidate recruiter build test test-server personas eval eval-check recommend clean

help:
	@echo "make setup       install python + node dependencies"
	@echo "make api         run the backend on :8000"
	@echo "make candidate   run the candidate app on :5173"
	@echo "make recruiter   run the recruiter console on :5174"
	@echo "make build       build both frontends"
	@echo "make test        unit + contract tests (no server needed)"
	@echo "make test-server run the full suite against a running API"
	@echo "make eval-check  are the configured model ids still live?"
	@echo "make eval        run the model evaluation, then write MODEL_EVALUATION.md"
	@echo "make recommend   write MODEL_RECOMMENDATION.md from the last run"

setup:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q -r requirements.txt pytest
	npm install

api:
	$(PY) -m uvicorn services.api.app:app --port 8000 --reload

candidate:
	npm run dev:candidate

recruiter:
	npm run dev:recruiter

build:
	npm run build

test:
	$(PY) -m pytest -q

test-server:
	$(PY) -m pytest -q -m "server or not server"

personas:
	$(PY) tests/personas.py --persona strong
	$(PY) tests/personas.py --persona thin
	$(PY) tests/personas.py --persona messy

eval-check:
	$(PY) -m evals.cli check-models

eval:
	$(PY) -m evals.cli run --all

recommend:
	$(PY) -m evals.cli recommend

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf apps/*/dist apps/*/*.tsbuildinfo
