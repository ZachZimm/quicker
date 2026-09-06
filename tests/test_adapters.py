import json

import httpx
import pytest
from quicker.contracts import ModelConfig
from quicker.extraction import ModelError, VisionAdapter
from quicker.review import signed_minor


def test_money_is_exact_and_missing_or_nonfinite_is_not_guessed():
    assert signed_minor("1,200.00") == -120000
    assert signed_minor("(12.34)", refund=True) == 1234
    for value in ["NaN", "Infinity", "1.005", None, "not an amount"]:
        assert signed_minor(value) is None


@pytest.mark.parametrize("protocol", ["chat-completions", "responses"])
def test_protocol_adapters_omit_absent_key_and_parse_content(monkeypatch, protocol):
    captured = []

    def handler(request):
        captured.append(request)
        assert "authorization" not in request.headers
        assert request.url.host == "models.example"
        assert request.url.port == 4321
        body = json.loads(request.content)
        assert body["model"] == "chosen-model"
        if protocol == "chat-completions":
            assert body["messages"][1]["content"][1]["type"] == "image_url"
            return httpx.Response(
                200, json={"choices": [{"finish_reason": "stop", "message": {"content": "QUICKER 284"}}]}
            )
        assert body["input"][0]["content"][1]["type"] == "input_image"
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": "QUICKER 284"}]}],
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)
    )
    config = ModelConfig(host="models.example", port=4321, model="chosen-model", protocol=protocol)
    assert VisionAdapter(config).check()["vision"] is True
    assert len(captured) == 1


def test_truncated_response_never_becomes_partial_transactions(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"document_type":"invoice","transactions":[]}'},
                    }
                ]
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)
    )
    with pytest.raises(ModelError, match="truncated"):
        VisionAdapter(ModelConfig()).complete("Read this", [b"test"])
