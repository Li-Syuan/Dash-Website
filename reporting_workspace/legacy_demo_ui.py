"""Offline integration UI. Authentication is a deliberately synthetic server session.

Run only on loopback. No production modules, credentials, SMTP or databases are used.
The production adapter must replace demo login, storage and mock mail together.
"""
import base64
import json
import secrets
from types import SimpleNamespace

from dash import Dash, Input, Output, State, ctx, dcc, html, dash_table, no_update
import dash_bootstrap_components as dbc
import dash_mantine_components as dmc
from flask import Flask, abort, redirect, request, session
from markupsafe import escape

from .qa_enhancements_ui import revision_panel, wizard_panel, register_enhancements, render_diff

PREFIX = '/QA_portal/'
FIELDS = ['Material_Type', 'Vendor_Code', 'Vendor_Name', 'Country', 'City', 'Rev', 'Supplier_Level']
DEMO_USERS = {
    'reader': dict(id='reader', orgcode='ORG_QA01', is_authenticated=True),
    'editor': dict(id='owner01', orgcode='ORG_OTHER', is_authenticated=True),
    'developer': dict(id='developer', orgcode='ORG_OTHER', is_authenticated=True, is_dev=True),
    'admin': dict(id='admin', orgcode='ORG_OTHER', is_authenticated=True, is_admin=True),
    'outsider': dict(id='outsider', orgcode='ORG_OTHER', is_authenticated=True),
}


def current_demo_user():
    """Only resolve an allowlisted identity from the signed server session."""
    record = DEMO_USERS.get(session.get('qa_demo_identity'))
    return SimpleNamespace(**record) if record else SimpleNamespace(id='', orgcode='', is_authenticated=False)


def _panel(title, children):
    return dmc.Card([dmc.Title(title, order=3), dmc.Space(h=12)] + children,
                    withBorder=True, shadow='sm', radius='md', style={'marginBottom': 20})


