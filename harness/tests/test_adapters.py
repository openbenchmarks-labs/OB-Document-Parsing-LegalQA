from harness.adapters.common import redact


def test_provider_exchange_redacts_secrets():
    assert redact({"Authorization": "secret", "x": "ok"}) == {
        "Authorization": "<redacted>", "x": "ok"
    }


def test_unstructured_key_header_is_redacted():
    assert redact({"unstructured-api-key": "secret", "Prefer": "wait=0"}) == {
        "unstructured-api-key": "<redacted>", "Prefer": "wait=0"
    }


def test_fable_load_bedrock_environment_has_no_external_import():
    from harness.adapters import fable

    fable._load_bedrock_environment()
