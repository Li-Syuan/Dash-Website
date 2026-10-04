"""Trusted deployment-only ETL registry and offline, side-effect-free adapters.

Adapters receive a small context and independent copies of dependency rows.
They return bounded JSON row lists. They must not publish externally: only the
engine's fenced SQLite transaction publishes. Register callables in reviewed
server code, never request-supplied code, SQL, import paths or credentials.
Changing adapter behavior requires a new explicit adapter version and storage
migration; changing a callable in place is outside this contract.
"""
from dataclasses import dataclass, field
import hashlib
import json
import re
from types import MappingProxyType
from typing import Callable, Tuple

_ID = re.compile(r'[a-z][a-z0-9_-]{0,63}\Z')


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError('Registry identifiers must be bounded lowercase identifiers.')
    return value


@dataclass(frozen=True)
class SnapshotAdapter:
    name: str
    version: str
    function: Callable = field(repr=False, compare=False)
    retry_safe: bool = True

    def __post_init__(self):
        _identifier(self.name)
        _identifier(self.version)
        if not callable(self.function) or type(self.retry_safe) is not bool:
            raise ValueError('A trusted snapshot callable and explicit retry policy are required.')


@dataclass(frozen=True)
class StepSpec:
    name: str
    adapter: SnapshotAdapter
    depends_on: Tuple[str, ...] = ()
    min_rows: int = 1
    max_rows: int = 1000
    required_fields: Tuple[str, ...] = ()
    unique_fields: Tuple[str, ...] = ()
    nonnegative_fields: Tuple[str, ...] = ()
    count_matches: str = None

    def __post_init__(self):
        _identifier(self.name)
        if not isinstance(self.adapter, SnapshotAdapter):
            raise ValueError('Steps require trusted registered snapshot adapters.')
        for name in ('depends_on', 'required_fields', 'unique_fields', 'nonnegative_fields'):
            values = tuple(getattr(self, name))
            if len(values) > 32 or len(set(values)) != len(values):
                raise ValueError('Step fields and dependencies must be unique and bounded.')
            for value in values:
                _identifier(value)
            object.__setattr__(self, name, values)
        if (type(self.min_rows) is not int or type(self.max_rows) is not int or
                not 0 <= self.min_rows <= self.max_rows <= 1000):
            raise ValueError('Step row bounds must be integers from zero to 1000.')
        if self.count_matches is not None and self.count_matches not in self.depends_on:
            raise ValueError('Count comparison must name a direct dependency.')


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    name: str
    description: str
    org: str
    steps: Tuple[StepSpec, ...]
    publish_step: str
    read_roles: Tuple[str, ...] = ('admin', 'user')
    manage_roles: Tuple[str, ...] = ('admin',)
    allowed_user_ids: Tuple[str, ...] = ()

    def __post_init__(self):
        _identifier(self.job_id)
        for value, limit in ((self.name, 120), (self.description, 500), (self.org, 128)):
            if (not isinstance(value, str) or not value.strip() or len(value) > limit or
                    any(ord(char) < 32 for char in value)):
                raise ValueError('Registry metadata must be reviewed bounded plain text.')
        steps = tuple(self.steps)
        if not 1 <= len(steps) <= 20 or not all(isinstance(step, StepSpec) for step in steps):
            raise ValueError('A pipeline requires one to twenty trusted steps.')
        names = [step.name for step in steps]
        if len(set(names)) != len(names) or self.publish_step not in names:
            raise ValueError('Pipeline step names and publication target must be valid.')
        for step in steps:
            if any(dep not in names or dep == step.name for dep in step.depends_on):
                raise ValueError('Pipeline dependencies must name other registered steps.')
        pending, ordered = list(steps), []
        while pending:
            ready = [step for step in pending if set(step.depends_on) <= {s.name for s in ordered}]
            if not ready:
                raise ValueError('Pipeline dependency cycles are not permitted.')
            ordered.extend(ready)
            pending = [step for step in pending if step not in ready]
        # Publication must depend (transitively) on every other step so that no
        # unchecked branch is accidentally excluded from the published result.
        ancestors = set()
        def visit(name):
            for dep in next(step for step in ordered if step.name == name).depends_on:
                if dep not in ancestors:
                    ancestors.add(dep)
                    visit(dep)
        visit(self.publish_step)
        if ancestors != set(names) - {self.publish_step}:
            raise ValueError('The publication step must include every pipeline branch.')
        object.__setattr__(self, 'steps', tuple(ordered))
        for field_name in ('read_roles', 'manage_roles'):
            roles = tuple(getattr(self, field_name))
            if not roles or not set(roles) <= {'admin', 'user'} or len(set(roles)) != len(roles):
                raise ValueError('Registry action roles are invalid.')
            object.__setattr__(self, field_name, roles)
        if not set(self.manage_roles) <= set(self.read_roles):
            raise ValueError('Managers must also have read access.')
        identifiers = tuple(self.allowed_user_ids)
        if len(identifiers) > 100 or any(not isinstance(v, str) or not 1 <= len(v) <= 255 for v in identifiers):
            raise ValueError('Job identity restrictions are invalid.')
        object.__setattr__(self, 'allowed_user_ids', identifiers)

    @property
    def signature(self):
        metadata = dict(job_id=self.job_id, org=self.org, publish_step=self.publish_step,
                        read_roles=self.read_roles, manage_roles=self.manage_roles,
                        allowed_user_ids=self.allowed_user_ids, steps=[dict(
                            name=s.name, adapter=s.adapter.name, version=s.adapter.version,
                            retry_safe=s.adapter.retry_safe, depends_on=s.depends_on,
                            min_rows=s.min_rows, max_rows=s.max_rows, required_fields=s.required_fields,
                            unique_fields=s.unique_fields, nonnegative_fields=s.nonnegative_fields,
                            count_matches=s.count_matches) for s in self.steps])
        return hashlib.sha256(json.dumps(metadata, sort_keys=True).encode('utf-8')).hexdigest()


