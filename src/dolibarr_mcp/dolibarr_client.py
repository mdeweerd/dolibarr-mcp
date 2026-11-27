"""Professional Dolibarr API client with comprehensive CRUD operations."""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from uuid import uuid4

import aiohttp
from aiohttp import ClientSession, ClientTimeout

from .config import Config


class DolibarrAPIError(Exception):
    """Custom exception for Dolibarr API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None, response_data: Optional[Dict] = None):  # noqa: B042
        self.message = message
        self.status_code = status_code
        self.response_data = response_data
        super().__init__(self.message)


class DolibarrValidationError(DolibarrAPIError):
    """Raised for client-side validation failures before hitting the API."""


class DolibarrClient:
    """Professional Dolibarr API client with comprehensive functionality."""

    def __init__(self, config: Config):
        """Initialize the Dolibarr client."""
        self.config = config
        self.base_url = config.dolibarr_url.rstrip("/")
        self.api_key = config.api_key
        self.session: Optional[ClientSession] = None
        self.logger = logging.getLogger(__name__)
        self.debug_mode = getattr(config, "debug_mode", False)
        self.allow_ref_autogen = getattr(config, "allow_ref_autogen", False)
        self.ref_autogen_prefix = getattr(config, "ref_autogen_prefix", "AUTO")
        self.max_retries = getattr(config, "max_retries", 2)
        self.retry_backoff_seconds = getattr(config, "retry_backoff_seconds", 0.5)

        # Configure timeout
        self.timeout = ClientTimeout(total=30, connect=10)
        self.logger.setLevel(config.log_level)

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close_session()

    async def start_session(self):
        """Start the HTTP session."""
        if not self.session:
            self.session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={"DOLAPIKEY": self.api_key, "Content-Type": "application/json", "Accept": "application/json"},
            )

    async def close_session(self):
        """Close the HTTP session."""
        if self.session:
            await self.session.close()
            self.session = None

    @staticmethod
    def _extract_identifier(response: Any) -> Any:
        """Return the identifier from Dolibarr responses when available."""
        if isinstance(response, dict):
            if "id" in response:
                return response["id"]
            success = response.get("success")
            if isinstance(success, dict) and "id" in success:
                return success["id"]
        return response

    @staticmethod
    def _merge_payload(data: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        """Merge an optional dictionary with keyword overrides."""
        payload: Dict[str, Any] = {}
        if data:
            payload.update(data)
        if kwargs:
            payload.update(kwargs)
        return payload

    async def request(
        self, method: str, endpoint: str, params: Optional[Dict] = None, data: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Public helper retained for compatibility with legacy integrations and tests."""
        return await self._make_request(method, endpoint, params=params, data=data)

    def _build_url(self, endpoint: str) -> str:
        """Build full API URL."""
        endpoint = endpoint.lstrip("/")
        base = self.base_url.rstrip("/")
        return f"{base}/{endpoint}"

    def _mask_api_key(self) -> str:
        """Return a masked representation of the API key for logging."""
        if not self.api_key:
            return "<not-set>"
        if len(self.api_key) <= 6:
            return "*" * len(self.api_key)
        return f"{self.api_key[:2]}***{self.api_key[-2:]}"

    @staticmethod
    def _now_iso() -> str:
        """Return current UTC timestamp in ISO format with Z suffix."""
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _generate_correlation_id() -> str:
        """Create a unique correlation identifier."""
        return str(uuid4())

    def _generate_reference(self) -> str:
        """Generate a unique reference using prefix, timestamp, and a UUID suffix."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        suffix = uuid4().hex[:8]
        return f"{self.ref_autogen_prefix}_{timestamp}_{suffix}"

    def _build_validation_error(
        self,
        endpoint: str,
        missing_fields: Optional[List[str]] = None,
        invalid_fields: Optional[List[Dict[str, str]]] = None,
        message: str = "Validation failed",
        status: int = 400,
    ) -> Dict[str, Any]:
        """Build a structured validation error response."""
        return {
            "error": "Bad Request",
            "status": status,
            "message": message,
            "missing_fields": missing_fields or [],
            "invalid_fields": invalid_fields or [],
            "endpoint": f"/{endpoint.lstrip('/')}",
            "timestamp": self._now_iso(),
        }

    @staticmethod
    def _upstream_message(response_data: Any) -> Optional[str]:
        """Extract Dolibarr's error text (flat or RestException-shaped)."""
        if not isinstance(response_data, dict):
            return None
        error = response_data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str) and error:
            return error
        if response_data.get("message"):
            return str(response_data["message"])
        if response_data.get("raw_response"):
            return str(response_data["raw_response"])[:500]
        return None

    def _build_http_error(
        self,
        endpoint: str,
        status: int,
        reason: Optional[str],
        response_data: Any,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build an error payload that keeps Dolibarr's raw error JSON verbatim.

        Dolibarr answers errors as ``{"error": {"code": ..., "message": ...}, "debug": {...}}``;
        those keys are preserved untouched so the caller sees the server's own
        status/code/message/details. The wrapper adds ``status``, ``message``,
        ``correlation_id``, ``endpoint`` and ``timestamp`` on top.
        """
        payload: Dict[str, Any] = dict(response_data) if isinstance(response_data, dict) else {"raw_response": response_data}
        upstream = self._upstream_message(response_data)
        if not payload.get("message"):
            payload["message"] = upstream or f"HTTP {status}: {reason}"
        error = payload.get("error")
        if isinstance(error, dict) and error.get("code") is not None and "code" not in payload:
            payload["code"] = error["code"]
        payload["status"] = status
        payload["correlation_id"] = correlation_id or self._generate_correlation_id()
        payload["endpoint"] = f"/{endpoint.lstrip('/')}"
        payload["timestamp"] = self._now_iso()
        return payload

    def _build_internal_error(self, endpoint: str, message: str, correlation_id: str) -> Dict[str, Any]:
        """Build a structured internal server error response."""
        return {
            "error": "Internal Server Error",
            "status": 500,
            "message": message,
            "correlation_id": correlation_id,
            "endpoint": f"/{endpoint.lstrip('/')}",
            "timestamp": self._now_iso(),
        }

    def _apply_aliases(self, payload: Dict[str, Any], aliases: Dict[str, List[str]]) -> None:
        """Promote alias fields to canonical names."""
        for target, options in aliases.items():
            if target not in payload:
                for alias in options:
                    if alias in payload and payload[alias] not in (None, ""):
                        payload[target] = payload.pop(alias)
                        break

    def _validate_payload(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        required_fields: List[str],
        aliases: Optional[Dict[str, List[str]]] = None,
        numeric_positive: Optional[List[str]] = None,
        enum_fields: Optional[Dict[str, List[Any]]] = None,
        required_any_of: Optional[List[List[str]]] = None,
        non_empty_fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Validate payload before sending to Dolibarr and optionally auto-generate refs."""
        aliases = aliases or {}
        numeric_positive = numeric_positive or []
        enum_fields = enum_fields or {}
        required_any_of = required_any_of or []
        non_empty_fields = non_empty_fields or []

        self._apply_aliases(payload, aliases)

        missing_fields = [field for field in required_fields if field not in payload or payload[field] in (None, "")]

        invalid_fields: List[Dict[str, str]] = []

        for group in required_any_of:
            if all(payload.get(field) in (None, "") for field in group):
                missing_fields.append(" or ".join(group))

        for field in non_empty_fields:
            if field in payload and payload[field] in (None, "") and field not in missing_fields:
                missing_fields.append(field)

        for field in numeric_positive:
            if field in payload and isinstance(payload[field], (int, float)) and payload[field] < 0:
                invalid_fields.append({"field": field, "message": "must be a positive number"})

        for field, values in enum_fields.items():
            if field in payload and payload[field] not in values:
                invalid_fields.append({"field": field, "message": f"must be one of {values}"})

        if "ref" in missing_fields and self.allow_ref_autogen:
            payload["ref"] = self._generate_reference()
            missing_fields = [f for f in missing_fields if f != "ref"]

        if missing_fields or invalid_fields:
            details: List[str] = []
            if missing_fields:
                details.append(f"missing: {', '.join(missing_fields)}")
            if invalid_fields:
                details.append("invalid: " + ", ".join(f["field"] for f in invalid_fields))  # noqa: W503
            message = "Validation failed" + (f" ({'; '.join(details)})" if details else "")
            error_data = self._build_validation_error(
                endpoint=endpoint,
                missing_fields=missing_fields,
                invalid_fields=invalid_fields,
                message=message,
            )
            raise DolibarrValidationError(
                message=error_data["message"],
                status_code=error_data["status"],
                response_data=error_data,
            )

        return payload

    async def _make_request(
        self, method: str, endpoint: str, params: Optional[Dict] = None, data: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make HTTP request to Dolibarr API."""
        if not self.session:
            await self.start_session()

        url = self._build_url(endpoint)

        last_exception: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                if self.debug_mode:
                    self.logger.debug(
                        "Making %s request to %s with params=%s payload_keys=%s api_key=%s",
                        method,
                        url,
                        params or {},
                        list((data or {}).keys()),
                        self._mask_api_key(),
                    )

                kwargs = {
                    "params": params or {},
                }

                if data and method.upper() in ["POST", "PUT"]:
                    kwargs["json"] = data

                async with self.session.request(method, url, **kwargs) as response:  # type: ignore[union-attr, arg-type]
                    response_text = await response.text()

                    # Log response for debugging without leaking secrets
                    if self.debug_mode:
                        self.logger.debug("Response status: %s", response.status)
                        self.logger.debug("Response body (truncated): %s", response_text[:500])

                    # Try to parse JSON response
                    try:
                        response_data = json.loads(response_text) if response_text else {}
                    except json.JSONDecodeError:
                        response_data = {"raw_response": response_text}

                    # Handle error responses: for every HTTP status >= 400, keep
                    # Dolibarr's raw error JSON (status, code, message, details)
                    # verbatim and add correlation_id/endpoint on top, so the
                    # caller always sees the server's own cause.
                    if response.status >= 400:
                        correlation_id = self._generate_correlation_id()
                        error_data = self._build_http_error(
                            endpoint=endpoint,
                            status=response.status,
                            reason=response.reason,
                            response_data=response_data,
                            correlation_id=correlation_id,
                        )

                        if response.status == 400:
                            # Keep the normalized wrapper fields when Dolibarr sent
                            # them, plus the legacy "ref" heuristic.
                            missing: List[str] = []
                            invalid: List[Dict[str, str]] = []
                            if isinstance(response_data, dict):
                                if "missing_fields" in response_data:
                                    missing = response_data.get("missing_fields") or []
                                if "invalid_fields" in response_data:
                                    invalid = response_data.get("invalid_fields") or []
                                if not missing and "ref" in str(error_data.get("message") or "").lower():
                                    missing.append("ref")
                            error_data["missing_fields"] = missing
                            error_data["invalid_fields"] = invalid
                            self.logger.error(
                                "Bad request for %s (correlation_id=%s): %s",
                                endpoint,
                                correlation_id,
                                response_text[:500],
                            )
                            raise DolibarrValidationError(
                                message=error_data["message"],
                                status_code=response.status,
                                response_data=error_data,
                            )

                        if response.status >= 500:
                            self.logger.error(
                                "Server error %s for %s (correlation_id=%s): %s",
                                response.status,
                                endpoint,
                                correlation_id,
                                response_text[:500],
                            )

                        raise DolibarrAPIError(
                            message=error_data["message"],
                            status_code=response.status,
                            response_data=error_data,
                        )

                    return response_data

            except aiohttp.ClientError as e:
                last_exception = e
                if endpoint == "status" and not url.endswith("/api/status"):
                    try:
                        alt_url = f"{self.base_url}/setup/modules"
                        self.logger.debug("Status failed, trying alternative: %s", alt_url)

                        async with self.session.get(alt_url) as response:  # type: ignore[union-attr]
                            if response.status == 200:
                                return {"success": 1, "dolibarr_version": "API Available", "api_version": "1.0"}
                    except Exception as alt_exc:  # pylint: disable=broad-except
                        last_exception = alt_exc

                if (
                    attempt < self.max_retries
                    and isinstance(e, aiohttp.ClientResponseError)  # noqa: W503
                    and getattr(e, "status", None) in {502, 503, 504}  # noqa: W503
                ):
                    backoff = self.retry_backoff_seconds * (2**attempt)
                    await asyncio.sleep(backoff)
                    continue
                break
            except DolibarrAPIError:
                raise
            except Exception as e:  # pylint: disable=broad-except
                last_exception = e
                break

        if isinstance(last_exception, DolibarrAPIError):
            raise last_exception

        if isinstance(last_exception, Exception):
            correlation_id = self._generate_correlation_id()
            internal_error = self._build_internal_error(
                endpoint=endpoint,
                message=str(last_exception),
                correlation_id=correlation_id,
            )
            self.logger.error(
                "Unexpected error during %s %s (correlation_id=%s): %s",
                method,
                endpoint,
                correlation_id,
                last_exception,
            )
            raise DolibarrAPIError(
                message=internal_error["message"],
                status_code=500,
                response_data=internal_error,
            ) from last_exception

        raise DolibarrAPIError(f"HTTP client error: {endpoint}")

    # ============================================================================
    # SYSTEM ENDPOINTS
    # ============================================================================

    async def test_connection(self) -> Dict[str, Any]:
        """Compatibility helper that proxies to get_status."""
        return await self.get_status()

    async def get_status(self) -> Dict[str, Any]:
        """Get API status and version information."""
        try:
            # First try the standard status endpoint
            return await self.request("GET", "status")
        except DolibarrAPIError:
            # If status fails, try to get module list as a connectivity test
            try:
                result = await self.request("GET", "setup/modules")
                if result:
                    return {
                        "success": 1,
                        "dolibarr_version": "Connected",
                        "api_version": "1.0",
                        "modules_available": isinstance(result, (list, dict)),
                    }
            except Exception:
                pass

            # If all else fails, try a simple user list
            try:
                result = await self.request("GET", "users?limit=1")
                if result is not None:
                    return {"success": 1, "dolibarr_version": "API Working", "api_version": "1.0"}
            except Exception as exc:
                raise DolibarrAPIError("Cannot connect to Dolibarr API. Please check your configuration.") from exc

    # ============================================================================
    # SETUP DICTIONARIES
    # ============================================================================

    #: Dictionaries exposed by ``GET /setup/dictionary/{name}`` whose codes
    #: (e.g. ``VIR``, ``AC_TEL``) write operations must resolve to ids.
    KNOWN_DICTIONARIES = ("payment_types", "event_types")

    async def get_dictionary(self, name: str) -> List[Dict[str, Any]]:
        """Get a setup dictionary such as ``payment_types`` or ``event_types``.

        Returns one row per entry with its instance-specific numeric ``id``,
        its stable ``code`` and its ``label``. The ids are assigned by the
        instance, not by Dolibarr defaults, so write tools must resolve codes
        through this dictionary instead of hardcoding ids.
        """
        if not name or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            error_data = self._build_validation_error(
                endpoint=f"setup/dictionary/{name}",
                invalid_fields=[{"field": "name", "message": f"invalid dictionary name '{name}'"}],
                message=f"Invalid dictionary name '{name}'",
            )
            raise DolibarrValidationError(
                message=error_data["message"],
                status_code=error_data["status"],
                response_data=error_data,
            )
        try:
            result = await self.request("GET", f"setup/dictionary/{quote(name)}")
        except DolibarrAPIError as exc:
            if exc.status_code == 404:
                error_data = self._build_validation_error(
                    endpoint=f"setup/dictionary/{name}",
                    invalid_fields=[{"field": "name", "message": f"unknown dictionary '{name}'"}],
                    message=f"Unknown dictionary '{name}'. Known dictionaries: {', '.join(self.KNOWN_DICTIONARIES)}",
                    status=404,
                )
                raise DolibarrValidationError(
                    message=error_data["message"],
                    status_code=404,
                    response_data=error_data,
                ) from exc
            raise
        rows: List[Dict[str, Any]] = result if isinstance(result, list) else []
        return [self._normalize_dictionary_row(row) for row in rows if isinstance(row, dict)]

    @staticmethod
    def _normalize_dictionary_row(row: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize a dictionary row so ``id`` is numeric (Dolibarr sends it as a string)."""
        normalized = dict(row)
        raw_id = row.get("id")
        if raw_id is not None:
            try:
                normalized["id"] = int(raw_id)
            except (TypeError, ValueError):
                pass
        return normalized

    # ============================================================================
    # USER MANAGEMENT
    # ============================================================================

    async def _get_list(self, endpoint: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """GET a list endpoint; Dolibarr answers 404 when the (filtered) list is empty."""
        try:
            result = await self.request("GET", endpoint, params=params)
        except DolibarrAPIError as exc:
            if exc.status_code == 404:
                return []
            raise
        return result if isinstance(result, list) else []

    @staticmethod
    def _apply_page(params: Dict[str, Any], page: Optional[int]) -> None:
        """Translate a 1-based tool page into Dolibarr's 0-based ``page`` parameter.

        Dolibarr list endpoints number pages from 0: sending ``page=1`` returns
        the second page, so a first-page request must send ``page=0`` (or omit it).
        """
        if page and page > 1:
            params["page"] = page - 1

    async def get_users(self, limit: int = 100, page: int = 1) -> List[Dict[str, Any]]:
        """Get list of users (``page`` starts at 1)."""
        params: Dict[str, Any] = {"limit": limit}
        self._apply_page(params, page)

        result = await self.request("GET", "users", params=params)
        return result if isinstance(result, list) else []

    async def get_user_by_id(self, user_id: int) -> Dict[str, Any]:
        """Get specific user by ID."""
        return await self.request("GET", f"users/{user_id}")

    async def create_user(
        self,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new user."""
        payload = self._merge_payload(data, **kwargs)
        result = await self.request("POST", "users", data=payload)
        return self._extract_identifier(result)

    async def update_user(
        self,
        user_id: int,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Update an existing user."""
        payload = self._merge_payload(data, **kwargs)
        return await self.request("PUT", f"users/{user_id}", data=payload)

    async def delete_user(self, user_id: int) -> Dict[str, Any]:
        """Delete a user."""
        return await self.request("DELETE", f"users/{user_id}")

    # ============================================================================
    # CUSTOMER/THIRD PARTY MANAGEMENT
    # ============================================================================

    async def search_customers(self, sqlfilters: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search customers using SQL filters."""
        params = {"limit": limit, "sqlfilters": sqlfilters}
        result = await self.request("GET", "thirdparties", params=params)
        return result if isinstance(result, list) else []

    async def search_thirdparties(self, sqlfilters: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search third parties (customers, suppliers, or both) using SQL filters."""
        params = {"limit": limit, "sqlfilters": sqlfilters}
        result = await self.request("GET", "thirdparties", params=params)
        return result if isinstance(result, list) else []

    async def get_customers(self, limit: int = 100, page: int = 1) -> List[Dict[str, Any]]:
        """Get list of customers/third parties (``page`` starts at 1)."""
        params: Dict[str, Any] = {"limit": limit}
        self._apply_page(params, page)

        result = await self.request("GET", "thirdparties", params=params)
        return result if isinstance(result, list) else []

    async def get_customer_by_id(self, customer_id: int) -> Dict[str, Any]:
        """Get specific customer by ID."""
        return await self.request("GET", f"thirdparties/{customer_id}")

    async def create_customer(
        self,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new customer/third party."""
        payload = self._merge_payload(data, **kwargs)

        type_value = payload.pop("type", None)
        if type_value is not None:
            payload.setdefault("client", 1 if type_value in (1, 3) else 0)
            payload.setdefault("fournisseur", 1 if type_value in (2, 3) else 0)
        else:
            payload.setdefault("client", 1)

        payload.setdefault("status", payload.get("status", 1))
        payload.setdefault("country_id", payload.get("country_id", 1))

        # With numbering modules (e.g. "elephant": CU2026-0001 / SU2026-0001)
        # Dolibarr requires the customer/supplier code and only generates it
        # when asked with "-1"; without it the creation fails with a 500.
        if payload.get("client") in (1, 3, "1", "3"):
            payload.setdefault("code_client", "-1")
        if payload.get("fournisseur") in (1, "1"):
            payload.setdefault("code_fournisseur", "-1")

        result = await self.request("POST", "thirdparties", data=payload)
        return self._extract_identifier(result)

    async def update_customer(
        self,
        customer_id: int,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Update an existing customer."""
        payload = self._merge_payload(data, **kwargs)

        type_value = payload.pop("type", None)
        if type_value is not None:
            payload["client"] = 1 if type_value in (1, 3) else 0
            payload["fournisseur"] = 1 if type_value in (2, 3) else 0

        return await self.request("PUT", f"thirdparties/{customer_id}", data=payload)

    async def delete_customer(self, customer_id: int) -> Dict[str, Any]:
        """Delete a customer."""
        return await self.request("DELETE", f"thirdparties/{customer_id}")

    # ============================================================================
    # PRODUCT MANAGEMENT
    # ============================================================================

    async def search_products(self, sqlfilters: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search products using SQL filters."""
        params = {"limit": limit, "sqlfilters": sqlfilters}
        result = await self.request("GET", "products", params=params)
        return result if isinstance(result, list) else []

    async def get_products(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get list of products."""
        params = {"limit": limit}
        result = await self.request("GET", "products", params=params)
        return result if isinstance(result, list) else []

    async def get_product_by_id(self, product_id: int) -> Dict[str, Any]:
        """Get specific product by ID."""
        return await self.request("GET", f"products/{product_id}")

    async def create_product(
        self,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new product or service."""
        payload = self._merge_payload(data, **kwargs)
        payload = self._validate_payload(
            endpoint="products",
            payload=payload,
            required_fields=["ref", "label", "type"],
            aliases={"label": ["name"]},
            numeric_positive=["price", "price_ttc"],
            enum_fields={"type": ["product", "service", 0, 1]},
            required_any_of=[["price", "price_ttc"]],
            non_empty_fields=["price", "price_ttc", "tva_tx"],
        )
        result = await self.request("POST", "products", data=payload)
        return self._extract_identifier(result)

    async def update_product(
        self,
        product_id: int,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Update an existing product."""
        payload = self._merge_payload(data, **kwargs)
        return await self.request("PUT", f"products/{product_id}", data=payload)

    async def delete_product(self, product_id: int) -> Dict[str, Any]:
        """Delete a product."""
        return await self.request("DELETE", f"products/{product_id}")

    # ============================================================================
    # INVOICE MANAGEMENT
    # ============================================================================

    async def get_invoices(
        self,
        limit: int = 100,
        status: Optional[str] = None,
        customer_id: Optional[int] = None,
        page: int = 0,
        sortfield: Optional[str] = None,
        sortorder: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get list of invoices.

        ``page`` is zero-based, as in the Dolibarr REST API. ``customer_id``
        maps to Dolibarr's ``thirdparty_ids`` filter.
        """
        params: Dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        if customer_id is not None:
            params["thirdparty_ids"] = str(customer_id)
        if page:
            params["page"] = page
        if sortfield:
            params["sortfield"] = sortfield
        if sortorder:
            params["sortorder"] = sortorder

        try:
            result = await self.request("GET", "invoices", params=params)
        except DolibarrAPIError as exc:
            # Dolibarr answers 404 when the filtered list is empty.
            if exc.status_code == 404:
                return []
            raise
        return result if isinstance(result, list) else []

    async def get_invoice_by_id(self, invoice_id: int) -> Dict[str, Any]:
        """Get specific invoice by ID including invoice lines.

        Note: Dolibarr API separates invoice header and lines into different endpoints.
        This method combines both to provide complete invoice data including lines.
        """
        invoice = await self.request("GET", f"invoices/{invoice_id}")

        # Fetch invoice lines separately (Dolibarr API design)
        try:
            lines = await self.request("GET", f"invoices/{invoice_id}/lines")
            invoice["lines"] = lines if isinstance(lines, list) else []
        except DolibarrAPIError as e:
            # If lines endpoint fails, return invoice without lines
            # This ensures backward compatibility with older Dolibarr versions
            self.logger.warning("Failed to fetch lines for invoice %s: %s", invoice_id, str(e))
            invoice["lines"] = []

        return invoice

    @staticmethod
    def _map_line_vat(line: Dict[str, Any]) -> None:
        """Map the tool-level ``vat`` field to Dolibarr's ``tva_tx``.

        Dolibarr ignores an unknown ``vat`` key and silently books the line
        at 0% VAT, so the rate must travel as ``tva_tx``.
        """
        if "vat" in line:
            vat = line.pop("vat")
            line.setdefault("tva_tx", vat)

    async def create_invoice(
        self,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new invoice."""
        payload = self._merge_payload(data, **kwargs)

        # Fix: Map customer_id to socid
        if "customer_id" in payload and "socid" not in payload:
            payload["socid"] = payload.pop("customer_id")

        # Fix: Map product_id to fk_product in lines
        if "lines" in payload and isinstance(payload["lines"], list):
            for line in payload["lines"]:
                if "product_id" in line:
                    line["fk_product"] = line.pop("product_id")
                self._map_line_vat(line)
                # Ensure product_type is passed if present (0=Product, 1=Service)
                if "product_type" in line:
                    line["product_type"] = line["product_type"]

        payload = self._validate_payload(
            endpoint="invoices",
            payload=payload,
            required_fields=["socid"],
        )

        result = await self.request("POST", "invoices", data=payload)
        return self._extract_identifier(result)

    async def update_invoice(
        self,
        invoice_id: int,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Update an existing invoice."""
        payload = self._merge_payload(data, **kwargs)
        return await self.request("PUT", f"invoices/{invoice_id}", data=payload)

    async def delete_invoice(self, invoice_id: int) -> Dict[str, Any]:
        """Delete an invoice."""
        return await self.request("DELETE", f"invoices/{invoice_id}")

    async def add_invoice_line(
        self,
        invoice_id: int,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Add a line to an invoice."""
        payload = self._merge_payload(data, **kwargs)

        # Map product_id to fk_product if present
        if "product_id" in payload:
            payload["fk_product"] = payload.pop("product_id")
        self._map_line_vat(payload)

        existing_ids = await self._invoice_line_ids(invoice_id)
        try:
            return await self.request("POST", f"invoices/{invoice_id}/lines", data=payload)
        except DolibarrAPIError as exc:
            if not exc.status_code or exc.status_code < 500 or existing_ids is None:
                raise
            # Dolibarr sometimes answers 5xx although the line was inserted.
            # Report the created line instead of an error so callers don't
            # retry and duplicate it.
            created = (await self._invoice_line_ids(invoice_id) or set()) - existing_ids
            if len(created) != 1:
                raise
            line_id = created.pop()
            self.logger.warning(
                "Dolibarr returned HTTP %s for invoices/%s/lines but line %s was created",
                exc.status_code,
                invoice_id,
                line_id,
            )
            return line_id

    async def _invoice_line_ids(self, invoice_id: int) -> Optional[set]:
        """Return the ids of an invoice's lines, or None if they can't be read."""
        try:
            lines = await self.request("GET", f"invoices/{invoice_id}/lines")
        except DolibarrAPIError:
            return None
        if not isinstance(lines, list):
            return None
        return {int(line.get("id") or line.get("rowid")) for line in lines}

    async def update_invoice_line(
        self,
        invoice_id: int,
        line_id: int,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Update a line in an invoice.

        Dolibarr's PUT on an invoice line resets every field that is not sent
        (qty, price, description... fall back to 0/empty), so the current line
        is read first and the requested changes are merged into it.
        """
        changes = self._merge_payload(data, **kwargs)
        self._map_line_vat(changes)

        lines = await self.request("GET", f"invoices/{invoice_id}/lines")
        lines_list: List[Dict[str, Any]] = lines if isinstance(lines, list) else []
        current = next(
            (line for line in lines_list if str(line.get("id") or line.get("rowid")) == str(line_id)),
            None,
        )
        if current is None:
            raise DolibarrAPIError(
                message=f"Line {line_id} not found on invoice {invoice_id}",
                status_code=404,
            )

        payload = {field: current.get(field) for field in self._INVOICE_LINE_FIELDS if current.get(field) not in (None, "")}
        if "desc" not in payload and current.get("description"):
            payload["desc"] = current["description"]
        payload.update(changes)
        return await self.request("PUT", f"invoices/{invoice_id}/lines/{line_id}", data=payload)

    # Line fields Dolibarr rewrites on PUT invoices/{id}/lines/{lineid}.
    _INVOICE_LINE_FIELDS = (
        "desc",
        "subprice",
        "qty",
        "tva_tx",
        "localtax1_tx",
        "localtax2_tx",
        "fk_product",
        "product_type",
        "remise_percent",
        "info_bits",
        "special_code",
        "fk_parent_line",
        "label",
        "fk_unit",
        "pa_ht",
        "fk_fournprice",
        "date_start",
        "date_end",
        "situation_percent",
        "multicurrency_subprice",
        "vat_src_code",
        "rang",
        "array_options",
    )

    async def delete_invoice_line(self, invoice_id: int, line_id: int) -> Dict[str, Any]:
        """Delete a line from an invoice."""
        return await self.request("DELETE", f"invoices/{invoice_id}/lines/{line_id}")

    async def validate_invoice(self, invoice_id: int, warehouse_id: int = 0, not_trigger: int = 0) -> Dict[str, Any]:
        """Validate an invoice."""
        payload = {"idwarehouse": warehouse_id, "not_trigger": not_trigger}
        return await self.request("POST", f"invoices/{invoice_id}/validate", data=payload)

    # ============================================================================
    # SUPPLIER INVOICE MANAGEMENT
    # ============================================================================

    # Line fields the supplier-invoice API honors. ``desc``/``subprice`` are
    # silently ignored there (line created with empty description, price 0).
    _SUPPLIER_LINE_FIELDS = (
        "description",
        "pu_ht",
        "qty",
        "tva_tx",
        "localtax1_tx",
        "localtax2_tx",
        "fk_product",
        "product_type",
    )
    _SUPPLIER_LINE_FORBIDDEN = ("desc", "subprice")

    @staticmethod
    def _date_to_epoch(value: Any) -> Any:
        """Convert a YYYY-MM-DD date to a local-midnight timestamp (Dolibarr stores dates that way)."""
        if value in (None, "") or isinstance(value, (int, float)):
            return value
        return int(datetime.strptime(str(value), "%Y-%m-%d").timestamp())

    @staticmethod
    def _date_to_epoch_utc(value: Any) -> Any:
        """Convert a YYYY-MM-DD date to a midnight-UTC timestamp.

        Instance convention for date columns (llx_bank.datev included): a
        local-midnight timestamp is truncated to the previous day by the
        server's date column, so date strings travel as midnight UTC.
        """
        if value in (None, "") or isinstance(value, (int, float)):
            return value
        return int(datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())

    @staticmethod
    def _to_date_str(value: Any) -> str:
        """Normalize a filter bound to a YYYY-MM-DD string (llx_bank date columns are DATE type).

        Timestamps are converted as UTC dates. Anything else must already be
        a YYYY-MM-DD string, otherwise the value is rejected (it would be
        interpolated into a sqlfilters string).
        """
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(int(value), timezone.utc).date().isoformat()
        text = str(value)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            raise DolibarrValidationError(f"Invalid date {value!r}: expected YYYY-MM-DD or a Unix timestamp", status_code=400)
        return text

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        """Best-effort int conversion (Dolibarr sends ids/flags as strings)."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _check_supplier_line_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Reject fields Dolibarr would silently drop and normalize the rest."""
        forbidden = [field for field in self._SUPPLIER_LINE_FORBIDDEN if field in payload]
        if forbidden:
            raise DolibarrValidationError(
                f"Fields {forbidden} are not supported on supplier invoice lines: Dolibarr silently ignores "
                "them (empty description, price 0). Use 'description' and 'pu_ht' instead.",
                status_code=400,
            )
        if "product_id" in payload:
            payload["fk_product"] = payload.pop("product_id")
        payload.setdefault("localtax1_tx", 0)
        payload.setdefault("localtax2_tx", 0)
        return payload

    async def get_supplier_invoices(
        self,
        limit: int = 100,
        sqlfilters: Optional[str] = None,
        page: int = 0,
        sortfield: Optional[str] = None,
        sortorder: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List supplier invoices.

        ``sqlfilters`` must be a single ``t.``-prefixed condition (e.g.
        ``(t.fk_soc:=:3)``); combined conditions return 0 rows in Dolibarr.
        ``page`` is zero-based, as in the Dolibarr REST API.
        """
        params: Dict[str, Any] = {"limit": limit}
        if sqlfilters:
            params["sqlfilters"] = sqlfilters
        if page:
            params["page"] = page
        if sortfield:
            params["sortfield"] = sortfield
        if sortorder:
            params["sortorder"] = sortorder

        try:
            result = await self.request("GET", "supplierinvoices", params=params)
        except DolibarrAPIError as exc:
            # Dolibarr answers 404 when the filtered list is empty.
            if exc.status_code == 404:
                return []
            raise
        return result if isinstance(result, list) else []

    async def create_supplier_invoice(
        self,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a supplier invoice draft, then add the optional ``lines`` one by one.

        ``type=2`` creates a credit note. Returns the new invoice id.
        """
        payload = self._merge_payload(data, **kwargs)
        if "supplier_id" in payload and "socid" not in payload:
            payload["socid"] = payload.pop("supplier_id")
        if not payload.get("socid"):
            raise DolibarrValidationError("socid (supplier_id) is required", status_code=400)
        for field in ("date", "datef", "date_lim_reglement"):
            if field in payload:
                payload[field] = self._date_to_epoch(payload[field])
        if "date" in payload:
            payload.setdefault("datef", payload["date"])
        if "due_date" in payload:
            payload["date_lim_reglement"] = self._date_to_epoch(payload.pop("due_date"))

        lines = payload.pop("lines", None) or []
        result = await self.request("POST", "supplierinvoices", data=payload)
        invoice_id = self._extract_identifier(result)
        for line in lines:
            await self.add_supplier_invoice_line(invoice_id, **line)
        return invoice_id

    async def add_supplier_invoice_line(
        self,
        invoice_id: int,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Add a line to a draft supplier invoice.

        Raises an explicit error when the invoice is not a draft: Dolibarr
        answers a silent no-op (response 0) when adding a line to a validated
        invoice, which must not pass unnoticed.
        """
        payload = self._check_supplier_line_payload(self._merge_payload(data, **kwargs))

        invoice = await self.request("GET", f"supplierinvoices/{invoice_id}")
        status = invoice.get("status", invoice.get("statut")) if isinstance(invoice, dict) else None
        if str(status) != "0":
            raise DolibarrAPIError(
                message=(
                    f"Cannot add a line to supplier invoice {invoice_id}: it is not a draft "
                    f"(status={status}). Use set_supplier_invoice_to_draft first."
                ),
                status_code=409,
            )

        result = await self.request("POST", f"supplierinvoices/{invoice_id}/lines", data=payload)
        if not result:
            raise DolibarrAPIError(
                message=f"Dolibarr did not create the line on supplier invoice {invoice_id} (empty response)",
                status_code=409,
            )
        return result

    async def update_supplier_invoice_line(
        self,
        invoice_id: int,
        line_id: int,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Update a line of a supplier invoice.

        Dolibarr's PUT on a supplier invoice line resets every field that is
        not sent, so the current line is read first and changes are merged in.
        """
        changes = self._check_supplier_line_payload(self._merge_payload(data, **kwargs))

        lines = await self.request("GET", f"supplierinvoices/{invoice_id}/lines")
        lines_list: List[Dict[str, Any]] = lines if isinstance(lines, list) else []
        current = next(
            (line for line in lines_list if str(line.get("id") or line.get("rowid")) == str(line_id)),
            None,
        )
        if current is None:
            raise DolibarrAPIError(
                message=f"Line {line_id} not found on supplier invoice {invoice_id}",
                status_code=404,
            )

        payload = {field: current.get(field) for field in self._SUPPLIER_LINE_FIELDS if current.get(field) not in (None, "")}
        payload.update(changes)
        return await self.request("PUT", f"supplierinvoices/{invoice_id}/lines/{line_id}", data=payload)

    async def validate_supplier_invoice(self, invoice_id: int, warehouse_id: int = 0, not_trigger: int = 0) -> Dict[str, Any]:
        """Validate a draft supplier invoice; returns the final ref (``SI...`` / ``SA...``)."""
        payload = {"idwarehouse": warehouse_id, "not_trigger": not_trigger}
        return await self.request("POST", f"supplierinvoices/{invoice_id}/validate", data=payload)

    async def set_supplier_invoice_to_draft(self, invoice_id: int) -> Dict[str, Any]:
        """Set a validated supplier invoice back to draft (ref and payments kept).

        Dolibarr >= 22 exposes ``POST {id}/settodraft``; older versions answer
        404 there, so fall back to ``PUT status=0`` (same effect, ref kept).
        """
        try:
            return await self.request("POST", f"supplierinvoices/{invoice_id}/settodraft", data={})
        except DolibarrAPIError as exc:
            if exc.status_code != 404:
                raise
            return await self.request("PUT", f"supplierinvoices/{invoice_id}", data={"status": 0})

    async def set_supplier_invoice_paid(self, invoice_id: int) -> Dict[str, Any]:
        """Mark a validated supplier invoice as paid.

        Dolibarr >= 22 exposes ``POST {id}/settopaid``; older versions answer
        404 there, so fall back to ``PUT status=2, paye=1``.
        """
        try:
            return await self.request("POST", f"supplierinvoices/{invoice_id}/settopaid", data={})
        except DolibarrAPIError as exc:
            if exc.status_code != 404:
                raise
            return await self.request("PUT", f"supplierinvoices/{invoice_id}", data={"status": 2, "paye": 1})

    async def update_supplier_invoice(
        self,
        invoice_id: int,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Update a supplier invoice (label, ref_supplier; idempotent on validated/paid invoices)."""
        payload = self._merge_payload(data, **kwargs)
        return await self.request("PUT", f"supplierinvoices/{invoice_id}", data=payload)

    # ============================================================================
    # SUPPLIER RECURRING INVOICE TEMPLATES (llx_facture_fourn_rec / _det_rec)
    # ============================================================================

    #: Would-be REST route, mirroring the customer templates at /invoices/templates.
    #: Stock Dolibarr (<= 24.0.4 and develop) ships no route for supplier recurring
    #: invoice templates, so these calls degrade with an actionable error until a
    #: release exposes them; no DB access is added for this.
    _SUPPLIER_TEMPLATES_ENDPOINT = "supplierinvoices/templates"

    @staticmethod
    def _is_supplier_templates_absent(exc: DolibarrAPIError) -> bool:
        """True when the answer means the templates route does not exist on this instance.

        Stock Dolibarr answers 501 "API not found" for unknown API classes, 404 for
        unknown routes — but ``supplierinvoices/templates`` falls through to the
        ``supplierinvoices/{id}`` route and answers 400 "invalid value specified for
        `id`", which counts as absent too.
        """
        if exc.status_code in (404, 501):
            return True
        return exc.status_code == 400 and "invalid value specified for `id`" in (exc.message or "")

    async def _request_supplier_templates(self, method: str, path: str = "", **kwargs) -> Any:
        """Call the templates endpoint, degrading with an actionable error when absent."""
        endpoint = self._SUPPLIER_TEMPLATES_ENDPOINT if not path else f"{self._SUPPLIER_TEMPLATES_ENDPOINT}/{path.lstrip('/')}"
        try:
            return await self.request(method, endpoint, **kwargs)
        except DolibarrAPIError as exc:
            if self._is_supplier_templates_absent(exc):
                raise DolibarrAPIError(
                    message=(
                        f"This Dolibarr instance exposes no REST endpoint for supplier recurring invoice "
                        f"templates ({endpoint} answered HTTP {exc.status_code}: {exc.message}). Manage them "
                        "in the Dolibarr UI (fourn/facture/card-rec.php) or upgrade to a Dolibarr release "
                        "that ships the endpoint; these tools start working automatically once it exists."
                    ),
                    status_code=exc.status_code,
                    response_data=exc.response_data,
                ) from exc
            raise

    async def get_supplier_invoice_templates(
        self,
        limit: int = 100,
        supplier_id: Optional[int] = None,
        page: int = 0,
    ) -> List[Dict[str, Any]]:
        """List supplier recurring invoice templates, optionally of one supplier (fk_soc)."""
        params: Dict[str, Any] = {"limit": limit}
        if supplier_id is not None:
            params["sqlfilters"] = f"(t.fk_soc:=:{int(supplier_id)})"
        if page:
            params["page"] = page
        result = await self._request_supplier_templates("GET", params=params)
        return result if isinstance(result, list) else []

    async def get_supplier_invoice_template(self, template_id: int) -> Dict[str, Any]:
        """Get one supplier recurring invoice template with its lines attached."""
        template = await self._request_supplier_templates("GET", str(template_id))
        lines = await self._request_supplier_templates("GET", f"{template_id}/lines")
        if isinstance(template, dict):
            template["lines"] = lines if isinstance(lines, list) else []
        return template

    async def update_supplier_invoice_template(
        self,
        template_id: int,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Update a supplier recurring invoice template (title/label, ref_supplier)."""
        payload = self._merge_payload(data, **kwargs)
        if "label" in payload:
            payload.setdefault("title", payload.pop("label"))
        return await self._request_supplier_templates("PUT", str(template_id), data=payload)

    async def update_supplier_invoice_template_line(
        self,
        template_id: int,
        line_id: int,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Update a line of a supplier recurring invoice template (pu_ht, qty).

        As on supplier invoice lines, a PUT resets every field not sent, so the
        current line is read first and changes are merged in.
        """
        changes = self._merge_payload(data, **kwargs)
        lines = await self._request_supplier_templates("GET", f"{template_id}/lines")
        lines_list: List[Dict[str, Any]] = lines if isinstance(lines, list) else []
        current = next(
            (line for line in lines_list if str(line.get("id") or line.get("rowid")) == str(line_id)),
            None,
        )
        if current is None:
            raise DolibarrAPIError(
                message=f"Line {line_id} not found on supplier invoice template {template_id}",
                status_code=404,
            )
        payload = {
            field: current.get(field)
            for field in ("description", "pu_ht", "qty", "tva_tx")
            if current.get(field) not in (None, "")
        }
        payload.update(changes)
        return await self._request_supplier_templates("PUT", f"{template_id}/lines/{line_id}", data=payload)

    # ============================================================================
    # PAYMENTS
    # ============================================================================

    async def _resolve_payment_mode_id(self, payment_mode: Any) -> int:
        """Resolve a payment mode code (e.g. ``VIR``) or numeric id to the instance's numeric id.

        Ids are assigned by the instance, so codes are resolved through the
        ``payment_types`` dictionary rather than hardcoded.
        """
        if isinstance(payment_mode, bool):
            raise DolibarrValidationError(f"Invalid payment mode {payment_mode!r}", status_code=400)
        if isinstance(payment_mode, int):
            return payment_mode
        text = str(payment_mode).strip()
        if text.isdigit():
            return int(text)
        for row in await self.get_dictionary("payment_types"):
            if str(row.get("code", "")).upper() == text.upper():
                return int(row["id"])
        raise DolibarrValidationError(
            f"Unknown payment mode '{payment_mode}': pass a numeric payment_mode_id or a code "
            "listed by get_dictionary('payment_types') (e.g. VIR, LIQ, CHQ, CB, PRE).",
            status_code=400,
        )

    def _payment_datepaye(self, payment_date: Any) -> int:
        """Payment date as integer Unix timestamp (the payments endpoints reject strings)."""
        if payment_date in (None, ""):
            return int(datetime.now().timestamp())
        return int(self._date_to_epoch(payment_date))

    @staticmethod
    def _closepaidinvoices_param(closepaidinvoices: bool) -> str:
        """The payments endpoints want the strings 'yes'/'no', never a boolean."""
        return "yes" if closepaidinvoices else "no"

    async def pay_invoice(
        self,
        invoice_id: int,
        payment_mode: Any,
        accountid: Optional[int] = None,
        direction: str = "customer",
        payment_date: Any = None,
        closepaidinvoices: bool = True,
        num_payment: Optional[str] = None,
        comment: Optional[str] = None,
        amount: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record a payment on a CUSTOMER invoice via ``POST /invoices/{id}/payments``.

        The endpoint pays the full remaining amount (no ``amount`` parameter) and
        500s on supplier invoice ids, so the invoice is fetched first: a supplier
        invoice id is rejected with a pointer to :meth:`pay_supplier_invoice`.
        ``direction='supplier'`` delegates to :meth:`pay_supplier_invoice`.
        Returns the new payment id.
        """
        if direction == "supplier":
            if amount is None:
                raise DolibarrValidationError("amount is required when direction='supplier'", status_code=400)
            return await self.pay_supplier_invoice(
                invoice_id,
                amount=amount,
                payment_mode=payment_mode,
                accountid=accountid,
                payment_date=payment_date,
                closepaidinvoices=closepaidinvoices,
                num_payment=num_payment,
                comment=comment,
            )
        if direction != "customer":
            raise DolibarrValidationError("direction must be 'customer' or 'supplier'", status_code=400)
        try:
            await self.request("GET", f"invoices/{invoice_id}")
        except DolibarrAPIError as exc:
            if exc.status_code == 404:
                try:
                    await self.request("GET", f"supplierinvoices/{invoice_id}")
                except DolibarrAPIError as supplier_exc:
                    raise exc from supplier_exc  # not a supplier invoice either: keep the original 404
                raise DolibarrAPIError(
                    message=(
                        f"Invoice {invoice_id} is a supplier invoice: pay_invoice only records customer "
                        "payments. Use pay_supplier_invoice (or direction='supplier') instead."
                    ),
                    status_code=400,
                ) from exc
            raise
        params: Dict[str, Any] = {
            "datepaye": self._payment_datepaye(payment_date),
            "paymentid": await self._resolve_payment_mode_id(payment_mode),
            "closepaidinvoices": self._closepaidinvoices_param(closepaidinvoices),
        }
        if accountid is not None:
            params["accountid"] = int(accountid)
        if num_payment:
            params["num_payment"] = str(num_payment)
        if comment:
            params["comment"] = str(comment)
        return await self.request("POST", f"invoices/{invoice_id}/payments", params=params)

    async def pay_supplier_invoice(
        self,
        invoice_id: int,
        amount: float,
        payment_mode: Any,
        accountid: Optional[int] = None,
        payment_date: Any = None,
        closepaidinvoices: bool = True,
        num_payment: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record a payment on a supplier invoice via ``POST /supplierinvoices/{id}/payments``.

        The endpoint accepts its fields only as QUERY parameters: ``datepaye`` as
        integer Unix timestamp, ``payment_mode_id`` numeric (codes resolved via
        the ``payment_types`` dictionary), ``closepaidinvoices`` as 'yes'/'no',
        plus ``accountid`` and ``amount``. Returns the new payment id.
        """
        params: Dict[str, Any] = {
            "datepaye": self._payment_datepaye(payment_date),
            "payment_mode_id": await self._resolve_payment_mode_id(payment_mode),
            "closepaidinvoices": self._closepaidinvoices_param(closepaidinvoices),
            "amount": amount,
        }
        if accountid is not None:
            params["accountid"] = int(accountid)
        if num_payment:
            params["num_payment"] = str(num_payment)
        if comment:
            params["comment"] = str(comment)
        return await self.request("POST", f"supplierinvoices/{invoice_id}/payments", params=params)

    async def get_invoice_payments(self, invoice_id: int, direction: str = "customer") -> List[Dict[str, Any]]:
        """List the payments recorded on an invoice, customer or supplier.

        Returns one entry per payment with its date, amount, mode code,
        ``num_payment`` and bank account id (resolved from the bank line).
        """
        if direction not in ("customer", "supplier"):
            raise DolibarrValidationError("direction must be 'customer' or 'supplier'", status_code=400)
        endpoint = "invoices" if direction == "customer" else "supplierinvoices"
        try:
            rows = await self.request("GET", f"{endpoint}/{invoice_id}/payments")
        except DolibarrAPIError as exc:
            if exc.status_code == 404:
                return []
            raise
        rows_list: List[Dict[str, Any]] = rows if isinstance(rows, list) else []
        line_to_account = await self._bank_line_accounts(
            {str(row.get("fk_bank_line")) for row in rows_list if row.get("fk_bank_line")}
        )
        return [
            {
                "ref": row.get("ref"),
                "date": row.get("date"),
                "amount": row.get("amount"),
                "mode": row.get("type"),
                "num_payment": row.get("num"),
                "bank_line_id": row.get("fk_bank_line"),
                "bank_account_id": line_to_account.get(str(row.get("fk_bank_line"))),
            }
            for row in rows_list
        ]

    async def _bank_line_accounts(self, line_ids: set) -> Dict[str, Any]:
        """Map bank line ids to their bank account id (best effort, bank module)."""
        remaining = {line_id for line_id in line_ids if line_id}
        resolved: Dict[str, Any] = {}
        if not remaining:
            return resolved
        try:
            accounts = await self.request("GET", "bankaccounts", params={"limit": 100})
        except DolibarrAPIError:
            return resolved
        if not isinstance(accounts, list):
            return resolved
        for account in accounts:
            if not remaining:
                break
            try:
                lines = await self.request("GET", f"bankaccounts/{account.get('id')}/lines", params={"limit": 100})
            except DolibarrAPIError:
                continue
            if not isinstance(lines, list):
                continue
            for line in lines:
                line_id = str(line.get("id"))
                if line_id in remaining:
                    resolved[line_id] = line.get("fk_account") or account.get("id")
                    remaining.discard(line_id)
        return resolved

    # ============================================================================
    # BANK MANAGEMENT (accounts, lines, line links - statement reconciliation)
    # ============================================================================

    async def get_bank_accounts(self, limit: int = 100) -> List[Dict[str, Any]]:
        """List bank accounts (``GET /bankaccounts``)."""
        return await self._get_list("bankaccounts", {"limit": limit})

    @staticmethod
    def _escape_like_value(value: str) -> str:
        """Make a user value safe inside a quoted ``like`` sqlfilters value.

        Same rule as the server's ``_escape_sqlfilter``: a literal apostrophe
        never matches (and would close the quoted string), so it is replaced
        by the LIKE single-character wildcard ``_``.
        """
        for apostrophe in ("'", "’", "‘"):
            value = value.replace(apostrophe, "_")
        return value

    @staticmethod
    def _compact_bank_line(row: Dict[str, Any]) -> Dict[str, Any]:
        """Keep only the reconciliation-relevant fields of a raw bank line (~10 KB -> ~150 B)."""
        return {
            "rowid": int(row.get("id") or row.get("rowid") or 0),
            "datev": row.get("datev"),
            "dateo": row.get("dateo"),
            "label": row.get("label"),
            "amount": float(row.get("amount") or 0),
            "num_releve": row.get("num_releve"),
            "rappro": int(row.get("rappro") or 0),
        }

    async def get_bank_lines(
        self,
        account_id: int,
        from_date: Optional[Any] = None,
        to_date: Optional[Any] = None,
        label_contains: Optional[str] = None,
        num_releve: Optional[str] = None,
        amount: Optional[float] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List bank lines of an account as compact rows (see :meth:`_compact_bank_line`).

        Filtering is done server-side, but this endpoint rejects the ``t.``
        prefix (5xx) and multi-condition ``sqlfilters`` (0 rows observed), so
        exactly ONE condition per call is sent, without prefix, and multiple
        filters are merged locally by rowid. ``limit`` caps the returned rows
        (the endpoint itself ignores the ``limit`` parameter). Date bounds are
        compared on datev as quoted YYYY-MM-DD strings: datev is a SQL DATE
        column, so epoch values never match it.
        """
        endpoint = f"bankaccounts/{account_id}/lines"
        conditions: List[str] = []
        if from_date not in (None, ""):
            conditions.append(f"(datev:>=:'{self._to_date_str(from_date)}')")
        if to_date not in (None, ""):
            conditions.append(f"(datev:<=:'{self._to_date_str(to_date)}')")
        if label_contains:
            conditions.append(f"(label:like:'%{self._escape_like_value(str(label_contains))}%')")
        if num_releve:
            conditions.append(f"(num_releve:like:'%{self._escape_like_value(str(num_releve))}%')")
        if amount is not None:
            conditions.append(f"(amount:=:{amount})")

        if not conditions:
            rows = await self._get_list(endpoint, {})
        else:
            merged: Optional[Dict[str, Dict[str, Any]]] = None
            for condition in conditions:
                rows = await self._get_list(endpoint, {"sqlfilters": condition})
                by_id = {str(row.get("id")): row for row in rows}
                merged = by_id if merged is None else {key: row for key, row in merged.items() if key in by_id}
            rows = list((merged or {}).values())
        return [self._compact_bank_line(row) for row in rows[:limit]]

    async def get_bank_line_links(self, account_id: int, line_ids: List[int]) -> Dict[str, List[Dict[str, Any]]]:
        """Resolve the objects linked to bank lines (``GET /bankaccounts/{id}/lines/{line_id}/links``).

        One call resolves a whole batch of line ids (each link lookup costs
        ~1s server-side). Returns ``{line_id: [links]}``; a line without links
        maps to an empty list.
        """

        async def _links(line_id: int) -> List[Dict[str, Any]]:
            try:
                links = await self.request("GET", f"bankaccounts/{account_id}/lines/{line_id}/links")
            except DolibarrAPIError as exc:
                if exc.status_code == 404:
                    return []
                raise
            return links if isinstance(links, list) else []

        resolved = await asyncio.gather(*[_links(int(line_id)) for line_id in line_ids])
        return {str(line_id): links for line_id, links in zip(line_ids, resolved)}

    async def create_bank_line(
        self,
        account_id: int,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a manual bank line (``POST /bankaccounts/{id}/lines``); returns the new line id.

        ``date``/``datev`` accept YYYY-MM-DD or a Unix timestamp. This is the
        only place ``num_releve`` (bank statement number) can be set through
        the API. ``type`` is a payment mode code (VIR, LIQ, CHQ, CB, PRE...).
        """
        payload = self._merge_payload(data, **kwargs)
        payload = self._validate_payload(
            endpoint=f"bankaccounts/{account_id}/lines",
            payload=payload,
            required_fields=["date", "type", "label", "amount"],
        )
        for field in ("date", "datev"):
            if field in payload:
                payload[field] = self._date_to_epoch_utc(payload[field])
        payload.setdefault("datev", payload["date"])
        result = await self.request("POST", f"bankaccounts/{account_id}/lines", data=payload)
        return self._extract_identifier(result)

    # ============================================================================
    # ORDER MANAGEMENT
    # ============================================================================

    async def get_orders(self, limit: int = 100, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get list of orders."""
        params: Dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status

        result = await self.request("GET", "orders", params=params)
        return result if isinstance(result, list) else []

    async def get_order_by_id(self, order_id: int) -> Dict[str, Any]:
        """Get specific order by ID."""
        return await self.request("GET", f"orders/{order_id}")

    async def create_order(
        self,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new order."""
        payload = self._merge_payload(data, **kwargs)
        result = await self.request("POST", "orders", data=payload)
        return self._extract_identifier(result)

    async def update_order(
        self,
        order_id: int,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Update an existing order."""
        payload = self._merge_payload(data, **kwargs)
        return await self.request("PUT", f"orders/{order_id}", data=payload)

    async def delete_order(self, order_id: int) -> Dict[str, Any]:
        """Delete an order."""
        return await self.request("DELETE", f"orders/{order_id}")

    # ============================================================================
    # CONTACT MANAGEMENT
    # ============================================================================

    async def get_contacts(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get list of contacts."""
        params = {"limit": limit}
        result = await self.request("GET", "contacts", params=params)
        return result if isinstance(result, list) else []

    async def get_contact_by_id(self, contact_id: int) -> Dict[str, Any]:
        """Get specific contact by ID."""
        return await self.request("GET", f"contacts/{contact_id}")

    async def create_contact(
        self,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new contact."""
        payload = self._merge_payload(data, **kwargs)
        result = await self.request("POST", "contacts", data=payload)
        return self._extract_identifier(result)

    async def update_contact(
        self,
        contact_id: int,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Update an existing contact."""
        payload = self._merge_payload(data, **kwargs)
        # PUT /contacts/{id} only writes the third party when socid and fk_soc
        # are both present; a lone one is a silent no-op, so send both.
        for key in ("socid", "fk_soc"):
            if payload.get(key) not in (None, ""):
                payload["socid"] = payload["fk_soc"] = payload[key]
                break
        return await self.request("PUT", f"contacts/{contact_id}", data=payload)

    async def delete_contact(self, contact_id: int) -> Dict[str, Any]:
        """Delete a contact."""
        return await self.request("DELETE", f"contacts/{contact_id}")

    # ============================================================================
    # PROJECT MANAGEMENT
    # ============================================================================

    async def get_projects(self, limit: int = 100, page: int = 1, status: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get list of projects (``page`` starts at 1)."""
        params: Dict[str, Any] = {"limit": limit}
        self._apply_page(params, page)
        if status is not None:
            params["status"] = status
        result = await self.request("GET", "projects", params=params)
        return result if isinstance(result, list) else []

    async def get_project_by_id(self, project_id: int) -> Dict[str, Any]:
        """Get specific project by ID."""
        return await self.request("GET", f"projects/{project_id}")

    async def search_projects(self, sqlfilters: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search projects using SQL filters."""
        params = {"limit": limit, "sqlfilters": sqlfilters}
        result = await self.request("GET", "projects", params=params)
        return result if isinstance(result, list) else []

    async def create_project(self, data: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        """Create a new project."""
        payload = self._merge_payload(data, **kwargs)
        payload = self._validate_payload(
            endpoint="projects",
            payload=payload,
            required_fields=["ref", "name", "socid"],
            aliases={"name": ["title"]},
            non_empty_fields=["socid"],
        )
        result = await self.request("POST", "projects", data=payload)
        return self._extract_identifier(result)

    async def update_project(self, project_id: int, data: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        """Update an existing project."""
        payload = self._merge_payload(data, **kwargs)
        return await self.request("PUT", f"projects/{project_id}", data=payload)

    async def delete_project(self, project_id: int) -> Dict[str, Any]:
        """Delete a project."""
        return await self.request("DELETE", f"projects/{project_id}")

    # ============================================================================
    # PROJECT TASKS & CONSUMED TIME (timespent)
    # ============================================================================

    @staticmethod
    def _timespent_date(value: Any) -> str:
        """Normalize a timespent date to ``YYYY-MM-DD HH:MM:SS`` in UTC.

        Accepted inputs: an epoch timestamp, an explicitly-zoned ISO string
        (converted to UTC), a naive ``YYYY-MM-DD HH:MM:SS`` string (passed
        through; the API stores it as UTC) or a bare ``YYYY-MM-DD`` (midnight
        UTC, the hour is ignored by the server).
        """
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(int(value), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        text = str(value).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return f"{text} 00:00:00"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", text):
            return text.replace("T", " ")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        raise DolibarrValidationError(
            f"Invalid date {value!r}: expected YYYY-MM-DD[ HH:MM:SS], a Unix timestamp, or a zoned ISO string",
            status_code=400,
        )

    @staticmethod
    def _clean_timespent_line(row: Dict[str, Any]) -> Dict[str, Any]:
        """Map a raw ``timespent_line_*`` row to clean field names.

        The bare ``date``/``duration``/``fk_user`` fields of such rows are
        always null; the real values live behind the ``timespent_line_``
        prefix, which is stripped here.
        """
        entry: Dict[str, Any] = {}
        for key, val in row.items():
            if key.startswith("timespent_line_"):
                entry[key.removeprefix("timespent_line_")] = val
        for key in ("id", "date", "datehour", "duration", "fk_user"):
            if entry.get(key) not in (None, ""):
                entry[key] = int(entry[key])
        return entry

    async def create_task(self, project_id: int, ref: str, label: str, **kwargs) -> Dict[str, Any]:
        """Create a task on a project (``POST /tasks``).

        ``ref`` is required by the API and becomes the final task reference
        (no ``TK`` auto-generation); ``label`` is the task title.
        """
        payload = self._merge_payload(None, fk_project=int(project_id), ref=ref, label=label, **kwargs)
        payload = self._validate_payload(endpoint="tasks", payload=payload, required_fields=["ref", "label", "fk_project"])
        result = await self.request("POST", "tasks", data=payload)
        return self._extract_identifier(result)

    async def add_task_time(self, task_id: int, date: Any, duration: int, user_id: int = 0, note: str = "") -> Dict[str, Any]:
        """Add a consumed-time entry to a task (``POST /tasks/{id}/addtimespent``).

        ``date`` is stored as UTC (see :meth:`_timespent_date`), ``duration``
        is in seconds, ``user_id`` 0 means the connected user.
        """
        payload = {"date": self._timespent_date(date), "duration": int(duration), "user_id": int(user_id), "note": note or ""}
        return await self.request("POST", f"tasks/{int(task_id)}/addtimespent", data=payload)

    async def update_task_time(
        self, task_id: int, timespent_id: int, date: Any, duration: int, user_id: int = 0, note: str = ""
    ) -> Dict[str, Any]:
        """Correct a consumed-time entry (``PUT /tasks/{id}/timespent/{timespent_id}``), same fields as :meth:`add_task_time`."""
        payload = {"date": self._timespent_date(date), "duration": int(duration), "user_id": int(user_id), "note": note or ""}
        return await self.request("PUT", f"tasks/{int(task_id)}/timespent/{int(timespent_id)}", data=payload)

    async def delete_task_time(self, task_id: int, timespent_id: int) -> Dict[str, Any]:
        """Delete a consumed-time entry (``DELETE /tasks/{id}/timespent/{timespent_id}``)."""
        return await self.request("DELETE", f"tasks/{int(task_id)}/timespent/{int(timespent_id)}")

    async def get_task_timespent(self, task_id: int) -> Dict[str, Any]:
        """Consumed-time entries of one task with clean field names.

        Tries ``GET /tasks/{id}/timespent`` (newer Dolibarr: list of
        ``timespent_line_*`` rows); on 404 falls back to
        ``GET /tasks/{id}?includetimespent=2`` whose ``lines`` carry the same
        fields. ``total_duration`` (seconds) is surfaced in both cases.
        """
        try:
            rows = await self.request("GET", f"tasks/{int(task_id)}/timespent")
            entries = [self._clean_timespent_line(r) for r in rows] if isinstance(rows, list) else []
        except DolibarrAPIError as exc:
            if exc.status_code != 404:
                raise
            task = await self.request("GET", f"tasks/{int(task_id)}", params={"includetimespent": 2})
            entries = [self._clean_timespent_line(r) for r in (task.get("lines") or []) if isinstance(r, dict)]
        return {
            "task_id": int(task_id),
            "total_duration": sum(int(e.get("duration") or 0) for e in entries),
            "entries": entries,
        }

    async def get_project_timespent(self, project_id: int) -> Dict[str, Any]:
        """Consolidated consumed time of a project.

        Tries ``GET /projects/{id}/timespent`` (newer Dolibarr: consolidated
        ``total_duration`` in element ``[0]``); on 404 or an empty result it
        falls back to direct reads: the project-scoped task list
        (``GET /projects/{id}/tasks``) can wrongly return empty, so an empty
        list is retried via ``GET /tasks?sqlfilters=(t.fk_projet:=:<id>)``,
        and each task's entries are read with :meth:`get_task_timespent`.
        """
        project_id = int(project_id)
        try:
            rows = await self.request("GET", f"projects/{project_id}/timespent")
        except DolibarrAPIError as exc:
            if exc.status_code != 404:
                raise
            rows = None
        if isinstance(rows, list) and rows and isinstance(rows[0], dict) and rows[0].get("total_duration") is not None:
            lines = [self._clean_timespent_line(r) for r in rows[1:] if isinstance(r, dict)]
            return {"project_id": project_id, "total_duration": int(rows[0]["total_duration"] or 0), "entries": lines}

        task_rows: Any = await self.request("GET", f"projects/{project_id}/tasks", params={"limit": 100})
        if not isinstance(task_rows, list) or not task_rows:
            task_rows = await self.request("GET", "tasks", params={"limit": 100, "sqlfilters": f"(t.fk_projet:=:{project_id})"})
        task_rows = task_rows if isinstance(task_rows, list) else []
        entries: List[Dict[str, Any]] = []
        for task in task_rows:
            task_id = int(task.get("id") or task.get("rowid") or 0)
            if not task_id:
                continue
            for entry in (await self.get_task_timespent(task_id))["entries"]:
                entry["task_id"] = task_id
                entries.append(entry)
        return {
            "project_id": project_id,
            "total_duration": sum(int(e.get("duration") or 0) for e in entries),
            "entries": entries,
        }

    # ============================================================================
    # INTERVENTIONS (fichinter)
    # ============================================================================

    @staticmethod
    def _compact_intervention(row: Dict[str, Any]) -> Dict[str, Any]:
        """Map a raw intervention row to a compact summary."""
        summary: Dict[str, Any] = {}
        for key in ("id", "ref", "socid", "fk_project", "status", "description"):
            if key in row:
                summary[key] = row[key]
        for key in ("id", "socid", "fk_project", "status"):
            if summary.get(key) not in (None, ""):
                summary[key] = int(summary[key])
        return summary

    @staticmethod
    def _compact_intervention_line(row: Dict[str, Any]) -> Dict[str, Any]:
        """Map a raw intervention line to ``id``/``desc``/``date``/``duration``."""
        line: Dict[str, Any] = {}
        for key in ("id", "desc", "date", "duration"):
            if key in row:
                line[key] = row[key]
        for key in ("id", "date", "duration"):
            if line.get(key) not in (None, ""):
                line[key] = int(line[key])
        return line

    @staticmethod
    def _epoch_date(value: Any) -> int:
        """Normalize a date to a Unix timestamp (naive strings are read as UTC)."""
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
        text = str(value).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            text = f"{text} 00:00:00"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", text):
            parsed = datetime.strptime(text.replace("T", " "), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            return int(parsed.timestamp())
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.astimezone(timezone.utc).timestamp())

    async def get_interventions(self, limit: int = 100, page: int = 1) -> List[Dict[str, Any]]:
        """Compact list of intervention sheets (``GET /interventions``)."""
        params: Dict[str, Any] = {"limit": limit}
        self._apply_page(params, page)
        rows = await self._get_list("interventions", params)
        return [self._compact_intervention(r) for r in rows]

    async def get_intervention(self, intervention_id: int) -> Dict[str, Any]:
        """One intervention sheet with compact lines (``GET /interventions/{id}``).

        Raw line objects carry dozens of null fields; only ``id``, ``desc``,
        ``date`` (Unix timestamp) and ``duration`` (seconds) are kept.
        """
        sheet = await self.request("GET", f"interventions/{int(intervention_id)}")
        lines = sheet.get("lines")
        if isinstance(lines, list):
            sheet["lines"] = [self._compact_intervention_line(line) for line in lines if isinstance(line, dict)]
        return sheet

    async def create_intervention(self, project_id: int, socid: int, description: str, **kwargs) -> Dict[str, Any]:
        """Create a project-linked intervention sheet (``POST /interventions``).

        The API requires ``socid``, ``fk_project`` and ``description``.
        """
        payload = self._merge_payload(None, socid=int(socid), fk_project=int(project_id), description=description, **kwargs)
        payload = self._validate_payload(
            endpoint="interventions", payload=payload, required_fields=["socid", "fk_project", "description"]
        )
        result = await self.request("POST", "interventions", data=payload)
        return self._extract_identifier(result)

    async def add_intervention_line(self, intervention_id: int, description: str, date: Any, duration: int) -> Dict[str, Any]:
        """Add a line to an intervention sheet (``POST /interventions/{id}/lines``).

        ``date`` is a Unix timestamp (string dates are read as UTC), ``duration``
        is in seconds. The write side requires ``description`` (not ``desc``);
        the pinned Dolibarr 21.0.0 validates the duration under the French wire
        name ``duree``, so both spellings are sent with the same value.
        """
        seconds = int(duration)
        payload = {
            "description": description,
            "date": self._epoch_date(date),
            "duration": seconds,
            "duree": seconds,
        }
        return await self.request("POST", f"interventions/{int(intervention_id)}/lines", data=payload)

    # ============================================================================
    # AGENDA EVENTS
    # ============================================================================

    async def _resolve_event_type_code(self, event_type: Any) -> str:
        """Resolve an agenda event type code (``AC_TEL``) or dictionary id to the wire code.

        Ids are assigned by the instance, so numeric types are resolved through
        the ``event_types`` dictionary rather than hardcoded.
        """
        if event_type is None or isinstance(event_type, bool):
            raise DolibarrValidationError("Provide 'type_code' or 'type'", status_code=400)
        rows = await self.get_dictionary("event_types")
        if isinstance(event_type, int) or str(event_type).strip().isdigit():
            type_id = int(event_type)
            for row in rows:
                if int(row["id"]) == type_id:
                    return str(row["code"])
            raise DolibarrValidationError(
                f"Unknown agenda event type id {type_id}: pass a code listed by "
                "get_dictionary('event_types') (e.g. AC_TEL) or its numeric id.",
                status_code=400,
            )
        text = str(event_type).strip()
        for row in rows:
            if str(row.get("code", "")).upper() == text.upper():
                return str(row["code"])
        raise DolibarrValidationError(
            f"Unknown agenda event type '{event_type}': pass a code listed by "
            "get_dictionary('event_types') (e.g. AC_TEL) or its numeric id.",
            status_code=400,
        )

    async def create_agenda_event(
        self,
        label: str,
        datep: Any,
        datef: Any,
        userownerid: int,
        type_code: Optional[Any] = None,
        type: Optional[int] = None,
        socid: Optional[int] = None,
        percentage: Optional[int] = None,
        note: Optional[str] = None,
        contact_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create an agenda event (``POST /agendaevents``).

        The write side requires ``userownerid`` and ``type_code`` (the field name
        ``type`` is rejected on the wire); ``type_code``/``type`` are resolved to
        the code through the ``event_types`` dictionary. ``datep``/``datef`` are
        Unix timestamps (naive date strings are read as UTC). ``socid`` links a
        third party, ``note`` is stored as ``note_private``. ``contact_id`` is
        sent but NOT persisted by upstream Dolibarr.
        """
        code = await self._resolve_event_type_code(type if type is not None else type_code)
        payload: Dict[str, Any] = {
            "type_code": code,
            "userownerid": int(userownerid),
            "label": label,
            "datep": self._epoch_date(datep),
            "datef": self._epoch_date(datef),
        }
        if socid is not None:
            payload["socid"] = int(socid)
        if percentage is not None:
            payload["percentage"] = int(percentage)
        if note:
            payload["note_private"] = note
        if contact_id is not None:
            payload["contact_id"] = int(contact_id)
        payload = self._validate_payload(
            endpoint="agendaevents", payload=payload, required_fields=["type_code", "userownerid", "label", "datep", "datef"]
        )
        result = await self.request("POST", "agendaevents", data=payload)
        return self._extract_identifier(result)

    async def get_agenda_event(self, event_id: int) -> Dict[str, Any]:
        """Get one agenda event by id (``GET /agendaevents/{id}``)."""
        return await self.request("GET", f"agendaevents/{int(event_id)}")

    # ============================================================================
    # DOCUMENTS (attached / generated files)
    # ============================================================================

    async def get_documents(
        self,
        modulepart: str,
        ref: Optional[str] = None,
        id: Optional[int] = None,
        subdir: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List files attached to an object (GET /documents).

        A 404 "does not return any document" means the object has no attached
        file: return an empty list instead of raising. Moduleparts the listing
        endpoint does not implement (tax, bank, ...) raise as usual.
        """
        params: Dict[str, Any] = {"modulepart": modulepart}
        if ref:
            params["ref"] = ref
        if id is not None:
            params["id"] = id
        if subdir:
            params["subdir"] = subdir
        try:
            result = await self.request("GET", "documents", params=params)
        except DolibarrAPIError as exc:
            if exc.status_code == 404 and "does not return any document" in exc.message:
                return []
            raise
        return result if isinstance(result, list) else []

    @staticmethod
    def _file_op_modulepart(modulepart: str) -> str:
        """Modulepart name for the download/delete endpoints.

        On Dolibarr 21 those endpoints name the supplier-invoice module
        'invoice_supplier'; 'supplier_invoice' is only valid for list/upload.
        """
        return "invoice_supplier" if modulepart == "supplier_invoice" else modulepart

    async def download_document(self, modulepart: str, original_file: str) -> Dict[str, Any]:
        """Download one file (GET /documents/download); content is base64-encoded."""
        params = {"modulepart": self._file_op_modulepart(modulepart), "original_file": original_file}
        return await self.request("GET", "documents/download", params=params)

    @staticmethod
    def _document_original_file(
        modulepart: str,
        filename: str,
        ref: Optional[str] = None,
        id: Optional[int] = None,
        subdir: Optional[str] = None,
    ) -> str:
        """Relative path of an uploaded file, per Dolibarr's conventions.

        - ``supplier_invoice``: ``{id mod 10}/{floor(id/10) mod 10}/{ref}/{filename}``
          (the numeric prefix is derived from the Dolibarr id),
        - ``tax``: ``subdir`` is the social-charge rowid,
        - ``bank``: ``subdir`` is ``{account_id}/statement/{num}`` (num = YYYYMM),
        - everything else: ``{ref}/{filename}``.
        """
        if modulepart == "supplier_invoice":
            if id is None or not ref:
                raise DolibarrValidationError(
                    "modulepart 'supplier_invoice' needs both 'ref' and 'id' to compute the file path",
                    status_code=400,
                )
            prefix = f"{int(id) % 10}/{(int(id) // 10) % 10}/{ref}"
        else:
            prefix = subdir or ref or ""
        return f"{prefix}/{filename}" if prefix else filename

    async def upload_document(
        self,
        modulepart: str,
        filename: str,
        filecontent: str,
        ref: Optional[str] = None,
        id: Optional[int] = None,
        subdir: Optional[str] = None,
        overwrite: bool = True,
    ) -> Dict[str, Any]:
        """Upload a base64-encoded file (POST /documents/upload).

        All fields go in the JSON body — the query string fails beyond ~8 KB.
        Returns the stored file name and its computed ``original_file`` path.
        """
        payload: Dict[str, Any] = {
            "modulepart": modulepart,
            "filename": filename,
            "filecontent": filecontent,
            "fileencoding": "base64",
            "overwriteifexists": 1 if overwrite else 0,
        }
        if ref:
            payload["ref"] = ref
        if subdir:
            payload["subdir"] = subdir
        stored = await self.request("POST", "documents/upload", data=payload)
        return {
            "filename": stored if isinstance(stored, str) and stored else filename,
            "original_file": self._document_original_file(modulepart, filename, ref=ref, id=id, subdir=subdir),
            "modulepart": modulepart,
        }

    async def delete_document(self, modulepart: str, original_file: str) -> Dict[str, Any]:
        """Delete an attached file (DELETE /documents).

        A 404 "File not found" (already deleted) counts as success.
        """
        params = {"modulepart": self._file_op_modulepart(modulepart), "original_file": original_file}
        try:
            return await self.request("DELETE", "documents", params=params)
        except DolibarrAPIError as exc:
            if exc.status_code == 404:
                return {"success": {"code": 200, "message": f"File not found (already deleted): {original_file}"}}
            raise

    # ============================================================================
    # TICKET MANAGEMENT
    # ============================================================================

    async def get_tickets(self, limit: int = 100, page: int = 1, status: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get list of tickets.

        When ``status`` is None the open tickets are returned (excluding closed
        and cancelled). The Dolibarr ``status`` query parameter is unreliable for
        tickets, so filtering is done via ``sqlfilters`` on ``t.fk_statut``.
        ``page`` starts at 1.
        """
        params: Dict[str, Any] = {"limit": limit}
        self._apply_page(params, page)
        if status is not None:
            params["sqlfilters"] = f"(t.fk_statut:=:{int(status)})"
        else:
            params["sqlfilters"] = "(t.fk_statut:!=:8) and (t.fk_statut:!=:9)"
        return await self._get_list("tickets", params)

    async def get_ticket_by_ref(self, ref: str) -> Dict[str, Any]:
        """Get a specific ticket by its business reference (e.g. ``TI1046``).

        The response includes the ``messages`` array and the ``track_id`` needed
        to post new messages.
        """
        return await self.request("GET", f"tickets/ref/{quote(ref, safe='')}")

    async def search_tickets(self, sqlfilters: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search tickets using SQL filters."""
        params = {"limit": limit, "sqlfilters": sqlfilters}
        return await self._get_list("tickets", params)

    async def create_ticket(self, data: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        """Create a new ticket.

        Dolibarr requires ``subject`` and ``message``. ``socid`` links the ticket
        to a thirdparty and is accepted via the ``fk_soc`` alias as well.
        """
        payload = self._merge_payload(data, **kwargs)
        payload = self._validate_payload(
            endpoint="tickets",
            payload=payload,
            required_fields=["subject", "message"],
            aliases={"socid": ["fk_soc"]},
            non_empty_fields=["subject", "message"],
        )
        result = await self.request("POST", "tickets", data=payload)
        return self._extract_identifier(result)

    async def _resolve_ticket_track_id(self, ref: str) -> str:
        """Resolve a ticket reference to its ``track_id``."""
        ticket = await self.get_ticket_by_ref(ref)
        track_id = ticket.get("track_id") if isinstance(ticket, dict) else None
        if not track_id:
            error_data = self._build_validation_error(
                endpoint="tickets/messages",
                invalid_fields=[{"field": "ref", "message": f"no track_id found for ticket ref '{ref}'"}],
                message=f"Could not resolve track_id for ticket ref '{ref}'",
            )
            raise DolibarrValidationError(
                message=error_data["message"],
                status_code=error_data["status"],
                response_data=error_data,
            )
        return track_id

    async def add_ticket_message(self, data: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        """Add a message to a ticket.

        The Dolibarr endpoint ``POST /tickets/messages`` requires ``track_id`` and
        ``message``. For convenience a ``ref`` may be supplied instead of
        ``track_id``; it is resolved to the matching ``track_id`` first.
        """
        payload = self._merge_payload(data, **kwargs)
        payload = self._validate_payload(
            endpoint="tickets/messages",
            payload=payload,
            required_fields=["message"],
            required_any_of=[["track_id", "ref"]],
            non_empty_fields=["message"],
        )

        if not payload.get("track_id"):
            payload["track_id"] = await self._resolve_ticket_track_id(payload["ref"])
        # ``ref`` is not part of the API payload; drop it before sending.
        payload.pop("ref", None)

        return await self.request("POST", "tickets/messages", data=payload)

    async def update_ticket(
        self,
        ticket_id: Optional[int] = None,
        ref: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Update an existing ticket.

        The Dolibarr ``PUT`` endpoint only accepts the numeric id. When only a
        ``ref`` is known it is resolved to the internal id first.
        """
        payload = self._merge_payload(data, **kwargs)
        if ticket_id is None:
            if not ref:
                error_data = self._build_validation_error(
                    endpoint="tickets",
                    missing_fields=["ticket_id or ref"],
                    message="Validation failed (missing: ticket_id or ref)",
                )
                raise DolibarrValidationError(
                    message=error_data["message"],
                    status_code=error_data["status"],
                    response_data=error_data,
                )
            ticket = await self.get_ticket_by_ref(ref)
            ticket_id = ticket.get("id") if isinstance(ticket, dict) else None
            if not ticket_id:
                error_data = self._build_validation_error(
                    endpoint="tickets",
                    invalid_fields=[{"field": "ref", "message": f"no ticket found for ref '{ref}'"}],
                    message=f"Could not resolve ticket id for ref '{ref}'",
                )
                raise DolibarrValidationError(
                    message=error_data["message"],
                    status_code=error_data["status"],
                    response_data=error_data,
                )
        return await self.request("PUT", f"tickets/{ticket_id}", data=payload)

    # ============================================================================
    # SOCIAL CONTRIBUTIONS (charges and taxes)
    # ============================================================================

    #: Raw French field name accepted by the API but rejected on write; written
    #: split so spellcheckers do not flag it.
    _RAW_PERIOD_FIELD = "perio" "de"

    def _social_contribution_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send only the server-side field names to /socialcontributions.

        The endpoint accepts ``fk_type`` and ``period`` only: ``type`` and the
        raw French period field are rejected with a 400. ``date_ech`` and
        ``period`` are sent as integer Unix timestamps. ``paye`` cannot be set
        by POST/PUT (it follows the payments), so it is dropped if present.
        """
        payload = dict(payload)
        if "type" in payload:
            payload["fk_type"] = payload.pop("type")
        if self._RAW_PERIOD_FIELD in payload:
            payload["period"] = payload.pop(self._RAW_PERIOD_FIELD)
        payload.pop("paye", None)
        for field in ("date_ech", "period"):
            if field in payload and payload[field] not in (None, ""):
                # Instance convention: midnight UTC (a local-midnight timestamp would
                # be truncated to the previous day by the server's date column).
                value = payload[field]
                if not isinstance(value, (int, float)):
                    value = datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
                payload[field] = int(value)
        return payload

    async def get_social_contributions(
        self,
        limit: int = 100,
        page: int = 1,
        period: Optional[Any] = None,
        type: Optional[int] = None,
        paid: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """List social contributions (``page`` starts at 1).

        ``period`` (YYYY-MM-DD or timestamp), ``type`` (fk_type) and ``paid``
        filter client-side: the endpoint honors only one sqlfilter condition,
        so combined server-side filters would silently return 0 rows.
        """
        params: Dict[str, Any] = {"limit": limit}
        self._apply_page(params, page)
        rows = await self._get_list("socialcontributions", params)

        period_date = None
        if period not in (None, ""):
            value = (
                period
                if isinstance(period, (int, float))
                else datetime.strptime(str(period), "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
            )
            period_date = datetime.fromtimestamp(int(value), timezone.utc).date()

        result: List[Dict[str, Any]] = []
        for row in rows:
            if type is not None and self._to_int(row.get("type", row.get("fk_type"))) != int(type):
                continue
            if period_date is not None:
                row_period = row.get("period", row.get(self._RAW_PERIOD_FIELD))
                if row_period in (None, "") or datetime.fromtimestamp(int(row_period), timezone.utc).date() != period_date:
                    continue
            if paid is not None and (int(row.get("paye") or 0) == 1) != bool(paid):
                continue
            result.append(row)
        return result

    async def create_social_contribution(
        self,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a social contribution (charge/tax) via ``POST /socialcontributions``.

        Friendly names are accepted: ``type`` is sent as ``fk_type`` and
        the raw French period field as ``period`` - never ``type`` or the raw
        French period field. Returns the new id.
        """
        payload = self._merge_payload(data, **kwargs)
        payload = self._validate_payload(
            endpoint="socialcontributions",
            payload=payload,
            required_fields=["fk_type", "date_ech", "period", "amount", "label"],
            aliases={"fk_type": ["type"], "period": [self._RAW_PERIOD_FIELD]},
        )
        payload = self._social_contribution_payload(payload)
        result = await self.request("POST", "socialcontributions", data=payload)
        return self._extract_identifier(result)

    async def update_social_contribution(
        self,
        contribution_id: int,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Update a social contribution via ``PUT /socialcontributions/{id}``.

        Same friendly-name translation as :meth:`create_social_contribution`.
        """
        payload = self._social_contribution_payload(self._merge_payload(data, **kwargs))
        return await self.request("PUT", f"socialcontributions/{contribution_id}", data=payload)

    async def add_social_contribution_payment(
        self,
        contribution_id: int,
        amount: float,
        payment_mode: Any,
        accountid: Optional[int] = None,
        payment_date: Any = None,
        num_payment: Optional[str] = None,
        note: Optional[str] = None,
        closepaidcontrib: bool = True,
    ) -> Dict[str, Any]:
        """Record a payment on a social contribution via ``POST /socialcontributions/{id}/payments``.

        Sends ``datepaye`` (integer timestamp), ``amount``, ``paiementtype``
        (numeric mode id; codes resolved via the ``payment_types`` dictionary),
        ``accountid`` (creates the linked bank line), and optional
        ``num_payment``, ``note``, ``closepaidcontrib``. The charge's ``paye``
        flag follows these payments. Returns the new payment id.
        """
        payload: Dict[str, Any] = {
            "datepaye": self._payment_datepaye(payment_date),
            "amount": amount,
            "paiementtype": await self._resolve_payment_mode_id(payment_mode),
            "closepaidcontrib": 1 if closepaidcontrib else 0,
        }
        if accountid is not None:
            payload["accountid"] = int(accountid)
        if num_payment:
            payload["num_payment"] = str(num_payment)
        if note:
            payload["note"] = str(note)
        return await self.request("POST", f"socialcontributions/{contribution_id}/payments", data=payload)

    # ============================================================================
    # RAW API CALL
    # ============================================================================

    async def dolibarr_raw_api(
        self, method: str, endpoint: str, params: Optional[Dict] = None, data: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make raw API call to any Dolibarr endpoint."""
        return await self.request(method, endpoint, params=params, data=data)
