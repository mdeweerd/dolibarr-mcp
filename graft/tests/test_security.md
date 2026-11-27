# tests/test_security.py

- \_cfg · function · L12-L15 — def \_cfg(\*\*kw)
- test_write_tool_detection · function · L18-L31 — def test_write_tool_detection()
- test_raw_api_disabled_by_default · function · L34-L37 — def test_raw_api_disabled_by_default()
- test_read_only_hides_all_write_tools · function · L40-L44 — def test_read_only_hides_all_write_tools()
- test_call_tool_rejects_disabled_tool · function · L48-L55 — async def
  test_call_tool_rejects_disabled_tool(monkeypatch)
- test_defaults_are_safe · function · L58-L62 — def test_defaults_are_safe()
- \_DummySessionManager · class · L65-L79 — class \_DummySessionManager
- run · method · L66-L74 — def run(self)
- \_Ctx · class · L67-L72 — class \_Ctx
- __aenter__ · method · L68-L69 — async def __aenter__(self_inner)
- __aexit__ · method · L71-L72 — async def __aexit__(self_inner, \*exc)
- handle_request · method · L76-L79 — async def handle_request(self, scope, receive, send)
- test_http_requires_bearer_token · function · L82-L88 — def test_http_requires_bearer_token()
- test_cors_disabled_by_default · function · L91-L95 — def test_cors_disabled_by_default()
- test_cors_allowlist · function · L98-L113 — def test_cors_allowlist()
- test_refuses_public_bind_without_token · function · L117-L119 — async def
  test_refuses_public_bind_without_token()
