"""Tests for the supplier recurring invoice template tools (FR-8).

Stock Dolibarr exposes no REST endpoint for supplier recurring invoice
templates, so the client methods call the would-be ``supplierinvoices/templates``
route and degrade with an actionable error on 404/501. These tests mock the
HTTP layer (``client.request``) and the server client, like
test_invoice_listing.py / test_crud_operations.py.
"""

import json
import os
import sys
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dolibarr_mcp import Config, DolibarrClient  # noqa: E402
from dolibarr_mcp.dolibarr_client import DolibarrAPIError  # noqa: E402
from dolibarr_mcp.dolibarr_mcp_server import (  # noqa: E402
    _summarize_supplier_invoice_template,
    handle_call_tool,
    is_write_tool,
)


@pytest.fixture
def config():
    return Config(dolibarr_url="https://test.dolibarr.com", dolibarr_api_key="test_api_key", log_level="INFO")


@pytest.fixture
def client(config):
    return DolibarrClient(config)


RAW_TEMPLATE = {
    "id": "7",
    "ref": "REC-SUP-0001",
    "title": "Annual rent",
    "ref_supplier": "RENT-2026",
    "fk_soc": "3",
    "note_private": "large raw payload that must not leak into the summary",
}

RAW_LINES = [
    {"id": "21", "description": "Rent December", "pu_ht": "1000.00", "qty": "1", "tva_tx": "20.0"},
    {"id": "22", "description": "Charges", "pu_ht": "100.00", "qty": "12", "tva_tx": "20.0"},
]


class TestClientSupplierInvoiceTemplates:
    @pytest.mark.asyncio
    async def test_list_per_supplier_sends_soc_filter(self, client):
        with patch.object(client, "request", new=AsyncMock(return_value=[RAW_TEMPLATE])) as mock_request:
            templates = await client.get_supplier_invoice_templates(limit=50, supplier_id=3, page=2)
        assert templates == [RAW_TEMPLATE]
        mock_request.assert_awaited_once_with(
            "GET", "supplierinvoices/templates", params={"limit": 50, "sqlfilters": "(t.fk_soc:=:3)", "page": 2}
        )

    @pytest.mark.asyncio
    async def test_list_without_supplier_omits_sqlfilters(self, client):
        with patch.object(client, "request", new=AsyncMock(return_value=[])) as mock_request:
            templates = await client.get_supplier_invoice_templates()
        assert templates == []
        assert mock_request.call_args.kwargs["params"] == {"limit": 100}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status_code, message",
        [
            (501, "API not found (failed to include API file)"),
            (404, "Not found"),
            # Stock Dolibarr routes supplierinvoices/templates to supplierinvoices/{id}.
            (400, "Bad Request: invalid value specified for `id`"),
        ],
    )
    async def test_list_degrades_with_actionable_error_when_endpoint_absent(self, client, status_code, message):
        with patch.object(client, "request", new=AsyncMock(side_effect=DolibarrAPIError(message, status_code=status_code))):
            with pytest.raises(DolibarrAPIError) as excinfo:
                await client.get_supplier_invoice_templates(supplier_id=3)
        assert excinfo.value.status_code == status_code
        assert "supplierinvoices/templates" in excinfo.value.message
        assert "fourn/facture/card-rec.php" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_unrelated_400_still_raises_untouched(self, client):
        with patch.object(client, "request", new=AsyncMock(side_effect=DolibarrAPIError("some other 400", status_code=400))):
            with pytest.raises(DolibarrAPIError) as excinfo:
                await client.get_supplier_invoice_templates(supplier_id=3)
        assert excinfo.value.message == "some other 400"

    @pytest.mark.asyncio
    async def test_get_template_attaches_lines(self, client):
        mock_request = AsyncMock(side_effect=[RAW_TEMPLATE, RAW_LINES])
        with patch.object(client, "request", new=mock_request):
            template = await client.get_supplier_invoice_template(7)
        assert template["lines"] == RAW_LINES
        assert [c.args[1] for c in mock_request.await_args_list] == [
            "supplierinvoices/templates/7",
            "supplierinvoices/templates/7/lines",
        ]

    @pytest.mark.asyncio
    async def test_update_maps_label_to_title(self, client):
        with patch.object(client, "request", new=AsyncMock(return_value={"id": 7})) as mock_request:
            await client.update_supplier_invoice_template(7, label="Rent 2027", ref_supplier="RENT-2027")
        mock_request.assert_awaited_once_with(
            "PUT", "supplierinvoices/templates/7", data={"title": "Rent 2027", "ref_supplier": "RENT-2027"}
        )

    @pytest.mark.asyncio
    async def test_update_line_reads_merges_and_puts(self, client):
        mock_request = AsyncMock(side_effect=[RAW_LINES, {"id": 21, "pu_ht": "1200.00"}])
        with patch.object(client, "request", new=mock_request):
            await client.update_supplier_invoice_template_line(7, 21, pu_ht=1200)
        put_call = mock_request.await_args_list[-1]
        assert put_call.args == ("PUT", "supplierinvoices/templates/7/lines/21")
        # Unsent fields keep their current values (PUT would reset them).
        assert put_call.kwargs["data"] == {
            "description": "Rent December",
            "pu_ht": 1200,
            "qty": "1",
            "tva_tx": "20.0",
        }

    @pytest.mark.asyncio
    async def test_update_line_unknown_line_raises_404(self, client):
        with patch.object(client, "request", new=AsyncMock(return_value=RAW_LINES)):
            with pytest.raises(DolibarrAPIError) as excinfo:
                await client.update_supplier_invoice_template_line(7, 999, qty=2)
        assert excinfo.value.status_code == 404
        assert "999" in excinfo.value.message


