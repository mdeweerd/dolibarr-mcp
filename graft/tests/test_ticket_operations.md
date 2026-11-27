# tests/test_ticket_operations.py

- TestTicketOperations · class · L24-L201 — class TestTicketOperations
- config · method · L28-L33 — def config(self)
- client · method · L36-L37 — def client(self, config)
- test_get_tickets_defaults_to_open · method · L40-L49 — async def
  test_get_tickets_defaults_to_open(self, client)
- test_get_tickets_with_status_filter · method · L52-L58 — async def
  test_get_tickets_with_status_filter(self, client)
- test_get_ticket_by_ref · method · L61-L73 — async def test_get_ticket_by_ref(self, client)
- test_search_tickets · method · L76-L89 — async def test_search_tickets(self, client)
- test_create_ticket · method · L92-L106 — async def test_create_ticket(self, client)
- test_create_ticket_fk_soc_alias · method · L109-L116 — async def test_create_ticket_fk_soc_alias(self,
  client)
- test_create_ticket_requires_subject_and_message · method · L119-L125 — async def
  test_create_ticket_requires_subject_and_message(self, client)
- test_add_ticket_message_with_track_id · method · L128-L140 — async def
  test_add_ticket_message_with_track_id(self, client)
- test_add_ticket_message_resolves_ref · method · L143-L156 — async def
  test_add_ticket_message_resolves_ref(self, client)
- test_add_ticket_message_requires_ref_or_track_id · method · L159-L165 — async def
  test_add_ticket_message_requires_ref_or_track_id(self, client)
- test_update_ticket_by_id · method · L168-L176 — async def test_update_ticket_by_id(self, client)
- test_update_ticket_resolves_ref · method · L179-L192 — async def test_update_ticket_resolves_ref(self,
  client)
- test_update_ticket_requires_id_or_ref · method · L195-L201 — async def
  test_update_ticket_requires_id_or_ref(self, client)
- TestTicketAdaptations · class · L204-L229 — class TestTicketAdaptations
- client · method · L208-L209 — def client(self)
- test_get_tickets_page_is_zero_based · method · L213-L216 — async def
  test_get_tickets_page_is_zero_based(self, client, page, expected)
- test_get_tickets_empty_list_on_404 · method · L219-L223 — async def
  test_get_tickets_empty_list_on_404(self, client)
- test_get_ticket_by_ref_is_url_encoded · method · L226-L229 — async def
  test_get_ticket_by_ref_is_url_encoded(self, client)
