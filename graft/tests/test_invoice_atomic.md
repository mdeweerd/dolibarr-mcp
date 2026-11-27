# tests/test_invoice_atomic.py

- TestInvoiceAtomic · class · L11-L158 — class TestInvoiceAtomic
- client · method · L14-L16 — def client(self)
- test_add_invoice_line · method · L19-L32 — async def test_add_invoice_line(self, mock_request, client)
- test_update_invoice_line · method · L35-L74 — async def test_update_invoice_line(self, mock_request,
  client)
- test_update_invoice_line_unknown_line · method · L77-L86 — async def
  test_update_invoice_line_unknown_line(self, mock_request, client)
- test_add_invoice_line_5xx_after_insert · method · L89-L99 — async def
  test_add_invoice_line_5xx_after_insert(self, mock_request, client)
- test_add_invoice_line_5xx_without_insert · method · L102-L113 — async def
  test_add_invoice_line_5xx_without_insert(self, mock_request, client)
- test_delete_invoice_line · method · L116-L127 — async def test_delete_invoice_line(self, mock_request,
  client)
- test_validate_invoice · method · L130-L142 — async def test_validate_invoice(self, mock_request,
  client)
- test_line_vat_is_sent_as_tva_tx · method · L145-L158 — async def test_line_vat_is_sent_as_tva_tx(self,
  mock_request, client)