class TestServerSupplierInvoiceTemplateTools:
    def _mock_client(self, MockClient, **methods):
        instance = MockClient.return_value
        instance.__aenter__.return_value = instance
        for name, value in methods.items():
            setattr(instance, name, AsyncMock(return_value=value))
        return instance

    def test_summarize_template_keeps_useful_fields_only(self):
        assert _summarize_supplier_invoice_template(RAW_TEMPLATE) == {
            "id": 7,
            "ref": "REC-SUP-0001",
            "title": "Annual rent",
            "ref_supplier": "RENT-2026",
            "socid": 3,
        }

    def test_update_tools_are_write_tools_for_read_only_policy(self):
        assert not is_write_tool("get_supplier_invoice_templates")
        assert not is_write_tool("get_supplier_invoice_template")
        assert is_write_tool("update_supplier_invoice_template")
        assert is_write_tool("update_supplier_invoice_template_line")

    @pytest.mark.asyncio
    async def test_get_supplier_invoice_templates_dispatch(self):
        with patch("dolibarr_mcp.dolibarr_mcp_server.DolibarrClient") as MockClient:
            client = self._mock_client(MockClient, get_supplier_invoice_templates=[RAW_TEMPLATE])
            result = await handle_call_tool("get_supplier_invoice_templates", {"supplier_id": 3, "limit": 50})
        client.get_supplier_invoice_templates.assert_awaited_once_with(limit=50, supplier_id=3, page=0)
        payload = json.loads(result[0].text)
        assert payload == [{"id": 7, "ref": "REC-SUP-0001", "title": "Annual rent", "ref_supplier": "RENT-2026", "socid": 3}]

    @pytest.mark.asyncio
    async def test_get_supplier_invoice_template_dispatch_returns_compact_lines(self):
        with patch("dolibarr_mcp.dolibarr_mcp_server.DolibarrClient") as MockClient:
            client = self._mock_client(MockClient, get_supplier_invoice_template={**RAW_TEMPLATE, "lines": RAW_LINES})
            result = await handle_call_tool("get_supplier_invoice_template", {"template_id": 7})
        client.get_supplier_invoice_template.assert_awaited_once_with(7)
        payload = json.loads(result[0].text)
        assert payload["title"] == "Annual rent"
        assert payload["lines"] == [
            {"id": 21, "description": "Rent December", "pu_ht": "1000.00", "qty": "1", "tva_tx": "20.0"},
            {"id": 22, "description": "Charges", "pu_ht": "100.00", "qty": "12", "tva_tx": "20.0"},
        ]
        assert "note_private" not in payload

    @pytest.mark.asyncio
    async def test_update_supplier_invoice_template_dispatch(self):
        with patch("dolibarr_mcp.dolibarr_mcp_server.DolibarrClient") as MockClient:
            client = self._mock_client(MockClient, update_supplier_invoice_template={"id": 7})
            result = await handle_call_tool(
                "update_supplier_invoice_template", {"template_id": 7, "label": "Rent 2027", "ref_supplier": "R-27"}
            )
        client.update_supplier_invoice_template.assert_awaited_once_with(7, label="Rent 2027", ref_supplier="R-27")
        assert json.loads(result[0].text) == {"id": 7}

    @pytest.mark.asyncio
    async def test_update_supplier_invoice_template_line_dispatch(self):
        with patch("dolibarr_mcp.dolibarr_mcp_server.DolibarrClient") as MockClient:
            client = self._mock_client(MockClient, update_supplier_invoice_template_line={"id": 21})
            await handle_call_tool("update_supplier_invoice_template_line", {"template_id": 7, "line_id": 21, "pu_ht": 1200})
        client.update_supplier_invoice_template_line.assert_awaited_once_with(7, 21, pu_ht=1200)

    @pytest.mark.asyncio
    async def test_endpoint_absent_surfaces_structured_error(self):
        with patch("dolibarr_mcp.dolibarr_mcp_server.DolibarrClient") as MockClient:
            client = self._mock_client(MockClient)
            client.get_supplier_invoice_templates = AsyncMock(
                side_effect=DolibarrAPIError(
                    "no REST endpoint for supplier recurring invoice templates (HTTP 501)", status_code=501
                )
            )
            result = await handle_call_tool("get_supplier_invoice_templates", {})
        # _error_result marks the MCP result as an error with the structured payload.
        assert result.isError is True
        assert "no REST endpoint" in result.content[0].text


