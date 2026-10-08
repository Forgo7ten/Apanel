"""Import target for the Celery worker process."""

from app.core.config import get_settings

from .celery_app import celery_app

get_settings().validate_worker_credentials()

# Importing the module registers task decorators in a worker process.  The
# scheduler itself only needs task names and therefore does not import this.
from . import jobs as _jobs  # noqa: F401,E402

__all__ = ["celery_app"]
