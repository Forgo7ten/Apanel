"""Import target for the Celery worker process."""

# Importing the module registers task decorators in a worker process.  The
# scheduler itself only needs task names and therefore does not import this.
from . import jobs as _jobs  # noqa: F401,E402
from .celery_app import celery_app

__all__ = ["celery_app"]
