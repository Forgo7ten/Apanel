"""Regression tests for Compose build networking and service wiring."""

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
BUILD_SERVICE_NAMES = (
    "migrate",
    "market-data-hub",
    "security-bootstrap",
    "backend",
    "scheduler",
    "worker",
    "frontend",
)
PROXY_ENV_KEYS = {
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
}
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
        command_environment = os.environ.copy()
        command_environment.pop("DOCKER_BUILD_NETWORK", None)
        command_environment.pop("PYPI_MIRROR_URL", None)
        command_environment.update(values)
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
            env=command_environment,
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        pytest.fail("docker compose config failed for Compose wiring fixture")
    return json.loads(result.stdout)


def _service(config: dict[str, object], service_name: str) -> dict[str, object]:
    services = config["services"]
    assert isinstance(services, dict)
    service = services[service_name]
    assert isinstance(service, dict)
    return service


def _service_environment(config: dict[str, object], service_name: str) -> dict[str, str]:
    environment = _service(config, service_name).get("environment", {})
    assert isinstance(environment, dict)
    return environment


def test_build_network_defaults_to_host_for_every_built_service(
    compose_config_available: None,
) -> None:
    config = _render_compose_config({})

    for service_name in BUILD_SERVICE_NAMES:
        build = _service(config, service_name)["build"]
        assert isinstance(build, dict)
        assert build["network"] == "host"
        assert build["args"] == {"PYPI_MIRROR_URL": ""}


def test_build_network_can_be_overridden_to_default(
    compose_config_available: None,
) -> None:
    config = _render_compose_config({"DOCKER_BUILD_NETWORK": "default"})

    for service_name in BUILD_SERVICE_NAMES:
        build = _service(config, service_name)["build"]
        assert isinstance(build, dict)
        assert build["network"] == "default"
        assert build["args"] == {"PYPI_MIRROR_URL": ""}


def test_python_dockerfiles_use_the_domestic_pypi_mirror() -> None:
    mirror_command = 'pip config set global.index-url "$PYPI_MIRROR_URL"'
    for dockerfile in (ROOT / "backend" / "Dockerfile", ROOT / "market-data-hub" / "Dockerfile"):
        contents = dockerfile.read_text(encoding="utf-8")
        assert "ARG PYPI_MIRROR_URL=" in contents
        assert mirror_command in contents


def test_pypi_mirror_url_is_forwarded_to_every_python_build(
    compose_config_available: None,
) -> None:
    mirror_url = "https://mirror.example/simple"
    config = _render_compose_config({"PYPI_MIRROR_URL": mirror_url})

    for service_name in BUILD_SERVICE_NAMES:
        build = _service(config, service_name)["build"]
        assert isinstance(build, dict)
        assert build["args"] == {"PYPI_MIRROR_URL": mirror_url}


def test_proxy_environment_and_host_gateway_are_not_configured(
    compose_config_available: None,
) -> None:
    config = _render_compose_config({})

    services = config["services"]
    assert isinstance(services, dict)
    for service_name in services:
        service_environment = _service_environment(config, service_name)
        assert PROXY_ENV_KEYS.isdisjoint(service_environment)
        assert "extra_hosts" not in _service(config, service_name)


def test_market_data_healthcheck_uses_the_canonical_path_only(
    compose_config_available: None,
) -> None:
    config = _render_compose_config({})
    healthcheck = _service(config, "market-data-hub")["healthcheck"]
    assert isinstance(healthcheck, dict)
    command = healthcheck["test"]
    assert isinstance(command, list)
    assert "/health" in command[1]
    assert "/api/health" not in command[1]
    assert "/api/v1/health" not in command[1]
