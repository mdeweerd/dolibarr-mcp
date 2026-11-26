______________________________________________________________________

## name: dolibarr-mcp-dev description: Load when developing on the dolibarr-mcp project (Python MCP server for Dolibarr ERP/CRM) — setting up the dev environment, running or testing the server, adding MCP tools, or following the repo's documentation and commit conventions.

# Dolibarr MCP development

Working conventions for this repository. Read the focused guide for the task at hand:

- [develop.md](develop.md) — environment setup, running the server (STDIO / HTTP / Docker),
  configuration.
- [test.md](test.md) — running tests, coverage, integration tests, pre-commit hooks.
- [tools.md](tools.md) — adding or modifying an MCP tool (client method → tool registry → dispatch).
- [docs.md](docs.md) — SPECIFICATION.md / PLAN.md / TODO.md discipline, `backups/`, `docs/`, graft graph.

## Key facts

- Source lives in `src/dolibarr_mcp/`; tests in `tests/`; docs in `docs/`.
- Python >= 3.10, `src/` layout, installable with `pip install -e .`; console entry point `dolibarr-mcp`.
- The server is a stateless MCP client of a Dolibarr REST API — never modify Dolibarr itself.
- Dolibarr documents its maximum PHP version in `htdocs/install/check.php` (`$arrayphpmaxversionwarning`,
  e.g. `array(8, 5, 0)` on recent sources; `array(8, 3, 0)` for the 21.0.0 tag pinned in CI). Never
  install a newer PHP than that for the test instance — the integration workflow derives
  `DOLIBARR_MAX_PHP` from this file and fails if the installed PHP exceeds it (see [test.md](test.md)).
- Security floor: HTTP listener refuses non-loopback bind without `MCP_HTTP_TOKEN`; raw API tool is off
  unless `MCP_ENABLE_RAW_API=true`.
- Commit on a feature branch, never directly on `main` (enforced by the `no-commit-to-branch` pre-commit
  hook).
