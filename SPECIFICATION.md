# Dolibarr MCP Server — Specification

## Purpose and context

`dolibarr-mcp` is a Model Context Protocol (MCP) server that exposes a Dolibarr ERP/CRM instance to
MCP-aware AI clients (Claude Desktop, Open WebUI, LM Studio, MCPO). It wraps the Dolibarr REST API behind
a curated set of typed tools so an agent can manage customers, products, invoices, orders, contacts,
projects, users, documents and tickets without loading raw API payloads into its context window.

The project mirrors the architecture and documentation layout of the sibling
[`prestashop-mcp`](https://github.com/latinogino/prestashop-mcp) repository.

### Hard constraints

- Target platform: Dolibarr REST API (developed against v21.x, tested against instances exposing
  `/api/index.php`).
- Runtime: Python >= 3.10. Async/await throughout (`aiohttp` for HTTP, `mcp` SDK for the protocol).
- Deployment models: STDIO (default, for Claude Desktop), Streamable HTTP (for Open WebUI and remote
  clients), and Docker Compose for the HTTP listener.
- No modification of the Dolibarr server itself; the MCP server is a pure API client.
- Security floor: the HTTP listener must refuse to bind a non-loopback interface without a bearer token
  (`MCP_HTTP_TOKEN`); Docker Compose must refuse to start without it.

## Roles

| Role                | What they may do                                                                  |
| ------------------- | --------------------------------------------------------------------------------- |
| MCP host / AI agent | Call any exposed tool; subject to `MCP_READ_ONLY` and `MCP_ENABLE_RAW_API` policy |
| Dolibarr admin      | Configure the instance, issue API keys, grant least-privilege rights              |
| Developer           | Run tests, extend tool coverage, maintain docs and CI                             |

## Functional requirements

### FR-1 — Connectivity

- FR-1.1: `get_status` returns the Dolibarr version and access-lock state.
- FR-1.2: `test_connection` / `python -m dolibarr_mcp.test_connection` validates URL + API key standalone
  before wiring into an MCP host.

### FR-2 — CRUD tool groups

Each resource group exposes list, get-by-id/ref, create, update, delete (where the API allows):

- FR-2.1 Users: `get_users`, `get_user_by_id`, `create_user`, `update_user`, `delete_user`
- FR-2.2 Customers (third parties): `get_customers`, `get_customer_by_id`, `create_customer`,
  `update_customer`, `delete_customer`
- FR-2.3 Products: `get_products`, `get_product_by_id`, `create_product`, `update_product`,
  `delete_product`
- FR-2.4 Invoices: `get_invoices`, `get_invoice_by_id`, `create_invoice`, `create_invoice_draft`,
  `update_invoice`, `delete_invoice`, `add_invoice_line`, `update_invoice_line`, `delete_invoice_line`
- FR-2.5 Orders: `get_orders`, `get_order_by_id`, `create_order`, `update_order`, `delete_order`
- FR-2.6 Projects: `get_projects`, `get_project_by_id`, `create_project`, `update_project`,
  `delete_project`
- FR-2.7 Contacts: `get_contacts`, `get_contact_by_id`, `create_contact`, `update_contact`,
  `delete_contact`
- FR-2.8 Tickets: `get_tickets`, `get_ticket_by_ref`, `create_ticket`, `add_ticket_message`,
  `update_ticket`

Acceptance: every tool returns structured JSON; errors are returned as structured error results, never as
unhandled exceptions.

### FR-3 — Server-side search (token efficiency)

- FR-3.1: `search_products_by_ref`, `search_products_by_label`, `resolve_product_ref`,
  `search_customers`, `search_projects`, `search_tickets` filter server-side via Dolibarr `sqlfilters` so
  the agent never paginates thousands of records.
- FR-3.2: SQL filter values are escaped (`_escape_sqlfilter`) to prevent injection through tool
  arguments.

### FR-4 — Documents

- FR-4.1: `get_documents` lists files attached to an object by ref or id.
- FR-4.2: `download_document` returns a file as an embedded MCP resource (10 MB limit) and extracts files
  embedded in a PDF (e.g. Factur-X `factur-x.xml`) as text.

### FR-5 — Transports

- FR-5.1: STDIO transport by default (`python -m dolibarr_mcp.dolibarr_mcp_server`).
- FR-5.2: Streamable HTTP transport when `MCP_TRANSPORT=http`, with bearer-token auth, optional CORS
  (`MCP_CORS_ORIGINS`), and loopback-only default binding.
- FR-5.3: Docker Compose service publishes the HTTP listener on `127.0.0.1` only by default.

### FR-6 — Tool policy

- FR-6.1: `MCP_READ_ONLY=true` exposes only read tools (no create/update/delete/validate).
- FR-6.2: `dolibarr_raw_api` is disabled unless `MCP_ENABLE_RAW_API=true`.

### FR-7 — Accounting and back-office toolset (issue #37)

- FR-7.1 Error surfacing: every 4xx/5xx tool result carries the Dolibarr raw error body plus HTTP status,
  `correlation_id` and endpoint (sub-issue 5).
- FR-7.2 Dictionaries: `get_dictionary(name)` reads setup dictionaries, incl. `payment_types` and
  `event_types` (8).
- FR-7.3 Contacts: `update_contact` sends `socid` and `fk_soc` together; the phone null-read quirk is
  documented (12).
- FR-7.4 Third-party search: `search_thirdparties(query, kind=customer|supplier|any)` with apostrophe
  wildcard (13).
- FR-7.5 Supplier invoices: `get_supplier_invoices` (compact, single-condition filters, `ref_supplier`),
  `create_supplier_invoice` (`type=2` for credit note), `add_supplier_invoice_line` /
  `update_supplier_invoice_line` (description/pu_ht only), `validate_supplier_invoice`,
  `set_supplier_invoice_to_draft`, `set_supplier_invoice_paid`, `update_supplier_invoice`; adding a line
  to a validated invoice returns an explicit error (2).
- FR-7.6 Payments: `pay_supplier_invoice` (query-param contract, VIR code via dictionary,
  `closepaidinvoices` yes/no), `pay_invoice` guards supplier ids to `pay_supplier_invoice`,
  `get_invoice_payments(invoice_id, direction)` (1).
- FR-7.7 Social contributions: `get_social_contributions` (compact), `create_social_contribution` /
  `update_social_contribution` (`fk_type`/period server names), `add_social_contribution_payment`;
  version-aware skip when the instance lacks `/socialcontributions` (3).
- FR-7.8 Bank: `get_bank_accounts`, `get_bank_lines` (single-condition no-prefix filters, merged
  locally), `get_bank_line_links` (batch), `create_bank_line` (4).
- FR-7.9 Documents: `upload_document` / `delete_document` (JSON body, modulepart path conventions incl.
  supplier_invoice numeric prefix, tax, bank; a 404 delete counts as success); `get_documents` /
  `download_document` extended to supplier_invoice/tax/bank with explicit degradation when listing
  endpoints fail (6, 7).
- FR-7.10 Tasks/time: `create_task`, `add_task_time` / `update_task_time` / `delete_task_time`,
  `get_task_timespent`, `get_project_timespent` (timespent_line\_\* mapping, `total_duration`, fallback
  reads) (9).
- FR-7.11 Fichinter: `get_interventions`, `get_intervention`, `create_intervention`,
  `add_intervention_line` (description/date/duration) (10).
- FR-7.12 Agenda: `create_agenda_event` (`type_code` via dictionary, e.g. AC_TEL), `get_agenda_event`;
  `contact_id` is not persisted upstream (11).

### FR-8 — Supplier recurring invoice templates

- FR-8.1: `get_supplier_invoice_templates` lists templates (llx_facture_fourn_rec), optionally per
  supplier (`supplier_id` → `sqlfilters (t.fk_soc:=:N)`), as compact rows (id, ref, title, ref_supplier,
  socid).
- FR-8.2: `get_supplier_invoice_template` returns one template with its lines (llx_facture_fourn_det_rec)
  as compact rows (id, description, pu_ht, qty, tva_tx).
- FR-8.3: `update_supplier_invoice_template` updates title (label alias) and `ref_supplier`;
  `update_supplier_invoice_template_line` updates a line's `pu_ht`/`qty` with read-merge-PUT (a PUT
  resets unsent fields).
