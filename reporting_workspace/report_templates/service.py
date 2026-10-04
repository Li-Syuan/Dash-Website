"""Fresh identity checks, bounded tenant-scoped queries and safe CSV output."""

from collections.abc import Mapping
import csv
import io

from demo_services import AccessDenied
from ..authorization import current_identity
from ..errors import ProviderUnavailable
from .contract import (COLUMNS, MAX_EXPORT_BYTES, MAX_SOURCE_ROWS, POLICY,
                       Query, csv_cell, validated_row)


class InspectionReport:
    """Stateless service; no current actor, row cache, connection or request lives here."""

    def __init__(self, identities, repository):
        if not callable(getattr(identities, 'get_user', None)):
            raise TypeError('A current identity provider is required.')
        if not callable(getattr(repository, 'rows_for_org', None)):
            raise TypeError('An explicit report repository is required.')
        self.identities, self.repository = identities, repository

    def _authorize(self, supplied):
        actor = current_identity(self.identities, supplied)
        if not POLICY.allows(actor):
            raise AccessDenied('Report access denied.')
        return actor

    def _load(self, user, values):
        actor = self._authorize(user)
        query = Query.parse(values)
        iterator = None
        try:
            source = self.repository.rows_for_org(actor['org'])
            if isinstance(source, (str, bytes, Mapping)):
                raise ValueError('Invalid repository result.')
            iterator = iter(source)
            rows, identifiers = [], set()
            for index, value in enumerate(iterator):
                if index >= MAX_SOURCE_ROWS:
                    raise ValueError('Report result exceeds the supported bound.')
                row = validated_row(value, actor['org'])
                if row['sample_id'] in identifiers:
                    raise ValueError('Duplicate sample identifier.')
                identifiers.add(row['sample_id'])
                rows.append(row)
        except Exception:
            raise ProviderUnavailable() from None
        finally:
            if iterator is not None and callable(getattr(iterator, 'close', None)):
                try:
                    iterator.close()
                except Exception:
                    raise ProviderUnavailable() from None
        self._authorize(actor)
        search = query.search.casefold()
        rows = [row for row in rows
                if (not query.department or row['department'] == query.department)
                and (not search or any(search in row[name].casefold()
                                       for name in ('sample_id', 'product', 'inspection_date')))]
        # Stable sample-ID tie ordering remains ascending for either direction.
        rows.sort(key=lambda row: row['sample_id'])
        rows.sort(key=lambda row: row[query.order_by], reverse=query.direction == 'desc')
        return actor, query, rows

    def query(self, user, values):
        actor, query, rows = self._load(user, values)
        result = dict(rows=rows[query.offset:query.offset + query.limit], total=len(rows),
                      limit=query.limit, offset=query.offset)
        self._authorize(actor)
        return result

    def export_csv(self, user, values):
        """Export all matching server rows, bounded at 1000, in the chosen order.

        Paging controls affect the table only. No client table data is accepted.
        A fresh query is made; no previously rendered rows or tokens grant access.
        """
        actor, query, rows = self._load(user, values)
        output = io.StringIO(newline='')
        writer = csv.writer(output, lineterminator='\r\n')
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow([csv_cell(row[name]) for name in COLUMNS])
        content = output.getvalue()
        if len(content.encode('utf-8')) > MAX_EXPORT_BYTES:
            raise ProviderUnavailable()
        self._authorize(actor)
        return content
