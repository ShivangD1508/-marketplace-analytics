# Everything this project does, in the order you would do it.
#
#   make setup         install Python dependencies
#   make sample-data   generate the dataset (schema-identical to the real one)
#   make build         run and test every model
#   make verify        prove int_events handles late-arriving data
#   make docs          build the documentation site locally
#   make charts        regenerate the three figures in ANALYSIS.md
#   make all           setup -> sample-data -> build -> verify -> charts -> docs

DBT      := dbt --profiles-dir dbt --project-dir dbt
ORDERS   ?= 100000
RAW_DIR  ?= data/raw

.PHONY: all setup sample-data download-data build test verify docs charts serve-docs clean

all: setup sample-data build verify charts docs

setup:
	pip install -r requirements.txt

## Generate the dataset. Deterministic: same output on every machine.
sample-data:
	python scripts/generate_sample_data.py --orders $(ORDERS) --out $(RAW_DIR)

## Fetch the real Olist dataset instead. Needs Kaggle API credentials in
## ~/.kaggle/kaggle.json; see README "Getting the data".
download-data:
	python scripts/download_olist.py --out $(RAW_DIR)

## Run and test everything. This is the command the "done when" bar refers to.
build:
	$(DBT) build

test:
	$(DBT) test

## Behaviour a normal build cannot exercise: late arrivals and idempotency.
verify:
	python scripts/verify_incremental.py

docs:
	$(DBT) docs generate --static
	@echo "Docs site written to dbt/target/static_index.html"

serve-docs: docs
	@python -m http.server 8080 --directory dbt/target

charts:
	python scripts/build_charts.py

clean:
	rm -rf dbt/target dbt/logs data/*.duckdb data/*.duckdb.wal
