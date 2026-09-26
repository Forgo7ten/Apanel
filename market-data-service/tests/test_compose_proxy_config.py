"""Regression tests for the market-data-only outbound proxy wiring."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "compose.yaml"
PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
INTERNAL_HOSTS = ("postgres", "redis", "backend", "market-data-service")
REQUIRED_ENV = {
    "APP_ENV": "test",
    "POSTGRES_PASSWORD": "test-only-password",
    "JWT_SECRET_KEY": "test-only-jwt-secret",
    "INTERNAL_API_TOKEN": "test-only-internal-token",
    "NODE_ENV": "test",
}


def _compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "compose", "version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


@pytest.fixture(scope="module")
def compose_config_available() -> None:
    if not _compose_available():
        pytest.skip("docker compose is required for Compose wiring regression tests")


def _render_compose_config(overrides: dict[str, str]) -> dict[str, object]:
    values = {**REQUIRED_ENV, **overrides}
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="apanel-compose-",
        suffix=".env",
    ) as env_file:
        for key, value in values.items():
            env_file.write(f"{key}={value}\n")
        env_file.flush()
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                env_file.name,
                "-f",
                str(COMPOSE_FILE),
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        pytest.fail("docker compose config failed for proxy regression fixture")
    return json.loads(result.stdout)


def _service_environment(config: dict[str, object], service_name: str) -> dict[str, str]:
    services = config["services"]
    assert isinstance(services, dict)
    service = services[service_name]
    assert isinstance(service, dict)
    environment = service.get("environment", {})
    assert isinstance(environment, dict)
    return environment


def _assert_proxy_is_scoped_to_market_data(config: dict[str, object]) -> None:
    services = config["services"]
    assert isinstance(services, dict)
    market_data_environment = _service_environment(config, "market-data-service")
    assert set(PROXY_ENV_KEYS).issubset(market_data_environment)
    for service_name in services:
        if service_name == "market-data-service":
            continue
        service_environment = _service_environment(config, service_name)
        assert set(PROXY_ENV_KEYS).isdisjoint(service_environment)


def test_default_proxy_values_are_empty_and_internal_hosts_bypass_proxy(
    compose_config_available: None,
) -> None:
    config = _render_compose_config(
        {
            "MARKET_DATA_HTTP_PROXY": "",
            "MARKET_DATA_HTTPS_PROXY": "",
            "MARKET_DATA_ALL_PROXY": "",
            "MARKET_DATA_NO_PROXY": "",
        }
    )

    market_data_environment = _service_environment(config, "market-data-service")
    assert market_data_environment["HTTP_PROXY"] == ""
    assert market_data_environment["HTTPS_PROXY"] == ""
    assert market_data_environment["ALL_PROXY"] == ""
    assert set(INTERNAL_HOSTS).issubset(set(market_data_environment["NO_PROXY"].split(",")))
    _assert_proxy_is_scoped_to_market_data(config)

    services = config["services"]
    assert isinstance(services, dict)
    market_data_service = services["market-data-service"]
    assert isinstance(market_data_service, dict)
    assert "host.docker.internal=host-gateway" in market_data_service["extra_hosts"]


def test_custom_proxy_values_remain_scoped_and_extend_no_proxy(
    compose_config_available: None,
) -> None:
    proxy_values = {
        "MARKET_DATA_HTTP_PROXY": "http://proxy-user:proxy-password@host.docker.internal:8123",
        "MARKET_DATA_HTTPS_PROXY": "http://host.docker.internal:8123",
        "MARKET_DATA_ALL_PROXY": "socks5://host.docker.internal:1080",
        "MARKET_DATA_NO_PROXY": "localhost,example.internal",
    }
    config = _render_compose_config(proxy_values)

    market_data_environment = _service_environment(config, "market-data-service")
    assert market_data_environment["HTTP_PROXY"] == proxy_values["MARKET_DATA_HTTP_PROXY"]
    assert market_data_environment["HTTPS_PROXY"] == proxy_values["MARKET_DATA_HTTPS_PROXY"]
    assert market_data_environment["ALL_PROXY"] == proxy_values["MARKET_DATA_ALL_PROXY"]
    no_proxy_values = set(market_data_environment["NO_PROXY"].split(","))
    assert {"localhost", "example.internal", *INTERNAL_HOSTS}.issubset(no_proxy_values)
    _assert_proxy_is_scoped_to_market_data(config)
