"""Known legacy URLs remain explicit permission fixtures, not business pages."""
from ..registry import AccessPolicy, PageSpec
from .shared import heading


def page1(runtime):
    return heading(PAGE1.title, 'Admin-only compatibility route.')


def page2(runtime):
    return heading(PAGE2.title, 'Role admin/user AND organization A.')


PAGE1 = PageSpec('page1', '/page1', 'Page 1', page1,
                 AccessPolicy.require(roles=('admin',)), nav_order=10, catalog_category='Access fixtures',
                 catalog_description='Admin-only permission fixture. This placeholder contains no business report.',
                 catalog_tags=('fixture', 'admin'))
PAGE2 = PageSpec('page2', '/page2', 'Page 2', page2,
                 AccessPolicy.require(roles=('admin', 'user'), org='A'), nav_order=20, catalog_category='Access fixtures',
                 catalog_description='Organization A permission fixture. This placeholder contains no business report.',
                 catalog_tags=('fixture', 'organization'))
