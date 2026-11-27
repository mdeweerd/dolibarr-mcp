# src/dolibarr_mcp/dolibarr_client.py

- DolibarrAPIError · class · L18-L25 — class DolibarrAPIError(Exception)
- __init__ · method · L21-L25 — def __init__(self, message: str, status_code: Optional[int] = None,
  response_data: Optional[Dict] = None): # noqa: B042
- DolibarrValidationError · class · L28-L29 — class DolibarrValidationError(DolibarrAPIError)
- DolibarrClient · class · L32-L2450 — class DolibarrClient
- __init__ · method · L35-L50 — def __init__(self, config: Config)
- __aenter__ · method · L52-L55 — async def __aenter__(self)
- __aexit__ · method · L57-L59 — async def __aexit__(self, exc_type, exc_val, exc_tb)
- start_session · method · L61-L67 — async def start_session(self)
- close_session · method · L69-L73 — async def close_session(self)
- \_extract_identifier · method · L76-L84 — def \_extract_identifier(response: Any) -> Any
- \_merge_payload · method · L87-L94 — def \_merge_payload(data: Optional\[Dict[str, Any]\] = None,
  \*\*kwargs) -> Dict[str, Any]
- request · method · L96-L100 — async def request( self, method: str, endpoint: str, params:
  Optional[Dict] = None, data: Optional[Dict] = None ) -> Dict[str, Any]
- \_build_url · method · L102-L106 — def \_build_url(self, endpoint: str) -> str
- \_mask_api_key · method · L108-L114 — def \_mask_api_key(self) -> str
- \_now_iso · method · L117-L119 — def \_now_iso() -> str
- \_generate_correlation_id · method · L122-L124 — def \_generate_correlation_id() -> str
- \_generate_reference · method · L126-L130 — def \_generate_reference(self) -> str
- \_build_validation_error · method · L132-L149 — def \_build_validation_error( self, endpoint: str,
  missing_fields: Optional\[List[str]\] = None, invalid_fields: Optional\[List\[Dict[str, str]\]\] =
  None, message: str = "Validation failed", status: int = 400, ) -> Dict[str, Any]
- \_upstream_message · method · L152-L165 — def \_upstream_message(response_data: Any) -> Optional[str]
- \_build_http_error · method · L167-L193 — def \_build_http_error( self, endpoint: str, status: int,
  reason: Optional[str], response_data: Any, correlation_id: Optional[str] = None, ) -> Dict[str, Any]
- \_build_internal_error · method · L195-L204 — def \_build_internal_error(self, endpoint: str, message:
  str, correlation_id: str) -> Dict[str, Any]
- \_apply_aliases · method · L206-L213 — def \_apply_aliases(self, payload: Dict[str, Any], aliases:
  Dict\[str, List[str]\]) -> None
- \_validate_payload · method · L215-L278 — def \_validate_payload( self, endpoint: str, payload:
  Dict[str, Any], required_fields: List[str], aliases: Optional\[Dict\[str, List[str]\]\] = None,
  numeric_positive: Optional\[List[str]\] = None, enum_fields: Optional\[Dict\[str, List[Any]\]\] = None,
  required_any_of: Optional\[List\[List[str]\]\] = None, non_empty_fields: Optional\[List[str]\] = None,
  ) -> Dict[str, Any]
- \_make_request · method · L280-L432 — async def \_make_request( self, method: str, endpoint: str,
  params: Optional[Dict] = None, data: Optional[Dict] = None ) -> Dict[str, Any]
- test_connection · method · L438-L440 — async def test_connection(self) -> Dict[str, Any]
- get_status · method · L442-L467 — async def get_status(self) -> Dict[str, Any]
- get_dictionary · method · L477-L513 — async def get_dictionary(self, name: str) -> List\[Dict\[str,
  Any\]\]
- \_normalize_dictionary_row · method · L516-L525 — def \_normalize_dictionary_row(row: Dict[str, Any])
  -> Dict[str, Any]
- \_get_list · method · L531-L539 — async def \_get_list(self, endpoint: str, params: Dict[str, Any]) ->
  List\[Dict[str, Any]\]
- \_apply_page · method · L542-L549 — def \_apply_page(params: Dict[str, Any], page: Optional[int]) ->
  None
- get_users · method · L551-L557 — async def get_users(self, limit: int = 100, page: int = 1) ->
  List\[Dict[str, Any]\]
- get_user_by_id · method · L559-L561 — async def get_user_by_id(self, user_id: int) -> Dict[str, Any]
- create_user · method · L563-L571 — async def create_user( self, data: Optional\[Dict[str, Any]\] =
  None, \*\*kwargs, ) -> Dict[str, Any]
