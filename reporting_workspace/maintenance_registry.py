"""App-owned maintenance definitions and bounded, request-authorized adapters.

Registration preserves the original ``model / init_db / bind / model_config``
contract. It never imports providers, opens an engine, or reads rows. The caller
must supply the server's authenticated user, or an identity provider that reloads
that user's ID. Browser metadata, hidden buttons and shared CRUD flags have no
part in authorization. SQLite fixtures are synthetic; the optional ORM adapter
is an integration contract, not a claim that company Oracle was tested.
"""

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from types import MappingProxyType, SimpleNamespace
import unicodedata

from .legacy_crud import (AdapterUnavailable, InvalidInput, LegacyCrudError,
                          PermissionDenied, RecordConflict, RecordNotFound,
                          StorageUnavailable)
from .legacy_policy import Policy, scope
from .providers import validate_identity


_SQL_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,62}\Z')
_KEY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
_SERVER_FIELDS = frozenset(('org', 'version', 'deleted'))
_MAX_INT = 9223372036854775807
_KINDS = frozenset(('text', 'integer', 'number', 'boolean', 'date', 'datetime'))
_CONFIG_KEYS = frozenset(('Status', 'Create_Lock', 'Update_Lock', 'data',
                          'required', 'primarykey', 'description', 'placeholder',
                          'label', 'kind', 'max_length', 'default'))


class BindingMismatch(InvalidInput):
    """The read engine and model's write bind must be the same configured bind."""
    code = 'bind_mismatch'


def _text(value, maximum=255, required=True):
    if (not isinstance(value, str) or len(value) > maximum or
            (required and not value.strip()) or
            any(unicodedata.category(c) in ('Cc', 'Cs') for c in value)):
        raise InvalidInput('Invalid bounded text.')
    return value


def _identifier(value, sql=False):
    if not isinstance(value, str) or (_SQL_NAME if sql else _KEY).fullmatch(value) is None:
        raise InvalidInput('Invalid registered identifier.')
    return value


def _integer(value, minimum=0, maximum=_MAX_INT):
    if type(value) is not int or not minimum <= value <= maximum:
        raise InvalidInput('Invalid integer or row bound.')
    return value


def _bounds(limit, offset):
    return _integer(limit, 1, 100), _integer(offset, 0, 100000)


def _sequence(value, name):
    if not isinstance(value, (tuple, list)) or len(value) > 64:
        raise InvalidInput('Invalid policy collection.')
    result = tuple(_text(v, 128) for v in value)
    if len(set(result)) != len(result):
        raise InvalidInput('Duplicate policy entries.')
    return result


def _claims(user):
    if isinstance(user, Mapping):
        try:
            return validate_identity(user)
        except ValueError:
            raise PermissionDenied('Operation denied.') from None
    if getattr(user, 'is_authenticated', False) is not True:
        raise PermissionDenied('Operation denied.')
    try:
        return {'id': _text(getattr(user, 'id', None)),
                'org': _text(getattr(user, 'orgcode', None), 128),
                'role': _text(getattr(user, 'role', 'legacy'), 64)}
    except InvalidInput:
        raise PermissionDenied('Operation denied.') from None


@dataclass(frozen=True)
class MaintenancePolicy:
    """Explicit AND role/org permission; writes require a separate role grant."""
    read_roles: tuple = ('admin', 'user')
    write_roles: tuple = ('admin',)
    orgs: tuple = ()

    def __post_init__(self):
        for name in ('read_roles', 'write_roles', 'orgs'):
            object.__setattr__(self, name, _sequence(getattr(self, name), name))
        if not self.read_roles or not set(self.write_roles).issubset(self.read_roles):
            raise InvalidInput('Writes require an explicit read grant.')

    def access(self, identity):
        if (identity['role'] not in self.read_roles or
                (self.orgs and identity['org'] not in self.orgs)):
            return None
        return 'crud' if identity['role'] in self.write_roles else 'read'


