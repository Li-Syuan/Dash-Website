import unittest
from types import SimpleNamespace as User
from reporting_workspace.legacy_policy import Policy, denied, scope, authorize, guard


def user(**kwargs):
    values = dict(id='qa-reader', is_authenticated=True, orgcode='ORG_QA01')
    values.update(kwargs)
    return User(**values)


class LegacyPolicyTests(unittest.TestCase):
    def test_empty_policy_order(self):
        self.assertTrue(denied(user(is_admin=True)))
        self.assertFalse(denied(user(is_dev=True)))
        self.assertFalse(denied(user(is_test=True)))
        self.assertTrue(denied(user(is_authenticated=False, is_dev=True)))

    def test_or_prefix_and_role_alias(self):
        self.assertFalse(denied(user(), ['ORG_QA'], ['someone']))
        self.assertFalse(denied(user(orgcode='OTHER', id='owner'), ['ORG_QA'], ['owner']))
        self.assertFalse(denied(user(orgcode=None, is_dev=True), ['ORG_QA'], [], ['is_dev']))
        self.assertTrue(denied(user(orgcode=None), ['ORG_QA']))

    def test_admin_entry_not_crud(self):
        p = Policy(orgcode='ORG_QA', crud_roles=['dev'])
        admin = user(orgcode='OTHER', is_admin=True)
        self.assertEqual(scope(admin, p), 'read')
        self.assertFalse(authorize(admin, p, 'delete'))
        self.assertTrue(authorize(admin, p, 'export'))
        self.assertFalse(authorize(admin, p, 'unknown'))

    def test_crud_list_also_grants_entry(self):
        p = Policy(crud_user_ids=['owner'], crud_roles=['dev'])
        self.assertEqual(scope(user(id='owner', orgcode='OTHER'), p), 'crud')
        self.assertEqual(scope(user(orgcode='OTHER', is_dev=True), p), 'crud')

    def test_policy_defensively_copies(self):
        ids = ['owner']
        p = Policy(crud_user_ids=ids)
        ids.append('attacker')
        self.assertEqual(p.crud_user_ids, ('owner',))
        with self.assertRaises(ValueError):
            Policy(user_roles='dev')

    def test_callback_checks_each_invocation(self):
        state = {'user': user(id='owner'), 'policy': Policy(crud_user_ids=['owner'])}
        calls = []
        @guard(lambda: state['user'], lambda u: state['policy'], 'update')
        def save():
            calls.append('saved')
        save()
        state['policy'] = Policy(orgcode=['ORG_QA'])
        with self.assertRaises(PermissionError):
            save()
        state['user'] = user(is_authenticated=False)
        with self.assertRaises(PermissionError):
            save()
        self.assertEqual(calls, ['saved'])

if __name__ == '__main__':
    unittest.main()
