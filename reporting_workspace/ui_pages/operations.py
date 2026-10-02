"""Authenticated operations workbench; all data actions remain service-authorized."""
import json
import zipfile
import os
import tempfile
from pathlib import Path

from dash import Input, Output, State, ctx, html, dcc
from flask import current_app, jsonify
from urllib.parse import quote
import dash_bootstrap_components as dbc
from ..registry import AccessPolicy, PageSpec
from demo_services import AccessDenied
from ..legacy_crud import PermissionDenied, LegacyCrudError, AdapterUnavailable
from ..crud import CrudError, NotFound

POLICY = AccessPolicy.require(roles=('admin', 'user'), org='A')


def panel(title, children):
    return dbc.Card(dbc.CardBody([html.H3(title)] + children), className='mb-3')


def show(value):
    """Render bounded service results as readable sections/tables, never raw HTML."""
    labels = {'added': '新增', 'removed': '移除', 'changed': '異動', 'definition_changes': '設定差異',
              'counts': '影響數量', 'groups': '影響分類', 'matches': '相似報表', 'issues': '品質問題',
              'passed': '品質通過', 'status': '狀態', 'row_count': '筆數', 'baseline_count': '前次筆數',
              'totals': '總計', 'reports': '報表', 'items': '目錄項目', 'total': '總數',
              'last_success': '最近成功', 'next_run': '下次執行', 'duration_seconds': '耗時秒數',
              'runs': '執行紀錄', 'steps': 'ETL 步驟', 'data_freshness_seconds': '資料距今秒數',
              'scheduler_running': '排程執行緒運行中', 'enabled': '已啟用', 'published_summary': '最近成功發布結果'}
    def cell(item):
        if isinstance(item, (dict, list)):
            return json.dumps(item, ensure_ascii=False, default=str)
        return '—' if item is None else str(item)
    if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        keys = list(dict.fromkeys(key for row in value for key in row))
        return html.Div(dbc.Table([html.Thead(html.Tr([html.Th(labels.get(key, key)) for key in keys])),
            html.Tbody([html.Tr([html.Td(cell(row.get(key))) for key in keys]) for row in value])],
            bordered=True, striped=True, hover=True, size='sm'), style={'overflowX': 'auto', 'maxHeight': 480})
    if isinstance(value, dict):
        return html.Div([html.Div([html.Strong(labels.get(key, key) + '：'), show(item)], className='mt-2')
                         for key, item in value.items()])
    if isinstance(value, list):
        return html.Ul([html.Li(cell(item)) for item in value])
    return html.Span(cell(value))


