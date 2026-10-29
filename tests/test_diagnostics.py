import asyncio
import base64
import io
import logging
from urllib.parse import quote


from bridge.diagnostics import REDACTOR, Redactor, SafeFormatter, debug_option, drain_output


def test_debug_option_uses_supervisor_boolean_and_defaults_off(tmp_path, monkeypatch):
    path = tmp_path / "options.json"
    monkeypatch.delenv("DEBUG_LOGGING", raising=False)
    assert debug_option(path) is False
    monkeypatch.setenv("DEBUG_LOGGING", "true")
    assert debug_option(path) is True
    path.write_text('{"debug_logging": false}')
    assert debug_option(path) is False
    path.write_text('{"debug_logging": true}')
    assert debug_option(path) is True
    path.write_text('{"debug_logging": "false"}')
    assert debug_option(path) is False
    path.write_text("invalid-json")
    assert debug_option(path) is False


def test_redaction_covers_urls_passwords_tokens_and_headers():
    r = Redactor()
    password = "complex+password&value"
    r.credentials("admin", password)
    r.add("private-token")
    encoded = base64.b64encode(("admin:" + password).encode()).decode()
    output = r.clean(
        f"https://admin:{quote(password, safe='')}@192.0.2.1/camera/private-token?auth=other-secret password={password} encoded={encoded}\nAuthorization: Digest secret-material\ntoken=private-token"
    )
    for secret in [password, quote(password, safe=""), encoded, "private-token", "other-secret", "secret-material"]:
        assert secret not in output
    assert "192.0.2.1" in output
    assert "redacted" in output


async def test_child_logs_are_redacted_before_logging_and_pipes_are_drained(caplog):
    REDACTOR.add("private-child-secret")
    stream = asyncio.StreamReader()
    stream.feed_data(b"encoder started\npassword=private-child-secret\n")
    stream.feed_eof()
    caplog.set_level(logging.DEBUG, logger="bridge.diagnostics.test")
    await drain_output(stream, logging.getLogger("bridge.diagnostics.test"), "FFmpeg test")
    assert "encoder started" in caplog.text
    assert "private-child-secret" not in caplog.text
    assert "[redacted]" in caplog.text


async def test_oversized_records_are_discarded_as_a_whole(caplog):
    stream = asyncio.StreamReader()
    stream.feed_data(b"x" * 12000 + b"private-tail\nnormal record\n")
    stream.feed_eof()
    caplog.set_level(logging.DEBUG, logger="bridge.diagnostics.test")
    await drain_output(stream, logging.getLogger("bridge.diagnostics.test"), "FFmpeg test")
    assert "private-tail" not in caplog.text
    assert "normal record" in caplog.text


def test_formatter_also_redacts_exception_details():
    REDACTOR.add("private-exception-secret")
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(SafeFormatter("%(message)s"))
    logger = logging.getLogger("safe-formatter-test")
    logger.addHandler(handler)
    try:
        try:
            raise ValueError("private-exception-secret")
        except ValueError:
            logger.exception("connection failed")
        assert "private-exception-secret" not in output.getvalue()
        assert "[redacted]" in output.getvalue()
    finally:
        logger.removeHandler(handler)
