"""Explicit page catalog. No filesystem scanning or dynamic plugin loading."""
from .overview import SPEC as OVERVIEW
from .authentication import LOGIN, LOGOUT
from .reports import SPEC as REPORTS
from .administration import SPEC as ADMINISTRATION
from .compatibility import PAGE1, PAGE2
from .maintenance import SPEC as MAINTENANCE
from .managed_reports import SPEC as MANAGED_REPORTS


def default_pages():
    return (OVERVIEW, LOGIN, LOGOUT, PAGE1, PAGE2, REPORTS, ADMINISTRATION, MAINTENANCE, MANAGED_REPORTS)
