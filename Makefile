# Simulated A/B test of a mobile-game onboarding change (SYNTHETIC DATA).
PYTHON ?= python

.PHONY: all data power cohorts analysis report site test recovery clean

all: data cohorts analysis report site test recovery

data:            ## Generate synthetic raw event data (+ ground truth for validation)
	$(PYTHON) -m src.simulate

power:           ## Sample-size / MDE tables and power curve (pre-registration inputs)
	$(PYTHON) -m src.power

cohorts:         ## Run the SQL layer in DuckDB -> data/processed/*.parquet
	$(PYTHON) -m src.cohorts

analysis: power  ## Statistical analysis -> outputs/tables, outputs/figures
	$(PYTHON) -m src.analysis

report:          ## Render docs/02_experiment_readout.md from output tables
	$(PYTHON) -m src.report

site:            ## Build the interactive results website -> site/index.html
	$(PYTHON) -m src.site

test:            ## Unit + integration + effect-recovery tests
	$(PYTHON) -m pytest -q

recovery:        ## Validation only: compare estimates with simulated ground truth
	$(PYTHON) -m src.recovery_check

clean:
	rm -rf data/raw/*.parquet data/raw/*.json data/processed/*.parquet outputs/validation
