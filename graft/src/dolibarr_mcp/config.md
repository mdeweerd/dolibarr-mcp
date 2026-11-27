# src/dolibarr_mcp/config.py

- Config · class · L15-L289 — class Config(BaseSettings)
- validate_dolibarr_url · method · L107-L141 — def validate_dolibarr_url(cls, v: str) -> str
- validate_api_key · method · L145-L166 — def validate_api_key(cls, v: str) -> str
- validate_log_level · method · L170-L179 — def validate_log_level(cls, v: str) -> str
- validate_transport · method · L183-L191 — def validate_transport(cls, v: str) -> str
- validate_http_host · method · L195-L197 — def validate_http_host(cls, v: str) -> str
- cors_origins · method · L200-L202 — def cors_origins(self) -> List[str]
- http_host_is_loopback · method · L205-L207 — def http_host_is_loopback(self) -> bool
- validate_http_port · method · L211-L219 — def validate_http_port(cls, v: int) -> int
- from_env · method · L222-L266 — def from_env(cls) -> "Config"
- validate_config · method · L268-L279 — def validate_config(self) -> None
- api_key · method · L282-L284 — def api_key(self) -> str
- api_key · method · L287-L289 — def api_key(self, value: str) -> None
