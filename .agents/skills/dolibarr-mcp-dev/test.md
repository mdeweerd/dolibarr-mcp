# Test — suite, coverage, integration, pre-commit

## Unit tests

```bash
pytest                                   # full suite (no live Dolibarr needed)
pytest --cov=src/dolibarr_mcp --cov-report=term-missing
python3 -m pytest                        # if `pytest` is not on PATH
```

Layout: `tests/test_*.py` (unit, HTTP layer mocked), `tests/integration/` (marked
`@pytest.mark.integration`, skipped without credentials).

## Integration tests

```bash
export DOLIBARR_URL="https://your-dolibarr.example.com/api/index.php"
export DOLIBARR_API_KEY="your-api-key"
pytest -m integration
```

The integration workflow (`.github/workflows/integration.yml`) runs these against a real Dolibarr
instance it provisions in the job. PHP for that instance is pinned to Dolibarr's documented maximum, read
from `htdocs/install/check.php` in the downloaded sources (`$arrayphpmaxversionwarning = array(8, 5, 0);`
on recent sources — warning when the installed PHP is higher; `array(8, 3, 0)` for the `21.0.0` tag used
in CI). The workflow derives `DOLIBARR_MAX_PHP` from that file and fails the job if the installed PHP
exceeds it, so a `DOLIBARR_TEST_REF` bump re-pins PHP automatically.

## Pre-commit

```bash
pipx install pre-commit && pre-commit install   # one-time
pre-commit run --all-files                       # before pushing
```

Hooks include mdformat (wrap 105), pylint, flake8, black, isort, mypy, codespell, and
`no-commit-to-branch --branch main`. The same hooks run in CI (`.github/workflows/pre-commit.yml`); keep
both green.

## Docker sanity check

```bash
docker compose --profile test up dolibarr-mcp-test   # runs test_connection in a container
```
