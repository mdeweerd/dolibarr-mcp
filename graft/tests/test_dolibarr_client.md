# tests/test_dolibarr_client.py

- TestDolibarrClient · class · L15-L245 — class TestDolibarrClient
- test_config_validation · method · L18-L31 — def test_config_validation(self)
- test_client_session_management · method · L34-L49 — async def test_client_session_management(self)
- test_context_manager · method · L52-L60 — async def test_context_manager(self)
- test_successful_api_request · method · L64-L79 — async def test_successful_api_request(self,
  mock_request)
- test_api_error_handling · method · L83-L99 — async def test_api_error_handling(self, mock_request)
- test_validation_error_on_missing_ref · method · L102-L117 — async def
  test_validation_error_on_missing_ref(self)
- test_autogen_ref_when_enabled · method · L120-L137 — async def test_autogen_ref_when_enabled(self)
- test_product_validation_requires_price_field · method · L140-L154 — async def
  test_product_validation_requires_price_field(self)
- test_product_validation_allows_price_ttc · method · L157-L171 — async def
  test_product_validation_allows_price_ttc(self)
- test_project_validation_missing_socid · method · L174-L188 — async def
  test_project_validation_missing_socid(self)
- test_project_autogen_ref_when_enabled · method · L191-L207 — async def
  test_project_autogen_ref_when_enabled(self)
- test_internal_error_correlation_id · method · L211-L226 — async def
  test_internal_error_correlation_id(self, mock_request)
- test_url_building · method · L228-L245 — def test_url_building(self)
- TestDolibarrAPIError · class · L248-L265 — class TestDolibarrAPIError
- test_error_creation · method · L251-L257 — def test_error_creation(self)
- test_error_without_optional_params · method · L259-L265 — def test_error_without_optional_params(self)
- TestDolibarrIntegration · class · L270-L288 — class TestDolibarrIntegration
- test_real_connection · method · L274-L288 — async def test_real_connection(self)
