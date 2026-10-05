PY ?= python
export PYTHONPATH := src:.

.PHONY: help install results benchmark benchmark-parse drawings figures optimise csv test paper all reproduce clean-results clean

help:
	@echo "make install          - editable install (pip install -e .[dev])"
	@echo "make results          - run every analysis block            -> results/parts/*.json, results/results.json"
	@echo "make benchmark        - EnergyPlus comparison (needs ENERGYPLUS_DIR): BESTEST-style 600/900 and storey box -> results/comparative.json"
	@echo "make benchmark-parse  - the same from the stored EnergyPlus runs (no EnergyPlus needed) -> results/comparative.json"
	@echo "make drawings         - regenerate the DXF drawing set and PDF sheets (src/drawings/) and the Fig. 3 panels (figures/)"
	@echo "make figures          - regenerate the data-driven figures   -> figures/"
	@echo "make optimise         - compare the 49 thermostat policies (experiments/optimiser/)"
	@echo "make csv              - export every paper table and result block as CSV -> results/csv/"
	@echo "make paper            - compile paper/main.tex -> paper/paper.pdf"
	@echo "make test             - run the reproduction tests"
	@echo "make all              - results + figures + csv"
	@echo "make reproduce        - FULL PIPELINE from scratch: delete results, rerun every block, EnergyPlus parse,"
	@echo "                        drawings, figures, paper, CSV, tests"

install:
	pip install -e .[dev]

results:
	$(PY) -m hvac_savings.reproduce

benchmark:
	$(PY) -m hvac_savings.comparative

benchmark-parse:
	$(PY) -m hvac_savings.comparative --parse-only

drawings:
	cd src/drawings && $(PY) make_dxf.py && $(PY) make_dxf_single.py && $(PY) make_building.py && $(PY) export_pdf.py && $(PY) make_figure_panels.py

figures:
	$(PY) -m hvac_savings.figures

optimise:
	$(PY) -m experiments.optimiser.optimiser

csv:
	$(PY) src/paper_tools/export_csv.py

test:
	$(PY) -m pytest -q

paper:
	cd paper && pdflatex -interaction=nonstopmode main.tex && bibtex main && pdflatex -interaction=nonstopmode main.tex && pdflatex -interaction=nonstopmode main.tex
	cp paper/main.pdf paper/paper.pdf

all: results figures csv

clean-results:
	rm -rf results/parts results/results.json results/csv

reproduce: clean-results results benchmark-parse drawings figures paper csv test
	@echo "(the numbers in paper/main.tex are the ones in results/; tests/test_reproduction.py checks the headline values)"

clean:
	rm -rf src/hvac_savings/__pycache__ src/drawings/__pycache__ tests/__pycache__ src/*.egg-info build dist
	cd paper && rm -f *.aux *.log *.out *.bbl *.blg *.toc main.pdf
	@echo "(results/energyplus is kept: make benchmark-parse needs it; delete it by hand to force a fresh EnergyPlus run)"
