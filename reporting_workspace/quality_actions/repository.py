"""Isolated in-memory synthetic cases; never connects to company systems."""

from datetime import date, timedelta
from .contract import PRIORITIES


class SyntheticActionRepository:
    is_demo = True

    def rows_for_org(self, organization):
        if organization not in ('A', 'B'):
            return
        for index in range(1, 37):
            opened = date(2026, 8, 20) + timedelta(days=index)
            due = opened + timedelta(days=7 + index % 4)
            closed = opened + timedelta(days=5 + index % 12) if index % 3 == 0 else None
            yield dict(org=organization, case_id='action-{:04d}'.format(index),
                       opened_date=opened.isoformat(), due_date=due.isoformat(),
                       closed_date=closed.isoformat() if closed else '',
                       priority=PRIORITIES[(index - 1) % 3],
                       finding='{} synthetic finding {:02d}'.format(organization, index))