def layout(runtime):
    return html.Div([
        html.H1('報表營運中心'),
        dbc.Alert('合成 SQLite 驗證環境。Oracle 僅介面契約，郵件只寫本機 mock 紀錄。既有 QSL 彈窗保留於原入口。', color='info'),
        html.A('功能 TODO / DONE 與驗收證據', href='/QA_portal/feature-todo', target='_blank'),
        panel('1 · 改動影響預覽', [
            dbc.Label('來源或報表 ID'), dbc.Input(id='ops-impact-id', value='synthetic-monthly'),
            dcc.Dropdown(id='ops-impact-kind', options=[{'label': x, 'value': x} for x in ('source', 'report')], value='source', clearable=False),
            dbc.Input(id='ops-impact-field', placeholder='欄位（留白＝整個來源）'),
            dbc.Button('預覽受影響報表 / 匯出 / API / 排程', id='ops-impact', className='mt-2'), html.Div(id='ops-impact-result')]),
        panel('2 · 報表版本差異', [
            dbc.Input(id='ops-report', value='monthly-performance', placeholder='報表 ID'),
            dbc.Row([dbc.Col(dbc.Input(id='ops-before', type='number', value=1, min=1)), dbc.Col(dbc.Input(id='ops-after', type='number', value=2, min=1))]),
            dbc.Button('比較版本', id='ops-diff', className='mt-2'), html.Div(id='ops-diff-result'),
            dbc.Input(id='ops-record', placeholder='來源紀錄 ID（從差異結果複製）'),
            dbc.Button('查看來源紀錄', id='ops-source', className='mt-2'), html.Div(id='ops-source-result')]),
        panel('3 · 新需求先找既有報表', [
            dbc.Textarea(id='ops-request', placeholder='例如：供應商 材料 異常', maxLength=500),
            dbc.Button('找相似報表', id='ops-search', className='mt-2'), html.Div(id='ops-search-result')]),
        panel('4 · 資料品質與寄信攔截', [
            dcc.Dropdown(id='ops-scenario', options=[{'label': '正常合成快照', 'value': 'clean'}, {'label': '失敗 / 缺欄 / 重複鍵快照', 'value': 'bad'}], value='bad', clearable=False),
            dbc.Button('檢查快照', id='ops-quality', className='mt-2 me-2'),
            dbc.Button('測試 mock 寄送（管理者）', id='ops-send', className='mt-2'), html.Div(id='ops-quality-result')]),
        panel('5 · 使用與成果統計', [
            html.P('只記固定事件與必要歸屬，不記查詢文字或資料列，也不宣稱省下多少工時。'),
            dbc.Button('更新統計', id='ops-usage'), html.Div(id='ops-usage-result')]),
        panel('自動 ETL 排程 / Job 監控', [
            html.P('固定安全範例：來源快照 → 清理與品質檢查 → SQLite 結果發布。正式 app 啟動後可開啟定時執行；不寄外部信件。'),
            dbc.Input(id='ops-job-interval', type='number', min=60, max=86400, value=300),
            dbc.Checkbox(id='ops-job-enabled', label='啟用自動排程', value=False),
            dbc.Button('儲存排程（管理者）', id='ops-job-save', className='me-2'),
            dbc.Button('立即執行範例（管理者）', id='ops-job-run', className='me-2'),
            dbc.Button('更新監控', id='ops-job-status'),
            dcc.Interval(id='ops-job-poll', interval=10000, n_intervals=0),
            html.Div(id='ops-job-result'),
            dbc.Input(id='ops-job-run-id', placeholder='欲匯出診斷的 run_id'),
            dbc.Button('下載去識別診斷 ZIP', id='ops-job-export', className='mt-2'),
            dcc.Download(id='ops-job-download'), html.Div(id='ops-job-export-status'),
            html.H4('Job owner 失敗通知（mock）', className='mt-3'),
            dbc.Input(id='ops-job-owner', value='owner@example.invalid', placeholder='僅接受 example.invalid 合成信箱'),
            dbc.Checkbox(id='ops-job-notify-enabled', label='啟用失敗通知模擬', value=False),
            dbc.Button('儲存 owner 通知（管理者）', id='ops-job-notify-save', className='mt-2'),
            html.Div(id='ops-job-notify-result'),
        ]),
        panel('多維護表 / 多 bind 目錄', [
            html.P('100 個合成定義按需分頁；Oracle 未接線不會宣稱可用。'),
            dbc.Input(id='ops-tables-query', placeholder='表名關鍵字', maxLength=100),
            dbc.Input(id='ops-tables-page', type='number', min=1, value=1),
            dbc.Button('查詢維護表目錄', id='ops-tables', className='mt-2'), html.Div(id='ops-tables-result'),
            dbc.Input(id='ops-table-key', value='fixture-001', placeholder='維護表 key'),
            dbc.Button('讀取所選維護表（前 20 筆）', id='ops-table-read', className='mt-2'), html.Div(id='ops-table-data')]),
    ])


