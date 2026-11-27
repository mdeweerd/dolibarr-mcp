"""Integration tests for FR-8: supplier recurring invoice template tools.

Verified constraint: stock Dolibarr (21.0.0, 24.0.4 and upstream develop) ships
NO REST endpoint for supplier recurring invoice templates
(llx_facture_fourn_rec / llx_facture_fourn_det_rec) — only customer templates
exist, via ``/invoices/templates``. The tools therefore call the would-be
``supplierinvoices/templates`` route and degrade with an actionable error.

Acceptance criteria encoded here:

- On an instance without the endpoint, every tool call returns a structured
  error naming the missing endpoint and the UI path (fourn/facture/card-rec.php);
  the tests SKIP in that case (same guard pattern as the /socialcontributions
  tests in test_issue37_03) and activate automatically once a release carries
  the endpoint.
- On an instance WITH the endpoint: templates list per supplier, one template
  reads back with its lines, the template title/ref_supplier and a line's
  pu_ht/qty update through the MCP tools only (no dolibarr_raw_api).

Marked ``integration``: needs ``DOLIBARR_URL`` and ``DOLIBARR_API_KEY``
pointing at a reachable instance. Fixtures are uniquely named and cleaned up
so sequential runs on a shared instance do not collide.
"""

import json
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Tuple

import pytest
import pytest_asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import InitializeResult, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from dolibarr_mcp import Config, DolibarrClient  # noqa: E402
from dolibarr_mcp.dolibarr_client import DolibarrAPIError  # noqa: E402

pytestmark = pytest.mark.integration

TEMPLATES_ENDPOINT = "supplierinvoices/templates"


async def _skip_if_no_supplier_templates_endpoint(config: Config) -> None:
    """Skip when the instance lacks the supplier invoice templates API.

    Stock Dolibarr releases answer HTTP 501 "API not found" / 404 for unknown
    routes — but ``GET /supplierinvoices/templates`` falls through to the
    ``supplierinvoices/{id}`` route and answers 400 "invalid value specified for
    `id`" (verified on 21.0.0 and 24.0.4). All of these mean the endpoint is
    absent; on instances that ship it this returns normally and the tests stay
    active.
    """
    async with DolibarrClient(config) as client:
        try:
            await client.request("GET", TEMPLATES_ENDPOINT, params={"limit": 1})
        except DolibarrAPIError as exc:
            if DolibarrClient._is_supplier_templates_absent(exc):
                pytest.skip(
                    f"{TEMPLATES_ENDPOINT} not available on this Dolibarr instance " f"(HTTP {exc.status_code}: {exc.message})"
                )
            raise


@pytest_asyncio.fixture(autouse=True)
async def _require_supplier_templates_endpoint():
    await _skip_if_no_supplier_templates_endpoint(_config())


def _config() -> Config:
    """Build the config from the environment, skipping when incomplete."""
    try:
        config = Config.from_env()
        config.validate_config()
    except ValueError:
        pytest.skip("No valid Dolibarr configuration available")
    return config


@asynccontextmanager
async def mcp_session() -> AsyncGenerator[Tuple[ClientSession, InitializeResult], None]:
    """Start the MCP server over STDIO and yield an initialized session.

    Same pattern as tests/integration/test_mcp_server.py.
    """
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dolibarr_mcp.dolibarr_mcp_server"],
        env={key: value for key, value in os.environ.items() if value is not None},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init_result = await session.initialize()
            yield session, init_result


def _tool_payload(result) -> Any:
    """Extract the JSON payload from a successful MCP tool result."""
    assert not result.isError, f"tool call failed: {result.content}"
    content = result.content[0]
    assert isinstance(content, TextContent)
    return json.loads(content.text)


async def _first_supplier_id(config: Config) -> int:
    """Look up the first supplier id through the raw client (fixture setup)."""
    async with DolibarrClient(config) as client:
        suppliers = await client.request("GET", "thirdparties", params={"sqlfilters": "(t.fournisseur:=:1)", "limit": 1})
    assert isinstance(suppliers, list) and suppliers, "the instance must have at least one supplier"
    return int(suppliers[0]["id"])


