from __future__ import annotations

from datetime import date

import httpx
import pytest

from app.providers.notification import (
    FeishuWebhookError,
    FeishuWebhookProvider,
    NotificationMessage,
)


class FakeResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        return self._body


class InvalidJsonResponse(FakeResponse):
    def json(self) -> object:
        raise ValueError("not-json")


class FakeClient:
    def __init__(
        self,
        response: FakeResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[str, dict[str, object], float]] = []

    async def post(self, url: str, *, json: dict[str, object], timeout: float) -> FakeResponse:
        self.calls.append((url, json, timeout))
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def message() -> NotificationMessage:
    return NotificationMessage(
        stock_name="贵州茅台",
        stock_code="600519",
        indicator="BOLL",
        state="BOLL_WIDTH_NARROWING",
        current_value=0.2,
        previous_value=0.3,
        date=date(2026, 9, 24),
    )


@pytest.mark.asyncio
async def test_feishu_provider_posts_text_payload_with_injected_client() -> None:
    client = FakeClient(FakeResponse(200, {"code": 0, "msg": "success"}))
    provider = FeishuWebhookProvider(
        "https://open.feishu.cn/open-apis/bot/v2/hook/secret-token",
        client=client,
        timeout_seconds=1.25,
    )

    await provider.send(None, message())

    assert len(client.calls) == 1
    url, payload, timeout = client.calls[0]
    assert url.endswith("secret-token")
    assert timeout == 1.25
    assert payload["msg_type"] == "text"
    assert "600519" in payload["content"]["text"]


@pytest.mark.asyncio
async def test_feishu_provider_reads_user_webhook_and_supports_string_message() -> None:
    client = FakeClient(FakeResponse(200, {"StatusCode": 0}))
    provider = FeishuWebhookProvider(client=client)

    await provider.send(
        {"settings": {"feishu_webhook_url": "https://example.test/hook?token=secret"}},
        "hello",
    )

    assert client.calls[0][0].startswith("https://example.test/")
    assert client.calls[0][1]["content"] == {"text": "hello"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"msg": "success"},
        [],
        "{\"code\":0}",
        None,
        {"code": 1},
        {"StatusCode": 1},
        {"code": "0"},
        {"code": False},
    ],
)
async def test_feishu_provider_requires_explicit_numeric_zero_business_code(body: object) -> None:
    client = FakeClient(FakeResponse(200, body))
    provider = FeishuWebhookProvider("https://example.test/hook", client=client)

    with pytest.raises(FeishuWebhookError):
        await provider.send(None, message())


@pytest.mark.asyncio
async def test_feishu_provider_rejects_invalid_json_body() -> None:
    client = FakeClient(InvalidJsonResponse(200, object()))
    provider = FeishuWebhookProvider("https://example.test/hook", client=client)

    with pytest.raises(FeishuWebhookError, match="invalid response"):
        await provider.send(None, message())


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 500])
async def test_feishu_provider_rejects_non_2xx_without_leaking_webhook(
    status_code: int,
) -> None:
    token = "super-secret-token"
    client = FakeClient(FakeResponse(status_code, {"error": token}))
    provider = FeishuWebhookProvider(f"https://example.test/hook?token={token}", client=client)

    with pytest.raises(FeishuWebhookError) as raised:
        await provider.send(None, message())

    assert raised.value.status_code == status_code
    assert token not in str(raised.value)


@pytest.mark.asyncio
async def test_feishu_provider_rejects_feishu_business_error_without_leaking_body() -> None:
    token = "business-secret"
    client = FakeClient(FakeResponse(200, {"code": 19001, "msg": token}))
    provider = FeishuWebhookProvider(f"https://example.test/hook?token={token}", client=client)

    with pytest.raises(FeishuWebhookError) as raised:
        await provider.send(None, message())

    assert raised.value.business_code == 19001
    assert token not in str(raised.value)


@pytest.mark.asyncio
async def test_feishu_provider_wraps_timeout_and_transport_errors() -> None:
    timeout_client = FakeClient(error=httpx.ReadTimeout("timed out"))
    provider = FeishuWebhookProvider("https://example.test/hook", client=timeout_client)

    with pytest.raises(FeishuWebhookError, match="timed out"):
        await provider.send(None, message())

    transport_client = FakeClient(error=httpx.ConnectError("https://secret.invalid/hook"))
    provider = FeishuWebhookProvider("https://example.test/hook", client=transport_client)

    with pytest.raises(FeishuWebhookError, match="request failed") as raised:
        await provider.send(None, message())
    assert "secret.invalid" not in str(raised.value)
