from __future__ import annotations

import json
import logging

from llamawebui.services.logging_utils import JsonFormatter, RedactionFilter, redact_text


def test_redact_text_removes_representative_secret_formats() -> None:
    text = (
        "Authorization: Bearer lwui_very_secret_value "
        "HF_TOKEN=hf_private_value key=custom-secret"
    )

    redacted = redact_text(text, secrets=("custom-secret",))

    assert "lwui_very_secret_value" not in redacted
    assert "hf_private_value" not in redacted
    assert "custom-secret" not in redacted
    assert redacted.count("[REDACTED]") == 3


def test_filter_and_formatter_emit_structured_redacted_record() -> None:
    record = logging.LogRecord(
        "llamawebui.router",
        logging.INFO,
        __file__,
        1,
        "router ready Authorization: Bearer raw-token",
        (),
        None,
    )
    record.component = "router"
    record.event = "ready"
    record.endpoint = "http://127.0.0.1:1234"
    record.secret_field = "hf_private_value"

    redactor = RedactionFilter(("raw-token", "hf_private_value"))
    assert redactor.filter(record)
    rendered = JsonFormatter().format(record)
    payload = json.loads(rendered)

    assert payload["level"] == "INFO"
    assert payload["component"] == "router"
    assert payload["event"] == "ready"
    assert payload["endpoint"] == "http://127.0.0.1:1234"
    assert "raw-token" not in rendered
    assert "hf_private_value" not in rendered
    assert "timestamp" in payload


def test_router_log_tail_redacts_secret_values() -> None:
    from llamawebui.services.router_supervisor import RouterSupervisor

    supervisor = RouterSupervisor()
    supervisor._logs.append(redact_text("api-key=hf_private_value"))

    assert supervisor.logs == ("api-key=[REDACTED]",)