@dataclass(frozen=True)
class MaintenanceField:
    name: str
    label: str
    kind: str = 'text'
    required: bool = False
    create_lock: bool = False
    update_lock: bool = False
    max_length: int = 255
    options: tuple = ()
    default: object = None
    description: str = ''
    placeholder: str = ''
    primary_key: bool = False
    unique: bool = False

    def value(self, value):
        if value is None:
            if self.required:
                raise InvalidInput('Required field missing.')
            return None
        if self.kind == 'text':
            value = _text(value, self.max_length, self.required)
            if self.required and not value.strip():
                raise InvalidInput('Required field missing.')
        elif self.kind == 'integer':
            value = _integer(value, -_MAX_INT, _MAX_INT)
        elif self.kind == 'number':
            if type(value) not in (int, float) or abs(value) > _MAX_INT or not math.isfinite(value):
                raise InvalidInput('Invalid numeric field.')
        elif self.kind == 'boolean':
            if type(value) is not bool:
                raise InvalidInput('Invalid boolean field.')
        elif self.kind in ('date', 'datetime'):
            parser = date.fromisoformat if self.kind == 'date' else datetime.fromisoformat
            if isinstance(value, (datetime, date)):
                if self.kind == 'date' and isinstance(value, datetime):
                    raise InvalidInput('Invalid date field.')
                value = value.isoformat()
            value = _text(value, 40)
            try:
                parsed = parser(value)
            except ValueError:
                raise InvalidInput('Invalid ISO date field.') from None
            value = parsed.isoformat()
        if self.options and value not in self.options:
            raise InvalidInput('Choose a registered field option.')
        return value

    def metadata(self):
        return {'name': self.name, 'label': self.label, 'kind': self.kind,
                'required': self.required, 'create_lock': self.create_lock,
                'update_lock': self.update_lock, 'max_length': self.max_length,
                'options': list(self.options), 'description': self.description,
                'placeholder': self.placeholder, 'primary_key': self.primary_key}


def _column_kind(column):
    value = type(getattr(column, 'type', None)).__name__.lower()
    if 'bool' in value:
        return 'boolean'
    if 'datetime' in value or 'timestamp' in value:
        return 'datetime'
    if value == 'date':
        return 'date'
    if 'int' in value:
        return 'integer'
    if any(part in value for part in ('numeric', 'float', 'decimal', 'double')):
        return 'number'
    if any(part in value for part in ('string', 'text', 'varchar', 'char')):
        return 'text'
    raise InvalidInput('Unsupported model field type; configure a supported kind.')


def _model_fields(model, config):
    columns = tuple(getattr(getattr(model, '__table__', None), 'columns', ()))
    if not 1 <= len(columns) <= 35 or not isinstance(config, Mapping):
        raise InvalidInput('A bounded declarative model and model_config are required.')
    names = [_identifier(getattr(c, 'name', None), sql=True) for c in columns]
    if len(set(names)) != len(names) or not set(config).issubset(names):
        raise InvalidInput('Unknown or duplicated model fields.')
    if set(config).intersection(_SERVER_FIELDS):
        raise InvalidInput('Server-owned model fields cannot be configured.')
    primary = [c.name for c in columns if getattr(c, 'primary_key', False) is True]
    if len(primary) != 1:
        raise InvalidInput('One explicit model primary key is required.')
    fields, frozen = [], {}
    for column in columns:
        name = column.name
        if name in _SERVER_FIELDS:
            continue
        settings = dict(config.get(name, {}))
        if not set(settings).issubset(_CONFIG_KEYS):
            raise InvalidInput('Unsupported model_config option.')
        for option in ('required', 'Create_Lock', 'Update_Lock', 'primarykey'):
            if option in settings and type(settings[option]) is not bool:
                raise InvalidInput('Model flags must be booleans.')
        is_primary = name == primary[0]
        if 'primarykey' in settings and settings['primarykey'] != is_primary:
            raise InvalidInput('Configured primary key disagrees with the model.')
        status = settings.get('Status', True)
        if type(status) is not bool:
            raise InvalidInput('Status must be a boolean.')
        options = settings.get('data', ())
        if not isinstance(options, (list, tuple)) or len(options) > 100:
            raise InvalidInput('Field options must be bounded.')
        normalized = []
        for option in options:
            if isinstance(option, Mapping):
                if set(option) != {'label', 'value'}:
                    raise InvalidInput('Invalid field option.')
                _text(option['label'], 128)
                option = option['value']
            if type(option) not in (str, int, float, bool):
                raise InvalidInput('Invalid field option.')
            normalized.append(option)
        kind = settings.get('kind') or _column_kind(column)
        if kind not in _KINDS:
            raise InvalidInput('Unsupported model field kind.')
        length = settings.get('max_length', getattr(getattr(column, 'type', None), 'length', None) or 255)
        item = MaintenanceField(
            name=name, label=_text(settings.get('label', name), 128), kind=kind,
            required=settings.get('required', getattr(column, 'nullable', True) is False) and not is_primary,
            create_lock=is_primary or not status or settings.get('Create_Lock', False),
            update_lock=is_primary or not status or settings.get('Update_Lock', False),
            max_length=_integer(length, 1, 4000), options=tuple(normalized),
            default=settings.get('default'), primary_key=is_primary,
            unique=getattr(column, 'unique', False) is True,
            description=_text(settings.get('description', ''), 500, False),
            placeholder=_text(settings.get('placeholder', ''), 255, False))
        if item.default is not None:
            item.value(item.default)
        for option in item.options:
            item.value(option)
        fields.append(item)
        frozen[name] = MappingProxyType(dict(settings, data=item.options))
    pk = next(item for item in fields if item.primary_key)
    if pk.kind != 'integer':
        raise InvalidInput('This adapter contract requires an integer primary key.')
    return tuple(fields), MappingProxyType(frozen), primary[0]