def _layout():
    defaults = dict(Material_Type='IC', Vendor_Code='SYN003', Vendor_Name='Synthetic Vendor Three',
                    Country='TW', City='Taoyuan', Rev='A', Supplier_Level='LEVEL 2')
    return dmc.MantineProvider(children=html.Main([
        dmc.Title('QA Portal · 整合示範', order=1),
        dmc.Text('離線合成資料。無 Oracle、LDAP、SMTP 或背景排程。正式環境請替換 adapter。', color='dimmed'),
        html.P([html.A('切換模擬身分 / 登出', href=PREFIX + 'demo-login'),
                ' · reader 唯讀 / editor、developer 可維護 / admin 僅入場 / outsider 拒絕']),
        _panel('QSL 供應商資料', [
            dmc.TextInput(id='qa-filter', label='篩選供應商名稱（字面包含）', value=''),
            dmc.Group([dmc.Button('重新查詢', id='qa-read'), dmc.Button('匯出篩選 CSV', id='qa-export'), dmc.Button('匯出篩選 XLSX', id='qa-export-xlsx'), dmc.Button('下載 CSV 模板', id='qa-template')], spacing='sm', mt=12),
            html.Div(id='qa-status', role='status', style={'marginTop': 12}),
            dash_table.DataTable(id='qa-table', data=[], columns=[{'name': f, 'id': f} for f in ['id', 'version'] + FIELDS],
                                 page_size=10, style_table={'overflowX': 'auto'},
                                 style_cell={'textAlign': 'left', 'padding': 10, 'fontFamily': 'sans-serif'}),
            dcc.Download(id='qa-download'),
        ]),
        _panel('新增 / 修改 / 刪除', [
            dmc.Text('所有操作都由伺服器重新判斷權限；隱藏按鈕不是安全邊界。', size='sm'),
            dmc.NumberInput(id='qa-record-id', label='修改 / 刪除的 ID', min=1, value=1),
            dmc.Button('查詢 ID 並載入表單', id='qa-load-id', mt=8),
            html.Div(id='qa-load-status', role='status'),
            dmc.NumberInput(id='qa-record-version', label='目前 version（查詢後填入，避免覆蓋別人的更新）', min=1, value=1),
            dmc.SimpleGrid([dmc.TextInput(id='qa-field-' + f, label=f, value=defaults[f]) for f in FIELDS], cols=3, spacing='md'),
            dmc.Group([dmc.Button('新增', id='qa-create'), dmc.Button('預覽修改差異', id='qa-update', color='orange')], spacing='sm', mt=12),
            dcc.Store(id='qa-change-token', storage_type='memory'),
            html.Div(id='qa-change-preview', role='status', style={'marginTop': 12}),
            dmc.Group([dmc.Button('確認套用已預覽修改', id='qa-confirm-update', color='orange'),
                       dmc.Button('取消預覽', id='qa-cancel-update', variant='outline')], spacing='sm', mt=8),
            dmc.Checkbox(id='qa-delete-confirm', label='確認刪除指定 ID（僅示範資料）', checked=False, mt=12),
            dmc.Button('刪除指定 ID', id='qa-delete', color='red', mt=8),
        ]),
        revision_panel(),
        wizard_panel(),
        _panel('CSV / XLSX 暫存匯入', [
            dmc.Text('先看新增／更新／衝突摘要與前 10 筆差異，再提交整批；有錯誤時全部回滾。暫存 token 綁定登入者與資料表。', size='sm'),
            dcc.Upload(id='qa-upload', children=html.Button('選擇 CSV / XLSX'), accept='.csv,.xlsx', multiple=False),
            html.Pre(id='qa-preview', style={'whiteSpace': 'pre-wrap', 'maxHeight': 260, 'overflow': 'auto'}),
            dcc.Store(id='qa-stage-token', storage_type='memory'),
            dmc.Button('提交已預覽匯入', id='qa-import', mt=8),
        ]),
        _panel('模擬報表與寄信流程', [
            dmc.Text('手動執行 ETL → 產生報表 → mock mailbox；不會寄出真實郵件。', size='sm'),
            dcc.Store(id='qa-mail-version', data=0),
            dmc.Select(id='qa-mail-cadence', label='模擬週期', value='daily', data=['daily', 'weekly', 'quarterly']),
            dmc.TextInput(id='qa-mail-time', label='執行時刻 HH:MM', value='10:00'),
            dmc.Select(id='qa-mail-timezone', label='時區', value='Asia/Taipei', data=['Asia/Taipei', 'UTC']),
            dmc.NumberInput(id='qa-mail-weekday', label='每週星期（0=一、6=日）', value=0, min=0, max=6),
            dmc.Checkbox(id='qa-mail-enabled', label='排程設定啟用（僅預覽，不啟動背景排程）', checked=True),
            dmc.Select(id='qa-mail-delivery', label='模擬寄信結果', value='accepted', data=['accepted', 'partial', 'uncertain', 'failure']),
            dmc.Checkbox(id='qa-source-ok', label='模擬來源資料成功', checked=True),
            dmc.Checkbox(id='qa-report-ok', label='模擬報表產生成功', checked=True),
            dmc.TextInput(id='qa-mail-to', label='模擬收件人（僅允許 .invalid）', value='qa@example.invalid'),
            dmc.TextInput(id='qa-run-key', label='冪等執行鍵（相同鍵避免重複寄送）', value='demo-daily-001'),
            dmc.Group([dmc.Button('儲存模擬設定', id='qa-mail-save'), dmc.Button('執行一次', id='qa-mail-run'),
                       dmc.Button('刷新紀錄', id='qa-mail-refresh')], spacing='sm', mt=12),
            html.Pre(id='qa-mail-result', style={'whiteSpace': 'pre-wrap'}),
        ]),
    ], style={'maxWidth': 1180, 'margin': '24px auto', 'padding': 20, 'background': '#f5f7fb'}))


