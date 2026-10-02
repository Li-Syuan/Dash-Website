"""Authorized report discovery, independent of report data providers.

Catalog metadata always starts with PageRegistry.catalog(current_identity).
Browser favorites and recent IDs are hints only: they never resolve a route or
expand this authorized set. No report rows, queries or identities are persisted.
"""
import hashlib
import json

from dash import ALL, ClientsideFunction, Input, Output, State, ctx, dcc, html

from ..registry import AccessPolicy, PageSpec
from .shared import heading


POLICY = AccessPolicy.require()
PAGE_SIZE = 12
MAX_PREFERENCES = 100
MAX_QUERY = 160
SHORTCUT_LIMIT = 4


def catalog_scope(user):
    """Opaque per-user/organization/role browser-preference partition."""
    if not POLICY.allows(user):
        return None
    claims = [user['id'], user['org'], user['role']]
    return hashlib.sha256(json.dumps(claims, ensure_ascii=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def clean_preferences(value, scope, allowed):
    """Bound and intersect all browser-controlled IDs before using metadata."""
    cleaned = {'scope': scope, 'favorites': [], 'recent': []}
    if not isinstance(value, dict) or not scope or value.get('scope') != scope:
        return cleaned
    for name in ('favorites', 'recent'):
        source = value.get(name)
        if not isinstance(source, list):
            continue
        seen = set()
        for page_id in source[:MAX_PREFERENCES]:
            if (isinstance(page_id, str) and len(page_id) <= 128
                    and page_id in allowed and page_id not in seen):
                cleaned[name].append(page_id)
                seen.add(page_id)
    return cleaned


def catalog_model(registry, user, query='', category='', view='all', preferences=None,
                  page_state=None, action=None):
    """Pure bounded view model used by the layout and real Dash dispatcher."""
    pages = registry.catalog(user) if POLICY.allows(user) else ()
    by_id = {page.page_id: page for page in pages}
    scope = catalog_scope(user)
    prefs = clean_preferences(preferences, scope, by_id)
    categories = {}
    for page in pages:
        categories[page.catalog_category] = categories.get(page.catalog_category, 0) + 1
    query = query[:MAX_QUERY].strip().casefold() if isinstance(query, str) else ''
    category = category if isinstance(category, str) and category in categories else ''
    view = view if isinstance(view, str) and view in ('all', 'favorites', 'recent') else 'all'
    tokens = query.split()
    filtered = []
    for page in pages:
        text = ' '.join((page.page_id, page.title, page.nav_label or '',
                         page.catalog_description, ' '.join(page.catalog_tags))).casefold()
        if (not category or page.catalog_category == category) and all(token in text for token in tokens):
            filtered.append(page)
    if view == 'favorites':
        chosen = set(prefs['favorites'])
        filtered = [page for page in filtered if page.page_id in chosen]
    elif view == 'recent':
        filtered_ids = {page.page_id for page in filtered}
        filtered = [by_id[page_id] for page_id in prefs['recent'] if page_id in filtered_ids]
    filter_key = hashlib.sha256(json.dumps([query, category, view],
                                          ensure_ascii=True).encode('utf-8')).hexdigest()
    index = 0
    if isinstance(page_state, dict) and page_state.get('filters') == filter_key:
        requested = page_state.get('index')
        if isinstance(requested, int) and not isinstance(requested, bool) and 0 <= requested <= 1000000:
            index = requested
        if action == 'catalog-next':
            index += 1
        elif action == 'catalog-prev':
            index -= 1
    total = len(filtered)
    page_count = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    index = min(max(0, index), page_count - 1)
    start = index * PAGE_SIZE
    visible = filtered[start:start + PAGE_SIZE]
    shortcuts = {}
    if view == 'all' and not query and not category:
        shortcuts = {name: [by_id[page_id] for page_id in prefs[name][:SHORTCUT_LIMIT]]
                     for name in ('favorites', 'recent')}
    return dict(pages=visible, total=total, authorized_total=len(pages), categories=categories,
                preferences=prefs, shortcuts=shortcuts, scope=scope, allowed_ids=list(by_id),
                page_state={'index': index, 'filters': filter_key}, page_count=page_count,
                start=start + 1 if total else 0, end=start + len(visible), view=view)


def _report_link(page, scope, children, class_name):
    # href comes only from validated PageSpec, never from browser preference data.
    return html.A(children, href=page.path, className=class_name,
                  **{'data-report-id': page.page_id, 'data-report-scope': scope})


def _card(page, model):
    favorite = page.page_id in model['preferences']['favorites']
    return html.Article([
        html.Button('★' if favorite else '☆',
                    id={'type': 'catalog-favorite', 'report': page.page_id},
                    n_clicks=0, type='button', className='catalog-favorite',
                    title=('Remove favorite' if favorite else 'Add favorite'),
                    **{'aria-label': ('Remove {} from favorites' if favorite else 'Add {} to favorites').format(page.title),
                       'aria-pressed': 'true' if favorite else 'false'}),
        _report_link(page, model['scope'], [
            html.P(page.catalog_category, className='catalog-card-category'),
            html.H3(page.title, className='catalog-card-title'),
            html.P(page.catalog_description, className='catalog-card-description'),
            html.Ul([html.Li(tag) for tag in page.catalog_tags], className='catalog-tags'),
            html.Span(['Open report ', html.Span('→', **{'aria-hidden': 'true'})],
                      className='catalog-card-action'),
        ], 'catalog-card-link'),
    ], className='catalog-card')


def render_catalog(model):
    sections = []
    for name, title in (('favorites', 'Favorites'), ('recent', 'Recently opened')):
        pages = model['shortcuts'].get(name, [])
        if pages:
            sections.append(html.Section([
                html.H2(title),
                html.Div([_report_link(page, model['scope'], [
                    html.Span(page.title), html.Small(page.catalog_category),
                    html.Span('→', **{'aria-hidden': 'true'}),
                ], 'catalog-shortcut') for page in pages], className='catalog-shortcut-grid'),
            ], className='catalog-section catalog-shortcuts'))
    title = {'all': 'All reports', 'favorites': 'Favorite reports', 'recent': 'Recently opened'}[model['view']]
    if model['pages']:
        contents = html.Div([_card(page, model) for page in model['pages']], className='catalog-grid')
    else:
        message = ('No reports are available for your account.' if not model['authorized_total']
                   else 'No reports match these filters. Try another search or category.')
        if model['view'] == 'favorites':
            message = 'No favorites match these filters. Use the star on a report to save it here.'
        elif model['view'] == 'recent':
            message = 'No recently opened reports match these filters. Open a report to add it here.'
        contents = html.Div([html.H3('Nothing to show yet'), html.P(message)], className='catalog-empty')
    sections.append(html.Section([html.H2(title), contents], className='catalog-section'))
    return sections


def _category_options(model):
    return [{'label': 'All categories ({})'.format(model['authorized_total']), 'value': ''}] + [
        {'label': '{} ({})'.format(name, count), 'value': name}
        for name, count in sorted(model['categories'].items(), key=lambda item: item[0].casefold())]


def _summary(model):
    return '{}–{} of {} reports'.format(model['start'], model['end'], model['total']) if model['total'] else '0 reports'


def layout(runtime):
    model = catalog_model(runtime.page_registry, runtime.identity())
    return [
        heading(SPEC.title, 'Find the report you need. Keep favorites close and pick up where you left off.'),
        dcc.Store(id='catalog-scope', storage_type='memory',
                  data={'scope': model['scope'], 'allowed_ids': model['allowed_ids']}),
        dcc.Store(id='catalog-preferences', storage_type='memory'),
        dcc.Store(id='catalog-page', storage_type='memory', data=model['page_state']),
        html.Div([
            html.Div([
                html.Label('Find a report', htmlFor='catalog-query'),
                dcc.Input(id='catalog-query', type='search', placeholder='Search name, description or tags',
                          value='', maxLength=MAX_QUERY, debounce=True, className='catalog-search'),
            ], className='catalog-search-field'),
            html.Div([
                html.Label('Category', htmlFor='catalog-category'),
                dcc.Dropdown(id='catalog-category', options=_category_options(model), value='',
                             clearable=False, searchable=False),
            ], className='catalog-category-field'),
        ], className='catalog-toolbar'),
        html.Div([
            dcc.RadioItems(id='catalog-view', options=[
                {'label': 'All reports', 'value': 'all'}, {'label': 'Favorites', 'value': 'favorites'},
                {'label': 'Recently opened', 'value': 'recent'},
            ], value='all', inline=True, className='catalog-filters'),
            html.P(_summary(model), id='catalog-summary', role='status', **{'aria-live': 'polite'}),
        ], className='catalog-meta'),
        html.Div(render_catalog(model), id='catalog-results'),
        html.Div([
            html.Button('Previous', id='catalog-prev', n_clicks=0, disabled=True, type='button',
                        **{'aria-label': 'Previous report page'}),
            html.Span('Page 1 of {}'.format(model['page_count']), id='catalog-page-label'),
            html.Button('Next', id='catalog-next', n_clicks=0, disabled=model['page_count'] <= 1, type='button',
                        **{'aria-label': 'Next report page'}),
        ], className='catalog-pagination'),
        html.P('Favorites and recently opened reports are saved in this browser for this account. '
               'Only report IDs are stored; report data is never saved here.', className='catalog-preference-note'),
    ]


def register_callbacks(callbacks, runtime):
    callbacks.clientside_callback(
        ClientsideFunction(namespace='workspace_catalog', function_name='preferences'),
        Output('catalog-preferences', 'data'),
        Input({'type': 'catalog-favorite', 'report': ALL}, 'n_clicks'), Input('catalog-scope', 'data'),
        State('catalog-preferences', 'data'), callback_id='catalog.preferences',
        policy=AccessPolicy.public(), page_id='overview',
    )

    @callbacks.callback(
        Output('catalog-results', 'children'), Output('catalog-summary', 'children'),
        Output('catalog-page-label', 'children'), Output('catalog-prev', 'disabled'),
        Output('catalog-next', 'disabled'), Output('catalog-page', 'data'),
        Output('catalog-category', 'options'),
        Input('catalog-query', 'value'), Input('catalog-category', 'value'), Input('catalog-view', 'value'),
        Input('catalog-preferences', 'data'), Input('catalog-prev', 'n_clicks'), Input('catalog-next', 'n_clicks'),
        State('catalog-page', 'data'), callback_id='catalog.render', policy=POLICY, page_id='overview',
    )
    def update_catalog(query, category, view, preferences, previous, following, page_state):
        model = catalog_model(runtime.page_registry, runtime.identity(), query, category, view,
                              preferences, page_state, ctx.triggered_id)
        index = model['page_state']['index']
        return (render_catalog(model), _summary(model),
                'Page {} of {}'.format(index + 1, model['page_count']), index == 0,
                index + 1 >= model['page_count'], model['page_state'], _category_options(model))


SPEC = PageSpec('overview', '/', 'Report catalog', layout, POLICY,
                nav_label='Reports', nav_order=0, register_callbacks=register_callbacks)