@dataclass(frozen=True)
class MaintenanceDefinition:
    key: str
    label: str
    model: object
    init_db: object
    bind: str
    model_config: Mapping
    policy_resolver: object
    adapter: object = None
    backend: str = 'sqlite'
    only_update: bool = False
    fields: tuple = field(init=False, repr=False)
    primary_key: str = field(init=False)
    table: str = field(init=False, repr=False)

    def __post_init__(self):
        _identifier(self.key)
        _text(self.label, 128)
        _identifier(self.bind)
        if not isinstance(self.model, type) or not callable(self.policy_resolver):
            raise InvalidInput('Explicit model and current server policy resolver are required.')
        self.check_bind()
        if self.backend not in ('sqlite', 'oracle') or type(self.only_update) is not bool:
            raise InvalidInput('Unsupported maintenance backend or mode.')
        table = _identifier(getattr(self.model, '__tablename__', None), sql=True)
        if getattr(getattr(self.model, '__table__', None), 'name', table) != table:
            raise InvalidInput('Model table names disagree.')
        fields, config, primary = _model_fields(self.model, self.model_config)
        object.__setattr__(self, 'fields', fields)
        object.__setattr__(self, 'model_config', config)
        object.__setattr__(self, 'primary_key', primary)
        object.__setattr__(self, 'table', table)
        if self.adapter is not None and (getattr(self.adapter, 'backend', None) != self.backend or
                any(not callable(getattr(self.adapter, method, None))
                    for method in ('query', 'get', 'create', 'update', 'delete'))):
            raise InvalidInput('An explicit matching maintenance adapter is required.')

    def check_bind(self):
        if getattr(self.model, '__bind_key__', None) != self.bind:
            raise BindingMismatch('Model and configured database bind disagree.')

    def metadata(self, access):
        return {'key': self.key, 'label': self.label, 'bind': self.bind,
                'backend': self.backend, 'available': self.adapter is not None,
                'access': access, 'only_update': self.only_update}


def _payload(definition, payload, create=False):
    if not isinstance(payload, Mapping) or not payload:
        raise InvalidInput('An editable field mapping is required.')
    by_name = {item.name: item for item in definition.fields}
    if any(name not in by_name or by_name[name].primary_key for name in payload):
        raise InvalidInput('Unknown or server-owned fields are not editable.')
    result = {}
    for item in definition.fields:
        if item.primary_key:
            continue
        locked = item.create_lock if create else item.update_lock
        if item.name in payload:
            if locked:
                raise InvalidInput('A locked field is not editable.')
            result[item.name] = item.value(payload[item.name])
        elif create:
            result[item.name] = item.value(item.default)
    return result


def _filters(definition, filters):
    if filters is None:
        return {}
    if not isinstance(filters, Mapping) or len(filters) > 8:
        raise InvalidInput('Invalid bounded field filters.')
    by_name = {item.name: item for item in definition.fields}
    if any(name not in by_name for name in filters):
        raise InvalidInput('Unknown filter field.')
    return {name: by_name[name].value(value) for name, value in filters.items()}


def _record(definition, row):
    result = {}
    try:
        for item in definition.fields:
            value = row[item.name] if isinstance(row, Mapping) or isinstance(row, sqlite3.Row) else getattr(row, item.name)
            if item.kind == 'boolean' and value is not None:
                if type(value) not in (int, bool) or value not in (0, 1):
                    raise InvalidInput('Invalid stored boolean.')
                value = bool(value)
            if item.primary_key:
                value = _integer(value, 1)
            else:
                value = item.value(value)
            result[item.name] = value
        for name in ('version', 'deleted'):
            value = row[name] if isinstance(row, Mapping) or isinstance(row, sqlite3.Row) else getattr(row, name)
            if name == 'version':
                result[name] = _integer(value, 1)
            else:
                if type(value) not in (int, bool) or value not in (0, 1):
                    raise InvalidInput('Invalid stored lifecycle state.')
                result[name] = bool(value)
    except (InvalidInput, KeyError, AttributeError):
        raise StorageUnavailable('Stored maintenance data does not match the registered model.') from None
    return result


