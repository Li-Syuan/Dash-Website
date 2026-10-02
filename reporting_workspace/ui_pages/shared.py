"""Presentation helpers only; permissions belong to PageSpec and services."""
from dash import html


def heading(title, subtitle):
    return html.Div([html.P('REPORT WORKSPACE', className='eyebrow'), html.H1(title),
                     html.P(subtitle, className='subtitle')], className='page-heading')


def request_id():
    from flask import g, has_request_context
    return getattr(g, 'request_id', None) if has_request_context() else None
