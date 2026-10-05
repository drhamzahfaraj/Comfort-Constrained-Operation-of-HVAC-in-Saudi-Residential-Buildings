# Contributing

This repository accompanies the paper; its primary purpose is
reproducibility. Bug reports and reproducibility issues are welcome.

- Install: `pip install -e .[dev]`
- Run the test suite before proposing changes: `make test`
- The simulator `src/hvac_savings/model.py` is the single source of truth for
  every reported number; changes to it must keep `make test` passing (the tests
  assert the paper's headline values).
