"""Synthetic corrective-action schema, date semantics and bounded query inputs."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
import re
import unicodedata

from ..registry import AccessPolicy

POLICY = AccessPolicy.require(roles=('admin', 'user'))
SOURCE_COLUMNS = ('case_id', 'opened_date', 'due_date', 'closed_date', 'priority', 'finding')
COLUMNS = SOURCE_COLUMNS + ('status', 'age_days', 'overdue_days')
PRIORITIES = ('Low', 'Medium', 'High')
DEFAULT_AS_OF = '2026-10-05'
MAX_SOURCE_ROWS = 1000
MAX_EXPORT_BYTES = 1024 * 1024
_FIELDS = frozenset(('search', 'status', 'priority', 'as_of', 'order_by', 'direction', 'limit', 'offset'))
_ORDERS = frozenset(('case_id', 'due_date', 'overdue_days', 'age_days'))
_DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}\Z')
_ID = re.compile(r'action-[0-9]{4}\Z')


class QueryError(ValueError):
    """Invalid query; callbacks show fixed safe messages only."""


def _text(value, maximum, allow_empty=True):
    if (type(value) is not str or len(value) > maximum
            or (not allow_empty and not value.strip())
            or any(unicodedata.category(char) in ('Cc', 'Cs') for char in value)):
        raise ValueError('Invalid report text.')
    return value


def calendar_date(value):
    if not _DATE.fullmatch(_text(value, 10, allow_empty=False)):
        raise ValueError('Invalid calendar date.')
    parsed = date.fromisoformat(value)
    if not date(2000, 1, 1) <= parsed <= date(2100, 12, 31):
        raise ValueError('Date outside supported interval.')
    return parsed


@dataclass(frozen=True)
class Query:
    search: str = ''
    status: str = ''
    priority: str = ''
    as_of: str = DEFAULT_AS_OF
    order_by: str = 'due_date'
    direction: str = 'asc'
    limit: int = 20
    offset: int = 0

    def __post_init__(self):
        try:
            _text(self.search, 80)
            calendar_date(self.as_of)
            if (self.status not in ('', 'Open', 'Closed')
                    or self.priority not in ('',) + PRIORITIES
                    or self.order_by not in _ORDERS or self.direction not in ('asc', 'desc')
                    or type(self.limit) is not int or not 1 <= self.limit <= 100
                    or type(self.offset) is not int or not 0 <= self.offset <= 10000):
                raise ValueError('Invalid report query.')
        except (TypeError, ValueError):
            raise QueryError('Invalid report filters.') from None

    @classmethod
    def parse(cls, values):
        if not isinstance(values, Mapping) or set(values) - _FIELDS:
            raise QueryError('Invalid report filters.')
        return cls(**dict(values))


def validated_row(value, organization):
    if not isinstance(value, Mapping) or set(value) != set(SOURCE_COLUMNS) | {'org'}:
        raise ValueError('Invalid action row.')
    if value['org'] != organization:
        raise ValueError('Unexpected report organization.')
    row = {name: value[name] for name in SOURCE_COLUMNS}
    if not _ID.fullmatch(_text(row['case_id'], 11, allow_empty=False)):
        raise ValueError('Invalid action identifier.')
    opened, due = calendar_date(row['opened_date']), calendar_date(row['due_date'])
    closed = calendar_date(row['closed_date']) if row['closed_date'] != '' else None
    if due < opened or (closed is not None and closed < opened):
        raise ValueError('Invalid action date sequence.')
    if row['priority'] not in PRIORITIES:
        raise ValueError('Invalid action priority.')
    _text(row['finding'], 200, allow_empty=False)
    return row


def at_date(row, as_of):
    """Derive a deterministic snapshot, not an actual company SLA calculation."""
    opened = calendar_date(row['opened_date'])
    if opened > as_of:
        return None
    closed = calendar_date(row['closed_date']) if row['closed_date'] else None
    effective_closed = closed if closed is not None and closed <= as_of else None
    end = effective_closed or as_of
    return dict(row, closed_date=effective_closed.isoformat() if effective_closed else '',
                status='Closed' if effective_closed else 'Open', age_days=(end - opened).days,
                overdue_days=max(0, (end - calendar_date(row['due_date'])).days))


def csv_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value
