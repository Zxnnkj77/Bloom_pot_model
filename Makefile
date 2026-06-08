.PHONY: setup test validate-evidence check

setup:
	python -m pip install -r requirements-dev.txt

test:
	python -m pytest -q

validate-evidence:
	python plant_evidence.py validate

check: test validate-evidence
