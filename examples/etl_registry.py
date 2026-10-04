"""Reviewed, offline integration example; no worker, IO or connection at import.

The fixed SQL below reads a caller-provisioned synthetic SQLite fixture only.
Never pass a browser-supplied path/SQL/connection string here. Real company
readers need their own reviewed adapter and permission/data-volume acceptance.
"""
from contextlib import closing
from datetime import date
from pathlib import Path
import re
import sqlite3

from reporting_workspace.etl_adapters import ETLRegistry, JobSpec, SnapshotAdapter, StepSpec


def create_fixture(database, business_date='2026-10-01'):
    """Explicit test setup: create a NEW synthetic source with two bounded rows."""
    path = Path(database)
    if not path.is_absolute() or path.exists() or not path.parent.is_dir():
        raise ValueError('Provide a new absolute local fixture file in an existing private directory.')
    if (not isinstance(business_date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', business_date)
            or date.fromisoformat(business_date).isoformat() != business_date):
        raise ValueError('A canonical fixture date is required.')
    # Exclusive creation prevents accidentally overwriting a real source file.
    path.touch(mode=0o600, exist_ok=False)
    with closing(sqlite3.connect(str(path))) as connection, connection:
        connection.execute('CREATE TABLE fixture_orders (record_id TEXT PRIMARY KEY, business_date TEXT NOT NULL, amount INTEGER NOT NULL)')
        connection.executemany('INSERT INTO fixture_orders VALUES (?,?,?)',
                               [('fixture-order-1', business_date, 120), ('fixture-order-2', business_date, 80)])
    return path


def build_registry(database):
    """Register a known local read-only source, pure transform and local proposal."""
    path = Path(database)
    if not path.is_absolute() or not path.is_file():
        raise ValueError('Provide an existing approved local synthetic SQLite fixture.')
    source_uri = path.resolve().as_uri() + '?mode=ro'

    def source(context, upstream):
        with closing(sqlite3.connect(source_uri, uri=True)) as connection, connection:
            connection.row_factory = sqlite3.Row
            # Bound the result while retaining one extra row so max_rows fails
            # closed instead of silently publishing a truncated data partition.
            rows = connection.execute(
                'SELECT record_id,business_date,amount FROM fixture_orders '
                'WHERE business_date=? ORDER BY record_id LIMIT 101',
                (context['business_date'],)).fetchall()
            return [dict(row) for row in rows]

    def normalize(context, upstream):
        return [dict(row) for row in upstream['source']]

    def summary(context, upstream):
        rows = upstream['normalize']
        return [dict(record_id='fixture-total', business_date=context['business_date'],
                     source_count=len(rows), amount=sum(row['amount'] for row in rows))]

    job = JobSpec(
        'approved-orders-fixture', '核准訂單 ETL 接線範例',
        'Read a local synthetic date partition, validate it, and publish a bounded local summary.', 'A',
        (StepSpec('source', SnapshotAdapter('fixture-read', 'v1', source),
                  min_rows=1, max_rows=100, required_fields=('record_id', 'business_date', 'amount'),
                  unique_fields=('record_id',), nonnegative_fields=('amount',)),
         StepSpec('normalize', SnapshotAdapter('fixture-normalize', 'v1', normalize), ('source',),
                  min_rows=1, max_rows=100, required_fields=('record_id', 'amount'),
                  unique_fields=('record_id',), nonnegative_fields=('amount',), count_matches='source'),
         StepSpec('summary', SnapshotAdapter('fixture-summary', 'v1', summary), ('normalize',),
                  min_rows=1, max_rows=1, required_fields=('record_id', 'source_count', 'amount'),
                  nonnegative_fields=('source_count', 'amount'))),
        'summary')
    return ETLRegistry((job,))