class MaintenanceRegistry:
    """Immutable catalog; authorization and identity reload precede data access."""
    def __init__(self, identities=None):
        if identities is not None and not callable(getattr(identities, 'get_user', None)):
            raise InvalidInput('A current server identity provider is required.')
        self.identities = identities
        self._definitions = {}
        self._frozen = False

    @property
    def definitions(self):
        return tuple(self._definitions.values())

    @property
    def frozen(self):
        return self._frozen

    def register(self, definition):
        if self._frozen:
            raise RuntimeError('Maintenance registry is frozen.')
        if not isinstance(definition, MaintenanceDefinition) or definition.key in self._definitions:
            raise InvalidInput('Invalid or duplicate maintenance definition.')
        self._definitions[definition.key] = definition
        return definition

    def freeze(self):
        self._frozen = True
        return self

    def _user(self, user):
        identity = _claims(user)
        if self.identities is not None:
            try:
                user = self.identities.get_user(identity['id'])
                identity = _claims(user)
            except Exception:
                raise PermissionDenied('Operation denied.') from None
        return user, identity

    @staticmethod
    def _access(definition, user, identity):
        try:
            policy = definition.policy_resolver(user)
            if isinstance(policy, MaintenancePolicy):
                return policy.access(identity)
            if isinstance(policy, Policy):
                if isinstance(user, Mapping):
                    role = user['role']
                    if _KEY.fullmatch(role) is None:
                        return None
                    user = SimpleNamespace(id=identity['id'], orgcode=identity['org'],
                                           is_authenticated=True, **{'is_' + role: True})
                return scope(user, policy)
        except Exception:
            return None
        return None

    def _authorized(self, user, key, action):
        user, identity = self._user(user)
        definition = self._definitions.get(key) if isinstance(key, str) else None
        if definition is None:
            raise RecordNotFound('Maintenance definition not found.')
        access = self._access(definition, user, identity)
        if access is None or (action != 'read' and access != 'crud'):
            raise PermissionDenied('Operation denied.')
        if definition.only_update and action in ('create', 'delete'):
            raise PermissionDenied('Operation denied.')
        definition.check_bind()
        return definition, identity, access

    def catalog(self, user, q='', limit=20, offset=0, backend=None):
        user, identity = self._user(user)
        q = _text(q, 120, False).casefold()
        limit, offset = _bounds(limit, offset)
        if backend is not None and backend not in ('sqlite', 'oracle'):
            raise InvalidInput('Unknown catalog backend.')
        items = []
        for definition in self._definitions.values():
            access = self._access(definition, user, identity)
            if access is not None and (backend is None or backend == definition.backend):
                metadata = definition.metadata(access)
                if not q or q in (definition.key + ' ' + definition.label + ' ' + definition.bind).casefold():
                    items.append(metadata)
        items.sort(key=lambda item: item['key'])
        return {'items': items[offset:offset + limit], 'total': len(items), 'limit': limit, 'offset': offset}

    def describe(self, user, key):
        definition, identity, access = self._authorized(user, key, 'read')
        return dict(definition.metadata(access), primary_key=definition.primary_key,
                    fields=[item.metadata() for item in definition.fields])

    @staticmethod
    def _adapter(definition):
        if definition.adapter is None:
            raise AdapterUnavailable('No configured data adapter is available.')
        return definition.adapter

    def query(self, user, key, filters=None, limit=20, offset=0):
        definition, identity, access = self._authorized(user, key, 'read')
        limit, offset = _bounds(limit, offset)
        filters = _filters(definition, filters)
        return self._adapter(definition).query(definition, identity, filters, limit, offset)

    def get(self, user, key, record_id):
        definition, identity, access = self._authorized(user, key, 'read')
        record_id = _integer(record_id, 1)
        return self._adapter(definition).get(definition, identity, record_id)

    def create(self, user, key, payload):
        definition, identity, access = self._authorized(user, key, 'create')
        payload = _payload(definition, payload, create=True)
        return self._adapter(definition).create(definition, identity, payload)

    def update(self, user, key, record_id, expected_version, payload):
        definition, identity, access = self._authorized(user, key, 'update')
        record_id, expected_version = _integer(record_id, 1), _integer(expected_version, 1, _MAX_INT - 1)
        payload = _payload(definition, payload)
        return self._adapter(definition).update(definition, identity, record_id, expected_version, payload)

    def delete(self, user, key, record_id, expected_version):
        definition, identity, access = self._authorized(user, key, 'delete')
        record_id, expected_version = _integer(record_id, 1), _integer(expected_version, 1, _MAX_INT - 1)
        return self._adapter(definition).delete(definition, identity, record_id, expected_version)

    def close(self):
        # No per-user state is retained by the registry or its adapters.
        for adapter in {id(d.adapter): d.adapter for d in self.definitions if d.adapter is not None}.values():
            close = getattr(adapter, 'close', None)
            if callable(close):
                close()


