# tests/test_documents.py

- \_pdf_bytes · function · L13-L20 — def \_pdf_bytes(attachment: bytes | None = None) -> bytes
- \_mock_client · function · L23-L26 — def \_mock_client(MockClient)
- \_download_payload · function · L29-L36 — def \_download_payload(data: bytes, mime: str =
  "application/pdf", filename: str = "FA1.pdf") -> dict
- test_document_tools_are_read_only · function · L39-L41 — def test_document_tools_are_read_only()
- test_get_documents_summarizes_entries · function · L45-L73 — async def
  test_get_documents_summarizes_entries()
- test_get_documents_requires_ref_or_id · function · L77-L85 — async def
  test_get_documents_requires_ref_or_id()
- test_download_facturx_extracts_embedded_xml · function · L89-L101 — async def
  test_download_facturx_extracts_embedded_xml()
- test_download_without_file_returns_only_metadata · function · L105-L116 — async def
  test_download_without_file_returns_only_metadata()
- test_download_text_file_returned_as_text · function · L120-L127 — async def
  test_download_text_file_returned_as_text()
- test_download_rejects_oversized_file · function · L131-L141 — async def
  test_download_rejects_oversized_file()
