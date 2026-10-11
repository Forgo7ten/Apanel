"""Bounded broker publication and post-response scheduling regression tests."""

from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import BackgroundTasks

from app.tasks import jobs
from app.tasks.bootstrap_scheduler import DeferredSecurityBootstrapScheduler


def test_scheduler_defers_publication_without_contacting_broker():
    tasks = BackgroundTasks()
    scheduler = DeferredSecurityBootstrapScheduler(tasks)
    scheduler.enqueue("600519")
    assert len(tasks.tasks) == 1
    assert tasks.tasks[0].args == ("600519",)
    assert tasks.tasks[0].func is jobs.enqueue_security_bootstrap


def test_enqueue_uses_single_nonretrying_publish_and_swallows_broker_outage(monkeypatch):
    monkeypatch.setattr(jobs, "get_settings", lambda: SimpleNamespace(app_env="production"))
    publish = Mock(side_effect=OSError("broker offline"))
    monkeypatch.setattr(jobs.bootstrap_security_data, "apply_async", publish)
    jobs.enqueue_security_bootstrap("600519")
    publish.assert_called_once_with(args=("600519",), retry=False)


def test_test_environment_does_not_enqueue(monkeypatch):
    monkeypatch.setattr(jobs, "get_settings", lambda: SimpleNamespace(app_env="test"))
    publish = Mock()
    monkeypatch.setattr(jobs.bootstrap_security_data, "apply_async", publish)
    jobs.enqueue_security_bootstrap("600519")
    publish.assert_not_called()


def test_successful_publish_uses_bounded_options(monkeypatch):
    monkeypatch.setattr(jobs, "get_settings", lambda: SimpleNamespace(app_env="production"))
    publish = Mock()
    monkeypatch.setattr(jobs.bootstrap_security_data, "apply_async", publish)
    jobs.enqueue_security_bootstrap("600519")
    publish.assert_called_once_with(args=("600519",), retry=False)