@dataclass(frozen=True)
class SQLiteDatabase:
    """Lazy synthetic init_db descriptor. No connection exists at construction."""
    path: str
    bind_key: str

    def __post_init__(self):
        if not isinstance(self.path, str) or not self.path or '\x00' in self.path or self.path == ':memory:':
            raise InvalidInput('A local durable SQLite fixture path is required.')
        _identifier(self.bind_key)


class SQLiteMaintenanceAdapter:
    """Bound-parameter local SQLite CRUD, soft delete and atomic payload-free audit."""
    backend = 'sqlite'

    def __init__(self, init_db, seed_rows=()):
        if not isinstance(init_db, SQLiteDatabase):
            raise InvalidInput('An explicit SQLite init_db descriptor is required.')
        self.init_db = init_db
        if not isinstance(seed_rows, (tuple, list)) or len(seed_rows) > 100:
            raise InvalidInput('Invalid synthetic seed rows.')
        self._seed_rows = tuple(dict(row) for row in seed_rows)
        self._lock = threading.RLock()

    def close(self):
        pass  # Connections are operation-local and already closed.

    @contextmanager
    def _transaction(self, definition, write=False):
        definition.check_bind()
        if definition.init_db is not self.init_db or definition.bind != self.init_db.bind_key:
            raise BindingMismatch('Adapter and configured database bind disagree.')
        connection = None
        with self._lock:
            try:
                connection = sqlite3.connect(self.init_db.path, timeout=10, isolation_level=None)
                connection.row_factory = sqlite3.Row
                connection.execute('BEGIN IMMEDIATE')
                self._schema(connection, definition)
                yield connection
                connection.execute('COMMIT')
            except Exception as error:
                if connection is not None and connection.in_transaction:
                    connection.execute('ROLLBACK')
                if isinstance(error, LegacyCrudError):
                    raise
                if isinstance(error, sqlite3.IntegrityError):
                    raise RecordConflict('Record conflicts with current data.') from None
                if isinstance(error, (sqlite3.Error, OSError)):
                    raise StorageUnavailable('Maintenance storage operation failed.') from None
                raise
            finally:
                if connection is not None:
                    connection.close()

    def _schema(self, connection, definition):
        # Only previously validated application-owned identifiers are interpolated.
        existed = connection.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?',
                                     ('table', definition.table)).fetchone() is not None
        sql_types = {'text': 'TEXT', 'integer': 'INTEGER', 'number': 'REAL',
                     'boolean': 'INTEGER', 'date': 'TEXT', 'datetime': 'TEXT'}
        columns = []
        for item in definition.fields:
            if item.primary_key:
                columns.append('"{}" INTEGER PRIMARY KEY AUTOINCREMENT'.format(item.name))
            else:
                columns.append('"{}" {}{}'.format(item.name, sql_types[item.kind],
                                                   ' NOT NULL' if item.required else ''))
        columns += ['org TEXT NOT NULL', 'version INTEGER NOT NULL DEFAULT 1',
                    'deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1))']
        connection.execute('CREATE TABLE IF NOT EXISTS "{}" ({})'.format(definition.table, ','.join(columns)))
        actual = connection.execute('PRAGMA table_info("{}")'.format(definition.table)).fetchall()
        expected = [item.name for item in definition.fields] + ['org', 'version', 'deleted']
        if ([row['name'] for row in actual] != expected or
                [row['name'] for row in actual if row['pk']] != [definition.primary_key]):
            raise StorageUnavailable('Maintenance schema does not match the registered model.')
        expected_types = [sql_types[item.kind] for item in definition.fields] + ['TEXT', 'INTEGER', 'INTEGER']
        expected_required = [int(item.required and not item.primary_key) for item in definition.fields] + [1, 1, 1]
        if ([row['type'].upper() for row in actual] != expected_types or
                [row['notnull'] for row in actual] != expected_required):
            raise StorageUnavailable('Maintenance schema does not match the registered model.')
        for item in definition.fields:
            if item.unique and not item.primary_key:
                index = 'maintenance_unique_' + hashlib.sha256(
                    (definition.table + ':' + item.name).encode('ascii')).hexdigest()[:24]
                connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS "{}" ON "{}"(org,"{}") WHERE deleted=0'.format(
                    index, definition.table, item.name))
        connection.execute('CREATE TABLE IF NOT EXISTS maintenance_audit ('
                           'id INTEGER PRIMARY KEY AUTOINCREMENT, definition_key TEXT NOT NULL,'
                           'org TEXT NOT NULL, actor_hash TEXT NOT NULL, action TEXT NOT NULL,'
                           'record_id INTEGER NOT NULL, version INTEGER NOT NULL, changed_fields TEXT NOT NULL,'
                           'at REAL NOT NULL)')
        if not existed:
            for seed in self._seed_rows:
                seed = dict(seed)
                org = _text(seed.pop('org', 'A'), 128)
                payload = _payload(definition, seed, create=True)
                names = list(payload) + ['org', 'version', 'deleted']
                values = list(payload.values()) + [org, 1, 0]
                connection.execute('INSERT INTO "{}" ({}) VALUES ({})'.format(definition.table,
                    ','.join('"{}"'.format(n) for n in names), ','.join('?' for _ in names)), values)

    @staticmethod
    def _where(filters):
        parts, values = ['org=?', 'deleted=0'], []
        for name, value in filters.items():
            parts.append('"{}" IS NULL'.format(name) if value is None else '"{}"=?'.format(name))
            if value is not None:
                values.append(value)
        return ' AND '.join(parts), values

    @staticmethod
    def _get(connection, definition, identity, record_id, deleted=False):
        row = connection.execute('SELECT * FROM "{}" WHERE "{}"=? AND org=?{}'.format(
            definition.table, definition.primary_key, '' if deleted else ' AND deleted=0'),
            (record_id, identity['org'])).fetchone()
        if row is None:
            raise RecordNotFound('Maintenance record not found.')
        return row

    @staticmethod
    def _audit(connection, definition, identity, action, row, fields):
        connection.execute('INSERT INTO maintenance_audit '
            '(definition_key,org,actor_hash,action,record_id,version,changed_fields,at) VALUES (?,?,?,?,?,?,?,?)',
            (definition.key, identity['org'], hashlib.sha256(identity['id'].encode('utf-8')).hexdigest(),
             action, row[definition.primary_key], row['version'], json.dumps(sorted(fields)), time.time()))

    def query(self, definition, identity, filters, limit, offset):
        where, values = self._where(filters)
        values = [identity['org']] + values
        with self._transaction(definition) as connection:
            total = connection.execute('SELECT COUNT(*) FROM "{}" WHERE {}'.format(definition.table, where), values).fetchone()[0]
            rows = connection.execute('SELECT * FROM "{}" WHERE {} ORDER BY "{}" LIMIT ? OFFSET ?'.format(
                definition.table, where, definition.primary_key), values + [limit, offset]).fetchall()
            return {'items': [_record(definition, row) for row in rows], 'total': total,
                    'limit': limit, 'offset': offset}

    def get(self, definition, identity, record_id):
        with self._transaction(definition) as connection:
            return _record(definition, self._get(connection, definition, identity, record_id))

    def create(self, definition, identity, payload):
        with self._transaction(definition, write=True) as connection:
            names = list(payload) + ['org', 'version', 'deleted']
            cursor = connection.execute('INSERT INTO "{}" ({}) VALUES ({})'.format(definition.table,
                ','.join('"{}"'.format(n) for n in names), ','.join('?' for _ in names)),
                list(payload.values()) + [identity['org'], 1, 0])
            row = self._get(connection, definition, identity, cursor.lastrowid)
            self._audit(connection, definition, identity, 'create', row, payload)
            return _record(definition, row)

    def _change(self, definition, identity, record_id, version, payload, delete=False):
        with self._transaction(definition, write=True) as connection:
            row = self._get(connection, definition, identity, record_id)
            if row['version'] != version:
                raise RecordConflict('The maintenance record changed. Reload it.')
            changes = dict(payload, version=version + 1)
            if delete:
                changes['deleted'] = 1
            names = list(changes)
            cursor = connection.execute('UPDATE "{}" SET {} WHERE "{}"=? AND org=? AND version=? AND deleted=0'.format(
                definition.table, ','.join('"{}"=?'.format(n) for n in names), definition.primary_key),
                list(changes.values()) + [record_id, identity['org'], version])
            if cursor.rowcount != 1:
                raise RecordConflict('The maintenance record changed. Reload it.')
            row = self._get(connection, definition, identity, record_id, deleted=delete)
            self._audit(connection, definition, identity, 'delete' if delete else 'update', row, payload)
            return _record(definition, row)

    def update(self, definition, identity, record_id, version, payload):
        return self._change(definition, identity, record_id, version, payload)

    def delete(self, definition, identity, record_id, version):
        return self._change(definition, identity, record_id, version, {}, delete=True)


