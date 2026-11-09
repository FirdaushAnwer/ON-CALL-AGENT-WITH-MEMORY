.PHONY: install run test data seed eval verify

install:
	pip install -r requirements.txt

run:
	uvicorn app.main:app --reload --port 8000

test:
	python -m pytest -v

data:
	python scripts/generate_incidents.py

seed:
	python scripts/seed_memory.py --per-pattern 1

verify:
	python scripts/verify_10_incidents.py

eval:
	python scripts/eval_learning_curve.py --waves 3