def _install_login(server):
    @server.route(PREFIX + 'demo-login', methods=['GET', 'POST'])
    def demo_login():
        if request.method == 'POST':
            expected = session.get('qa_demo_csrf')
            if not expected or not secrets.compare_digest(expected, request.form.get('csrf', '')):
                abort(403)
            identity = request.form.get('identity')
            if identity not in DEMO_USERS and identity != 'anonymous':
                abort(400)
            session.clear()
            if identity != 'anonymous':
                session['qa_demo_identity'] = identity
            return redirect(PREFIX)
        csrf = secrets.token_urlsafe(24)
        session['qa_demo_csrf'] = csrf
        choices = ''.join('<option value="{0}">{0}</option>'.format(escape(name))
                          for name in list(DEMO_USERS) + ['anonymous'])
        return ('<h1>Offline demo identity</h1><p>僅供合成資料示範，不是真實登入。</p>'
                '<form method="post"><input type="hidden" name="csrf" value="{}">'
                '<select name="identity">{}</select><button>登入模擬身分</button></form>').format(csrf, choices)


def create_demo(data_directory=None):
    """Build an isolated application; never starts a server on import."""
    server = Flask(__name__)
    server.secret_key = secrets.token_bytes(32)
    server.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict', MAX_CONTENT_LENGTH=15 * 1024 * 1024)
    _install_transport_guard(server)
    _install_login(server)
    app = Dash(__name__, server=server, url_base_pathname=PREFIX)
    app.layout = _layout
    _register_services_and_callbacks(app, server, data_directory)
    return app


def _register_services_and_callbacks(app, server, data_directory=None):
    import os
    import tempfile
    from .legacy_policy import Policy, authorize
    from .legacy_jobs import LegacyJobAdapter

    policy = Policy(orgcode=['ORG_QA'], crud_user_ids=['owner01'], crud_roles=['dev'])
    temporary = tempfile.TemporaryDirectory(prefix='qa-portal-synthetic-')
    server.extensions['qa_demo_temporary'] = temporary
    directory = temporary.name if data_directory is None else os.path.abspath(data_directory)
    os.makedirs(directory, exist_ok=True)
    server.extensions['qa_demo_directory'] = directory

    def job_authorize(actor, action, job_id):
        user = current_demo_user()
        if actor != getattr(user, 'id', None) or job_id != 'qsl-demo':
            return False
        return authorize(user, policy, 'read' if action == 'read' else 'crud')

    jobs = LegacyJobAdapter(os.path.join(directory, 'jobs.sqlite'), job_authorize)
    server.extensions['qa_demo_jobs'] = jobs

    @app.callback(Output('qa-mail-result', 'children'), Output('qa-mail-version', 'data'),
                  Input('qa-mail-save', 'n_clicks'), Input('qa-mail-run', 'n_clicks'),
                  Input('qa-mail-refresh', 'n_clicks'), State('qa-mail-to', 'value'),
                  State('qa-run-key', 'value'), State('qa-mail-version', 'data'),
                  State('qa-mail-cadence', 'value'), State('qa-mail-time', 'value'), State('qa-mail-timezone', 'value'),
                  State('qa-mail-weekday', 'value'), State('qa-mail-enabled', 'checked'), State('qa-mail-delivery', 'value'),
                  State('qa-source-ok', 'checked'), State('qa-report-ok', 'checked'),
                  prevent_initial_call=True)
    def mail_action(save, run, refresh, recipients, run_key, version, cadence, time_value, timezone, weekday, enabled, delivery, source_ok, report_ok):
        actor = current_demo_user().id
        try:
            action = ctx.triggered_id
            if action == 'qa-mail-save':
                result = jobs.save_settings(actor, 'qsl-demo', {
                    'cadence': cadence, 'time': time_value, 'timezone': timezone,
                    'weekday': weekday if cadence == 'weekly' else None, 'recipients': [item.strip() for item in (recipients or '').split(',')],
                    'enabled': enabled,
                }, expected_version=version)
            elif action == 'qa-mail-run':
                result = jobs.run_mock(actor, 'qsl-demo', run_key, source_ok=source_ok, report_ok=report_ok, delivery=delivery)
            else:
                result = {'settings': jobs.get_settings(actor, 'qsl-demo'),
                          'runs': jobs.list_runs(actor, 'qsl-demo'),
                          'audit': jobs.audit_log(actor, 'qsl-demo')}
                from datetime import datetime, timezone as tz
                result['next_scheduled_times'] = jobs.preview(actor, 'qsl-demo', datetime.now(tz.utc))
            settings = jobs.get_settings(actor, 'qsl-demo')
            return json.dumps(result, ensure_ascii=False, indent=2, default=str), settings['version']
        except (PermissionError, ValueError, RuntimeError) as exc:
            return '操作未完成：' + str(exc), no_update

    _register_crud(app, server, policy, directory)
    register_enhancements(app, server, policy, directory, current_demo_user)


