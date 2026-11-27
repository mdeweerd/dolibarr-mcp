# src/dolibarr_mcp/dolibarr_mcp_server.py

- \_error_result · function · L55-L60 — def \_error_result(payload: dict) -> CallToolResult
- \_escape_sqlfilter · function · L66-L78 — def \_escape_sqlfilter(value: str) -> str
- \_to_iso_date · function · L87-L98 — def \_to_iso_date(value: Any) -> str | None
- \_to_int · function · L101-L105 — def \_to_int(value: Any) -> int | None
- \_summarize_invoice · function · L108-L127 — def \_summarize_invoice(invoice: dict[str, Any]) ->
  dict[str, Any]
- \_summarize_supplier_invoice · function · L130-L148 — def \_summarize_supplier_invoice(invoice:
  dict[str, Any]) -> dict[str, Any]
- \_summarize_supplier_invoice_template · function · L151-L159 — def
  \_summarize_supplier_invoice_template(template: dict[str, Any]) -> dict[str, Any]
- \_summarize_supplier_invoice_template_line · function · L162-L170 — def
  \_summarize_supplier_invoice_template_line(line: dict[str, Any]) -> dict[str, Any]
- \_summarize_bank_account · function · L173-L179 — def \_summarize_bank_account(account: dict[str, Any])
  -> dict[str, Any]
- \_summarize_bank_line · function · L182-L192 — def \_summarize_bank_line(line: dict[str, Any]) ->
  dict[str, Any]
- \_summarize_social_contribution · function · L195-L204 — def \_summarize_social_contribution(contrib:
  dict[str, Any]) -> dict[str, Any]
- \_summarize_document · function · L212-L226 — def \_summarize_document(doc: dict, original_file: str |
  None = None) -> dict
- \_document_path_convention · function · L229-L237 — def \_document_path_convention(modulepart: str) ->
  str
- \_resolve_supplier_invoice_ref_id · function · L240-L252 — async def
  \_resolve_supplier_invoice_ref_id(client: DolibarrClient, ref: str | None, obj_id: int | None)
- \_extract_pdf_attachments · function · L255-L279 — def \_extract_pdf_attachments(data: bytes) ->
  list[dict]
- \_document_contents · function · L282-L334 — def \_document_contents(downloaded: dict, modulepart: str,
  original_file: str, include_file: bool = True)
- is_write_tool · function · L341-L343 — def is_write_tool(name: str) -> bool
- is_tool_allowed · function · L346-L352 — def is_tool_allowed(name: str, config: Config) -> bool
- handle_list_tools · function · L356-L359 — async def handle_list_tools()
- \_all_tools · function · L362-L2713 — def \_all_tools()
- handle_call_tool · function · L2717-L3337 — async def handle_call_tool(name: str, arguments: dict)
- test_api_connection · function · L3341-L3383 — async def test_api_connection(config: Config | None =
  None)
- \_run_stdio_server · function · L3386-L3400 — async def \_run_stdio_server(\_config: Config) -> None
- BearerAuthMiddleware · class · L3403-L3428 — class BearerAuthMiddleware
- __init__ · method · L3406-L3408 — def __init__(self, app, token: str)
- __call__ · method · L3410-L3428 — async def __call__(self, scope: Scope, receive: Receive, send: Send)
- \_build_http_app · function · L3431-L3480 — def \_build_http_app(session_manager:
  StreamableHTTPSessionManager, config: Config | None = None) -> Starlette
- ASGIEndpoint · class · L3435-L3442 — class ASGIEndpoint
- __init__ · method · L3438-L3439 — def __init__(self, handler)
- __call__ · method · L3441-L3442 — async def __call__(self, scope: Scope, receive: Receive, send: Send)
- lifespan · function · L3445-L3447 — async def lifespan(app)
- asgi_handler · function · L3449-L3451 — async def asgi_handler(scope, receive, send)
- \_run_http_server · function · L3483-L3508 — async def \_run_http_server(config: Config) -> None
- main · function · L3511-L3535 — async def main()