class ORMMaintenanceAdapter:
    """Explicit Flask-SQLAlchemy-style contract with matched read/write engines.

    Uses init_db.get_engine(bind=...), session.query/get/add/flush/commit/rollback.
    The model must have tenant ``org``, optimistic ``version`` and recoverable
    ``deleted`` columns. Existing company models lacking these need a reviewed
    company adapter; no unscoped fallback or inferred row permission is provided.
    Bulk compare-and-swap updates bypass ORM mapper update/delete listeners.
    Writes therefore require a trusted audit_hook(session, definition, event),
    invoked inside the same transaction. It must persist the payload-free event
    in that session; it must not commit, send mail, or perform external effects.
    The company must explicitly review other lifecycle hooks before adopting
    this adapter. It is not a drop-in replacement for historical unscoped models.
    Every operation obtains and removes/closes its session. Tests use a fake
    session only; this module does not import an Oracle driver or open a network.
    """
    def __init__(self, backend='oracle', session_factory=None, audit_hook=None):
        if (backend not in ('sqlite', 'oracle') or
                (session_factory is not None and not callable(session_factory)) or
                (audit_hook is not None and not callable(audit_hook))):
            raise InvalidInput('Invalid explicit ORM adapter configuration.')
        self.backend, self.session_factory = backend, session_factory
        self.audit_hook = audit_hook

    @contextmanager
    def _session(self, definition, write=False):
        definition.check_bind()
        columns = {column.name for column in definition.model.__table__.columns}
        if not _SERVER_FIELDS.issubset(columns):
            raise AdapterUnavailable('The model needs an explicit tenant/version/delete contract.')
        if write and self.audit_hook is None:
            raise AdapterUnavailable('ORM writes require an explicit transactional audit hook.')
        session = None
        try:
            # This returns a cached engine descriptor, not a connection.
            engine = definition.init_db.get_engine(bind=definition.bind)
            if getattr(getattr(engine, 'dialect', None), 'name', None) != self.backend:
                raise BindingMismatch('Configured engine backend disagrees with the adapter.')
            session = self.session_factory(definition) if self.session_factory else definition.init_db.session
            if session.get_bind(mapper=definition.model) is not engine:
                raise BindingMismatch('Read engine and model write engine disagree.')
            yield session
            if write:
                session.commit()
            else:
                session.rollback()
        except Exception as error:
            if session is not None:
                try:
                    session.rollback()
                except Exception:
                    pass
            if isinstance(error, LegacyCrudError):
                raise
            raise StorageUnavailable('Maintenance storage operation failed.') from None
        finally:
            if session is not None:
                cleanup = getattr(session, 'remove', None) or getattr(session, 'close', None)
                if callable(cleanup):
                    try:
                        cleanup()
                    except Exception:
                        pass

    @staticmethod
    def _query(session, definition, identity):
        return session.query(definition.model).filter_by(org=identity['org'], deleted=False)

    @classmethod
    def _get(cls, session, definition, identity, record_id):
        # session.get alone cannot express the tenant boundary; filter in SQL.
        row = cls._query(session, definition, identity).filter_by(**{definition.primary_key: record_id}).first()
        if row is None:
            raise RecordNotFound('Maintenance record not found.')
        return row

    def query(self, definition, identity, filters, limit, offset):
        with self._session(definition) as session:
            query = self._query(session, definition, identity).filter_by(**filters)
            total = query.count()
            rows = query.order_by(getattr(definition.model, definition.primary_key)).limit(limit).offset(offset).all()
            return {'items': [_record(definition, row) for row in rows], 'total': total,
                    'limit': limit, 'offset': offset}

    def get(self, definition, identity, record_id):
        with self._session(definition) as session:
            return _record(definition, self._get(session, definition, identity, record_id))

    def create(self, definition, identity, payload):
        with self._session(definition, write=True) as session:
            row = definition.model(**dict(self._typed_payload(definition, payload),
                                         org=identity['org'], version=1, deleted=False))
            session.add(row)
            session.flush()
            self._audit(session, definition, identity, 'create', row, payload)
            return _record(definition, row)

    @staticmethod
    def _typed_payload(definition, payload):
        # ORM date columns require Python dates, unlike SQLite's ISO text storage.
        kinds = {item.name: item.kind for item in definition.fields}
        result = dict(payload)
        for name, value in payload.items():
            if value is not None and kinds[name] in ('date', 'datetime'):
                parser = date.fromisoformat if kinds[name] == 'date' else datetime.fromisoformat
                result[name] = parser(value)
        return result

    def _audit(self, session, definition, identity, action, row, fields):
        event = {'definition_key': definition.key, 'org': identity['org'],
                 'actor_hash': hashlib.sha256(identity['id'].encode('utf-8')).hexdigest(),
                 'action': action, 'record_id': getattr(row, definition.primary_key),
                 'version': row.version, 'changed_fields': tuple(sorted(fields)),
                 'at': time.time()}
        self.audit_hook(session, definition, event)

    def _change(self, definition, identity, record_id, version, payload, delete=False):
        with self._session(definition, write=True) as session:
            self._get(session, definition, identity, record_id)
            changes = dict(self._typed_payload(definition, payload), version=version + 1)
            if delete:
                changes['deleted'] = True
            query = self._query(session, definition, identity).filter_by(
                **{definition.primary_key: record_id, 'version': version})
            if query.update(changes, synchronize_session=False) != 1:
                raise RecordConflict('The maintenance record changed. Reload it.')
            # Bulk CAS bypasses identity-map synchronization; reload before result.
            session.expire_all()
            row = session.query(definition.model).filter_by(
                **{definition.primary_key: record_id, 'org': identity['org']}).first()
            if row is None:
                raise RecordNotFound('Maintenance record not found.')
            self._audit(session, definition, identity, 'delete' if delete else 'update', row, payload)
            return _record(definition, row)

    def update(self, definition, identity, record_id, version, payload):
        return self._change(definition, identity, record_id, version, payload)

    def delete(self, definition, identity, record_id, version):
        return self._change(definition, identity, record_id, version, {}, delete=True)