class TestIntegrationSkipGuard:
    """The integration tests skip when the endpoint is absent (400-id/404/501), like the #37.3 guard."""

    @staticmethod
    @contextmanager
    def _patched_module(client):
        """Patch DolibarrClient in the integration module, keeping the real absent-check."""
        client.__aenter__.return_value = client
        with patch("tests.integration.test_supplier_invoice_templates.DolibarrClient", return_value=client) as mock_class:
            mock_class._is_supplier_templates_absent = DolibarrClient._is_supplier_templates_absent
            yield mock_class

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status_code, message",
        [
            (404, "Not found"),
            (501, "API not found (failed to include API file)"),
            (400, "Bad Request: invalid value specified for `id`"),
        ],
    )
    async def test_skips_when_endpoint_absent(self, status_code, message):
        from tests.integration.test_supplier_invoice_templates import (
            _skip_if_no_supplier_templates_endpoint,
        )

        client = AsyncMock()
        client.request.side_effect = DolibarrAPIError(message, status_code=status_code)
        with self._patched_module(client):
            with pytest.raises(pytest.skip.Exception):
                await _skip_if_no_supplier_templates_endpoint(
                    Config(dolibarr_url="https://test.dolibarr.com/api/index.php", dolibarr_api_key="test_key")
                )

    @pytest.mark.asyncio
    async def test_does_not_skip_when_endpoint_exists(self):
        from tests.integration.test_supplier_invoice_templates import (
            _skip_if_no_supplier_templates_endpoint,
        )

        client = AsyncMock()
        client.request.return_value = []
        with self._patched_module(client):
            await _skip_if_no_supplier_templates_endpoint(
                Config(dolibarr_url="https://test.dolibarr.com/api/index.php", dolibarr_api_key="test_key")
            )  # must not raise

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status_code, message",
        [(500, "Server error"), (400, "some other 400")],
    )
    async def test_other_errors_still_raise(self, status_code, message):
        from tests.integration.test_supplier_invoice_templates import (
            _skip_if_no_supplier_templates_endpoint,
        )

        client = AsyncMock()
        client.request.side_effect = DolibarrAPIError(message, status_code=status_code)
        with self._patched_module(client):
            with pytest.raises(DolibarrAPIError):
                await _skip_if_no_supplier_templates_endpoint(
                    Config(dolibarr_url="https://test.dolibarr.com/api/index.php", dolibarr_api_key="test_key")
                )