def _install_transport_guard(server):

    @server.before_request
    def local_only():
        if request.content_length is not None and request.content_length > server.config['MAX_CONTENT_LENGTH']:
            abort(413)
        if request.host.split(':')[0] not in ('127.0.0.1', 'localhost'):
            abort(403)
        if request.method == 'POST' and request.path.endswith('_dash-update-component'):
            if not request.is_json:
                abort(415)
            origin = request.headers.get('Origin')
            if origin and origin.rstrip('/') != request.host_url.rstrip('/'):
                abort(403)
            if request.headers.get('Sec-Fetch-Site') == 'cross-site':
                abort(403)


def _register_crud(app, server, policy, directory):
    import os
    from dataclasses import asdict
    from .legacy_crud import LegacyCrudService, LegacyCrudError, InvalidPreview
    from .legacy_policy import require
    new_database = not os.path.exists(os.path.join(directory, 'qsl.sqlite'))
    service = LegacyCrudService(os.path.join(directory, 'qsl.sqlite'), lambda user: policy, allow_upload=True)
    server.extensions['qa_demo_crud'] = service
    seed_user = SimpleNamespace(**DEMO_USERS['developer'])
    for index, city in ([(1, 'Taipei'), (2, 'Kaohsiung')] if new_database else []):
        service.create(seed_user, dict(Material_Type='IC', Vendor_Code='SYN00' + str(index),
                                      Vendor_Name='Synthetic Vendor ' + str(index), Country='TW', City=city,
                                      Rev='A', Supplier_Level='LEVEL 1'))

    @app.callback(*[Output('qa-field-' + f, 'value') for f in FIELDS],
                  Output('qa-record-version', 'value'), Output('qa-load-status', 'children'),
                  Input('qa-load-id', 'n_clicks'), State('qa-record-id', 'value'), prevent_initial_call=True)
    def load_record(clicks, record_id):
        try:
            row = service.get(current_demo_user(), record_id)
            return tuple(row[field] for field in FIELDS) + (row['version'], '已載入 ID {}，請確認欄位後再更新。'.format(row['id']))
        except LegacyCrudError as exc:
            return tuple(no_update for field in FIELDS) + (no_update, '載入未完成：' + str(exc))

    @app.callback(Output('qa-status', 'children'), Output('qa-table', 'data'),
                  Output('qa-download', 'data'), Output('qa-stage-token', 'data'),
                  Output('qa-preview', 'children'), Output('qa-change-token', 'data'), Output('qa-change-preview', 'children'),
                  Input('qa-read', 'n_clicks'), Input('qa-create', 'n_clicks'),
                  Input('qa-update', 'n_clicks'), Input('qa-delete', 'n_clicks'),
                  Input('qa-export', 'n_clicks'), Input('qa-export-xlsx', 'n_clicks'), Input('qa-template', 'n_clicks'),
                  Input('qa-upload', 'contents'), Input('qa-import', 'n_clicks'),
                  Input('qa-confirm-update', 'n_clicks'), Input('qa-cancel-update', 'n_clicks'),
                  State('qa-filter', 'value'), State('qa-record-id', 'value'),
                  State('qa-record-version', 'value'), State('qa-delete-confirm', 'checked'),
                  State('qa-stage-token', 'data'), State('qa-upload', 'filename'), State('qa-change-token', 'data'),
                  *[State('qa-field-' + f, 'value') for f in FIELDS], prevent_initial_call=True)
    def crud_action(read, create, update, delete, export, export_xlsx, template, contents, submit, confirm_update, cancel_update,
                    keyword, record_id, version, confirm_delete, token, filename, change_token, *values):
        user = current_demo_user()
        filters = {'Vendor_Name': keyword or ''}
        payload = dict(zip(FIELDS, values))
        download, stage, preview = no_update, no_update, no_update
        result = None
        change, changes = no_update, no_update
        try:
            action = ctx.triggered_id
            if action == 'qa-create':
                result = service.create(user, payload)
            elif action == 'qa-update':
                if change_token:
                    try:
                        service.discard_preview(user, change_token)
                    except InvalidPreview:
                        pass
                prepared = service.preview_update(user, record_id, payload, expected_version=version)
                change, changes = prepared['token'], render_diff(prepared)
            elif action == 'qa-confirm-update':
                result = service.confirm_update(user, change_token)
                change, changes = None, '修改已提交。若要再編輯，請重新載入最新版本。'
            elif action == 'qa-cancel-update':
                require(user, policy, 'crud')
                if change_token:
                    try:
                        service.discard_preview(user, change_token)
                    except InvalidPreview:
                        pass
                change, changes = None, '已取消預覽；未修改資料。'
            elif action == 'qa-delete':
                require(user, policy, 'delete')
                if confirm_delete is not True:
                    raise ValueError('請先確認刪除指定 ID。')
                result = service.delete(user, record_id, version)
            elif action == 'qa-export':
                download = dcc.send_bytes(service.export_csv(user, filters), 'synthetic-qsl-filtered.csv')
            elif action == 'qa-export-xlsx':
                download = dcc.send_bytes(service.export_xlsx(user, filters), 'synthetic-qsl-filtered.xlsx')
            elif action == 'qa-template':
                download = dcc.send_bytes(service.template_csv(user), 'synthetic-qsl-template.csv')
            elif action == 'qa-upload':
                require(user, policy, 'upload')
                if not isinstance(filename, str) or not filename.lower().endswith(('.csv', '.xlsx')):
                    raise ValueError('請上傳 UTF-8 CSV 或 XLSX。')
                if not isinstance(contents, str) or len(contents) > 14 * 1024 * 1024:
                    raise ValueError('檔案過大或內容格式錯誤。')
                content = base64.b64decode(contents.split(',', 1)[1], validate=True)
                stage = service.stage_xlsx(user, content) if filename.lower().endswith('.xlsx') else service.stage_csv(user, content)
                preview = json.dumps(service.stage_preview(user, stage), ensure_ascii=False, indent=2)
            elif action == 'qa-import':
                result = service.submit_stage(user, token, atomic=True)
                stage, preview = None, '匯入提交已完成；再次匯入請重新上傳。'
            else:
                require(user, policy, 'read')
            status = ('執行結果：' + json.dumps(asdict(result), ensure_ascii=False)) if result else '操作完成（合成資料）。'
            try:
                rows = service.query(user, filters)
            except LegacyCrudError:
                return status + '；畫面重新讀取失敗，請重試查詢。', no_update, download, stage, preview, change, changes
            return status, rows, download, stage, preview, change, changes
        except (LegacyCrudError, PermissionError, ValueError, TypeError, IndexError) as exc:
            return '操作未完成：' + str(exc), [], no_update, no_update, no_update, no_update, no_update
