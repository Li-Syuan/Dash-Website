"""App-owned page and callback declarations with explicit, fail-closed policy.

This module never creates an application, discovers modules, or touches Dash's
global page/callback registries. Build registries inside the application factory,
register trusted Python callables, and freeze them before serving requests.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from functools import wraps
import re
from types import MappingProxyType
from typing import Callable, Optional, Tuple
import unicodedata

from demo_services import AccessDenied


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_PATH = re.compile(r"/(?:[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*(?:/[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)*)?\Z")


def _text(value, maximum):
    return (isinstance(value, str) and 0 < len(value) <= maximum
            and bool(value.strip())
            and not any(unicodedata.category(char) in ('Cc', 'Cs') for char in value))


def _identifier(value, name):
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError('{} must be a bounded identifier'.format(name))


@dataclass(frozen=True)
class AccessPolicy:
    """Authentication AND optional role membership AND optional organization.

    ``None`` means an unrestricted role/organization, never an implicit public
    policy. Public access must be declared with ``public()`` or an explicit False.
    Role collections are copied into tuples so callers cannot mutate a policy.
    """

    authenticated: bool
    roles: Optional[Tuple[str, ...]] = None
    org: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.authenticated, bool):
            raise ValueError('authenticated must be an explicit boolean')
        if not self.authenticated and (self.roles is not None or self.org is not None):
            raise ValueError('public policies cannot restrict roles or organization')
        if self.roles is not None:
            if (not isinstance(self.roles, (tuple, list)) or not 1 <= len(self.roles) <= 64
                    or any(not _text(role, 64) for role in self.roles)
                    or len(set(self.roles)) != len(self.roles)):
                raise ValueError('roles must contain 1 to 64 distinct, bounded role strings')
            object.__setattr__(self, 'roles', tuple(self.roles))
        if self.org is not None and not _text(self.org, 128):
            raise ValueError('org must be non-empty text of at most 128 characters')

    @classmethod
    def public(cls):
        return cls(authenticated=False)

    @classmethod
    def require(cls, roles=None, org=None):
        return cls(authenticated=True, roles=roles, org=org)

    def allows(self, user):
        """Evaluate a validated identity mapping; missing/malformed claims deny.

        Public policy does not inspect ``user`` at all. Protected policies accept
        the same id/role/org shape as the application's identity providers.
        """
        if not self.authenticated:
            return True
        if not isinstance(user, Mapping):
            return False
        if any(not _text(user.get(name), maximum)
               for name, maximum in (('id', 255), ('role', 64), ('org', 128))):
            return False
        return ((self.roles is None or user['role'] in self.roles)
                and (self.org is None or user['org'] == self.org))

    def at_least_as_restrictive_as(self, other):
        """Whether every identity this policy permits is also permitted by other."""
        if not isinstance(other, AccessPolicy):
            raise ValueError('comparison requires an AccessPolicy')
        if not other.authenticated:
            return True
        if not self.authenticated:
            return False
        if other.roles is not None:
            if self.roles is None or not set(self.roles).issubset(other.roles):
                return False
        return other.org is None or self.org == other.org


@dataclass(frozen=True)
class PageSpec:
    """A trusted, app-local page definition with opt-in discovery metadata.

    A missing nav_label hides its navigation link. A missing category/description
    pair omits it from the catalog; neither choice changes its access policy.
    """

    page_id: str
    path: str
    title: str
    layout: Callable
    policy: AccessPolicy
    nav_label: Optional[str] = None
    nav_order: int = 0
    register_callbacks: Optional[Callable] = None
    catalog_category: Optional[str] = None
    catalog_description: Optional[str] = None
    catalog_tags: Tuple[str, ...] = ()

    def __post_init__(self):
        _identifier(self.page_id, 'page_id')
        if (not isinstance(self.path, str) or len(self.path) > 512
                or _PATH.fullmatch(self.path) is None):
            raise ValueError('path must be a canonical absolute page path')
        if not _text(self.title, 255):
            raise ValueError('title must be non-empty text of at most 255 characters')
        if not callable(self.layout):
            raise ValueError('layout must be an explicit callable')
        if not isinstance(self.policy, AccessPolicy):
            raise ValueError('every page requires an explicit AccessPolicy')
        if self.nav_label is not None and not _text(self.nav_label, 128):
            raise ValueError('nav_label must be non-empty text of at most 128 characters')
        if isinstance(self.nav_order, bool) or not isinstance(self.nav_order, int):
            raise ValueError('nav_order must be an integer')
        if self.register_callbacks is not None and not callable(self.register_callbacks):
            raise ValueError('register_callbacks must be an explicit callable')
        if (self.catalog_category is None) != (self.catalog_description is None):
            raise ValueError('catalog_category and catalog_description must be provided together')
        if self.catalog_category is not None:
            if not _text(self.catalog_category, 64):
                raise ValueError('catalog_category must be non-empty text of at most 64 characters')
            if not _text(self.catalog_description, 240):
                raise ValueError('catalog_description must be non-empty text of at most 240 characters')
        if (not isinstance(self.catalog_tags, (tuple, list)) or len(self.catalog_tags) > 12
                or any(not _text(tag, 32) for tag in self.catalog_tags)
                or len(set(self.catalog_tags)) != len(self.catalog_tags)):
            raise ValueError('catalog_tags must contain up to 12 distinct, bounded tag strings')
        if self.catalog_tags and self.catalog_category is None:
            raise ValueError('catalog_tags require catalog_category and catalog_description')
        object.__setattr__(self, 'catalog_tags', tuple(self.catalog_tags))


class PageRegistry:
    """A single application's explicitly registered, immutable page metadata."""

    def __init__(self):
        self._by_id = {}
        self._by_path = {}
        self._frozen = False

    @property
    def frozen(self):
        return self._frozen

    @property
    def pages(self):
        return tuple(self._by_id.values())

    def register(self, spec):
        if self._frozen:
            raise RuntimeError('page registry is frozen')
        if not isinstance(spec, PageSpec):
            raise ValueError('register requires a PageSpec instance')
        if spec.page_id in self._by_id:
            raise ValueError('duplicate page_id: {}'.format(spec.page_id))
        if spec.path in self._by_path:
            raise ValueError('duplicate page path: {}'.format(spec.path))
        self._by_id[spec.page_id] = spec
        self._by_path[spec.path] = spec
        return spec

    def get(self, path):
        return self._by_path.get(path) if isinstance(path, str) else None

    def get_by_id(self, page_id):
        return self._by_id.get(page_id) if isinstance(page_id, str) else None

    def navigation(self, user):
        return tuple(sorted(
            (page for page in self._by_id.values()
             if page.nav_label is not None and page.policy.allows(user)),
            key=lambda page: (page.nav_order, page.page_id),
        ))

    def catalog(self, user):
        """Only authorized, explicitly catalogued pages; never hidden metadata."""
        return tuple(sorted(
            (page for page in self._by_id.values()
             if page.catalog_category is not None and page.policy.allows(user)),
            key=lambda page: (page.nav_order, page.page_id),
        ))

    def freeze(self):
        self._frozen = True
        return self


