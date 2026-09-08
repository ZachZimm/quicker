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


def test_visual_exclusion_checks_each_page_without_dropping_uncertain_rows(monkeypatch):
    calls = []
    replies = iter(
        [
            json.dumps(
                {
                    "document_type": "credit_card",
                    "transactions": [
                        {"kind": "purchase", "payee": "A", "amount": "12.34", "page": 2, "crossed_out": True},
                        {"kind": "purchase", "payee": "B", "amount": "56.78", "page": 1},
                        {"kind": "purchase", "payee": "C", "amount": "90.12", "page": 1},
                    ],
                }
            ),
            '{"crossed_out": [1, 2], "uncertain": [2]}',
            '{"crossed_out": []}',
        ]
    )

    def complete(self, prompt, images, *args):
        calls.append(images)
        return next(replies)

    monkeypatch.setattr(VisionAdapter, "complete", complete)
    result = VisionAdapter(ModelConfig()).extract([b"first", b"second"], {"categories": [], "tags": []})
    assert calls == [[b"first", b"second"], [b"first"], [b"second"]]
    assert [row.crossed_out for row in result.transactions] == [False, True, False]
    assert "unsure" in result.transactions[2].warnings[0]


@pytest.mark.parametrize(
    "bad_check",
    [
        '{"crossed_out": [1]}',  # This row belongs to the other page.
        '{"crossed_out": [99]}',
        '{"crossed_out": [], "uncertain": [-1]}',
        '{"crossed_out": [true]}',
        "not json",
        "```",
    ],
)
def test_invalid_visual_exclusion_fails_extraction(monkeypatch, bad_check):
    replies = iter(
        [
            json.dumps(
                {
                    "document_type": "invoice",
                    "transactions": [
                        {"kind": "invoice", "page": 1},
                        {"kind": "invoice", "page": 2},
                    ],
                }
            ),
            bad_check,
        ]
    )
    monkeypatch.setattr(VisionAdapter, "complete", lambda *args: next(replies))
    with pytest.raises(ModelError, match="crossed-out item check"):
        VisionAdapter(ModelConfig()).extract([b"first", b"second"], {"categories": [], "tags": []})


def test_native_adapter_uses_explicit_reasoning_and_rejects_output_limit(monkeypatch):
    limited = False

    def handler(request):
        body = json.loads(request.content)
        assert request.url.path == "/api/v1/chat"
        assert body["reasoning"] == "off" and body["store"] is False
        assert body["input"][0]["type"] == "text"
        assert body["input"][1]["type"] == "image"
        assert body["max_output_tokens"] == 4000
        return httpx.Response(
            200,
            json={
                "output": [{"type": "message", "content": "QUICKER 284"}],
                "stats": {"total_output_tokens": 4000 if limited else 8},
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)
    )
    config = ModelConfig(protocol="lm-studio", base_path="/api/v1", reasoning="off", output_limit=4000)
    assert VisionAdapter(config).check()["vision"]
    limited = True
    with pytest.raises(ModelError, match="output limit"):
        VisionAdapter(config).check()
