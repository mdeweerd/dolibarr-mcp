"""Integration tests for the MCP server against a real Dolibarr instance.

All three tests are marked ``integration`` and need ``DOLIBARR_URL`` and
``DOLIBARR_API_KEY`` pointing at a reachable instance (the CI workflow
.github/workflows/integration.yml provides one). They are deselected by a
plain ``pytest`` run.
"""

import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Tuple

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import InitializeResult, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import dolibarr_mcp  # noqa: E402
from dolibarr_mcp import Config, DolibarrClient  # noqa: E402

pytestmark = pytest.mark.integration


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

    An async context manager (not a pytest-asyncio fixture) so the anyio
    cancel scopes of ``stdio_client`` are entered and exited in the same
    task as the test body.
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


@pytest.mark.asyncio
async def test_mcp_server_is_up_and_reports_its_version():
    """The MCP server answers initialize with its name and package version."""
    async with mcp_session() as (_session, init_result):
        assert init_result.serverInfo.name == "dolibarr-mcp"
        assert init_result.serverInfo.version == dolibarr_mcp.__version__


@pytest.mark.asyncio
async def test_simple_request_to_dolibarr_instance_works():
    """A direct client request to the Dolibarr REST API succeeds."""
    config = _config()
    async with DolibarrClient(config) as client:
        result = await client.get_status()
    assert "success" in result
    assert result["success"].get("dolibarr_version")


@pytest.mark.asyncio
async def test_api_request_through_mcp_server_works():
    """A Dolibarr API call routed through the MCP server returns data."""
    _config()  # skip early when no instance is configured
    async with mcp_session() as (session, _init_result):
        result = await session.call_tool("get_status", {})
    assert not result.isError
    content = result.content[0]
    assert isinstance(content, TextContent)
    payload = json.loads(content.text)
    assert "success" in payload
    assert payload["success"].get("dolibarr_version")