@dataclass(frozen=True)
class CallbackSpec:
    callback_id: str
    output_key: str
    policy: AccessPolicy
    page_id: Optional[str] = None
    kind: str = 'server'


@dataclass(frozen=True)
class _PageCallbacks:
    """Narrow registrar interface that cannot accidentally omit its page scope.

    This protects trusted page authors from registration mistakes. It is not an
    isolation boundary against Python code deliberately accessing private state.
    """

    _registry: 'CallbackRegistry'
    _page_id: str

    def callback(self, *dependencies, callback_id, policy, **kwargs):
        page_id = kwargs.pop('page_id', self._page_id)
        if page_id != self._page_id:
            raise ValueError('page-bound callback cannot change or remove its page_id')
        return self._registry.callback(
            *dependencies, callback_id=callback_id, policy=policy,
            page_id=self._page_id, **kwargs
        )

    def clientside_callback(self, clientside_function, *dependencies, callback_id, policy, **kwargs):
        page_id = kwargs.pop('page_id', self._page_id)
        if page_id != self._page_id:
            raise ValueError('page-bound callback cannot change or remove its page_id')
        return self._registry.clientside_callback(
            clientside_function, *dependencies, callback_id=callback_id,
            policy=policy, page_id=self._page_id, **kwargs
        )


def _output_targets(entry):
    """Use Dash's registered dependencies, not hand-built callback-map keys."""
    output = entry.get('output')
    outputs = output if isinstance(output, (tuple, list)) else (output,)
    targets = []
    for dependency in outputs:
        if not callable(getattr(dependency, 'component_id_str', None)):
            raise ValueError('registered callback has an unsupported output')
        targets.append((dependency.component_id_str(), dependency.component_property))
    if not targets or len(set(targets)) != len(targets):
        raise ValueError('callback must have distinct output targets')
    return tuple(targets)