def register_callbacks(callbacks, runtime):
    from ..operations import OperationsService
    server = current_app._get_current_object()
    if runtime.settings.state_path:
        directory = runtime.settings.state_path + '.operations'
        os.makedirs(directory, exist_ok=True)
    else:
        temporary = tempfile.TemporaryDirectory(prefix='portal-operations-')
        server.extensions['operations_temporary'] = temporary
        directory = temporary.name
    service = OperationsService(os.path.join(directory, 'operations.sqlite'), runtime.identities)
    server.extensions['operations_service'] = service
    if runtime.is_demo:
        seed_user = runtime.identities.get_user('demo-admin')
        if seed_user and seed_user.get('org') == 'A' and seed_user.get('role') == 'admin':
            service.seed_demo(seed_user)
    from ..maintenance_registry import build_fixture_registry
    tables = build_fixture_registry(directory, runtime.identities)
    server.extensions['maintenance_registry'] = tables
    from ..job_monitor import JobMonitor
    monitor = JobMonitor(os.path.join(directory, 'job_monitor.sqlite'), runtime.identities)
    server.extensions['job_monitor'] = monitor

    def invoke(function, *args, **kwargs):
        try:
            return show(function(runtime.identity(), *args, **kwargs))
        except (AccessDenied, PermissionDenied):
            return dbc.Alert('沒有權限執行這個操作。', color='danger')
        except AdapterUnavailable:
            return dbc.Alert('此資料來源尚未接線；Oracle 目前只有介面契約，沒有連線到公司資料庫。', color='warning')
        except (ValueError, KeyError, TypeError, LegacyCrudError, CrudError):
            return dbc.Alert('輸入或資料版本不正確，請檢查後重試。', color='warning')

    @callbacks.callback(Output('ops-impact-result', 'children'), Input('ops-impact', 'n_clicks'),
                        State('ops-impact-kind', 'value'), State('ops-impact-id', 'value'), State('ops-impact-field', 'value'),
                        callback_id='operations.impact', policy=POLICY, prevent_initial_call=True)
    def impact(n, kind, identifier, field):
        return invoke(service.impact, kind, identifier, field=field or None)

    @callbacks.callback(Output('ops-diff-result', 'children'), Input('ops-diff', 'n_clicks'),
                        State('ops-report', 'value'), State('ops-before', 'value'), State('ops-after', 'value'),
                        callback_id='operations.diff', policy=POLICY, prevent_initial_call=True)
    def diff(n, report, before, after):
        try:
            result = service.version_diff(runtime.identity(), report, before, after)
            links = []
            for group, version in (('added', after), ('removed', before), ('changed', after)):
                for row in result[group]:
                    record = row['record_id']
                    path = '/QA_portal/source/{}/{}/{}'.format(quote(report, safe=''), version, quote(record, safe=''))
                    links.append(html.Li(html.A('{} · {} · v{}'.format(group, record, version), href=path, target='_blank')))
            return html.Div([show(result), html.H4('授權來源紀錄'), html.Ul(links)])
        except AccessDenied:
            return dbc.Alert('沒有權限。', color='danger')
        except (CrudError, ValueError, TypeError):
            return dbc.Alert('報表或版本不正確。', color='warning')

    @callbacks.callback(Output('ops-source-result', 'children'), Input('ops-source', 'n_clicks'),
                        State('ops-report', 'value'), State('ops-after', 'value'), State('ops-record', 'value'),
                        callback_id='operations.source', policy=POLICY, prevent_initial_call=True)
    def source(n, report, version, record):
        return invoke(service.source_record, report, version, record)

    @callbacks.callback(Output('ops-search-result', 'children'), Input('ops-search', 'n_clicks'), State('ops-request', 'value'),
                        callback_id='operations.search', policy=POLICY, prevent_initial_call=True)
    def search(n, text):
        def action(user):
            result = service.search_catalog(user, text or '')
            # Private wizard definitions remain owner-private. Never copy them
            # to the organization catalog or accept a browser-supplied owner.
            builder = server.extensions.get('qa_demo_builder')
            resolver = server.extensions.get('qa_user_resolver')
            own = []
            if builder is not None and resolver is not None:
                legacy_user = resolver()
                terms = (text or '').casefold().split()
                for record in builder.list_definitions(legacy_user):
                    definition = builder.load(legacy_user, record['id'])['definition']
                    searchable = ' '.join([record['name'], definition['source']] + definition['columns']).casefold()
                    matched = [term for term in terms if term in searchable]
                    if matched:
                        own.append(dict(name=record['name'], id=record['id'], version=record['version'],
                                        matched=matched, suggestion='從 QSL 精靈載入此定義，檢查篩選條件後重用。'))
            result['my_saved_definitions'] = own[:20]
            return result
        return invoke(action)

    @callbacks.callback(Output('ops-quality-result', 'children'), Input('ops-quality', 'n_clicks'), Input('ops-send', 'n_clicks'),
                        State('ops-report', 'value'), State('ops-scenario', 'value'),
                        callback_id='operations.quality', policy=POLICY, prevent_initial_call=True)
    def quality(check, send, report, scenario):
        def action(user):
            fixture = service.quality_fixture(user, report, scenario)
            if ctx.triggered_id == 'ops-send':
                return service.simulate_send(user, report, fixture['rows'], ['tester@example.invalid'], update_failed=fixture['update_failed'])
            return service.check_quality(user, report, fixture['rows'], update_failed=fixture['update_failed'])
        return invoke(action)

    @callbacks.callback(Output('ops-usage-result', 'children'), Input('ops-usage', 'n_clicks'),
                        callback_id='operations.usage', policy=POLICY, prevent_initial_call=True)
    def usage(n):
        return invoke(service.usage_summary)

    @callbacks.callback(Output('ops-tables-result', 'children'), Input('ops-tables', 'n_clicks'),
                        State('ops-tables-query', 'value'), State('ops-tables-page', 'value'),
                        callback_id='operations.tables', policy=POLICY, prevent_initial_call=True)
    def catalog(n, query, page):
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 100:
            return dbc.Alert('頁碼需為 1–100 的整數。', color='warning')
        return invoke(tables.catalog, q=query or '', limit=20, offset=(page-1)*20)

    @callbacks.callback(Output('ops-table-data', 'children'), Input('ops-table-read', 'n_clicks'),
                        State('ops-table-key', 'value'), callback_id='operations.table-read', policy=POLICY,
                        prevent_initial_call=True)
    def table_read(n, key):
        def action(user):
            return dict(definition=tables.describe(user, key), data=tables.query(user, key, limit=20, offset=0))
        return invoke(action)

    @callbacks.callback(Output('ops-job-result', 'children'), Input('ops-job-save', 'n_clicks'),
                        Input('ops-job-run', 'n_clicks'), Input('ops-job-status', 'n_clicks'), Input('ops-job-poll', 'n_intervals'),
                        State('ops-job-enabled', 'value'), State('ops-job-interval', 'value'),
                        callback_id='operations.jobs', policy=POLICY, prevent_initial_call=True)
    def jobs(save, run, refresh, poll, enabled, interval):
        def action(user):
            if ctx.triggered_id == 'ops-job-save':
                return monitor.configure(user, enabled, interval)
            if ctx.triggered_id == 'ops-job-run':
                return monitor.run_now(user)
            return monitor.status(user)
        return invoke(action)

    @callbacks.callback(Output('ops-job-notify-result', 'children'), Input('ops-job-notify-save', 'n_clicks'),
                        State('ops-job-owner', 'value'), State('ops-job-notify-enabled', 'value'),
                        callback_id='operations.job-notify', policy=POLICY, prevent_initial_call=True)
    def notification_settings(n, owner, enabled):
        return invoke(monitor.configure_notification, owner, enabled)

    @callbacks.callback(Output('ops-job-download', 'data'), Output('ops-job-export-status', 'children'),
                        Input('ops-job-export', 'n_clicks'), State('ops-job-run-id', 'value'),
                        callback_id='operations.job-export', policy=POLICY, prevent_initial_call=True)
    def export_job(n, run_id):
        from dash import no_update
        try:
            diagnostic = monitor.export_diagnostics(runtime.identity(), run_id)
            def write_zip(buffer):
                with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr('diagnostic.json', json.dumps(diagnostic, ensure_ascii=False, indent=2))
                    archive.writestr('README.txt', 'Fixed synthetic ETL diagnostic export. Allowlisted run and step evidence only. '
                        'No credentials, environment variables, raw data rows or raw tracebacks. '
                        'This does not certify company ETL or Oracle deployment.\n')
            return dcc.send_bytes(write_zip, 'etl_diagnostic.zip'), '已產生所選批次的去識別診斷檔。'
        except (AccessDenied, PermissionDenied):
            return no_update, '無權限匯出此批次。'
        except (CrudError, LegacyCrudError, ValueError, TypeError):
            return no_update, '批次不存在或無法匯出；請從執行紀錄複製 run_id。'

    @server.route('/QA_portal/source/<report_id>/<int:version>/<record_id>')
    def source_link(report_id, version, record_id):
        if not POLICY.allows(runtime.identity()):
            raise AccessDenied()
        try:
            return jsonify(service.source_record(runtime.identity(), report_id, version, record_id))
        except NotFound:
            return jsonify(error='Source record not found'), 404
        except CrudError:
            return jsonify(error='Source unavailable'), 400

    @server.route('/QA_portal/feature-todo')
    def feature_todo():
        if not POLICY.allows(runtime.identity()):
            raise AccessDenied()
        content = (Path(__file__).resolve().parents[2] / 'FEATURE_TODO.md').read_text(encoding='utf-8')
        return server.response_class(content, mimetype='text/plain')


SPEC = PageSpec(page_id='operations', path='/QA_portal/operations', title='報表營運中心', layout=layout,
                policy=POLICY, nav_label='營運中心 / TODO', nav_order=36, register_callbacks=register_callbacks,
                catalog_category='QA', catalog_description='改動影響、報表差異、需求去重、品質攔截與使用統計。',
                catalog_tags=('品質', '差異', '維護表', 'TODO'))
