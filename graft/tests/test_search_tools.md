# tests/test_search_tools.py

- test_search_products_by_ref · function · L9-L28 — async def test_search_products_by_ref(): # Mock
  DolibarrClient
- test_resolve_product_ref_exact · function · L32-L44 — async def test_resolve_product_ref_exact()
- test_resolve_product_ref_ambiguous · function · L48-L64 — async def
  test_resolve_product_ref_ambiguous()
- test_search_customers · function · L68-L80 — async def test_search_customers()
- test_search_customers_apostrophe_becomes_like_wildcard · function · L85-L96 — async def
  test_search_customers_apostrophe_becomes_like_wildcard(query): # Dolibarr sqlfilters cannot match a
  literal apostrophe, whatever the escaping.
