"""Synthetic, side-effect-free adapter for a copyable read-only report.

A company implementation must query its approved bind with a parameterized
tenant predicate and a bounded result. This fixture does not connect to Oracle,
create SQLite state, execute client SQL, or implement private company schemas.
"""


class SyntheticInspectionRepository:
    is_demo = True

    def rows_for_org(self, organization):
        # Select the tenant before generating rows. Every tenant intentionally
        # has the same sample IDs, so an ID cannot stand in for authorization.
        if organization not in ('A', 'B'):
            return
        for index in range(1, 33):
            yield dict(org=organization, sample_id='sample-{:04d}'.format(index),
                       inspection_date='2026-10-{:02d}'.format((index - 1) % 28 + 1),
                       department='Assembly' if index % 2 else 'Laboratory',
                       product='{} synthetic product {:02d}'.format(organization, index),
                       inspected=100 + index, rejected=index % 5)
