"""Single-entry QSL maintenance, mounted in the main app's trusted registry.

This page deliberately uses synthetic SQLite providers in demo mode only. It
shares the main Flask-Login session and never installs a second login route.
Production mounting requires an independently reviewed company adapter.
"""
from types import SimpleNamespace

from flask import current_app

from ..registry import AccessPolicy, PageSpec

POLICY = AccessPolicy.require(roles=('admin', 'user'), org='A')


def layout(runtime):
    from ..legacy_demo_ui import _layout
    tree = _layout(integrated=True)
    identity = runtime.identity()
    if not identity or identity['role'] != 'admin':
        write_controls = {
            'qa-open-create', 'qa-open-update', 'qa-open-delete', 'qa-open-upload', 'qa-submit-update', 'qa-create', 'qa-update', 'qa-confirm-update', 'qa-delete',
            'qa-upload', 'qa-import', 'qa-history-load', 'qa-restore-preview',
            'qa-restore-confirm', 'qa-mail-save', 'qa-mail-run', 'qb-save',
        }

        def disable(node):
            if isinstance(node, (list, tuple)):
                for child in node:
                    disable(child)
            elif hasattr(node, 'children') or hasattr(node, 'id'):
                identifier = getattr(node, 'id', None)
                if isinstance(identifier, str) and identifier in write_controls:
                    node.disabled = True
                disable(getattr(node, 'children', None))
        disable(tree)
    return tree


class _RegisteredCallbacks:
    """Collect reused callbacks and consolidate shared outputs before registration.

    The existing main registry intentionally forbids duplicate output targets.
    A connected group (wizard preview/save/load) is therefore one dispatch
    callback, not an exception to that safety rule. All outputs have one owner.
    """
    def __init__(self, registry):
        self.registry = registry
        self.declarations = []

    def callback(self, *dependencies, **kwargs):
        from dash import Input, Output, State
        outputs = [item for item in dependencies if isinstance(item, Output)]
        inputs = [item for item in dependencies if isinstance(item, Input)]
        states = [item for item in dependencies if isinstance(item, State)]
        if len(outputs) + len(inputs) + len(states) != len(dependencies):
            raise ValueError('Unsupported callback dependency')

        def decorate(function):
            self.declarations.append((outputs, inputs, states, function, kwargs))
            return function
        return decorate

    @staticmethod
    def _key(dependency):
        return dependency.component_id_str(), dependency.component_property

    def flush(self):
        groups = []
        for declaration in self.declarations:
            targets = {self._key(item) for item in declaration[0]}
            merged = [declaration]
            retained = []
            for existing, old_targets in groups:
                if targets.intersection(old_targets):
                    merged += existing
                    targets.update(old_targets)
                else:
                    retained.append((existing, old_targets))
            groups = retained + [(merged, targets)]
        for number, (group, _) in enumerate(groups, 1):
            self._register_group(group, number)

    def _register_group(self, group, number):
        from dash import Output, ctx, no_update
        from dash.exceptions import PreventUpdate
        outputs, inputs, states = {}, {}, {}
        for out, inp, state, _, options in group:
            if options != {'prevent_initial_call': True}:
                raise ValueError('Reused maintenance callbacks must suppress initial execution')
            for item in out:
                outputs.setdefault(self._key(item), Output(item.component_id, item.component_property))
            for item in inp:
                inputs.setdefault(self._key(item), item)
            for item in state:
                states.setdefault(self._key(item), item)
        # A property shared as Input and State uses the input's current value.
        states = {key: item for key, item in states.items() if key not in inputs}
        value_keys = list(inputs) + list(states)
        output_keys = list(outputs)

        def dispatch(*values):
            supplied = dict(zip(value_keys, values))
            triggered = {item['prop_id'] for item in ctx.triggered}
            selected = [declaration for declaration in group if any(
                key[0] + '.' + key[1] in triggered for key in map(self._key, declaration[1]))]
            if len(selected) != 1:
                # Ambiguous multi-action requests must never perform two writes.
                raise PreventUpdate
            out, inp, state, function, _ = selected[0]
            result = function(*[supplied[self._key(item)] for item in inp + state])
            if len(out) == 1:
                result = [result]
            if not isinstance(result, (list, tuple)) or len(result) != len(out):
                raise ValueError('Invalid maintenance callback output shape')
            changes = dict(zip((self._key(item) for item in out), result))
            return tuple(changes.get(key, no_update) for key in output_keys)

        self.registry.callback(
            *list(outputs.values()), *list(inputs.values()), *list(states.values()),
            callback_id='qa-maintenance.action-{}'.format(number), policy=POLICY,
            prevent_initial_call=True,
        )(dispatch)


def register_callbacks(callbacks, runtime):
    from ..authorization import current_identity, lookup_identity
    from ..legacy_demo_ui import _register_services_and_callbacks
    from ..legacy_policy import Policy
    server = current_app._get_current_object()
    if not runtime.is_demo:
        raise ValueError('Synthetic QSL integration is demo-only')

    def legacy_identity(identity):
        if not POLICY.allows(identity):
            return SimpleNamespace(id='', orgcode='', is_authenticated=False)
        return SimpleNamespace(
            id=identity['id'], orgcode='ORG_QA01', is_authenticated=True,
            is_dev=identity['role'] == 'admin', is_admin=False,
        )

    def user_resolver():
        # Flask-Login caches its user within a request. Long exports must also
        # recheck the provider within that request, not only on the next one.
        identity = runtime.identity()
        return legacy_identity(current_identity(runtime.identities, identity)
                               if identity is not None else None)

    server.extensions['qa_user_resolver'] = user_resolver
    directory = None
    if runtime.settings.state_path:
        # A distinct sibling directory per workspace prevents unrelated app
        # instances sharing QSL records merely because they share a parent.
        directory = runtime.settings.state_path + '.qa'
    registrar = _RegisteredCallbacks(callbacks)
    policy = Policy(orgcode=['ORG_QA'], crud_roles=['dev'])
    _register_services_and_callbacks(
        registrar, server, directory, user_resolver=user_resolver,
        policy=policy,
    )

    def fresh_policy(user):
        # The trusted synthetic seed above precedes this integration boundary.
        # All subsequent service checks use an exact current main-app identity,
        # including checks while XLSX generation is already in progress.
        expected = legacy_identity(lookup_identity(runtime.identities,
                                                   getattr(user, 'id', None)))
        for name in ('id', 'orgcode', 'is_authenticated', 'is_dev', 'is_admin'):
            value, fresh = getattr(user, name, None), getattr(expected, name, None)
            if type(value) is not type(fresh) or value != fresh:
                raise PermissionError('Access denied')
        if expected.is_authenticated is not True:
            raise PermissionError('Access denied')
        return policy

    server.extensions['qa_demo_crud'].policy_resolver = fresh_policy
    registrar.flush()


SPEC = PageSpec(
    page_id='qa-maintenance', path='/QA_portal/maintenance',
    title='QSL 維護與報表精靈', layout=layout, policy=POLICY,
    nav_label='QSL 維護 / 精靈', nav_order=35,
    register_callbacks=register_callbacks, catalog_category='SQE',
    catalog_description='供應商維護、CSV/XLSX 匯入匯出、差異確認、版本還原及報表精靈。',
    catalog_tags=('QSL', '維護表', '版本', '報表'),
)