def _fixture_model(table, bind):
    def column(name, kind, primary=False, nullable=True, unique=False):
        classes = {'integer': 'Integer', 'text': 'String', 'boolean': 'Boolean'}
        datatype = type(classes[kind], (), {'length': 255})()
        return SimpleNamespace(name=name, type=datatype, primary_key=primary, nullable=nullable, unique=unique)
    columns = (column('id', 'integer', primary=True), column('code', 'text', nullable=False, unique=True),
               column('description', 'text'), column('enabled', 'boolean'))
    return type('SyntheticMaintenanceModel', (), {'__tablename__': table, '__bind_key__': bind,
               '__table__': SimpleNamespace(name=table, columns=columns)})


def build_fixture_registry(directory, identities=None, count=100):
    """100 demo metadata definitions; 75 usable SQLite, 25 unconnected Oracle.

    Only selecting a usable definition initializes its SQLite table and synthetic
    baseline. Oracle entries have no session/provider and always fail closed.
    This factory must be mounted in demo mode only. No account, engine, scheduler
    or network client is created, and no directory is created until data access.
    The caller creates the already-authorized local fixture directory.
    """
    if not isinstance(directory, (str, os.PathLike)):
        raise InvalidInput('A local fixture directory is required.')
    directory = os.fspath(directory)
    if not directory or '\x00' in directory:
        raise InvalidInput('A local fixture directory is required.')
    count = _integer(count, 1, 1000)
    registry = MaintenanceRegistry(identities)
    policy = MaintenancePolicy(orgs=('A',))
    sqlite_count = max(1, count * 3 // 4)
    databases = {bind: SQLiteDatabase(os.path.join(directory, bind + '.sqlite'), bind)
                 for bind in ('fixture-sqlite-a', 'fixture-sqlite-b', 'fixture-sqlite-c')}
    for number in range(1, count + 1):
        backend = 'sqlite' if number <= sqlite_count else 'oracle'
        bind = ('fixture-sqlite-' + 'abc'[(number - 1) % 3]) if backend == 'sqlite' else 'fixture-oracle-' + str((number - 1) % 2 + 1)
        database = databases[bind] if backend == 'sqlite' else None
        adapter = SQLiteMaintenanceAdapter(database, seed_rows=({'code': 'SYNTH-{:03d}'.format(number),
                    'description': 'Synthetic maintenance fixture', 'enabled': True, 'org': 'A'},)) if database else None
        registry.register(MaintenanceDefinition(
            key='fixture-{:03d}'.format(number), label='合成維護表 {:03d}'.format(number),
            model=_fixture_model('synthetic_maintenance_{:03d}'.format(number), bind),
            init_db=database, bind=bind, backend=backend,
            model_config={'code': {'label': 'Code', 'required': True, 'Update_Lock': True},
                          'description': {'label': 'Description', 'default': ''},
                          'enabled': {'label': 'Enabled', 'default': True}},
            policy_resolver=lambda user, current=policy: current, adapter=adapter))
    return registry.freeze()


def build_synthetic_registry(directory, policy_resolver=None, count=100, identities=None):
    """Alias for the explicit offline fixture factory; optional trusted policies."""
    registry = build_fixture_registry(directory, identities, count)
    if policy_resolver is not None:
        if not callable(policy_resolver):
            raise InvalidInput('A server policy resolver is required.')
        # Re-register immutable definitions instead of mutating a frozen registry.
        from dataclasses import replace
        replacement = MaintenanceRegistry(identities)
        for definition in registry.definitions:
            replacement.register(replace(definition, policy_resolver=policy_resolver))
        return replacement.freeze()
    return registry