- FR-8.4 Endpoint availability: stock Dolibarr (21.0.0, 24.0.4, upstream develop) exposes no REST route
  for these objects (only customer templates via `/invoices/templates`). The tools call the would-be
  `supplierinvoices/templates` route; when it is absent — HTTP 404/501, or 400 "invalid value specified
  for `id`" because the router falls through to `supplierinvoices/{id}` (verified on 21.0.0 and 24.0.4) —
  they return a structured error naming the missing endpoint and the UI path
  (`fourn/facture/card-rec.php`), and start working automatically once a release ships the endpoint. No
  DB access layer is added — the server stays a pure REST client. Integration tests skip on those
  statuses (same guard pattern as FR-7.7).

## Data model

The server is stateless; it holds only:

- `Config` (pydantic-settings): `DOLIBARR_URL`, `DOLIBARR_API_KEY`, transport/security/logging options
  loaded from environment and `.env`.
- An `aiohttp.ClientSession` per run, authenticated with the `DOLAPIKEY` header.
- MCP tool registry: a static dict of tool name → handler, filtered by policy at list time.

All business data lives in the Dolibarr instance; the server never persists it.

## Non-functional requirements

- NFR-1 Security: bearer token mandatory for non-loopback HTTP; `DOLAPIKEY` never logged; raw API tool
  off by default; read-only mode available.
- NFR-2 Reliability: HTTP client retries with backoff (`max_retries`, `retry_backoff_seconds`).
- NFR-3 Observability: logs to stderr (STDIO) with configurable `LOG_LEVEL`; structured error results
  include correlation ids.
- NFR-4 Testability: unit tests mock the HTTP layer; `integration`-marked tests hit a real instance and
  are skipped without credentials.
- NFR-5 Packaging: `pip install -e .` with a `dolibarr-mcp` console entry point; Docker image via
  `docker/Dockerfile`.

## Acceptance criteria summary

- `pytest` passes (unit suite) without a live Dolibarr instance.
- `pytest -m integration` passes against a real instance when credentials are provided.
- STDIO server starts and answers `tools/list` with the policy-filtered tool set.
- HTTP server refuses non-loopback bind without `MCP_HTTP_TOKEN`.
- Pre-commit hooks (`.pre-commit-config.yaml`) pass on all files.

## Out of scope

- Modifying or proxying the Dolibarr core; this is a client only.
- Row-level permissions beyond what the Dolibarr API key grants.
- A web UI of its own; MCP hosts provide the interface.
- Support for Dolibarr versions older than the REST API surface used here.