- update_user · method · L573-L581 — async def update_user( self, user_id: int, data:
  Optional\[Dict\[str, Any\]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- delete_user · method · L583-L585 — async def delete_user(self, user_id: int) -> Dict[str, Any]
- search_customers · method · L591-L595 — async def search_customers(self, sqlfilters: str, limit: int =
  20\) -> List\[Dict[str, Any]\]
- search_thirdparties · method · L597-L601 — async def search_thirdparties(self, sqlfilters: str, limit:
  int = 20) -> List\[Dict[str, Any]\]
- get_customers · method · L603-L609 — async def get_customers(self, limit: int = 100, page: int = 1) ->
  List\[Dict[str, Any]\]
- get_customer_by_id · method · L611-L613 — async def get_customer_by_id(self, customer_id: int) ->
  Dict[str, Any]
- create_customer · method · L615-L642 — async def create_customer( self, data: Optional\[Dict\[str,
  Any\]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- update_customer · method · L644-L658 — async def update_customer( self, customer_id: int, data:
  Optional\[Dict[str, Any]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- delete_customer · method · L660-L662 — async def delete_customer(self, customer_id: int) -> Dict\[str,
  Any\]
- search_products · method · L668-L672 — async def search_products(self, sqlfilters: str, limit: int =
  20\) -> List\[Dict[str, Any]\]
- get_products · method · L674-L678 — async def get_products(self, limit: int = 100) -> List\[Dict\[str,
  Any\]\]
- get_product_by_id · method · L680-L682 — async def get_product_by_id(self, product_id: int) ->
  Dict[str, Any]
- create_product · method · L684-L702 — async def create_product( self, data: Optional\[Dict[str, Any]\]
  = None, \*\*kwargs, ) -> Dict[str, Any]
- update_product · method · L704-L712 — async def update_product( self, product_id: int, data:
  Optional\[Dict[str, Any]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- delete_product · method · L714-L716 — async def delete_product(self, product_id: int) -> Dict[str, Any]
- get_invoices · method · L722-L755 — async def get_invoices( self, limit: int = 100, status:
  Optional[str] = None, customer_id: Optional[int] = None, page: int = 0, sortfield: Optional[str] =
  None, sortorder: Optional[str] = None, ) -> List\[Dict[str, Any]\]
- get_invoice_by_id · method · L757-L775 — async def get_invoice_by_id(self, invoice_id: int) ->
  Dict[str, Any]
- \_map_line_vat · method · L778-L786 — def \_map_line_vat(line: Dict[str, Any]) -> None
- create_invoice · method · L788-L817 — async def create_invoice( self, data: Optional\[Dict[str, Any]\]
  = None, \*\*kwargs, ) -> Dict[str, Any]
- update_invoice · method · L819-L827 — async def update_invoice( self, invoice_id: int, data:
  Optional\[Dict[str, Any]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- delete_invoice · method · L829-L831 — async def delete_invoice(self, invoice_id: int) -> Dict[str, Any]
- add_invoice_line · method · L833-L866 — async def add_invoice_line( self, invoice_id: int, data:
  Optional\[Dict[str, Any]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- \_invoice_line_ids · method · L868-L876 — async def \_invoice_line_ids(self, invoice_id: int) ->
  Optional[set]
- update_invoice_line · method · L878-L910 — async def update_invoice_line( self, invoice_id: int,
  line_id: int, data: Optional\[Dict[str, Any]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- delete_invoice_line · method · L939-L941 — async def delete_invoice_line(self, invoice_id: int,
  line_id: int) -> Dict[str, Any]
- validate_invoice · method · L943-L946 — async def validate_invoice(self, invoice_id: int, warehouse_id:
  int = 0, not_trigger: int = 0) -> Dict[str, Any]
- \_date_to_epoch · method · L967-L971 — def \_date_to_epoch(value: Any) -> Any
- \_date_to_epoch_utc · method · L974-L983 — def \_date_to_epoch_utc(value: Any) -> Any
- \_to_date_str · method · L986-L998 — def \_to_date_str(value: Any) -> str
- \_to_int · method · L1001-L1006 — def \_to_int(value: Any) -> Optional[int]
- \_check_supplier_line_payload · method · L1008-L1021 — def \_check_supplier_line_payload(self, payload:
  Dict[str, Any]) -> Dict[str, Any]
- get_supplier_invoices · method · L1023-L1054 — async def get_supplier_invoices( self, limit: int = 100,
  sqlfilters: Optional[str] = None, page: int = 0, sortfield: Optional[str] = None, sortorder:
  Optional[str] = None, ) -> List\[Dict[str, Any]\]
- create_supplier_invoice · method · L1056-L1083 — async def create_supplier_invoice( self, data:
  Optional\[Dict[str, Any]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- add_supplier_invoice_line · method · L1085-L1116 — async def add_supplier_invoice_line( self,
  invoice_id: int, data: Optional\[Dict[str, Any]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- update_supplier_invoice_line · method · L1118-L1146 — async def update_supplier_invoice_line( self,
  invoice_id: int, line_id: int, data: Optional\[Dict[str, Any]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- validate_supplier_invoice · method · L1148-L1151 — async def validate_supplier_invoice(self,
  invoice_id: int, warehouse_id: int = 0, not_trigger: int = 0) -> Dict[str, Any]
- set_supplier_invoice_to_draft · method · L1153-L1164 — async def set_supplier_invoice_to_draft(self,
  invoice_id: int) -> Dict[str, Any]
- set_supplier_invoice_paid · method · L1166-L1177 — async def set_supplier_invoice_paid(self,
  invoice_id: int) -> Dict[str, Any]
- update_supplier_invoice · method · L1179-L1187 — async def update_supplier_invoice( self, invoice_id:
  int, data: Optional\[Dict[str, Any]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- \_is_supplier_templates_absent · method · L1200-L1210 — def \_is_supplier_templates_absent(exc:
  DolibarrAPIError) -> bool
- \_request_supplier_templates · method · L1212-L1229 — async def \_request_supplier_templates(self,
  method: str, path: str = "", \*\*kwargs) -> Any
- get_supplier_invoice_templates · method · L1231-L1244 — async def get_supplier_invoice_templates( self,
  limit: int = 100, supplier_id: Optional[int] = None, page: int = 0, ) -> List\[Dict[str, Any]\]
- get_supplier_invoice_template · method · L1246-L1252 — async def get_supplier_invoice_template(self,
  template_id: int) -> Dict[str, Any]
- update_supplier_invoice_template · method · L1254-L1264 — async def update_supplier_invoice_template(
  self, template_id: int, data: Optional\[Dict[str, Any]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- update_supplier_invoice_template_line · method · L1266-L1296 — async def
  update_supplier_invoice_template_line( self, template_id: int, line_id: int, data: Optional\[Dict\[str,
  Any\]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- \_resolve_payment_mode_id · method · L1302-L1322 — async def \_resolve_payment_mode_id(self,
  payment_mode: Any) -> int
- \_payment_datepaye · method · L1324-L1328 — def \_payment_datepaye(self, payment_date: Any) -> int
- \_closepaidinvoices_param · method · L1331-L1333 — def \_closepaidinvoices_param(closepaidinvoices:
  bool) -> str
- pay_invoice · method · L1335-L1397 — async def pay_invoice( self, invoice_id: int, payment_mode: Any,
  accountid: Optional[int] = None, direction: str = "customer", payment_date: Any = None,
  closepaidinvoices: bool = True, num_payment: Optional[str] = None, comment: Optional[str] = None,
  amount: Optional[float] = None, ) -> Dict[str, Any]
- pay_supplier_invoice · method · L1399-L1429 — async def pay_supplier_invoice( self, invoice_id: int,
  amount: float, payment_mode: Any, accountid: Optional[int] = None, payment_date: Any = None,
  closepaidinvoices: bool = True, num_payment: Optional[str] = None, comment: Optional[str] = None, ) ->
  Dict[str, Any]
- get_invoice_payments · method · L1431-L1461 — async def get_invoice_payments(self, invoice_id: int,
  direction: str = "customer") -> List\[Dict[str, Any]\]
- \_bank_line_accounts · method · L1463-L1489 — async def \_bank_line_accounts(self, line_ids: set) ->
  Dict[str, Any]
- get_bank_accounts · method · L1495-L1497 — async def get_bank_accounts(self, limit: int = 100) ->
  List\[Dict[str, Any]\]
- \_escape_like_value · method · L1500-L1509 — def \_escape_like_value(value: str) -> str
- \_compact_bank_line · method · L1512-L1522 — def \_compact_bank_line(row: Dict[str, Any]) -> Dict\[str,
  Any\]
- get_bank_lines · method · L1524-L1566 — async def get_bank_lines( self, account_id: int, from_date:
  Optional[Any] = None, to_date: Optional[Any] = None, label_contains: Optional[str] = None, num_releve:
  Optional[str] = None, amount: Optional[float] = None, limit: int = 100, ) -> List\[Dict[str, Any]\]
- get_bank_line_links · method · L1568-L1586 — async def get_bank_line_links(self, account_id: int,
  line_ids: List[int]) -> Dict\[str, List\[Dict[str, Any]\]\]
- \_links · function · L1576-L1583 — async def \_links(line_id: int) -> List\[Dict[str, Any]\]
- create_bank_line · method · L1588-L1611 — async def create_bank_line( self, account_id: int, data:
  Optional\[Dict[str, Any]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- get_orders · method · L1617-L1624 — async def get_orders(self, limit: int = 100, status: Optional[str]
  = None) -> List\[Dict[str, Any]\]
- get_order_by_id · method · L1626-L1628 — async def get_order_by_id(self, order_id: int) -> Dict\[str,
  Any\]
- create_order · method · L1630-L1638 — async def create_order( self, data: Optional\[Dict[str, Any]\] =
  None, \*\*kwargs, ) -> Dict[str, Any]
- update_order · method · L1640-L1648 — async def update_order( self, order_id: int, data:
  Optional\[Dict[str, Any]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- delete_order · method · L1650-L1652 — async def delete_order(self, order_id: int) -> Dict[str, Any]
- get_contacts · method · L1658-L1662 — async def get_contacts(self, limit: int = 100) ->
  List\[Dict\[str, Any\]\]
- get_contact_by_id · method · L1664-L1666 — async def get_contact_by_id(self, contact_id: int) ->
  Dict[str, Any]
- create_contact · method · L1668-L1676 — async def create_contact( self, data: Optional\[Dict\[str,
  Any\]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- update_contact · method · L1678-L1692 — async def update_contact( self, contact_id: int, data:
  Optional\[Dict[str, Any]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- delete_contact · method · L1694-L1696 — async def delete_contact(self, contact_id: int) -> Dict\[str,
  Any\]
- get_projects · method · L1702-L1709 — async def get_projects(self, limit: int = 100, page: int = 1,
  status: Optional[int] = None) -> List\[Dict[str, Any]\]
- get_project_by_id · method · L1711-L1713 — async def get_project_by_id(self, project_id: int) ->
  Dict[str, Any]
- search_projects · method · L1715-L1719 — async def search_projects(self, sqlfilters: str, limit: int =
  20\) -> List\[Dict[str, Any]\]
- create_project · method · L1721-L1732 — async def create_project(self, data: Optional\[Dict[str, Any]\]
  = None, \*\*kwargs) -> Dict[str, Any]
- update_project · method · L1734-L1737 — async def update_project(self, project_id: int, data:
  Optional\[Dict[str, Any]\] = None, \*\*kwargs) -> Dict[str, Any]
- delete_project · method · L1739-L1741 — async def delete_project(self, project_id: int) -> Dict\[str,
  Any\]
- \_timespent_date · method · L1748-L1769 — def \_timespent_date(value: Any) -> str
- \_clean_timespent_line · method · L1772-L1786 — def \_clean_timespent_line(row: Dict[str, Any]) ->
  Dict[str, Any]
- create_task · method · L1788-L1797 — async def create_task(self, project_id: int, ref: str, label: str,
  \*\*kwargs) -> Dict[str, Any]
- add_task_time · method · L1799-L1806 — async def add_task_time(self, task_id: int, date: Any, duration:
  int, user_id: int = 0, note: str = "") -> Dict[str, Any]
- update_task_time · method · L1808-L1813 — async def update_task_time( self, task_id: int, timespent_id:
  int, date: Any, duration: int, user_id: int = 0, note: str = "" ) -> Dict[str, Any]
- delete_task_time · method · L1815-L1817 — async def delete_task_time(self, task_id: int, timespent_id:
  int) -> Dict[str, Any]
- get_task_timespent · method · L1819-L1839 — async def get_task_timespent(self, task_id: int) ->
  Dict[str, Any]
- get_project_timespent · method · L1841-L1878 — async def get_project_timespent(self, project_id: int)
  -> Dict[str, Any]
- \_compact_intervention · method · L1885-L1894 — def \_compact_intervention(row: Dict[str, Any]) ->
  Dict[str, Any]
- \_compact_intervention_line · method · L1897-L1906 — def \_compact_intervention_line(row: Dict\[str,
  Any\]) -> Dict[str, Any]
- \_epoch_date · method · L1909-L1922 — def \_epoch_date(value: Any) -> int
- get_interventions · method · L1924-L1929 — async def get_interventions(self, limit: int = 100, page:
  int = 1) -> List\[Dict[str, Any]\]
- get_intervention · method · L1931-L1941 — async def get_intervention(self, intervention_id: int) ->
  Dict[str, Any]
- create_intervention · method · L1943-L1953 — async def create_intervention(self, project_id: int,
  socid: int, description: str, \*\*kwargs) -> Dict[str, Any]
- add_intervention_line · method · L1955-L1970 — async def add_intervention_line(self, intervention_id:
  int, description: str, date: Any, duration: int) -> Dict[str, Any]
- \_resolve_event_type_code · method · L1976-L2003 — async def \_resolve_event_type_code(self,
  event_type: Any) -> str
- create_agenda_event · method · L2005-L2047 — async def create_agenda_event( self, label: str, datep:
  Any, datef: Any, userownerid: int, type_code: Optional[Any] = None, type: Optional[int] = None, socid:
  Optional[int] = None, percentage: Optional[int] = None, note: Optional[str] = None, contact_id:
  Optional[int] = None, ) -> Dict[str, Any]
- get_agenda_event · method · L2049-L2051 — async def get_agenda_event(self, event_id: int) -> Dict\[str,
  Any\]
- get_documents · method · L2057-L2083 — async def get_documents( self, modulepart: str, ref:
  Optional[str] = None, id: Optional[int] = None, subdir: Optional[str] = None, ) -> List\[Dict\[str,
  Any\]\]
- \_file_op_modulepart · method · L2086-L2092 — def \_file_op_modulepart(modulepart: str) -> str
- download_document · method · L2094-L2097 — async def download_document(self, modulepart: str,
  original_file: str) -> Dict[str, Any]
- \_document_original_file · method · L2100-L2124 — def \_document_original_file( modulepart: str,
  filename: str, ref: Optional[str] = None, id: Optional[int] = None, subdir: Optional[str] = None, ) ->
  str
- upload_document · method · L2126-L2157 — async def upload_document( self, modulepart: str, filename:
  str, filecontent: str, ref: Optional[str] = None, id: Optional[int] = None, subdir: Optional[str] =
  None, overwrite: bool = True, ) -> Dict[str, Any]
- delete_document · method · L2159-L2170 — async def delete_document(self, modulepart: str,
  original_file: str) -> Dict[str, Any]
- get_tickets · method · L2176-L2190 — async def get_tickets(self, limit: int = 100, page: int = 1,
  status: Optional[int] = None) -> List\[Dict[str, Any]\]
- get_ticket_by_ref · method · L2192-L2198 — async def get_ticket_by_ref(self, ref: str) -> Dict\[str,
  Any\]
- search_tickets · method · L2200-L2203 — async def search_tickets(self, sqlfilters: str, limit: int =
  20\) -> List\[Dict[str, Any]\]
- create_ticket · method · L2205-L2220 — async def create_ticket(self, data: Optional\[Dict[str, Any]\] =
  None, \*\*kwargs) -> Dict[str, Any]
- \_resolve_ticket_track_id · method · L2222-L2237 — async def \_resolve_ticket_track_id(self, ref: str)
  -> str
- add_ticket_message · method · L2239-L2260 — async def add_ticket_message(self, data:
  Optional\[Dict[str, Any]\] = None, \*\*kwargs) -> Dict[str, Any]
- update_ticket · method · L2262-L2300 — async def update_ticket( self, ticket_id: Optional[int] = None,
  ref: Optional[str] = None, data: Optional\[Dict[str, Any]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- \_social_contribution_payload · method · L2310-L2332 — def \_social_contribution_payload(self, payload:
  Dict[str, Any]) -> Dict[str, Any]
- get_social_contributions · method · L2334-L2372 — async def get_social_contributions( self, limit: int
  = 100, page: int = 1, period: Optional[Any] = None, type: Optional[int] = None, paid: Optional[bool] =
  None, ) -> List\[Dict[str, Any]\]
- create_social_contribution · method · L2374-L2394 — async def create_social_contribution( self, data:
  Optional\[Dict[str, Any]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- update_social_contribution · method · L2396-L2407 — async def update_social_contribution( self,
  contribution_id: int, data: Optional\[Dict[str, Any]\] = None, \*\*kwargs, ) -> Dict[str, Any]
- add_social_contribution_payment · method · L2409-L2440 — async def add_social_contribution_payment(
  self, contribution_id: int, amount: float, payment_mode: Any, accountid: Optional[int] = None,
  payment_date: Any = None, num_payment: Optional[str] = None, note: Optional[str] = None,
  closepaidcontrib: bool = True, ) -> Dict[str, Any]
- dolibarr_raw_api · method · L2446-L2450 — async def dolibarr_raw_api( self, method: str, endpoint: str,
  params: Optional[Dict] = None, data: Optional[Dict] = None ) -> Dict[str, Any]
