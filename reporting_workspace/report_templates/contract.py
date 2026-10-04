"""Fixed report schema and bounded inputs; browser claims are never accepted."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
import re
import unicodedata

from ..registry import AccessPolicy


POLICY = AccessPolicy.require(roles=('admin', 'user'))
COLUMNS = ('sample_id', 'inspection_date', 'department', 'product', 'inspected', 'rejected')
DEPARTMENTS = ('Assembly', 'Laboratory')
MAX_SOURCE_ROWS = 1000
MAX_EXPORT_BYTES = 1024 * 1024
_QUERY_FIELDS = frozenset(('search', 'department', 'order_by', 'direction', 'limit', 'offset'))
_ORDER_FIELDS = frozenset(('sample_id', 'inspection_date', 'rejected'))
_DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}\Z')
_SAMPLE_ID = re.compile(r'sample-[0-9]{4}\Z')


class QueryError(ValueError):
    """Invalid report filters; presentation uses a fixed safe message."""


def _text(value, maximum, allow_empty=True):
    if (type(value) is not str or len(value) > maximum
            or (not allow_empty and not value.strip())
            or any(unicodedata.category(char) in ('Cc', 'Cs') for char in value)):
        raise ValueError('Invalid report text.')
    return value


@dataclass(frozen=True)
class Query:
    search: str = ''
    department: str = ''
    order_by: str = 'inspection_date'
    direction: str = 'asc'
    limit: int = 20
    offset: int = 0

    def __post_init__(self):
        try:
            _text(self.search, 80)
            if (self.department not in ('',) + DEPARTMENTS
                    or self.order_by not in _ORDER_FIELDS
                    or self.direction not in ('asc', 'desc')
                    or type(self.limit) is not int or not 1 <= self.limit <= 100
                    or type(self.offset) is not int or not 0 <= self.offset <= 10000):
                raise ValueError('Invalid query.')
        except (TypeError, ValueError):
            raise QueryError('Invalid report filters.') from None

    @classmethod
    def parse(cls, values):
        if not isinstance(values, Mapping) or set(values) - _QUERY_FIELDS:
            raise QueryError('Invalid report filters.')
        return cls(**dict(values))


def validated_row(value, organization):
    """Validate each adapter row and remove its internal tenant discriminator.

    A foreign row invalidates the entire result instead of silently filtering a
    faulty company adapter. Neither HTML nor spreadsheet formulas are executed.
    """
    if not isinstance(value, Mapping) or set(value) != set(COLUMNS) | {'org'}:
        raise ValueError('Invalid report row.')
    if value['org'] != organization:
        raise ValueError('Unexpected report organization.')
    row = {name: value[name] for name in COLUMNS}
    if not _SAMPLE_ID.fullmatch(_text(row['sample_id'], 11, allow_empty=False)):
        raise ValueError('Invalid sample identifier.')
    text_date = _text(row['inspection_date'], 10, allow_empty=False)
    if not _DATE.fullmatch(text_date):
        raise ValueError('Invalid inspection date.')
    parsed_date = date.fromisoformat(text_date)
    if not date(2000, 1, 1) <= parsed_date <= date(2100, 12, 31):
        raise ValueError('Inspection date outside the supported interval.')
    if row['department'] not in DEPARTMENTS:
        raise ValueError('Invalid department.')
    _text(row['product'], 120, allow_empty=False)
    for field in ('inspected', 'rejected'):
        if type(row[field]) is not int or not 0 <= row[field] <= 1000000000:
            raise ValueError('Invalid inspection quantity.')
    if row['rejected'] > row['inspected']:
        raise ValueError('Rejected quantity exceeds inspected quantity.')
    return row


def csv_cell(value):
    """Preserve numeric types and neutralize formula-leading text, including spaces."""
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value
