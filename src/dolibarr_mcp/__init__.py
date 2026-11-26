"""
Dolibarr MCP Server Package

Professional Model Context Protocol server for complete Dolibarr ERP/CRM management.
"""

__version__ = "1.4.0"
__author__ = "Dolibarr MCP Team"

from .config import Config
from .dolibarr_client import DolibarrClient

# Note: dolibarr_mcp_server uses a functional pattern, not a class
# The server is run via the main() function in dolibarr_mcp_server.py

__all__ = [
    "DolibarrClient",
    "Config",
]