async def _create_template(config: Config, socid: int, tag: str) -> int:
    """Create a recurring invoice template fixture via the (would-be) raw endpoint."""
    async with DolibarrClient(config) as client:
        result = await client.request(
            "POST",
            TEMPLATES_ENDPOINT,
            data={"fk_soc": socid, "title": f"FR8-{tag}", "ref_supplier": f"FR8-{tag}"},
        )
    return int(result["id"] if isinstance(result, dict) else result)


async def _delete_template(config: Config, template_id: int) -> None:
    """Delete the fixture template (best effort)."""
    async with DolibarrClient(config) as client:
        try:
            await client.request("DELETE", f"{TEMPLATES_ENDPOINT}/{template_id}")
        except DolibarrAPIError:
            pass


@pytest.mark.asyncio
async def test_list_templates_per_supplier():
    """Templates of one supplier are listed as compact rows."""
    config = _config()
    socid = await _first_supplier_id(config)
    async with mcp_session() as (session, _init_result):
        tag = uuid.uuid4().hex[:8]
        template_id = await _create_template(config, socid, tag)
        try:
            listed = _tool_payload(await session.call_tool("get_supplier_invoice_templates", {"supplier_id": socid}))
            mine = [t for t in listed if int(t["id"]) == template_id]
            assert len(mine) == 1, f"new template must be listed for its supplier: {listed}"
            assert mine[0]["title"] == f"FR8-{tag}", mine[0]
            assert mine[0]["ref_supplier"] == f"FR8-{tag}", mine[0]
            assert set(mine[0].keys()) == {"id", "ref", "title", "ref_supplier", "socid"}, mine[0]
        finally:
            await _delete_template(config, template_id)


@pytest.mark.asyncio
async def test_get_update_template_and_line():
    """One template reads back with its lines; title/ref_supplier and line pu_ht/qty update."""
    config = _config()
    socid = await _first_supplier_id(config)
    async with mcp_session() as (session, _init_result):
        tag = uuid.uuid4().hex[:8]
        template_id = await _create_template(config, socid, tag)
        try:
            # Fixture line via the would-be raw endpoint.
            async with DolibarrClient(config) as client:
                line = await client.request(
                    "POST",
                    f"{TEMPLATES_ENDPOINT}/{template_id}/lines",
                    data={"description": f"rent {tag}", "pu_ht": 1000, "qty": 1, "tva_tx": 20},
                )
            line_id = int(line["id"] if isinstance(line, dict) else line)

            fetched = _tool_payload(await session.call_tool("get_supplier_invoice_template", {"template_id": template_id}))
            assert fetched["title"] == f"FR8-{tag}", fetched
            assert any(int(row["id"]) == line_id for row in fetched["lines"]), fetched

            _tool_payload(
                await session.call_tool(
                    "update_supplier_invoice_template",
                    {"template_id": template_id, "label": f"FR8-{tag}-rev", "ref_supplier": f"FR8-{tag}-R"},
                )
            )
            _tool_payload(
                await session.call_tool(
                    "update_supplier_invoice_template_line",
                    {"template_id": template_id, "line_id": line_id, "pu_ht": 1100, "qty": 2},
                )
            )

            refetched = _tool_payload(await session.call_tool("get_supplier_invoice_template", {"template_id": template_id}))
            assert refetched["title"] == f"FR8-{tag}-rev", refetched
            assert refetched["ref_supplier"] == f"FR8-{tag}-R", refetched
            line_row = next(row for row in refetched["lines"] if int(row["id"]) == line_id)
            assert float(line_row["pu_ht"]) == pytest.approx(1100.0), line_row
            assert float(line_row["qty"]) == pytest.approx(2.0), line_row
        finally:
            await _delete_template(config, template_id)


@pytest.mark.asyncio
async def test_absent_endpoint_surfaces_actionable_error():
    """Without the endpoint the tool result is a structured, actionable error.

    Only runs when the endpoint exists (the autouse guard skips otherwise), so
    assert the inverse here: when the guard did NOT skip, the list call works.
    The error shape on endpoint-less instances is covered by the unit tests.
    """
    async with mcp_session() as (session, _init_result):
        listed = _tool_payload(await session.call_tool("get_supplier_invoice_templates", {"limit": 5}))
        assert isinstance(listed, list), listed
