"""Professional Dolibarr MCP Server with comprehensive CRUD operations."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import uvicorn
from mcp.server import NotificationOptions, Server

# Import MCP components
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import (
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    TextContent,
    Tool,
)

# HTTP transport imports
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

# Import our Dolibarr components
from . import __version__
from .config import Config
from .dolibarr_client import DolibarrAPIError, DolibarrClient

# Configure logging to stderr so it doesn't interfere with MCP protocol
logging.basicConfig(
    level=logging.WARNING,  # Reduce noise in MCP communication
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)

# Create server instance
server = Server("dolibarr-mcp", version=__version__)


def _error_result(payload: dict) -> CallToolResult:
    """Wrap an error payload so MCP clients see it as a failed tool call."""
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, indent=2))],
        isError=True,
    )


_APOSTROPHES = ("'", "\u2019", "\u2018")


def _escape_sqlfilter(value: str) -> str:
    """Make a user value safe inside a quoted ``like`` value of Dolibarr sqlfilters.

    Dolibarr's sqlfilters cannot match a literal apostrophe: doubled (''),
    backslash-escaped (\\') or raw, a quote inside the value never matches
    (checked on Dolibarr 21), so a search for "O'BRIEN" returned 404.
    Apostrophes (straight or typographic) are replaced by the LIKE single-
    character wildcard ``_``, which matches the stored apostrophe and also
    keeps the value from closing the quoted string.
    """
    for apostrophe in _APOSTROPHES:
        value = value.replace(apostrophe, "_")
    return value


INVOICE_LIST_MAX_LIMIT = 200
INVOICE_SORT_FIELDS = {"date": "t.datef", "ref": "t.ref", "id": "t.rowid"}
INVOICE_STATUS_LABELS = {0: "draft", 1: "unpaid", 2: "paid", 3: "cancelled"}
INVOICE_TYPE_LABELS = {0: "standard", 1: "replacement", 2: "credit_note", 3: "deposit"}


def _to_iso_date(value: Any) -> str | None:
    """Convert a Dolibarr timestamp (int or numeric string) to YYYY-MM-DD.

    Dolibarr stores invoice dates as local midnight, so convert in local time:
    converting to UTC would shift dates one day back in Europe.
    """
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value)).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return str(value)


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _summarize_invoice(invoice: dict[str, Any]) -> dict[str, Any]:
    """Keep only the fields useful to list invoices (raw objects are ~10 KB each)."""
    status = _to_int(invoice.get("status", invoice.get("statut")))
    inv_type = _to_int(invoice.get("type"))
    return {
        "id": _to_int(invoice.get("id")),
        "ref": invoice.get("ref"),
        "ref_client": invoice.get("ref_client") or None,
        "socid": _to_int(invoice.get("socid")),
        "type": INVOICE_TYPE_LABELS.get(inv_type, inv_type) if inv_type is not None else None,
        "date": _to_iso_date(invoice.get("date")),
        "due_date": _to_iso_date(invoice.get("date_lim_reglement")),
        "total_ht": invoice.get("total_ht"),
        "total_tva": invoice.get("total_tva"),
        "total_ttc": invoice.get("total_ttc"),
        "status": INVOICE_STATUS_LABELS.get(status, status) if status is not None else None,
        "paid": str(invoice.get("paye")) == "1",
        "remain_to_pay": invoice.get("remaintopay"),
        "project_id": _to_int(invoice.get("fk_project")),
    }


def _summarize_supplier_invoice(invoice: dict[str, Any]) -> dict[str, Any]:
    """Compact summary of a supplier invoice, including the supplier's own ref."""
    status = _to_int(invoice.get("status", invoice.get("statut")))
    inv_type = _to_int(invoice.get("type"))
    return {
        "id": _to_int(invoice.get("id")),
        "ref": invoice.get("ref"),
        "ref_supplier": invoice.get("ref_supplier") or None,
        "socid": _to_int(invoice.get("socid")),
        "type": INVOICE_TYPE_LABELS.get(inv_type, inv_type) if inv_type is not None else None,
        "date": _to_iso_date(invoice.get("date")),
        "due_date": _to_iso_date(invoice.get("date_lim_reglement")),
        "total_ht": invoice.get("total_ht"),
        "total_tva": invoice.get("total_tva"),
        "total_ttc": invoice.get("total_ttc"),
        "status": INVOICE_STATUS_LABELS.get(status, status) if status is not None else None,
        "paid": str(invoice.get("paye")) == "1",
        "remain_to_pay": invoice.get("remaintopay"),
    }


def _summarize_supplier_invoice_template(template: dict[str, Any]) -> dict[str, Any]:
    """Compact summary of a supplier recurring invoice template (raw objects are large)."""
    return {
        "id": _to_int(template.get("id", template.get("rowid"))),
        "ref": template.get("ref"),
        "title": template.get("title", template.get("titre")),
        "ref_supplier": template.get("ref_supplier") or None,
        "socid": _to_int(template.get("socid", template.get("fk_soc"))),
    }


def _summarize_supplier_invoice_template_line(line: dict[str, Any]) -> dict[str, Any]:
    """Compact view of a supplier recurring invoice template line."""
    return {
        "id": _to_int(line.get("id", line.get("rowid"))),
        "description": line.get("description", line.get("desc")),
        "pu_ht": line.get("pu_ht"),
        "qty": line.get("qty"),
        "tva_tx": line.get("tva_tx"),
    }


def _summarize_bank_account(account: dict[str, Any]) -> dict[str, Any]:
    """Keep only id/label/bank of a bank account (raw objects are ~10 KB each)."""
    return {
        "id": _to_int(account.get("id")),
        "label": account.get("label"),
        "bank": account.get("bank"),
    }


def _summarize_bank_line(line: dict[str, Any]) -> dict[str, Any]:
    """Compact view of a bank line for statement reconciliation (dates as YYYY-MM-DD)."""
    return {
        "rowid": line.get("rowid"),
        "datev": _to_iso_date(line.get("datev")),
        "dateo": _to_iso_date(line.get("dateo")),
        "label": line.get("label"),
        "amount": line.get("amount"),
        "num_releve": line.get("num_releve"),
        "rappro": bool(line.get("rappro")),
    }


def _summarize_social_contribution(contrib: dict[str, Any]) -> dict[str, Any]:
    """Keep only the fields useful to list social contributions (raw objects are ~10 KB each)."""
    return {
        "id": _to_int(contrib.get("id")),
        "label": contrib.get("label"),
        "type": _to_int(contrib.get("type", contrib.get("fk_type"))),
        "amount": contrib.get("amount"),
        "period": _to_int(contrib.get("period", contrib.get("perio" "de"))),
        "paid": str(contrib.get("paye")) == "1",
    }


MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_TYPES = ("application/xml", "application/json")


def _summarize_document(doc: dict, original_file: str | None = None) -> dict:
    """Compact view of a /documents entry, with the path download_document expects."""
    name = doc.get("relativename") or doc.get("name")
    level1 = doc.get("level1name")
    timestamp = doc.get("date")
    date_iso = None
    if isinstance(timestamp, (int, float)) or (isinstance(timestamp, str) and timestamp.isdigit()):
        date_iso = datetime.fromtimestamp(int(timestamp), timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "name": name,
        "original_file": original_file or (f"{level1}/{name}" if level1 else name),
        "size": doc.get("size"),
        "date": date_iso,
        "type": doc.get("type"),
    }


def _document_path_convention(modulepart: str) -> str:
    """How ``original_file`` paths are built for a modulepart (upload/download)."""
    if modulepart == "supplier_invoice":
        return "{id mod 10}/{floor(id/10) mod 10}/{ref}/{filename} (pass both ref and id)"
    if modulepart == "tax":
        return "{charge rowid}/{filename} (pass the social-charge rowid as 'id' or 'subdir')"
    if modulepart == "bank":
        return "{account_id}/statement/{num}/{filename} (num = statement number YYYYMM, pass as 'subdir')"
    return "{ref}/{filename}"


async def _resolve_supplier_invoice_ref_id(client: DolibarrClient, ref: str | None, obj_id: int | None):
    """Resolve the (ref, id) pair of a supplier invoice.

    The stored document path embeds the numeric id, so both are needed.
    """
    if not ref and obj_id is not None:
        invoice = await client.request("GET", f"supplierinvoices/{obj_id}")
        ref = invoice.get("ref")
    elif ref and obj_id is None:
        found = await client.get_supplier_invoices(sqlfilters=f"(t.ref:=:'{_escape_sqlfilter(ref)}')", limit=1)
        if found:
            obj_id = int(found[0]["id"])
    return ref, obj_id


def _extract_pdf_attachments(data: bytes) -> list[dict]:
    """Return files embedded in a PDF (e.g. Factur-X 'factur-x.xml')."""
    try:
        from io import BytesIO

        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - pypdf is a declared dependency
        return []
    try:
        reader = PdfReader(BytesIO(data))
        attachments = reader.attachments
    except Exception as exc:  # malformed PDF: still return the file itself
        logging.getLogger(__name__).warning("Cannot read PDF attachments: %s", exc)
        return []
    extracted = []
    for filename, contents in attachments.items():
        for blob in contents:
            entry = {"name": filename, "size": len(blob)}
            try:
                entry["text"] = blob.decode("utf-8")
            except UnicodeDecodeError:
                entry["text"] = None
                entry["note"] = "binary attachment, not returned"
            extracted.append(entry)
    return extracted


def _document_contents(downloaded: dict, modulepart: str, original_file: str, include_file: bool = True):
    """Turn a /documents/download response into MCP content blocks."""
    content_b64 = downloaded.get("content") or ""
    encoding = downloaded.get("encoding")
    mime = downloaded.get("content-type") or "application/octet-stream"
    filename = downloaded.get("filename") or original_file.rsplit("/", 1)[-1]

    raw = base64.b64decode(content_b64) if encoding == "base64" else content_b64.encode("utf-8")
    if len(raw) > MAX_DOCUMENT_BYTES:
        return _error_result(
            {
                "error": "Payload Too Large",
                "status": 413,
                "message": f"'{original_file}' is {len(raw)} bytes (limit {MAX_DOCUMENT_BYTES}).",
            }
        )

    is_pdf = mime == "application/pdf" or filename.lower().endswith(".pdf")
    is_text = mime.startswith(_TEXT_MIME_PREFIXES) or mime in _TEXT_MIME_TYPES
    embedded = _extract_pdf_attachments(raw) if is_pdf else []

    meta = {
        "filename": filename,
        "original_file": original_file,
        "content_type": mime,
        "size": len(raw),
        "embedded_files": [{k: v for k, v in e.items() if k != "text"} for e in embedded],
    }
    blocks: list = [TextContent(type="text", text=json.dumps(meta, indent=2))]
    for entry in embedded:
        if entry.get("text") is not None:
            blocks.append(
                TextContent(
                    type="text",
                    text=f"--- embedded file: {entry['name']} ---\n{entry['text']}",
                )
            )

    if include_file:
        if is_text:
            blocks.append(TextContent(type="text", text=raw.decode("utf-8", errors="replace")))
        else:
            blocks.append(
                EmbeddedResource(
                    type="resource",
                    resource=BlobResourceContents(
                        uri=f"dolibarr://documents/{modulepart}/{original_file}",  # type: ignore[arg-type]
                        mimeType=mime,
                        blob=base64.b64encode(raw).decode("ascii"),
                    ),
                )
            )
    return blocks


RAW_API_TOOL = "dolibarr_raw_api"
_WRITE_PREFIXES = ("create_", "update_", "delete_", "add_", "set_", "validate_", "pay_", "upload_")


def is_write_tool(name: str) -> bool:
    """Return True for tools that modify data in Dolibarr."""
    return name == RAW_API_TOOL or name.startswith(_WRITE_PREFIXES)


def is_tool_allowed(name: str, config: Config) -> bool:
    """Apply read-only and raw-API policies to a tool name."""
    if name == RAW_API_TOOL and not config.mcp_enable_raw_api:
        return False
    if config.mcp_read_only and is_write_tool(name):
        return False
    return True


@server.list_tools()
async def handle_list_tools():
    """List the tools allowed by the current configuration."""
    config = Config()
    return [tool for tool in _all_tools() if is_tool_allowed(tool.name, config)]


def _all_tools():
    """Full catalogue of tools, before policy filtering."""
    return [
        # System & Info
        Tool(
            name="test_connection",
            description="Test Dolibarr API connection",
            inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        Tool(
            name="get_status",
            description="Get Dolibarr system status and version information",
            inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        Tool(
            name="get_dictionary",
            description=(
                "Get a Dolibarr setup dictionary (e.g. 'payment_types' for payment modes, "
                "'event_types' for agenda event types). Returns one row per entry with its "
                "instance-specific numeric 'id', its stable 'code' and its 'label'. Use this to "
                "resolve codes like 'VIR' or 'AC_TEL' to the numeric ids this instance uses, "
                "instead of hardcoding ids."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Dictionary name, e.g. 'payment_types' or 'event_types'",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
        # Search Tools
        Tool(
            name="search_products_by_ref",
            description=(
                "Search products by (partial) reference. Use this when a product reference appears in the text "
                "but may be incomplete or slightly uncertain. This tool returns a small, filtered list and should "
                "be preferred over get_products for any kind of lookup by reference."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ref_prefix": {
                        "type": "string",
                        "description": "Prefix of the product reference",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 20,
                    },
                },
                "required": ["ref_prefix"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="search_customers",
            description=(
                "Search customers/third parties by name or alias. Use this whenever you need to find a customer "
                "from a name in text instead of loading a full list. Pay attention to legal suffixes and exact matches "
                "(e.g. 'GmbH' vs 'OG', 'Inc', etc.). Do not use get_customers for name-based search."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term for name or alias",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 20,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="search_thirdparties",
            description=(
                "Search third parties by name or alias, including supplier-only companies that "
                "search_customers may not reach. Use kind to restrict the classification: "
                "'customer', 'supplier' or 'any' (default). Apostrophes in the query are matched "
                'via a single-character wildcard, so "O\'BRIEN" matches "O\'BRIEN".'
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term for name or alias",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["customer", "supplier", "any"],
                        "description": "Third-party classification to search (default: any)",
                        "default": "any",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 20,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="search_products_by_label",
            description=(
                "Search products by label/description text. Use this when you only know the human-readable product "
                "name or part of it. Prefer this over get_products for any label-based lookup to keep result sets small."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "label_search": {
                        "type": "string",
                        "description": "Search term in product label",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 20,
                    },
                },
                "required": ["label_search"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="resolve_product_ref",
            description=(
                "Resolve an exact product reference (ref) to a single product. Use this only when the exact reference "
                "string is known and you need a deterministic mapping to a product ID before creating orders or invoices. "
                "Returns a structured result with status 'ok', 'not_found', or 'ambiguous'. Do not use this for fuzzy search."
            ),
            inputSchema={
                "type": "object",
                "properties": {"ref": {"type": "string", "description": "Exact product reference"}},
                "required": ["ref"],
                "additionalProperties": False,
            },
        ),
        # User Management CRUD
        Tool(
            name="get_users",
            description=(
                "Get an unfiltered paginated list of users from Dolibarr. "
                "Use this only when you explicitly need a page of users for inspection or debugging. "
                "Do not use this tool to search by name, login or email (there is no server-side filter here)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of users to return (default: 100)",
                        "default": 100,
                    },
                    "page": {
                        "type": "integer",
                        "description": "Page number, starting at 1 (default: 1)",
                        "default": 1,
                    },
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_user_by_id",
            description=(
                "Get the details of exactly one user by numeric ID. "
                "Use this only when you already know the internal Dolibarr user_id. "
                "Do not pass login, email or name here."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "Exact numeric Dolibarr user ID (not login, not email).",
                    }
                },
                "required": ["user_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="create_user",
            description="Create a new user",
            inputSchema={
                "type": "object",
                "properties": {
                    "login": {"type": "string", "description": "User login"},
                    "lastname": {"type": "string", "description": "Last name"},
                    "firstname": {"type": "string", "description": "First name"},
                    "email": {"type": "string", "description": "Email address"},
                    "password": {"type": "string", "description": "Password"},
                    "admin": {
                        "type": "integer",
                        "description": "Admin level (0=No, 1=Yes)",
                        "default": 0,
                    },
                },
                "required": ["login", "lastname"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="update_user",
            description="Update an existing user",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer", "description": "User ID to update"},
                    "login": {"type": "string", "description": "User login"},
                    "lastname": {"type": "string", "description": "Last name"},
                    "firstname": {"type": "string", "description": "First name"},
                    "email": {"type": "string", "description": "Email address"},
                    "admin": {
                        "type": "integer",
                        "description": "Admin level (0=No, 1=Yes)",
                    },
                },
                "required": ["user_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="delete_user",
            description="Delete a user",
            inputSchema={
                "type": "object",
                "properties": {"user_id": {"type": "integer", "description": "User ID to delete"}},
                "required": ["user_id"],
                "additionalProperties": False,
            },
        ),
        # Customer/Third Party Management CRUD
        Tool(
            name="get_customers",
            description=(
                "Get an unfiltered paginated list of customers/third parties from Dolibarr. "
                "Intended for debugging or browsing only. DO NOT use this tool to search by name or alias "
                "(use the dedicated search_* tools such as search_customers instead)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of customers to return (default: 100)",
                        "default": 100,
                    },
                    "page": {
                        "type": "integer",
                        "description": "Page number, starting at 1 (default: 1)",
                        "default": 1,
                    },
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_customer_by_id",
            description=(
                "Get the details of exactly one customer by numeric ID. "
                "Use this only when you already know the internal Dolibarr customer_id. "
                "Do not pass name or email here."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "integer",
                        "description": "Exact numeric Dolibarr customer ID (not name).",
                    }
                },
                "required": ["customer_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="create_customer",
            description="Create a new customer/third party",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Customer name"},
                    "email": {"type": "string", "description": "Email address"},
                    "phone": {"type": "string", "description": "Phone number"},
                    "address": {"type": "string", "description": "Customer address"},
                    "town": {"type": "string", "description": "City/Town"},
                    "zip": {"type": "string", "description": "Postal code"},
                    "country_id": {
                        "type": "integer",
                        "description": "Country ID (default: 1)",
                        "default": 1,
                    },
                    "type": {
                        "type": "integer",
                        "description": "Customer type (1=Customer, 2=Supplier, 3=Both)",
                        "default": 1,
                    },
                    "status": {
                        "type": "integer",
                        "description": "Status (1=Active, 0=Inactive)",
                        "default": 1,
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="update_customer",
            description="Update an existing customer",
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "integer",
                        "description": "Customer ID to update",
                    },
                    "name": {"type": "string", "description": "Customer name"},
                    "email": {"type": "string", "description": "Email address"},
                    "phone": {"type": "string", "description": "Phone number"},
                    "address": {"type": "string", "description": "Customer address"},
                    "town": {"type": "string", "description": "City/Town"},
                    "zip": {"type": "string", "description": "Postal code"},
                    "status": {
                        "type": "integer",
                        "description": "Status (1=Active, 0=Inactive)",
                    },
                },
                "required": ["customer_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="delete_customer",
            description="Delete a customer",
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "integer",
                        "description": "Customer ID to delete",
                    }
                },
                "required": ["customer_id"],
                "additionalProperties": False,
            },
        ),
        # Product Management CRUD
        Tool(
            name="get_products",
            description=(
                "Get an unfiltered list of products from Dolibarr. "
                "Intended for debugging or bulk inspection only. DO NOT use this tool to search by reference or label "
                "(use search_products_by_ref or search_products_by_label instead)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of products to return (default: 100)",
                        "default": 100,
                    }
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_product_by_id",
            description=(
                "Get the details of exactly one product by numeric ID. "
                "Use this only when you already know the internal Dolibarr product_id. "
                "Do not pass reference or label here."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "integer",
                        "description": "Exact numeric Dolibarr product ID (not ref).",
                    }
                },
                "required": ["product_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="create_product",
            description="Create a new product",
            inputSchema={
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Product name/label"},
                    "price": {"type": "number", "description": "Product price"},
                    "description": {"type": "string", "description": "Product description"},
                    "stock": {
                        "type": "integer",
                        "description": "Initial stock quantity",
                    },
                },
                "required": ["label", "price"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="update_product",
            description="Update an existing product",
            inputSchema={
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "integer",
                        "description": "Product ID to update",
                    },
                    "label": {"type": "string", "description": "Product name/label"},
                    "price": {"type": "number", "description": "Product price"},
                    "description": {
                        "type": "string",
                        "description": "Product description",
                    },
                },
                "required": ["product_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="delete_product",
            description="Delete a product",
            inputSchema={
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "integer",
                        "description": "Product ID to delete",
                    }
                },
                "required": ["product_id"],
                "additionalProperties": False,
            },
        ),
        # Invoice Management CRUD
        Tool(
            name="get_invoices",
            description=(
                "List invoices, newest first by default, optionally filtered by customer and/or status. "
                "To get a customer's latest invoices, resolve the customer's socid first (search_customers) "
                "and pass it as customer_id. Returns a compact summary per invoice (ref, dates, totals, "
                "status, remaining amount); use get_invoice_by_id for lines and full details. "
                "Use page to walk through larger result sets."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "integer",
                        "description": "Only invoices of this third party (Dolibarr socid).",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["draft", "unpaid", "paid", "cancelled"],
                        "description": "Invoice status filter.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of invoices per page (default: 20, max: 200).",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 200,
                    },
                    "page": {
                        "type": "integer",
                        "description": "Zero-based page number (default: 0).",
                        "default": 0,
                        "minimum": 0,
                    },
                    "sort_by": {
                        "type": "string",
                        "enum": ["date", "ref", "id"],
                        "description": "Sort field (default: date).",
                        "default": "date",
                    },
                    "sort_order": {
                        "type": "string",
                        "enum": ["desc", "asc"],
                        "description": "Sort order (default: desc, newest first).",
                        "default": "desc",
                    },
                    "full": {
                        "type": "boolean",
                        "description": "Return the raw Dolibarr objects instead of the compact summary (much larger).",
                        "default": False,
                    },
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_invoice_by_id",
            description=(
                "Get the details of exactly one invoice by numeric ID. "
                "Use this only when you already know the internal Dolibarr invoice_id. "
                "Do not pass invoice reference here."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_id": {
                        "type": "integer",
                        "description": "Exact numeric Dolibarr invoice ID.",
                    }
                },
                "required": ["invoice_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="create_invoice",
            description=(
                "ALWAYS creates a new invoice. Do not use this tool to modify an existing invoice. "
                "Before calling this, resolve the correct customer and product IDs using the appropriate search_* tools "
                "(e.g. search_customers, search_products_by_ref, resolve_product_ref). "
                "For lines: Use product_id for existing products whenever possible and set product_type=0 for goods "
                "and product_type=1 for services. Use free-text lines only if no matching product exists in Dolibarr."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "integer",
                        "description": "Customer ID (Dolibarr socid of the third party to invoice)",
                    },
                    "date": {
                        "type": "string",
                        "description": "Invoice date (YYYY-MM-DD)",
                    },
                    "due_date": {
                        "type": "string",
                        "description": "Due date (YYYY-MM-DD)",
                    },
                    "lines": {
                        "type": "array",
                        "description": "Invoice lines",
                        "items": {
                            "type": "object",
                            "properties": {
                                "desc": {
                                    "type": "string",
                                    "description": "Line description",
                                },
                                "qty": {"type": "number", "description": "Quantity"},
                                "subprice": {
                                    "type": "number",
                                    "description": "Unit price",
                                },
                                "total_ht": {
                                    "type": "number",
                                    "description": "Total excluding tax",
                                },
                                "total_ttc": {
                                    "type": "number",
                                    "description": "Total including tax",
                                },
                                "vat": {"type": "number", "description": "VAT rate"},
                                "product_id": {
                                    "type": "integer",
                                    "description": "Product ID to link (optional)",
                                },
                                "product_type": {
                                    "type": "integer",
                                    "description": "Type of line (0=Product, 1=Service)",
                                },
                            },
                            "required": ["desc", "qty", "subprice"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["customer_id", "lines"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="update_invoice",
            description="Update an existing invoice",
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_id": {
                        "type": "integer",
                        "description": "Invoice ID to update",
                    },
                    "date": {
                        "type": "string",
                        "description": "Invoice date (YYYY-MM-DD)",
                    },
                    "due_date": {
                        "type": "string",
                        "description": "Due date (YYYY-MM-DD)",
                    },
                    "cond_reglement_id": {
                        "type": "integer",
                        "description": "Payment terms ID (Dolibarr c_payment_term rowid, e.g. the value used on the customer's previous invoices)",
                    },
                    "mode_reglement_id": {
                        "type": "integer",
                        "description": "Payment method ID (Dolibarr c_paiement id, e.g. the value used on the customer's previous invoices)",
                    },
                },
                "required": ["invoice_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="delete_invoice",
            description="Delete an invoice",
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_id": {
                        "type": "integer",
                        "description": "Invoice ID to delete",
                    }
                },
                "required": ["invoice_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="create_invoice_draft",
            description=(
                "Create a new invoice draft (header only). "
                "Use this to start a new invoice, then use add_invoice_line to add items. "
                "Returns the new invoice_id."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "integer",
                        "description": "Customer ID (Dolibarr socid)",
                    },
                    "date": {
                        "type": "string",
                        "description": "Invoice date (YYYY-MM-DD)",
                    },
                    "project_id": {
                        "type": "integer",
                        "description": "Linked project ID (optional)",
                    },
                },
                "required": ["customer_id", "date"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="add_invoice_line",
            description="Add a line item to an existing draft invoice.",
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_id": {
                        "type": "integer",
                        "description": "Invoice ID",
                    },
                    "desc": {
                        "type": "string",
                        "description": "Line description",
                    },
                    "qty": {
                        "type": "number",
                        "description": "Quantity",
                    },
                    "subprice": {
                        "type": "number",
                        "description": "Unit price (net)",
                    },
                    "product_id": {
                        "type": "integer",
                        "description": "Product ID (optional)",
                    },
                    "product_type": {
                        "type": "integer",
                        "description": "Type (0=Product, 1=Service)",
                        "default": 0,
                    },
                    "vat": {
                        "type": "number",
                        "description": "VAT rate (optional)",
                    },
                },
                "required": ["invoice_id", "desc", "qty", "subprice"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="update_invoice_line",
            description="Update an existing line in a draft invoice. Only the given fields change; the others keep their current values.",
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_id": {
                        "type": "integer",
                        "description": "Invoice ID",
                    },
                    "line_id": {
                        "type": "integer",
                        "description": "Line ID to update",
                    },
                    "desc": {
                        "type": "string",
                        "description": "New description",
                    },
                    "qty": {
                        "type": "number",
                        "description": "New quantity",
                    },
                    "subprice": {
                        "type": "number",
                        "description": "New unit price",
                    },
                    "vat": {
                        "type": "number",
                        "description": "New VAT rate",
                    },
                },
                "required": ["invoice_id", "line_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="delete_invoice_line",
            description="Delete a line from a draft invoice.",
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_id": {
                        "type": "integer",
                        "description": "Invoice ID",
                    },
                    "line_id": {
                        "type": "integer",
                        "description": "Line ID to delete",
                    },
                },
                "required": ["invoice_id", "line_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="set_invoice_project",
            description="Link an invoice to a project.",
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_id": {
                        "type": "integer",
                        "description": "Invoice ID",
                    },
                    "project_id": {
                        "type": "integer",
                        "description": "Project ID",
                    },
                },
                "required": ["invoice_id", "project_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="validate_invoice",
            description="Validate a draft invoice (change status to unpaid).",
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_id": {
                        "type": "integer",
                        "description": "Invoice ID",
                    },
                    "warehouse_id": {
                        "type": "integer",
                        "description": "Warehouse ID for stock decrease (optional)",
                        "default": 0,
                    },
                },
                "required": ["invoice_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="pay_invoice",
            description=(
                "Record a payment on a CUSTOMER invoice (POST /invoices/{id}/payments pays the full "
                "remaining amount). payment_mode accepts a dictionary code (e.g. VIR) or a numeric "
                "payment_mode_id (resolve codes with get_dictionary('payment_types')). accountid is a "
                "bank account id from GET /bankaccounts. closepaidinvoices=true marks the invoice paid "
                "when the payment balances it. Supplier invoices are rejected with an error pointing to "
                "pay_supplier_invoice; pass direction='supplier' (with amount) to pay one through this tool."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "integer", "description": "Invoice ID"},
                    "payment_mode": {
                        "type": ["string", "integer"],
                        "description": "Payment mode code (e.g. 'VIR') or numeric payment_mode_id",
                    },
                    "accountid": {"type": "integer", "description": "Bank account ID (from /bankaccounts)"},
                    "direction": {
                        "type": "string",
                        "enum": ["customer", "supplier"],
                        "description": "Invoice direction (default: customer). 'supplier' delegates to pay_supplier_invoice.",
                        "default": "customer",
                    },
                    "payment_date": {
                        "type": "string",
                        "description": "Payment date (YYYY-MM-DD, default: today)",
                    },
                    "closepaidinvoices": {
                        "type": "boolean",
                        "description": "Close the invoice when the payment balances it (default: true)",
                        "default": True,
                    },
                    "num_payment": {"type": "string", "description": "Payment number (optional)"},
                    "comment": {"type": "string", "description": "Private note (optional)"},
                    "amount": {
                        "type": "number",
                        "description": "Amount to pay (required when direction='supplier'; customer invoices are paid in full)",
                    },
                },
                "required": ["invoice_id", "payment_mode"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_invoice_payments",
            description=(
                "List the payments recorded on an invoice, with date, amount, payment mode, "
                "num_payment and bank account per payment. direction selects the customer "
                "(GET /invoices/{id}/payments) or supplier (GET /supplierinvoices/{id}/payments) side."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "integer", "description": "Invoice ID"},
                    "direction": {
                        "type": "string",
                        "enum": ["customer", "supplier"],
                        "description": "Invoice direction (default: customer)",
                        "default": "customer",
                    },
                },
                "required": ["invoice_id"],
                "additionalProperties": False,
            },
        ),
        # Supplier Invoice Management
        Tool(
            name="get_supplier_invoices",
            description=(
                "List supplier invoices, newest first by default. Returns a compact summary per invoice "
                "(ref, supplier ref, dates, totals, status, remaining amount) — never the raw objects. "
                "Accepts a single filter condition per call (Dolibarr sqlfilters return 0 rows for combined "
                "conditions): filter by supplier_id, ref_supplier or status, one at a time. "
                "Use page to walk through larger result sets."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "supplier_id": {
                        "type": "integer",
                        "description": "Only invoices of this supplier (Dolibarr socid).",
                    },
                    "ref_supplier": {
                        "type": "string",
                        "description": "Only invoices with this supplier invoice number (exact match).",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["draft", "unpaid", "paid", "cancelled"],
                        "description": "Supplier invoice status filter.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of invoices per page (default: 20, max: 200).",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 200,
                    },
                    "page": {
                        "type": "integer",
                        "description": "Zero-based page number (default: 0).",
                        "default": 0,
                        "minimum": 0,
                    },
                    "sort_by": {
                        "type": "string",
                        "enum": ["date", "ref", "id"],
                        "description": "Sort field (default: date).",
                        "default": "date",
                    },
                    "sort_order": {
                        "type": "string",
                        "enum": ["desc", "asc"],
                        "description": "Sort order (default: desc, newest first).",
                        "default": "desc",
                    },
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="create_supplier_invoice",
            description=(
                "Create a new supplier invoice draft for a supplier (socid), optionally with lines, then "
                "add more lines with add_supplier_invoice_line. Use type=2 for a credit note (SA... ref, "
                "negative amounts). Line fields are description/pu_ht/qty/tva_tx — desc/subprice do NOT "
                "work on supplier invoice lines. Returns the new invoice_id."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "supplier_id": {
                        "type": "integer",
                        "description": "Supplier ID (Dolibarr socid of the third party)",
                    },
                    "ref_supplier": {
                        "type": "string",
                        "description": "The supplier's own invoice number (optional)",
                    },
                    "label": {
                        "type": "string",
                        "description": "Invoice label (optional)",
                    },
                    "date": {
                        "type": "string",
                        "description": "Invoice date (YYYY-MM-DD)",
                    },
                    "due_date": {
                        "type": "string",
                        "description": "Due date (YYYY-MM-DD, optional)",
                    },
                    "type": {
                        "type": "integer",
                        "enum": [0, 2],
                        "description": "0 = standard invoice (default), 2 = credit note",
                        "default": 0,
                    },
                    "lines": {
                        "type": "array",
                        "description": "Lines to add to the new draft",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string", "description": "Line description"},
                                "pu_ht": {"type": "number", "description": "Unit price (net)"},
                                "qty": {"type": "number", "description": "Quantity"},
                                "tva_tx": {"type": "number", "description": "VAT rate (optional)"},
                                "product_id": {"type": "integer", "description": "Product ID to link (optional)"},
                                "product_type": {
                                    "type": "integer",
                                    "description": "Type of line (0=Product, 1=Service)",
                                    "default": 0,
                                },
                            },
                            "required": ["description", "pu_ht", "qty"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["supplier_id", "date"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="add_supplier_invoice_line",
            description=(
                "Add a line to a draft supplier invoice. Only description/pu_ht/qty/tva_tx are honored by "
                "Dolibarr (desc/subprice are rejected). Adding a line to a validated invoice is an error."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "integer", "description": "Supplier invoice ID"},
                    "description": {"type": "string", "description": "Line description"},
                    "pu_ht": {"type": "number", "description": "Unit price (net)"},
                    "qty": {"type": "number", "description": "Quantity"},
                    "tva_tx": {"type": "number", "description": "VAT rate (optional)"},
                    "product_id": {"type": "integer", "description": "Product ID (optional)"},
                    "product_type": {"type": "integer", "description": "Type (0=Product, 1=Service)", "default": 0},
                },
                "required": ["invoice_id", "description", "pu_ht", "qty"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="update_supplier_invoice_line",
            description=(
                "Update an existing line of a draft supplier invoice. Only the given fields change; the "
                "others keep their current values. Only description/pu_ht/qty/tva_tx are supported."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "integer", "description": "Supplier invoice ID"},
                    "line_id": {"type": "integer", "description": "Line ID to update"},
                    "description": {"type": "string", "description": "New description"},
                    "pu_ht": {"type": "number", "description": "New unit price (net)"},
                    "qty": {"type": "number", "description": "New quantity"},
                    "tva_tx": {"type": "number", "description": "New VAT rate"},
                },
                "required": ["invoice_id", "line_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="validate_supplier_invoice",
            description="Validate a draft supplier invoice (returns the final SI... ref; SA... for credit notes).",
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "integer", "description": "Supplier invoice ID"},
                    "warehouse_id": {
                        "type": "integer",
                        "description": "Warehouse ID for stock increase (optional)",
                        "default": 0,
                    },
                },
                "required": ["invoice_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="set_supplier_invoice_to_draft",
            description="Set a validated supplier invoice back to draft (the ref and payments are kept).",
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "integer", "description": "Supplier invoice ID"},
                },
                "required": ["invoice_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="set_supplier_invoice_paid",
            description="Mark a validated supplier invoice as paid.",
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "integer", "description": "Supplier invoice ID"},
                },
                "required": ["invoice_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="update_supplier_invoice",
            description=(
                "Update a supplier invoice: label and/or ref_supplier (the supplier's own invoice number). "
                "Works on validated and paid invoices."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "integer", "description": "Supplier invoice ID"},
                    "label": {"type": "string", "description": "New label"},
                    "ref_supplier": {"type": "string", "description": "New supplier invoice number"},
                },
                "required": ["invoice_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="pay_supplier_invoice",
            description=(
                "Record a payment on a SUPPLIER invoice (POST /supplierinvoices/{id}/payments; the raw "
                "endpoint takes its fields only as query parameters, handled here). payment_mode accepts a "
                "dictionary code (e.g. VIR) or a numeric payment_mode_id (resolve codes with "
                "get_dictionary('payment_types')). accountid is a bank account id from GET /bankaccounts. "
                "closepaidinvoices=true closes the invoice when the amount balances it. Returns the new "
                "payment id."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "integer", "description": "Supplier invoice ID"},
                    "amount": {"type": "number", "description": "Amount to pay"},
                    "payment_mode": {
                        "type": ["string", "integer"],
                        "description": "Payment mode code (e.g. 'VIR') or numeric payment_mode_id",
                    },
                    "accountid": {"type": "integer", "description": "Bank account ID (from /bankaccounts)"},
                    "payment_date": {
                        "type": "string",
                        "description": "Payment date (YYYY-MM-DD, default: today)",
                    },
                    "closepaidinvoices": {
                        "type": "boolean",
                        "description": "Close the invoice when the payment balances it (default: true)",
                        "default": True,
                    },
                    "num_payment": {"type": "string", "description": "Payment number (optional)"},
                    "comment": {"type": "string", "description": "Private note (optional)"},
                },
                "required": ["invoice_id", "amount", "payment_mode"],
                "additionalProperties": False,
            },
        ),
        # Supplier Recurring Invoice Templates
        Tool(
            name="get_supplier_invoice_templates",
            description=(
                "List supplier recurring invoice templates (llx_facture_fourn_rec), optionally of one "
                "supplier, as compact rows. Stock Dolibarr exposes no REST endpoint for these objects: "
                "on instances without it the call returns an explicit error pointing at the UI "
                "(fourn/facture/card-rec.php), and it starts working automatically once a Dolibarr "
                "release ships the endpoint."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "supplier_id": {
                        "type": "integer",
                        "description": "Only templates of this supplier (Dolibarr socid).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of templates per page (default: 100, max: 200).",
                        "default": 100,
                        "minimum": 1,
                        "maximum": 200,
                    },
                    "page": {
                        "type": "integer",
                        "description": "Zero-based page number (default: 0).",
                        "default": 0,
                        "minimum": 0,
                    },
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_supplier_invoice_template",
            description=(
                "Get one supplier recurring invoice template with its lines (description, pu_ht, qty, "
                "tva_tx). Generated drafts copy title, ref_supplier and the line prices/quantities from "
                "the template. Same endpoint-availability caveat as get_supplier_invoice_templates."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "template_id": {"type": "integer", "description": "Supplier recurring invoice template ID"},
                },
                "required": ["template_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="update_supplier_invoice_template",
            description=(
                "Update a supplier recurring invoice template: title (label) and/or ref_supplier (the "
                "supplier's own reference on generated drafts). Same endpoint-availability caveat as "
                "get_supplier_invoice_templates."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "template_id": {"type": "integer", "description": "Supplier recurring invoice template ID"},
                    "title": {"type": "string", "description": "New template title (label)"},
                    "label": {"type": "string", "description": "Alias for title"},
                    "ref_supplier": {"type": "string", "description": "New supplier reference"},
                },
                "required": ["template_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="update_supplier_invoice_template_line",
            description=(
                "Update a line of a supplier recurring invoice template: pu_ht (unit price net) and/or "
                "qty. Only the given fields change; the others keep their current values. Same "
                "endpoint-availability caveat as get_supplier_invoice_templates."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "template_id": {"type": "integer", "description": "Supplier recurring invoice template ID"},
                    "line_id": {"type": "integer", "description": "Template line ID to update"},
                    "pu_ht": {"type": "number", "description": "New unit price (net)"},
                    "qty": {"type": "number", "description": "New quantity"},
                },
                "required": ["template_id", "line_id"],
                "additionalProperties": False,
            },
        ),
        # Bank Management (statement reconciliation)
        Tool(
            name="get_bank_accounts",
            description=(
                "List the bank accounts as compact rows (id, label, bank). The raw API objects are "
                "~10 KB each; this returns only what is needed to pick an account for get_bank_lines."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of accounts to return (default: 100)",
                        "default": 100,
                    },
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_bank_lines",
            description=(
                "List the bank lines of an account as compact rows (rowid, datev, dateo, label, amount, "
                "num_releve, rappro flag) for statement reconciliation - the raw objects are ~10 KB each. "
                "Filters: from/to (YYYY-MM-DD, on the value date datev), label_contains, num_releve "
                "(bank statement number, e.g. '2026/02'), amount (exact). Filtering is done server-side "
                "with one single-condition sqlfilters call per filter, merged locally: this endpoint "
                "rejects the 't.' prefix and multi-condition filters, so they are never sent. Fetch one "
                "month with e.g. from='2026-02-01', to='2026-02-28'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "account_id": {"type": "integer", "description": "Bank account id (from get_bank_accounts)"},
                    "from": {"type": "string", "description": "Only lines with datev on/after this date (YYYY-MM-DD)"},
                    "to": {"type": "string", "description": "Only lines with datev on/before this date (YYYY-MM-DD)"},
                    "label_contains": {"type": "string", "description": "Only lines whose label contains this text"},
                    "num_releve": {"type": "string", "description": "Only lines of this bank statement (e.g. '2026/02')"},
                    "amount": {"type": "number", "description": "Only lines with exactly this amount"},
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines returned (default: 100)",
                        "default": 100,
                    },
                },
                "required": ["account_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_bank_line_links",
            description=(
                "Resolve the objects linked to a batch of bank lines in ONE call (each lookup costs ~1s "
                "server-side): GET /bankaccounts/{id}/lines/{line_id}/links per line. Payment-generated "
                "lines carry labels like '(SocialContributionPayment)' or '(SupplierInvoicePayment)'; "
                "the links tell which charge/invoice/payment was settled (type, label, url_id). "
                "Returns {line_id: [links]}."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "account_id": {"type": "integer", "description": "Bank account id (from get_bank_accounts)"},
                    "line_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Bank line ids (rowid from get_bank_lines) to resolve in one batch",
                    },
                },
                "required": ["account_id", "line_ids"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="create_bank_line",
            description=(
                "Create a manual bank line on an account (POST /bankaccounts/{id}/lines). This is the "
                "only place num_releve (bank statement number) can be set through the API, for "
                "a-posteriori reconciliation. date/datev accept YYYY-MM-DD or a Unix timestamp; type is "
                "a payment mode code (VIR, LIQ, CHQ, CB, PRE...). Returns the new line id (rowid)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "account_id": {"type": "integer", "description": "Bank account id (from get_bank_accounts)"},
                    "date": {"type": ["string", "integer"], "description": "Operation date (YYYY-MM-DD or Unix timestamp)"},
                    "type": {"type": "string", "description": "Payment mode code (VIR, LIQ, CHQ, CB, PRE...)"},
                    "label": {"type": "string", "description": "Label of the line"},
                    "amount": {"type": "number", "description": "Amount (negative for debits)"},
                    "datev": {
                        "type": ["string", "integer"],
                        "description": "Value date (YYYY-MM-DD or Unix timestamp, default: same as date)",
                    },
                    "num_releve": {"type": "string", "description": "Bank statement number (e.g. '2026/02')"},
                },
                "required": ["account_id", "date", "type", "label", "amount"],
                "additionalProperties": False,
            },
        ),
        # Social Contributions (charges and taxes)
        Tool(
            name="get_social_contributions",
            description=(
                "List social contributions (URSSAF, apprenticeship tax, CFE, insurance contributions...) "
                "as a compact summary (id, label, type, amount, period, paid flag) - the raw objects are "
                "~10 KB each. Optional filters: period (YYYY-MM-DD or timestamp), type (contribution type "
                "id, e.g. 1), paid (true/false)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of charges to return (default: 100)",
                        "default": 100,
                    },
                    "page": {"type": "integer", "description": "Page number, starts at 1 (default: 1)", "default": 1},
                    "period": {
                        "type": ["string", "integer"],
                        "description": "Only charges of this period (YYYY-MM-DD or Unix timestamp)",
                    },
                    "type": {"type": "integer", "description": "Only charges of this contribution type id (fk_type)"},
                    "paid": {"type": "boolean", "description": "Only paid (true) or unpaid (false) charges"},
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="create_social_contribution",
            description=(
                "Create a social contribution (charge/tax). Accepts the friendly names 'type' and 'period' "
                "(YYYY-MM-DD or Unix timestamp), translated to the server-side 'fk_type'/'period' - the raw "
                "the raw 'type'/French-period fields are never sent. The charge is created unpaid: 'paye' follows the "
                "payments recorded with add_social_contribution_payment. A charge that has a supporting "
                "document (e.g. a tax notice received before the direct debit) is created unpaid and the "
                "payment is recorded in the month of the debit."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Label of the charge"},
                    "type": {"type": "integer", "description": "Contribution type id (fk_type, e.g. 1)"},
                    "amount": {"type": "number", "description": "Amount of the charge"},
                    "date_ech": {"type": ["string", "integer"], "description": "Due date (YYYY-MM-DD or Unix timestamp)"},
                    "period": {
                        "type": ["string", "integer"],
                        "description": "Period of the charge (YYYY-MM-DD or Unix timestamp)",
                    },
                },
                "required": ["label", "type", "amount", "date_ech", "period"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="update_social_contribution",
            description=(
                "Update a social contribution (label, amount, type, date_ech, period). Same friendly-name "
                "translation as create_social_contribution: 'type'/'period' are sent as 'fk_type'/'period', "
                "never as the raw 'type'/French-period fields."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "contribution_id": {"type": "integer", "description": "Social contribution ID"},
                    "label": {"type": "string", "description": "New label"},
                    "type": {"type": "integer", "description": "New contribution type id (fk_type)"},
                    "amount": {"type": "number", "description": "New amount"},
                    "date_ech": {"type": ["string", "integer"], "description": "New due date (YYYY-MM-DD or Unix timestamp)"},
                    "period": {"type": ["string", "integer"], "description": "New period (YYYY-MM-DD or Unix timestamp)"},
                },
                "required": ["contribution_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="add_social_contribution_payment",
            description=(
                "Record a payment on a social contribution (POST /socialcontributions/{id}/payments). "
                "payment_mode accepts a dictionary code (e.g. VIR) or a numeric payment_mode_id (resolve "
                "codes with get_dictionary('payment_types')). accountid is a bank account id from "
                "GET /bankaccounts and creates the linked bank line. The charge's paid flag ('paye') "
                "follows these payments; closepaidcontrib=true (default) closes it when fully paid. "
                "Returns the new payment id."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "contribution_id": {"type": "integer", "description": "Social contribution ID"},
                    "amount": {"type": "number", "description": "Amount to pay"},
                    "payment_mode": {
                        "type": ["string", "integer"],
                        "description": "Payment mode code (e.g. 'VIR') or numeric payment_mode_id",
                    },
                    "accountid": {"type": "integer", "description": "Bank account ID (from /bankaccounts)"},
                    "payment_date": {"type": "string", "description": "Payment date (YYYY-MM-DD, default: today)"},
                    "num_payment": {"type": "string", "description": "Payment number (optional)"},
                    "note": {"type": "string", "description": "Note on the payment (optional)"},
                    "closepaidcontrib": {
                        "type": "boolean",
                        "description": "Close the charge when the payment balances it (default: true)",
                        "default": True,
                    },
                },
                "required": ["contribution_id", "amount", "payment_mode"],
                "additionalProperties": False,
            },
        ),
        # Order Management CRUD
        Tool(
            name="get_orders",
            description=(
                "Get a paginated list of orders from Dolibarr, optionally filtered by status. "
                "Use this for overviews or reporting. Not suitable for searching specific orders by customer, project "
                "or reference (there is no server-side search here)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of orders to return (default: 100)",
                        "default": 100,
                    },
                    "status": {
                        "type": "string",
                        "description": "Order status filter",
                    },
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_order_by_id",
            description=(
                "Get the details of exactly one order by numeric ID. "
                "Use this only when you already know the internal Dolibarr order_id. "
                "Do not pass order reference here."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "integer",
                        "description": "Exact numeric Dolibarr order ID.",
                    }
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="create_order",
            description=(
                "Create a new customer order. Use this only when you have already resolved the correct customer "
                "ID (socid) using search_customers or related tools. This tool does not update existing orders."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "integer",
                        "description": "Customer ID (socid)",
                    },
                    "date": {
                        "type": "string",
                        "description": "Order date (YYYY-MM-DD)",
                    },
                },
                "required": ["customer_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="update_order",
            description="Update an existing order",
            inputSchema={
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "integer",
                        "description": "Order ID to update",
                    },
                    "date": {
                        "type": "string",
                        "description": "Order date (YYYY-MM-DD)",
                    },
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="delete_order",
            description="Delete an order",
            inputSchema={
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "integer",
                        "description": "Order ID to delete",
                    }
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        ),
        # Contact Management CRUD
        Tool(
            name="get_contacts",
            description=(
                "Get a paginated list of contacts from Dolibarr. "
                "Use this only if you need a generic list of contacts. "
                "Do not treat this as a name search; if you need search-by-name, a dedicated search tool should be used."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of contacts to return (default: 100)",
                        "default": 100,
                    }
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_contact_by_id",
            description=(
                "Get the details of exactly one contact by numeric ID. "
                "Use this only when you already know the internal Dolibarr contact_id. "
                "Do not pass name or email here. "
                "Note: phone can be filled in the database yet be returned as null; "
                "trust a sqlfilters match on t.phone over a null read."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "contact_id": {
                        "type": "integer",
                        "description": "Exact numeric Dolibarr contact ID.",
                    }
                },
                "required": ["contact_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="create_contact",
            description="Create a new contact",
            inputSchema={
                "type": "object",
                "properties": {
                    "firstname": {"type": "string", "description": "First name"},
                    "lastname": {"type": "string", "description": "Last name"},
                    "email": {"type": "string", "description": "Email address"},
                    "phone": {"type": "string", "description": "Phone number"},
                    "socid": {
                        "type": "integer",
                        "description": "Associated company ID (thirdparty socid)",
                    },
                },
                "required": ["firstname", "lastname"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="update_contact",
            description="Update an existing contact",
            inputSchema={
                "type": "object",
                "properties": {
                    "contact_id": {
                        "type": "integer",
                        "description": "Contact ID to update",
                    },
                    "firstname": {"type": "string", "description": "First name"},
                    "lastname": {"type": "string", "description": "Last name"},
                    "email": {"type": "string", "description": "Email address"},
                    "phone": {"type": "string", "description": "Phone number"},
                    "socid": {
                        "type": "integer",
                        "description": "Move the contact to this third party (company) ID",
                    },
                    "fk_soc": {
                        "type": "integer",
                        "description": "Alias of socid; either one moves the contact, both are sent to Dolibarr",
                    },
                },
                "required": ["contact_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="delete_contact",
            description="Delete a contact",
            inputSchema={
                "type": "object",
                "properties": {
                    "contact_id": {
                        "type": "integer",
                        "description": "Contact ID to delete",
                    }
                },
                "required": ["contact_id"],
                "additionalProperties": False,
            },
        ),
        # Project Management CRUD
        Tool(
            name="get_projects",
            description=(
                "Get a paginated list of projects from Dolibarr, optionally filtered by status. "
                "Use this for overviews or when you need to iterate through project pages. "
                "Do not use this to search for a project by name or reference (use search_projects instead)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of projects to return (default: 100)",
                        "default": 100,
                    },
                    "page": {
                        "type": "integer",
                        "description": "Page number, starting at 1 (default: 1)",
                        "default": 1,
                    },
                    "status": {
                        "type": "integer",
                        "description": "Project status filter (e.g. 0=draft, 1=open, 2=closed)",
                        "default": 1,
                    },
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_project_by_id",
            description=(
                "Get the details of exactly one project by numeric ID. "
                "Use this only when you already know the internal Dolibarr project_id. "
                "Do not pass project reference here."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "integer",
                        "description": "Exact numeric Dolibarr project ID.",
                    }
                },
                "required": ["project_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="search_projects",
            description=(
                "Search projects by reference or title. Use this when you have a partial or full project ref/title "
                "and need to find matching projects without loading full project lists."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term for project ref or title",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 20,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="create_project",
            description="Create a new project",
            inputSchema={
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "Project reference (optional, if Dolibarr auto-generates)",
                    },
                    "title": {"type": "string", "description": "Project title"},
                    "description": {
                        "type": "string",
                        "description": "Project description",
                    },
                    "socid": {
                        "type": "integer",
                        "description": "Linked customer ID (thirdparty)",
                    },
                    "status": {
                        "type": "integer",
                        "description": "Project status (e.g. 1=open)",
                        "default": 1,
                    },
                },
                "required": ["title"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="update_project",
            description="Update an existing project",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "integer",
                        "description": "Project ID to update",
                    },
                    "title": {"type": "string", "description": "Project title"},
                    "description": {
                        "type": "string",
                        "description": "Project description",
                    },
                    "status": {
                        "type": "integer",
                        "description": "Project status",
                    },
                },
                "required": ["project_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="delete_project",
            description="Delete a project",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "integer",
                        "description": "Project ID to delete",
                    }
                },
                "required": ["project_id"],
                "additionalProperties": False,
            },
        ),
        # Project tasks & consumed time
        Tool(
            name="create_task",
            description=(
                "Create a task on a project. The given 'ref' is required by the API and becomes the "
                "final task reference (no automatic TK number is generated); 'label' is the task title."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "Project ID the task belongs to"},
                    "ref": {"type": "string", "description": "Task reference (used as-is, required)"},
                    "label": {"type": "string", "description": "Task label/title (required)"},
                },
                "required": ["project_id", "ref", "label"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="add_task_time",
            description=(
                "Add a consumed-time entry to a task. 'date' is stored as UTC: pass "
                "'YYYY-MM-DD HH:MM:SS' (interpreted as UTC), a Unix timestamp, or an explicitly-zoned "
                "ISO string. 'duration' is in seconds (3600 = 1h); 'user_id' 0 = connected user."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "Task ID"},
                    "date": {
                        "type": ["string", "integer"],
                        "description": "Entry date (UTC; 'YYYY-MM-DD HH:MM:SS', epoch, or zoned ISO string)",
                    },
                    "duration": {"type": "integer", "description": "Duration in seconds"},
                    "user_id": {"type": "integer", "description": "User ID (0 = connected user)", "default": 0},
                    "note": {"type": "string", "description": "Note", "default": ""},
                },
                "required": ["task_id", "date", "duration"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="update_task_time",
            description=(
                "Correct a consumed-time entry of a task (same fields as add_task_time); "
                "'timespent_id' is the entry id returned by get_task_timespent."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "Task ID"},
                    "timespent_id": {"type": "integer", "description": "Consumed-time entry ID"},
                    "date": {
                        "type": ["string", "integer"],
                        "description": "Entry date (UTC; 'YYYY-MM-DD HH:MM:SS', epoch, or zoned ISO string)",
                    },
                    "duration": {"type": "integer", "description": "Duration in seconds"},
                    "user_id": {"type": "integer", "description": "User ID (0 = connected user)", "default": 0},
                    "note": {"type": "string", "description": "Note", "default": ""},
                },
                "required": ["task_id", "timespent_id", "date", "duration"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="delete_task_time",
            description="Delete a consumed-time entry of a task; 'timespent_id' is the entry id returned by get_task_timespent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "Task ID"},
                    "timespent_id": {"type": "integer", "description": "Consumed-time entry ID"},
                },
                "required": ["task_id", "timespent_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_task_timespent",
            description=(
                "Consumed-time entries of one task. Raw API rows prefix every field with "
                "'timespent_line_'; this tool maps them to clean names (id, date, datehour, duration, "
                "fk_user, note, ...) and surfaces 'total_duration' in seconds."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "Task ID"},
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_project_timespent",
            description=(
                "Consolidated consumed time of a project: surfaces 'total_duration' in seconds and the "
                "individual entries (each tagged with its task_id). Project-scoped list endpoints can "
                "wrongly return empty, so the tool falls back to direct per-task reads."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "Project ID"},
                },
                "required": ["project_id"],
                "additionalProperties": False,
            },
        ),
        # Interventions (fichinter)
        Tool(
            name="get_interventions",
            description=(
                "List intervention sheets (fichinter) as compact summaries with the fields "
                "'id', 'ref', 'socid', 'fk_project', 'status', 'description'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max rows (default 100)", "default": 100},
                    "page": {"type": "integer", "description": "Page, starts at 1 (default 1)", "default": 1},
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_intervention",
            description=(
                "One intervention sheet by ID. Lines are compact: 'id', 'desc', 'date' " "(Unix timestamp), 'duration' (seconds)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "intervention_id": {"type": "integer", "description": "Intervention (fichinter) ID"},
                },
                "required": ["intervention_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="create_intervention",
            description=(
                "Create a project-linked intervention sheet. Requires 'project_id' (fk_project), "
                "'socid' (thirdparty) and 'description'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "Project ID the sheet belongs to (fk_project, required)"},
                    "socid": {"type": "integer", "description": "Thirdparty ID (required)"},
                    "description": {"type": "string", "description": "Sheet description (required)"},
                },
                "required": ["project_id", "socid", "description"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="add_intervention_line",
            description=(
                "Add a line to an intervention sheet. 'description' (the write side rejects 'desc'), "
                "'date' (Unix timestamp; a 'YYYY-MM-DD HH:MM:SS' string is read as UTC) and 'duration' "
                "in seconds (3600 = 1h). An open sheet accepts lines dated on other days."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "intervention_id": {"type": "integer", "description": "Intervention (fichinter) ID"},
                    "description": {"type": "string", "description": "Line description"},
                    "date": {
                        "type": ["string", "integer"],
                        "description": "Line date (Unix timestamp, or 'YYYY-MM-DD HH:MM:SS' read as UTC)",
                    },
                    "duration": {"type": "integer", "description": "Duration in seconds"},
                },
                "required": ["intervention_id", "description", "date", "duration"],
                "additionalProperties": False,
            },
        ),
        # Agenda events
        Tool(
            name="create_agenda_event",
            description=(
                "Create an agenda event (POST /agendaevents). Requires 'label', 'datep' and 'datef' "
                "(Unix timestamps, or 'YYYY-MM-DD HH:MM:SS' read as UTC), 'userownerid' and the event "
                "type as 'type_code' (a code like 'AC_TEL' listed by get_dictionary('event_types')) or "
                "'type' (its numeric dictionary id; both are resolved through the dictionary). Optional: "
                "'socid' (third party to link), 'percentage' (0-100, 100 = completed), 'note' (stored as "
                "note_private), 'contact_id' (accepted by the API but NOT persisted by upstream Dolibarr)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "type_code": {
                        "type": "string",
                        "description": "Event type code, e.g. 'AC_TEL' (see get_dictionary('event_types')); a numeric id string also works",
                    },
                    "type": {
                        "type": "integer",
                        "description": "Numeric event type id from get_dictionary('event_types') (alternative to type_code)",
                    },
                    "label": {"type": "string", "description": "Event label/title"},
                    "datep": {
                        "type": ["integer", "string"],
                        "description": "Start (Unix timestamp, or 'YYYY-MM-DD HH:MM:SS' read as UTC)",
                    },
                    "datef": {
                        "type": ["integer", "string"],
                        "description": "End (Unix timestamp, or 'YYYY-MM-DD HH:MM:SS' read as UTC)",
                    },
                    "socid": {"type": "integer", "description": "Third party ID to link the event to"},
                    "userownerid": {"type": "integer", "description": "User ID owning the event (required by the API)"},
                    "percentage": {"type": "integer", "description": "Completion percentage (0-100, 100 = completed)"},
                    "note": {"type": "string", "description": "Private note (stored as note_private)"},
                    "contact_id": {
                        "type": "integer",
                        "description": "Contact ID: accepted by the API but NOT persisted by upstream Dolibarr",
                    },
                },
                "required": ["label", "datep", "datef", "userownerid"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_agenda_event",
            description="Get one agenda event by id (GET /agendaevents/{id}), including type_code, label, datep/datef, socid, percentage and note_private.",
            inputSchema={
                "type": "object",
                "properties": {
                    "event_id": {"type": "integer", "description": "Agenda event ID"},
                },
                "required": ["event_id"],
                "additionalProperties": False,
            },
        ),
        # Documents
        Tool(
            name="get_documents",
            description=(
                "List the files attached to a Dolibarr object (generated PDFs, Factur-X PDFs, uploaded files). "
                "Identify the object by its ref (e.g. 'FA2024-0001') or numeric id. Each entry includes "
                "'original_file', to pass to download_document. If the instance cannot list a modulepart "
                "(tax, bank, ...), the result degrades explicitly: it returns the modulepart, the path "
                "convention and the object refs so files can still be located with download_document."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "modulepart": {
                        "type": "string",
                        "description": (
                            "Object type: invoice, supplier_invoice, order, supplier_order, propal, "
                            "shipment, contract, thirdparty, product, expensereport, member, user, tax "
                            "(social charges), bank (statements) (Dolibarr also accepts its French names: "
                            "facture, commande, societe...)."
                        ),
                    },
                    "ref": {"type": "string", "description": "Object reference (e.g. 'FA2024-0001')."},
                    "id": {
                        "type": "integer",
                        "description": "Object numeric id (alternative to ref). For 'tax' this is the "
                        "social-charge rowid; for 'bank' the bank account id.",
                    },
                    "subdir": {
                        "type": "string",
                        "description": "Subdirectory identifying the files when no ref applies: "
                        "'{account_id}/statement/{num}' for 'bank' (statement number YYYYMM).",
                    },
                },
                "required": ["modulepart"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="download_document",
            description=(
                "Download one attached file. PDFs are returned as an embedded resource; files embedded in "
                "the PDF (e.g. the Factur-X 'factur-x.xml') are extracted and returned as text. Text files "
                "are returned as text. Use get_documents first to obtain 'original_file'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "modulepart": {
                        "type": "string",
                        "description": "Same object type as used with get_documents (e.g. 'invoice', "
                        "'supplier_invoice', 'tax', 'bank'). 'supplier_invoice' is normalized to the "
                        "endpoint's 'invoice_supplier' internally.",
                    },
                    "original_file": {
                        "type": "string",
                        "description": "Relative path from get_documents (e.g. 'FA2024-0001/FA2024-0001_facturx.pdf', "
                        "or '0/0/SI2601-0009/scan.pdf' for supplier_invoice, '{rowid}/scan.pdf' for tax, "
                        "'{account_id}/statement/{num}/releve.pdf' for bank).",
                    },
                    "include_file": {
                        "type": "boolean",
                        "default": True,
                        "description": "Return the binary file itself (default true). Set false to get only "
                        "metadata and extracted embedded files (smaller response).",
                    },
                },
                "required": ["modulepart", "original_file"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="upload_document",
            description=(
                "Attach a file to a Dolibarr object (POST /documents/upload). The file content is "
                "base64 and is sent in the JSON body, so large files (tested with a 1 MB PDF) work. "
                "Returns the stored file name and its 'original_file' path (use it with "
                "download_document / delete_document). Path conventions computed internally: "
                "supplier_invoice -> '{id mod 10}/{floor(id/10) mod 10}/{ref}/{filename}' (pass both "
                "ref and id), tax -> subdir = social-charge rowid, bank -> subdir = "
                "'{account_id}/statement/{num}' (statement number YYYYMM), other moduleparts -> "
                "'{ref}/{filename}'. With overwrite=true (default) an existing file is replaced "
                "instead of being left untouched."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "modulepart": {
                        "type": "string",
                        "description": (
                            "Object type, same names as get_documents: invoice/facture, supplier_invoice, "
                            "order, supplier_order, propal, contract, thirdparty, product, expensereport, "
                            "tax (social charges), bank (statements), ..."
                        ),
                    },
                    "ref": {"type": "string", "description": "Object reference (e.g. 'FA2024-0001', 'SI2601-0009')."},
                    "id": {
                        "type": "integer",
                        "description": "Object numeric id. Required (with ref) for supplier_invoice, "
                        "whose path embeds the id; resolved from ref when omitted.",
                    },
                    "subdir": {
                        "type": "string",
                        "description": "Subdirectory when no ref applies: social-charge rowid for 'tax', "
                        "'{account_id}/statement/{num}' for 'bank'.",
                    },
                    "filename": {"type": "string", "description": "Name of the file to create (e.g. 'scan.pdf')."},
                    "filecontent": {"type": "string", "description": "File content, base64-encoded."},
                    "overwrite": {
                        "type": "boolean",
                        "default": True,
                        "description": "Replace the file if it already exists (default true).",
                    },
                },
                "required": ["modulepart", "filename", "filecontent"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="delete_document",
            description=(
                "Delete an attached file (DELETE /documents with modulepart + original_file, e.g. "
                "from get_documents or upload_document). Deleting an already-deleted file (404 "
                "'File not found') counts as success."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "modulepart": {
                        "type": "string",
                        "description": "Same object type as used with upload_document (e.g. 'supplier_invoice').",
                    },
                    "original_file": {
                        "type": "string",
                        "description": "Relative path as returned by upload_document / get_documents "
                        "(e.g. '0/0/SI2601-0009/scan.pdf').",
                    },
                },
                "required": ["modulepart", "original_file"],
                "additionalProperties": False,
            },
        ),
        # Ticket Management
        Tool(
            name="get_tickets",
            description=(
                "Get a list of tickets from Dolibarr. By default only open tickets are returned "
                "(closed and cancelled tickets are excluded). Optionally filter by a specific status. "
                "Status codes: 0=unread, 1=read, 3=assigned, 4=in progress, 5=waiting for customer, "
                "7=waiting for helpdesk, 8=closed, 9=cancelled. "
                "Do not use this to look up a single ticket by reference (use get_ticket_by_ref) "
                "or to search by subject/reference (use search_tickets)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of tickets to return (default: 100)",
                        "default": 100,
                    },
                    "page": {
                        "type": "integer",
                        "description": "Page number, starting at 1 (default: 1)",
                        "default": 1,
                    },
                    "status": {
                        "type": "integer",
                        "description": (
                            "Optional exact status filter (fk_statut). Omit to return all open "
                            "tickets. E.g. 4=in progress, 5=waiting for customer, 8=closed."
                        ),
                    },
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_ticket_by_ref",
            description=(
                "Get the full details of exactly one ticket by its business reference (e.g. 'TI1046'). "
                "This is the column 'ref' in Dolibarr, NOT the internal numeric row id. "
                "The response includes the messages array and the track_id required for add_ticket_message."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "Ticket reference, e.g. 'TI1046'.",
                    }
                },
                "required": ["ref"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="search_tickets",
            description=(
                "Search tickets by reference, subject or track_id. Use this when you have a partial or "
                "full term and need to find matching tickets without loading the full ticket list."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term matched against ticket ref, subject and track_id.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 20,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="create_ticket",
            description="Create a new ticket. Requires a subject and an initial message.",
            inputSchema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "Ticket subject/title"},
                    "message": {
                        "type": "string",
                        "description": "Initial ticket message body",
                    },
                    "socid": {
                        "type": "integer",
                        "description": "Linked thirdparty/customer ID (optional)",
                    },
                    "type_code": {
                        "type": "string",
                        "description": "Ticket type code, e.g. 'REQUEST', 'ISSUE' (optional)",
                    },
                    "category_code": {
                        "type": "string",
                        "description": "Ticket category code (optional)",
                    },
                    "severity_code": {
                        "type": "string",
                        "description": "Ticket severity code, e.g. 'NORMAL' (optional)",
                    },
                    "fk_user_assign": {
                        "type": "integer",
                        "description": "ID of the user the ticket is assigned to (optional)",
                    },
                },
                "required": ["subject", "message"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="add_ticket_message",
            description=(
                "Add a message to an existing ticket. Provide the ticket reference 'ref' (e.g. 'TI1046') "
                "OR the 'track_id'. If only 'ref' is given the track_id is resolved automatically. "
                "Prefer 'ref' unless you already know the track_id."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "Ticket reference, e.g. 'TI1046' (alternative to track_id).",
                    },
                    "track_id": {
                        "type": "string",
                        "description": "Ticket track_id (alternative to ref).",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message body to add to the ticket",
                    },
                    "private": {
                        "type": "integer",
                        "description": "1 for an internal/private message, 0 for public (optional)",
                    },
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="update_ticket",
            description=(
                "Update an existing ticket (e.g. change status, subject or assignment). "
                "Provide the ticket reference 'ref' (e.g. 'TI1046') OR the numeric 'ticket_id'. "
                "If only 'ref' is given the internal id is resolved automatically."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "Ticket reference, e.g. 'TI1046' (alternative to ticket_id).",
                    },
                    "ticket_id": {
                        "type": "integer",
                        "description": "Internal numeric ticket id (alternative to ref).",
                    },
                    "subject": {"type": "string", "description": "Updated subject"},
                    "message": {"type": "string", "description": "Updated message"},
                    "fk_statut": {
                        "type": "integer",
                        "description": (
                            "New status. 0=unread, 1=read, 3=assigned, 4=in progress, "
                            "5=waiting for customer, 7=waiting for helpdesk, 8=closed, 9=cancelled."
                        ),
                    },
                    "fk_user_assign": {
                        "type": "integer",
                        "description": "ID of the user the ticket is assigned to",
                    },
                    "severity_code": {
                        "type": "string",
                        "description": "Ticket severity code, e.g. 'NORMAL'",
                    },
                },
                "additionalProperties": False,
            },
        ),
        # Raw API Access
        Tool(
            name="dolibarr_raw_api",
            description=(
                "Low-level escape hatch to call any Dolibarr REST endpoint directly. "
                "Use this ONLY if there is no dedicated high-level tool available for your use case. "
                "You must pass a valid Dolibarr API path and parameters yourself; the server does not validate them. "
                "Incorrect usage can cause errors or side effects (such as creating or deleting unexpected data)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "description": "HTTP method",
                        "enum": ["GET", "POST", "PUT", "DELETE"],
                    },
                    "endpoint": {
                        "type": "string",
                        "description": "Dolibarr API endpoint path (e.g. '/thirdparties', '/invoices/123'). Must be a valid existing endpoint.",
                    },
                    "params": {
                        "type": "object",
                        "description": "Query parameters",
                    },
                    "data": {
                        "type": "object",
                        "description": "Request payload for POST/PUT requests",
                    },
                },
                "required": ["method", "endpoint"],
                "additionalProperties": False,
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict):
    """Handle all tool calls using the DolibarrClient."""

    try:
        # Initialize the config and client
        config = Config()

        if not is_tool_allowed(name, config):
            denied = {
                "error": "Forbidden",
                "status": 403,
                "message": f"Tool '{name}' is disabled by server policy " "(MCP_READ_ONLY / MCP_ENABLE_RAW_API).",
            }
            return _error_result(denied)

        async with DolibarrClient(config) as client:

            # System & Info
            if name == "test_connection":
                result = await client.get_status()
                if "success" not in result:
                    result = {"status": "success", "message": "API connection working", "data": result}

            elif name == "get_status":
                result = await client.get_status()

            elif name == "get_dictionary":
                result = await client.get_dictionary(arguments["name"])

            # Search Tools
            elif name == "search_products_by_ref":
                ref_prefix = _escape_sqlfilter(arguments["ref_prefix"])
                limit = arguments.get("limit", 20)
                sqlfilters = f"(t.ref:like:'{ref_prefix}%')"
                result = await client.search_products(sqlfilters=sqlfilters, limit=limit)

            elif name == "search_customers":
                query = _escape_sqlfilter(arguments["query"])
                limit = arguments.get("limit", 20)
                sqlfilters = f"((t.nom:like:'%{query}%') OR (t.name_alias:like:'%{query}%'))"
                result = await client.search_customers(sqlfilters=sqlfilters, limit=limit)

            elif name == "search_thirdparties":
                query = _escape_sqlfilter(arguments["query"])
                kind = arguments.get("kind", "any")
                limit = arguments.get("limit", 20)
                if kind not in ("customer", "supplier", "any"):
                    raise ValueError(f"Invalid kind: {kind!r} (expected customer, supplier or any)")
                sqlfilters = f"((t.nom:like:'%{query}%') OR (t.name_alias:like:'%{query}%'))"
                if kind == "customer":
                    sqlfilters += " AND (t.client:!=:0)"
                elif kind == "supplier":
                    sqlfilters += " AND (t.fournisseur:=:1)"
                result = await client.search_thirdparties(sqlfilters=sqlfilters, limit=limit)

            elif name == "search_products_by_label":
                label_search = _escape_sqlfilter(arguments["label_search"])
                limit = arguments.get("limit", 20)
                sqlfilters = f"(t.label:like:'%{label_search}%')"
                result = await client.search_products(sqlfilters=sqlfilters, limit=limit)

            elif name == "resolve_product_ref":
                ref = arguments["ref"]
                ref_esc = _escape_sqlfilter(ref)
                sqlfilters = f"(t.ref:like:'{ref_esc}')"
                products = await client.search_products(sqlfilters=sqlfilters, limit=2)

                if not products:
                    result = {"status": "not_found", "message": f"Product with ref '{ref}' not found"}
                elif len(products) == 1:
                    result = {"status": "ok", "product": products[0]}
                else:
                    # Check if one is exact match
                    exact_matches = [p for p in products if p.get("ref") == ref]
                    if len(exact_matches) == 1:
                        result = {"status": "ok", "product": exact_matches[0]}
                    else:
                        result = {
                            "status": "ambiguous",
                            "message": f"Multiple products found for ref '{ref}'",
                            "products": products,
                        }

            # User Management
            elif name == "get_users":
                result = await client.get_users(limit=arguments.get("limit", 100), page=arguments.get("page", 1))

            elif name == "get_user_by_id":
                result = await client.get_user_by_id(arguments["user_id"])

            elif name == "create_user":
                result = await client.create_user(**arguments)

            elif name == "update_user":
                user_id = arguments.pop("user_id")
                result = await client.update_user(user_id, **arguments)

            elif name == "delete_user":
                result = await client.delete_user(arguments["user_id"])

            # Customer Management
            elif name == "get_customers":
                result = await client.get_customers(limit=arguments.get("limit", 100), page=arguments.get("page", 1))

            elif name == "get_customer_by_id":
                result = await client.get_customer_by_id(arguments["customer_id"])

            elif name == "create_customer":
                result = await client.create_customer(**arguments)

            elif name == "update_customer":
                customer_id = arguments.pop("customer_id")
                result = await client.update_customer(customer_id, **arguments)

            elif name == "delete_customer":
                result = await client.delete_customer(arguments["customer_id"])

            # Product Management
            elif name == "get_products":
                result = await client.get_products(limit=arguments.get("limit", 100))

            elif name == "get_product_by_id":
                result = await client.get_product_by_id(arguments["product_id"])

            elif name == "create_product":
                result = await client.create_product(**arguments)

            elif name == "update_product":
                product_id = arguments.pop("product_id")
                result = await client.update_product(product_id, **arguments)

            elif name == "delete_product":
                result = await client.delete_product(arguments["product_id"])

            # Invoice Management
            elif name == "get_invoices":
                limit = max(1, min(int(arguments.get("limit", 20)), INVOICE_LIST_MAX_LIMIT))
                sort_by = arguments.get("sort_by", "date")
                sort_order = arguments.get("sort_order", "desc")
                if sort_by not in INVOICE_SORT_FIELDS:
                    raise ValueError(f"sort_by must be one of {sorted(INVOICE_SORT_FIELDS)}")
                if sort_order not in ("asc", "desc"):
                    raise ValueError("sort_order must be 'asc' or 'desc'")
                invoices = await client.get_invoices(
                    limit=limit,
                    status=arguments.get("status"),
                    customer_id=arguments.get("customer_id"),
                    page=max(0, int(arguments.get("page", 0))),
                    sortfield=INVOICE_SORT_FIELDS[sort_by],
                    sortorder=sort_order.upper(),
                )
                if arguments.get("full"):
                    result = invoices
                else:
                    result = [_summarize_invoice(inv) for inv in invoices]

            elif name == "get_invoice_by_id":
                result = await client.get_invoice_by_id(arguments["invoice_id"])

            elif name == "create_invoice":
                result = await client.create_invoice(**arguments)

            elif name == "update_invoice":
                invoice_id = arguments.pop("invoice_id")
                result = await client.update_invoice(invoice_id, **arguments)

            elif name == "delete_invoice":
                result = await client.delete_invoice(arguments["invoice_id"])

            elif name == "create_invoice_draft":
                # Map customer_id to socid for the API
                if "customer_id" in arguments:
                    arguments["socid"] = arguments.pop("customer_id")

                # Map project_id to fk_project if present
                if "project_id" in arguments:
                    arguments["fk_project"] = arguments.pop("project_id")

                result = await client.create_invoice(**arguments)

            elif name == "add_invoice_line":
                invoice_id = arguments.pop("invoice_id")
                result = await client.add_invoice_line(invoice_id, **arguments)

            elif name == "update_invoice_line":
                invoice_id = arguments.pop("invoice_id")
                line_id = arguments.pop("line_id")
                result = await client.update_invoice_line(invoice_id, line_id, **arguments)

            elif name == "delete_invoice_line":
                invoice_id = arguments.pop("invoice_id")
                line_id = arguments.pop("line_id")
                result = await client.delete_invoice_line(invoice_id, line_id)

            elif name == "set_invoice_project":
                invoice_id = arguments.pop("invoice_id")
                project_id = arguments.pop("project_id")
                result = await client.update_invoice(invoice_id, fk_project=project_id)

            elif name == "validate_invoice":
                invoice_id = arguments.pop("invoice_id")
                result = await client.validate_invoice(invoice_id, **arguments)

            elif name == "pay_invoice":
                invoice_id = arguments.pop("invoice_id")
                result = await client.pay_invoice(invoice_id, **arguments)

            elif name == "get_invoice_payments":
                result = await client.get_invoice_payments(
                    arguments["invoice_id"], direction=arguments.get("direction", "customer")
                )

            # Supplier Invoice Management
            elif name == "get_supplier_invoices":
                limit = max(1, min(int(arguments.get("limit", 20)), INVOICE_LIST_MAX_LIMIT))
                sort_by = arguments.get("sort_by", "date")
                sort_order = arguments.get("sort_order", "desc")
                if sort_by not in INVOICE_SORT_FIELDS:
                    raise ValueError(f"sort_by must be one of {sorted(INVOICE_SORT_FIELDS)}")
                if sort_order not in ("asc", "desc"):
                    raise ValueError("sort_order must be 'asc' or 'desc'")
                # Dolibarr sqlfilters on /supplierinvoices need the t. prefix and only
                # honor one condition per call, so map at most one filter here.
                conditions = []
                if arguments.get("supplier_id") is not None:
                    conditions.append(f"(t.fk_soc:=:{int(arguments['supplier_id'])})")
                if arguments.get("ref_supplier"):
                    ref_supplier = _escape_sqlfilter(str(arguments["ref_supplier"]))
                    conditions.append(f"(t.ref_supplier:=:'{ref_supplier}')")
                if arguments.get("status"):
                    status_ids = {label: code for code, label in INVOICE_STATUS_LABELS.items()}
                    if arguments["status"] not in status_ids:
                        raise ValueError(f"status must be one of {sorted(status_ids)}")
                    conditions.append(f"(t.fk_statut:=:{status_ids[arguments['status']]})")
                if len(conditions) > 1:
                    raise ValueError(
                        "get_supplier_invoices accepts a single filter condition per call "
                        "(combined sqlfilters return 0 rows); call it once per condition"
                    )
                invoices = await client.get_supplier_invoices(
                    limit=limit,
                    sqlfilters=conditions[0] if conditions else None,
                    page=max(0, int(arguments.get("page", 0))),
                    sortfield=INVOICE_SORT_FIELDS[sort_by],
                    sortorder=sort_order.upper(),
                )
                result = [_summarize_supplier_invoice(inv) for inv in invoices]

            elif name == "create_supplier_invoice":
                result = await client.create_supplier_invoice(**arguments)

            elif name == "add_supplier_invoice_line":
                invoice_id = arguments.pop("invoice_id")
                result = await client.add_supplier_invoice_line(invoice_id, **arguments)

            elif name == "update_supplier_invoice_line":
                invoice_id = arguments.pop("invoice_id")
                line_id = arguments.pop("line_id")
                result = await client.update_supplier_invoice_line(invoice_id, line_id, **arguments)

            elif name == "validate_supplier_invoice":
                invoice_id = arguments.pop("invoice_id")
                result = await client.validate_supplier_invoice(invoice_id, **arguments)

            elif name == "set_supplier_invoice_to_draft":
                result = await client.set_supplier_invoice_to_draft(arguments["invoice_id"])

            elif name == "set_supplier_invoice_paid":
                result = await client.set_supplier_invoice_paid(arguments["invoice_id"])

            elif name == "update_supplier_invoice":
                invoice_id = arguments.pop("invoice_id")
                result = await client.update_supplier_invoice(invoice_id, **arguments)

            elif name == "pay_supplier_invoice":
                invoice_id = arguments.pop("invoice_id")
                result = await client.pay_supplier_invoice(invoice_id, **arguments)

            # Supplier Recurring Invoice Templates
            elif name == "get_supplier_invoice_templates":
                templates = await client.get_supplier_invoice_templates(
                    limit=max(1, min(int(arguments.get("limit", 100)), 200)),
                    supplier_id=arguments.get("supplier_id"),
                    page=max(0, int(arguments.get("page", 0))),
                )
                result = [_summarize_supplier_invoice_template(t) for t in templates]

            elif name == "get_supplier_invoice_template":
                template = await client.get_supplier_invoice_template(arguments["template_id"])
                result = _summarize_supplier_invoice_template(template)
                result["lines"] = [_summarize_supplier_invoice_template_line(line) for line in template.get("lines", [])]

            elif name == "update_supplier_invoice_template":
                template_id = arguments.pop("template_id")
                result = await client.update_supplier_invoice_template(template_id, **arguments)

            elif name == "update_supplier_invoice_template_line":
                template_id = arguments.pop("template_id")
                line_id = arguments.pop("line_id")
                result = await client.update_supplier_invoice_template_line(template_id, line_id, **arguments)

            # Bank Management
            elif name == "get_bank_accounts":
                accounts = await client.get_bank_accounts(limit=max(1, min(int(arguments.get("limit", 100)), 200)))
                result = [_summarize_bank_account(a) for a in accounts]

            elif name == "get_bank_lines":
                account_id = arguments.pop("account_id")
                lines = await client.get_bank_lines(
                    account_id,
                    from_date=arguments.get("from"),
                    to_date=arguments.get("to"),
                    label_contains=arguments.get("label_contains"),
                    num_releve=arguments.get("num_releve"),
                    amount=arguments.get("amount"),
                    limit=max(1, min(int(arguments.get("limit", 100)), 1000)),
                )
                result = [_summarize_bank_line(line) for line in lines]

            elif name == "get_bank_line_links":
                result = await client.get_bank_line_links(
                    arguments["account_id"], [int(line_id) for line_id in arguments["line_ids"]]
                )

            elif name == "create_bank_line":
                account_id = arguments.pop("account_id")
                result = await client.create_bank_line(account_id, **arguments)

            # Social Contributions
            elif name == "get_social_contributions":
                contributions = await client.get_social_contributions(
                    limit=max(1, min(int(arguments.get("limit", 100)), 200)),
                    page=max(1, int(arguments.get("page", 1))),
                    period=arguments.get("period"),
                    type=arguments.get("type"),
                    paid=arguments.get("paid"),
                )
                result = [_summarize_social_contribution(c) for c in contributions]

            elif name == "create_social_contribution":
                result = await client.create_social_contribution(**arguments)

            elif name == "update_social_contribution":
                contribution_id = arguments.pop("contribution_id")
                result = await client.update_social_contribution(contribution_id, **arguments)

            elif name == "add_social_contribution_payment":
                contribution_id = arguments.pop("contribution_id")
                result = await client.add_social_contribution_payment(contribution_id, **arguments)

            # Order Management
            elif name == "get_orders":
                result = await client.get_orders(limit=arguments.get("limit", 100), status=arguments.get("status"))

            elif name == "get_order_by_id":
                result = await client.get_order_by_id(arguments["order_id"])

            elif name == "create_order":
                result = await client.create_order(**arguments)

            elif name == "update_order":
                order_id = arguments.pop("order_id")
                result = await client.update_order(order_id, **arguments)

            elif name == "delete_order":
                result = await client.delete_order(arguments["order_id"])

            # Contact Management
            elif name == "get_contacts":
                result = await client.get_contacts(limit=arguments.get("limit", 100))

            elif name == "get_contact_by_id":
                result = await client.get_contact_by_id(arguments["contact_id"])

            elif name == "create_contact":
                result = await client.create_contact(**arguments)

            elif name == "update_contact":
                contact_id = arguments.pop("contact_id")
                result = await client.update_contact(contact_id, **arguments)

            elif name == "delete_contact":
                result = await client.delete_contact(arguments["contact_id"])

            # Project Management
            elif name == "get_projects":
                result = await client.get_projects(
                    limit=arguments.get("limit", 100), page=arguments.get("page", 1), status=arguments.get("status")
                )

            elif name == "get_project_by_id":
                result = await client.get_project_by_id(arguments["project_id"])

            elif name == "search_projects":
                query = _escape_sqlfilter(arguments["query"])
                limit = arguments.get("limit", 20)
                sqlfilters = f"((t.ref:like:'%{query}%') OR (t.title:like:'%{query}%'))"
                result = await client.search_projects(sqlfilters=sqlfilters, limit=limit)

            elif name == "create_project":
                result = await client.create_project(**arguments)

            elif name == "update_project":
                project_id = arguments.pop("project_id")
                result = await client.update_project(project_id, **arguments)

            elif name == "delete_project":
                result = await client.delete_project(arguments["project_id"])

            # Project tasks & consumed time
            elif name == "create_task":
                result = await client.create_task(
                    project_id=arguments["project_id"],
                    ref=arguments["ref"],
                    label=arguments["label"],
                )

            elif name == "add_task_time":
                result = await client.add_task_time(
                    task_id=arguments["task_id"],
                    date=arguments["date"],
                    duration=arguments["duration"],
                    user_id=arguments.get("user_id", 0),
                    note=arguments.get("note", ""),
                )

            elif name == "update_task_time":
                result = await client.update_task_time(
                    task_id=arguments["task_id"],
                    timespent_id=arguments["timespent_id"],
                    date=arguments["date"],
                    duration=arguments["duration"],
                    user_id=arguments.get("user_id", 0),
                    note=arguments.get("note", ""),
                )

            elif name == "delete_task_time":
                result = await client.delete_task_time(
                    task_id=arguments["task_id"],
                    timespent_id=arguments["timespent_id"],
                )

            elif name == "get_task_timespent":
                result = await client.get_task_timespent(arguments["task_id"])

            elif name == "get_project_timespent":
                result = await client.get_project_timespent(arguments["project_id"])

            # Interventions (fichinter)
            elif name == "get_interventions":
                result = await client.get_interventions(limit=arguments.get("limit", 100), page=arguments.get("page", 1))

            elif name == "get_intervention":
                result = await client.get_intervention(arguments["intervention_id"])

            elif name == "create_intervention":
                result = await client.create_intervention(
                    project_id=arguments["project_id"],
                    socid=arguments["socid"],
                    description=arguments["description"],
                )

            elif name == "add_intervention_line":
                result = await client.add_intervention_line(
                    intervention_id=arguments["intervention_id"],
                    description=arguments["description"],
                    date=arguments["date"],
                    duration=arguments["duration"],
                )

            # Agenda events
            elif name == "create_agenda_event":
                result = await client.create_agenda_event(
                    label=arguments["label"],
                    datep=arguments["datep"],
                    datef=arguments["datef"],
                    userownerid=arguments["userownerid"],
                    type_code=arguments.get("type_code"),
                    type=arguments.get("type"),
                    socid=arguments.get("socid"),
                    percentage=arguments.get("percentage"),
                    note=arguments.get("note"),
                    contact_id=arguments.get("contact_id"),
                )

            elif name == "get_agenda_event":
                result = await client.get_agenda_event(arguments["event_id"])

            # Documents
            elif name == "get_documents":
                modulepart = arguments["modulepart"]
                ref = arguments.get("ref")
                obj_id = arguments.get("id")
                if not ref and obj_id is None:
                    return _error_result(
                        {
                            "error": "Bad Request",
                            "status": 400,
                            "message": "Provide either 'ref' or 'id'.",
                        }
                    )
                if modulepart == "supplier_invoice":
                    # The stored path embeds the numeric id: resolve whichever of ref/id is missing.
                    ref, obj_id = await _resolve_supplier_invoice_ref_id(client, ref, obj_id)
                extra = {"subdir": arguments["subdir"]} if arguments.get("subdir") else {}
                try:
                    docs = await client.get_documents(modulepart, ref=ref, id=obj_id, **extra)
                except DolibarrAPIError as exc:
                    # The listing endpoint does not implement this modulepart (tax, bank, ...):
                    # degrade explicitly with the path convention and object refs.
                    result = {
                        "modulepart": modulepart,
                        "ref": ref,
                        "id": obj_id,
                        "subdir": arguments.get("subdir"),
                        "listing": "unavailable",
                        "reason": exc.message,
                        "path_convention": _document_path_convention(modulepart),
                        "hint": "Build 'original_file' from path_convention (upload_document returns the "
                        "exact path) and pass it to download_document.",
                    }
                else:
                    result = [
                        _summarize_document(
                            d,
                            original_file=(
                                DolibarrClient._document_original_file(
                                    "supplier_invoice", d.get("relativename") or d.get("name") or "", ref=ref, id=obj_id
                                )
                                if modulepart == "supplier_invoice"
                                else None
                            ),
                        )
                        for d in docs
                    ]

            elif name == "download_document":
                downloaded = await client.download_document(arguments["modulepart"], arguments["original_file"])
                return _document_contents(
                    downloaded,
                    arguments["modulepart"],
                    arguments["original_file"],
                    include_file=arguments.get("include_file", True),
                )

            elif name == "upload_document":
                modulepart = arguments["modulepart"]
                ref = arguments.get("ref")
                obj_id = arguments.get("id")
                if modulepart == "supplier_invoice":
                    ref, obj_id = await _resolve_supplier_invoice_ref_id(client, ref, obj_id)
                result = await client.upload_document(
                    modulepart,
                    filename=arguments["filename"],
                    filecontent=arguments["filecontent"],
                    ref=ref,
                    id=obj_id,
                    subdir=arguments.get("subdir"),
                    overwrite=arguments.get("overwrite", True),
                )

            elif name == "delete_document":
                result = await client.delete_document(arguments["modulepart"], arguments["original_file"])

            # Ticket Management
            elif name == "get_tickets":
                result = await client.get_tickets(
                    limit=arguments.get("limit", 100),
                    page=arguments.get("page", 1),
                    status=arguments.get("status"),
                )

            elif name == "get_ticket_by_ref":
                result = await client.get_ticket_by_ref(arguments["ref"])

            elif name == "search_tickets":
                query = _escape_sqlfilter(arguments["query"])
                limit = arguments.get("limit", 20)
                sqlfilters = f"((t.ref:like:'%{query}%') OR (t.subject:like:'%{query}%') " f"OR (t.track_id:like:'%{query}%'))"
                result = await client.search_tickets(sqlfilters=sqlfilters, limit=limit)

            elif name == "create_ticket":
                result = await client.create_ticket(**arguments)

            elif name == "add_ticket_message":
                result = await client.add_ticket_message(**arguments)

            elif name == "update_ticket":
                ticket_id = arguments.pop("ticket_id", None)
                ref = arguments.pop("ref", None)
                result = await client.update_ticket(ticket_id=ticket_id, ref=ref, **arguments)

            # Raw API Access
            elif name == "dolibarr_raw_api":
                result = await client.dolibarr_raw_api(**arguments)

            else:
                result = {"error": f"Unknown tool: {name}"}

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    except DolibarrAPIError as e:
        error_payload = e.response_data or {
            "error": "Dolibarr API Error",
            "status": e.status_code or 500,
            "message": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        return _error_result(error_payload)

    except Exception as e:
        correlation_id = str(uuid.uuid4())
        error_result = {
            "error": "Internal Server Error",
            "status": 500,
            "message": f"Tool execution failed: {str(e)}",
            "correlation_id": correlation_id,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        print(f"🔥 Tool execution error ({correlation_id}): {e}", file=sys.stderr)  # Debug logging
        return _error_result(error_result)


@asynccontextmanager
async def test_api_connection(config: Config | None = None):
    """Test API connection and yield client if successful."""
    created_config = False
    api_ok = False
    try:
        if config is None:
            config = Config()
            created_config = True

        # Check if environment variables are set
        if not config.dolibarr_url or config.dolibarr_url == "https://your-dolibarr-instance.com/api/index.php":
            print("⚠️  Warning: DOLIBARR_URL not configured in .env file", file=sys.stderr)
            print("⚠️  Using placeholder URL - API calls will fail", file=sys.stderr)
            print("📝 Please configure your .env file with valid Dolibarr credentials", file=sys.stderr)
            yield False  # Configuration incomplete
            return

        if not config.api_key or config.api_key == "your_dolibarr_api_key_here":
            print("⚠️  Warning: DOLIBARR_API_KEY not configured in .env file", file=sys.stderr)
            print("⚠️  API authentication will fail", file=sys.stderr)
            print("📝 Please configure your .env file with valid Dolibarr credentials", file=sys.stderr)
            yield False  # Configuration incomplete
            return

        async with DolibarrClient(config) as client:
            print("🧪 Testing Dolibarr API connection...", file=sys.stderr)
            result = await client.get_status()
            if "success" in result or "dolibarr_version" in str(result):
                print("✅ Dolibarr API connection successful", file=sys.stderr)
                print("🎯 Full CRUD operations available for all Dolibarr modules", file=sys.stderr)
                api_ok = True
            else:
                print(f"⚠️  API test returned unexpected result: {result}", file=sys.stderr)
                print("⚠️  Server will start but API calls may fail", file=sys.stderr)
                api_ok = False
    except Exception as e:
        print(f"⚠️  API test error: {e}", file=sys.stderr)
        if config is None or created_config:
            print("💡 Check your .env file configuration", file=sys.stderr)
        print("⚠️  Server will start but API calls may fail", file=sys.stderr)
        api_ok = False

    yield api_ok


async def _run_stdio_server(_config: Config) -> None:
    """Run the MCP server over STDIO (default)."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="dolibarr-mcp",
                server_version=__version__,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


class BearerAuthMiddleware:
    """Pure ASGI middleware enforcing `Authorization: Bearer <token>`."""

    def __init__(self, app, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return
        provided = b""
        for key, value in scope.get("headers", []):
            if key == b"authorization":
                provided = value
                break
        if not hmac.compare_digest(provided, self.expected):
            response = Response(
                '{"error": "Unauthorized"}',
                status_code=401,
                media_type="application/json",
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _build_http_app(session_manager: StreamableHTTPSessionManager, config: Config | None = None) -> Starlette:
    """Create Starlette app that forwards to the StreamableHTTP session manager."""
    config = config or Config()

    class ASGIEndpoint:
        """Lightweight adapter so Route treats our handler as an ASGI app."""

        def __init__(self, handler):
            self.handler = handler

        async def __call__(self, scope: Scope, receive: Receive, send: Send):
            await self.handler(scope, receive, send)

    @asynccontextmanager
    async def lifespan(app):
        async with session_manager.run():
            yield

    async def asgi_handler(scope, receive, send):
        """Adapter to call the StreamableHTTPSessionManager with ASGI signature."""
        await session_manager.handle_request(scope, receive, send)

    asgi_endpoint = ASGIEndpoint(asgi_handler)

    middleware = []
    origins = config.cors_origins
    if origins:
        # CORS only for explicitly allowed origins (MCP_CORS_ORIGINS).
        middleware.append(
            Middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                allow_headers=["Authorization", "Content-Type", "Accept", "Mcp-Session-Id", "Mcp-Protocol-Version"],
                expose_headers=["Mcp-Session-Id"],
                allow_credentials=False,
            )
        )
    if config.mcp_http_token:
        # Added after CORS so preflight requests are answered before auth.
        middleware.append(Middleware(BearerAuthMiddleware, token=config.mcp_http_token))

    return Starlette(
        routes=[
            Route("/", asgi_endpoint, methods=["GET", "POST", "DELETE"]),
            Route("/{path:path}", asgi_endpoint, methods=["GET", "POST", "DELETE"]),
        ],
        middleware=middleware,
        lifespan=lifespan,
    )


async def _run_http_server(config: Config) -> None:
    """Run the MCP server over HTTP (StreamableHTTP)."""
    if not config.mcp_http_token and not config.http_host_is_loopback:
        raise RuntimeError(
            f"Refusing to listen on {config.mcp_http_host} without authentication: "
            "set MCP_HTTP_TOKEN (or bind MCP_HTTP_HOST to 127.0.0.1)."
        )
    if not config.mcp_http_token:
        print("⚠️  MCP_HTTP_TOKEN not set - HTTP listener is unauthenticated (loopback only)", file=sys.stderr)

    session_manager = StreamableHTTPSessionManager(server, json_response=False, stateless=False)
    app = _build_http_app(session_manager, config)
    print(
        f"🌐 Starting MCP HTTP server on {config.mcp_http_host}:{config.mcp_http_port}",
        file=sys.stderr,
    )
    uvicorn_config = uvicorn.Config(
        app,
        host=config.mcp_http_host,
        port=config.mcp_http_port,
        log_level=config.log_level.lower(),
        loop="asyncio",
        access_log=False,
    )
    uvicorn_server = uvicorn.Server(uvicorn_config)
    await uvicorn_server.serve()


async def main():
    """Run the Dolibarr MCP server."""
    config = Config()

    # Test API connection but don't fail if it's not working
    async with test_api_connection(config) as api_ok:
        if not api_ok:
            print("⚠️  Starting server without valid API connection", file=sys.stderr)
            print("📝 Configure your .env file to enable API functionality", file=sys.stderr)
        else:
            print("✅ API connection validated", file=sys.stderr)

    # Run server regardless of API status
    print("🚀 Starting Professional Dolibarr MCP server...", file=sys.stderr)
    print("✅ Server ready with comprehensive ERP management capabilities", file=sys.stderr)
    print("📝 Tools will attempt to connect when called", file=sys.stderr)

    try:
        if config.mcp_transport == "http":
            await _run_http_server(config)
        else:
            await _run_stdio_server(config)
    except Exception as e:
        print(f"💥 Server error: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Server stopped by user", file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f"❌ Server startup error: {e}", file=sys.stderr)
        sys.exit(1)
