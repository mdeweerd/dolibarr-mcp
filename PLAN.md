# Dolibarr MCP Server — Plan

## Phase 0 — Environment and scaffolding (done)

Dev environment, packaging and CI baseline. Exists so nothing else lands on sand.

- Python `src/` layout with `pyproject.toml`, console entry point `dolibarr-mcp`.
- `.env.example`, `docker/Dockerfile`, `docker-compose.yml`.
- Pre-commit hooks + GitHub Actions workflow (`.pre-commit-config.yaml`,
  `.github/workflows/pre-commit.yml`).
- Unit test suite under `tests/` with `pytest`, integration tests marked and skipped by default.
- `graft/` repo context graph and `AGENTS.md`.

Acceptance: `pip install -e .` works; `pytest` green; pre-commit CI green. FR-5, NFR-5.

## Phase 1 — Core client and STDIO server (done)

Async Dolibarr REST client + MCP STDIO server with the base tool set.

- `dolibarr_client.py` (aiohttp wrapper, auth, retries, pagination).
- `dolibarr_mcp_server.py` (MCP tool registry, STDIO transport, `get_status`, `test_connection`).
- `config.py` (pydantic-settings, env + `.env`).

Acceptance: STDIO server answers `tools/list`; `test_connection` validates credentials. FR-1, FR-5.1.

## Phase 2 — CRUD tool groups (done)

Full CRUD across users, customers, products, invoices, orders, projects, contacts.

Done — see SPECIFICATION FR-2.1–FR-2.7. Invoices additionally support draft creation and line management
(`add_invoice_line`, `update_invoice_line`, `delete_invoice_line`).

## Phase 3 — Search and token efficiency (done)

Server-side filtering tools so agents avoid loading full tables.

Done — `search_products_by_ref`, `search_products_by_label`, `resolve_product_ref`, `search_customers`,
`search_projects`, SQL-filter escaping. FR-3.

## Phase 4 — Documents and tickets (done)

- `get_documents` / `download_document` with PDF embedded-file extraction (Factur-X). FR-4.
- Ticket tools: `get_tickets`, `get_ticket_by_ref`, `search_tickets`, `create_ticket`,
  `add_ticket_message`, `update_ticket`. FR-2.8.

## Phase 5 — HTTP transport and security hardening (done)

- Streamable HTTP transport, bearer-token auth, loopback-only default bind, optional CORS.
- `MCP_READ_ONLY` and `MCP_ENABLE_RAW_API` policy filters. FR-5.2, FR-5.3, FR-6, NFR-1.

## Phase 6 — Supplier recurring invoice templates (done)

- `get_supplier_invoice_templates` / `get_supplier_invoice_template` / `update_supplier_invoice_template`
  / `update_supplier_invoice_template_line` against the would-be `supplierinvoices/templates` REST route,
  with an actionable structured error on instances without the endpoint (stock Dolibarr ships none;
  UI-only via `fourn/facture/card-rec.php`). FR-8. Use case: annual rent revision (ILAT, 1 December).

## Later / on demand

- Additional Dolibarr resources not yet wrapped (e.g. proposals, bank accounts, stock).
- Per-tool rate limiting or caching layer.
- Multi-instance support (several Dolibarr backends behind one server).

These are deliberately not in the current phases; promote them by updating SPECIFICATION.md first, in the
same commit as the code.
