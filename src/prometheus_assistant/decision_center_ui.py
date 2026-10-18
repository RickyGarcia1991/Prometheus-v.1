"""Owner-scoped read-only attention projection; never arms or dispatches work."""
import time
from copy import deepcopy

from . import scheduled_syntax_ui
from .decision_center import build_snapshot
from .scheduled_syntax import public_job


def snapshot(app, *, grant_owner, binding):
    # Hold authentication through the entire projection. Do not use the queue
    # status endpoint: that endpoint intentionally arms automatic resumption.
    with grant_owner() as owner:
        approval = app.action_approvals.peek(owner=owner, binding=binding)
        try:
            jobs = [public_job(row) for row in scheduled_syntax_ui.queue(app, owner).list()]
        except (OSError, ValueError, RuntimeError):
            jobs = None
        with app.lock:
            try:
                activity = app.activity_history.recent()
            except (OSError, ValueError, RuntimeError):
                activity = None
            age = time.monotonic() - getattr(app, '_decision_resource_cached_at', float('-inf'))
            resources = deepcopy(getattr(app, '_decision_resource_status', None)) if 0 <= age <= 15 else None
        return build_snapshot(approval=approval, jobs=jobs, activity=activity,
                              resources=resources, now=time.time())