class ETLRegistry:
    """Construct in trusted deployment code; engines take an immutable snapshot."""
    def __init__(self, jobs=()):
        self._jobs = {}
        for job in jobs:
            self.register(job)

    def register(self, job):
        if not isinstance(job, JobSpec) or job.job_id in self._jobs or len(self._jobs) >= 50:
            raise ValueError('Job registration must be unique, trusted and bounded.')
        self._jobs[job.job_id] = job
        return job

    def snapshot(self):
        if not self._jobs:
            raise ValueError('At least one trusted ETL job is required.')
        return MappingProxyType(dict(self._jobs))


def _sales_source(context, upstream):
    return [dict(record_id='synthetic-sale-{}'.format(i), revenue=revenue, cost=cost,
                 business_date=context['business_date'])
            for i, (revenue, cost) in enumerate(((110, 65), (90, 55), (125, 70), (95, 57)), 1)]


def _sales_clean(context, upstream):
    return [dict(row, profit=row['revenue']-row['cost']) for row in upstream['source']]


def _sales_summary(context, upstream):
    rows = upstream['validate']
    return [dict(record_id='synthetic-summary', revenue=sum(row['revenue'] for row in rows),
                 cost=sum(row['cost'] for row in rows), profit=sum(row['profit'] for row in rows),
                 source_count=len(rows), business_date=context['business_date'])]


def _inventory_source(context, upstream):
    return [dict(record_id='synthetic-sku-{}'.format(i), quantity=quantity, reorder_level=10)
            for i, quantity in enumerate((7, 25, 40), 1)]


def _inventory_clean(context, upstream):
    return [dict(row, needs_reorder=row['quantity'] < row['reorder_level']) for row in upstream['source']]


def _inventory_summary(context, upstream):
    rows = upstream['validate']
    return [dict(record_id='synthetic-inventory-summary', source_count=len(rows),
                 quantity=sum(row['quantity'] for row in rows),
                 reorder_count=sum(int(row['needs_reorder']) for row in rows),
                 business_date=context['business_date'])]


def default_registry():
    sales = JobSpec('synthetic-sales-daily', 'Synthetic daily sales / 每日合成銷售',
                    'Validate four offline sales rows and publish one local daily summary.', 'A', (
        StepSpec('source', SnapshotAdapter('sales-source', 'v1', _sales_source),
                 min_rows=4, max_rows=4, required_fields=('record_id','revenue','cost'), unique_fields=('record_id',)),
        StepSpec('validate', SnapshotAdapter('sales-clean', 'v1', _sales_clean), ('source',),
                 required_fields=('record_id','revenue','cost','profit'), unique_fields=('record_id',),
                 nonnegative_fields=('revenue','cost'), count_matches='source'),
        StepSpec('summary', SnapshotAdapter('sales-summary', 'v1', _sales_summary), ('validate',),
                 min_rows=1, max_rows=1, required_fields=('record_id','source_count','revenue','cost','profit'),
                 nonnegative_fields=('source_count','revenue','cost'))), 'summary')
    inventory = JobSpec('synthetic-inventory-health', 'Synthetic inventory health / 合成庫存檢查',
                       'Validate three offline stock rows and publish a local reorder summary.', 'A', (
        StepSpec('source', SnapshotAdapter('inventory-source', 'v1', _inventory_source),
                 min_rows=3, max_rows=3, required_fields=('record_id','quantity'), unique_fields=('record_id',)),
        StepSpec('validate', SnapshotAdapter('inventory-clean', 'v1', _inventory_clean), ('source',),
                 required_fields=('record_id','quantity','needs_reorder'), unique_fields=('record_id',),
                 nonnegative_fields=('quantity',), count_matches='source'),
        StepSpec('summary', SnapshotAdapter('inventory-summary', 'v1', _inventory_summary), ('validate',),
                 min_rows=1, max_rows=1, required_fields=('record_id','source_count','quantity','reorder_count'),
                 nonnegative_fields=('source_count','quantity','reorder_count'))), 'summary')
    return ETLRegistry((sales, inventory))