class CallbackRegistry:
    """Explicit callback declarations attached to actual Dash output keys.

    Register only through ``callback``. ``freeze`` verifies both Dash's callback
    map and dependency list to catch accidental direct ``app.callback`` calls.
    Public clientside callbacks are presentation-only and never receive an HTTP
    dispatch policy. Runtime ``policy_for`` also denies a server entry replaced
    after the build. This is a build-time guardrail for trusted application code,
    not a plugin sandbox.
    """

    def __init__(self, app, page_registry, identity_getter):
        if not isinstance(page_registry, PageRegistry):
            raise ValueError('page_registry must be a PageRegistry')
        if not callable(identity_getter):
            raise ValueError('identity_getter must be callable')
        if (not callable(getattr(app, 'callback', None))
                or not isinstance(getattr(app, 'callback_map', None), dict)
                or not isinstance(getattr(app, '_callback_list', None), list)):
            raise ValueError('app must be an explicitly constructed Dash application')
        self._app = app
        self._pages = page_registry
        self._identity_getter = identity_getter
        self._by_output = {}
        self._by_id = {}
        self._entries = {}
        self._functions = {}
        self._records = {}
        self._client_descriptors = {}
        self._targets = set()
        self._frozen = False

    @property
    def frozen(self):
        return self._frozen

    @property
    def callbacks(self):
        return MappingProxyType(self._by_output)

    def for_page(self, page_id):
        """Bind a page registrar to its registered page and mandatory policy.

        Pass this facade to PageSpec.register_callbacks. Shell-level callbacks
        may use the unbound registry directly. Matching explicit page_id values
        remain supported; an explicit None or a different page_id is rejected.
        """
        if self._frozen:
            raise RuntimeError('callback registry is frozen')
        if self._pages.get_by_id(page_id) is None:
            raise ValueError('callback registrar refers to an unregistered page')
        return _PageCallbacks(self, page_id)

    def _validate_declaration(self, callback_id, policy, page_id, enforce_page_policy=True):
        if self._frozen:
            raise RuntimeError('callback registry is frozen')
        _identifier(callback_id, 'callback_id')
        if callback_id in self._by_id:
            raise ValueError('duplicate callback_id: {}'.format(callback_id))
        if not isinstance(policy, AccessPolicy):
            raise ValueError('every callback requires an explicit AccessPolicy')
        if page_id is not None:
            page = self._pages.get_by_id(page_id)
            if page is None:
                raise ValueError('callback refers to an unregistered page')
            if enforce_page_policy and not policy.at_least_as_restrictive_as(page.policy):
                raise ValueError('callback policy cannot be weaker than its page policy')

    def _register_dash(self, register, kind, client_descriptor=None):
        """Capture Dash's own keys transactionally for either callback kind."""
        old_map = dict(self._app.callback_map)
        old_list = list(self._app._callback_list)
        try:
            register()
            new_map = self._app.callback_map
            new_keys = set(new_map) - set(old_map)
            if (len(new_keys) != 1 or set(old_map) - set(new_map)
                    or any(new_map[key] is not value for key, value in old_map.items())):
                raise ValueError('duplicate or ambiguous callback output key')
            output_key = next(iter(new_keys))
            entry = new_map[output_key]
            added = self._app._callback_list[len(old_list):]
            if (self._app._callback_list[:len(old_list)] != old_list
                    or len(added) != 1 or added[0].get('output') != output_key):
                raise ValueError('callback registration did not produce one callback')
            record = added[0]
            if kind == 'server':
                if (not callable(entry.get('callback'))
                        or record.get('clientside_function') is not None):
                    raise ValueError('callback registration did not produce a server callback')
            elif ('callback' in entry or not isinstance(record.get('clientside_function'), dict)
                  or record['clientside_function'] != client_descriptor):
                raise ValueError('callback registration did not produce the declared clientside function')
            targets = _output_targets(entry)
            existing_targets = set(self._targets)
            for old_entry in old_map.values():
                existing_targets.update(_output_targets(old_entry))
            if existing_targets.intersection(targets):
                raise ValueError('duplicate callback output target')
        except BaseException:
            # Dash may overwrite an existing output before returning its
            # decorator. Restore it so even a caught build error is safe.
            self._app.callback_map.clear()
            self._app.callback_map.update(old_map)
            self._app._callback_list[:] = old_list
            raise
        return output_key, entry, record, targets

    def _record_registration(self, callback_id, output_key, policy, page_id, kind, entry, record, targets):
        spec = CallbackSpec(callback_id, output_key, policy, page_id, kind)
        self._by_output[output_key] = spec
        self._by_id[callback_id] = spec
        self._entries[output_key] = entry
        self._functions[output_key] = entry.get('callback')
        self._records[output_key] = record
        if kind == 'client':
            descriptor = record['clientside_function']
            self._client_descriptors[output_key] = (descriptor, dict(descriptor))
        self._targets.update(targets)
        return spec

    def callback(self, *dependencies, callback_id, policy, page_id=None, **kwargs):
        self._validate_declaration(callback_id, policy, page_id)

        def decorate(function):
            self._validate_declaration(callback_id, policy, page_id)
            if not callable(function):
                raise ValueError('callback function must be callable')

            @wraps(function)
            def guarded(*args, **function_kwargs):
                # Login callbacks and shell controls must work outside an
                # authenticated session without consulting current_user.
                if policy.authenticated and not policy.allows(self._identity_getter()):
                    raise AccessDenied('Access denied')
                return function(*args, **function_kwargs)

            output_key, entry, record, targets = self._register_dash(
                lambda: self._app.callback(*dependencies, **kwargs)(guarded), 'server')
            self._record_registration(callback_id, output_key, policy, page_id,
                                      'server', entry, record, targets)
            return guarded

        return decorate

    def clientside_callback(self, clientside_function, *dependencies, callback_id, policy, page_id=None, **kwargs):
        """Register a named, explicitly public presentation callback immediately.

        Use Dash.ClientsideFunction with code in a local asset. Inline JavaScript
        strings and authenticated policies are rejected: browser execution is
        not an authorization boundary and must not perform server data access.
        A page_id records ownership, including ownership by a protected page; it
        never grants server access or changes the explicitly public execution.
        The returned CallbackSpec is metadata, not a Python callback decorator.
        """
        from dash import ClientsideFunction

        self._validate_declaration(callback_id, policy, page_id, enforce_page_policy=False)
        if policy.authenticated:
            raise ValueError('clientside callbacks require an explicit public policy')
        if not isinstance(clientside_function, ClientsideFunction):
            raise ValueError('clientside callback requires an explicit Dash.ClientsideFunction')
        _identifier(clientside_function.namespace, 'clientside namespace')
        _identifier(clientside_function.function_name, 'clientside function_name')
        descriptor = dict(namespace=clientside_function.namespace,
                          function_name=clientside_function.function_name)
        output_key, entry, record, targets = self._register_dash(
            lambda: self._app.clientside_callback(clientside_function, *dependencies, **kwargs),
            'client', descriptor)
        return self._record_registration(callback_id, output_key, policy, page_id,
                                         'client', entry, record, targets)

    def policy_for(self, output_key):
        """Return a server dispatch policy; client-only and unknown keys deny."""
        if not isinstance(output_key, str):
            return None
        spec = self._by_output.get(output_key)
        if spec is None or spec.kind != 'server':
            return None
        entry = self._app.callback_map.get(output_key)
        if (entry is not self._entries[output_key]
                or entry.get('callback') is not self._functions[output_key]):
            return None
        return spec.policy

    def freeze(self):
        """Reject undeclared/replaced callbacks, then close both registries."""
        actual = self._app.callback_map
        if set(actual) != set(self._by_output):
            raise ValueError('every Dash callback must have a declared policy')
        listed = [entry.get('output') for entry in self._app._callback_list]
        if len(listed) != len(actual) or set(listed) != set(actual):
            raise ValueError('Dash callback dependencies do not match declared policies')
        for record in self._app._callback_list:
            key = record['output']
            spec = self._by_output[key]
            entry = actual[key]
            if record is not self._records[key] or entry is not self._entries[key]:
                raise ValueError('a declared Dash callback was replaced outside its registry')
            if spec.kind == 'server':
                if self.policy_for(key) is None or record.get('clientside_function') is not None:
                    raise ValueError('a declared server callback was replaced outside its registry')
            else:
                descriptor, expected = self._client_descriptors[key]
                if ('callback' in entry or record.get('clientside_function') is not descriptor
                        or descriptor != expected):
                    raise ValueError('a declared clientside callback was replaced outside its registry')
        self._pages.freeze()
        self._frozen = True
        return self
