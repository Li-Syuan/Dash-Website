"""Explicit, app-scoped ETL dispatch workbench for the offline demo only.

Only service-allowlisted metadata is displayed. No adapter configuration, SQL,
source payloads, credentials or raw exception messages enter this page.
"""
from datetime import date, datetime, timedelta, timezone
import os
import tempfile
import threading
import time
import uuid

from dash import Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate
from flask import current_app
import dash_bootstrap_components as dbc

from demo_services import AccessDenied
from ..crud import Conflict, NotFound, StateUnavailable, ValidationError
from ..registry import AccessPolicy, PageSpec


POLICY = AccessPolicy.require(roles=('admin', 'user'), org='A')
MANAGE = AccessPolicy.require(roles=('admin',), org='A')
MAX_BACKFILL_DAYS = 31
_TAIPEI = timezone(timedelta(hours=8))
_ERROR_MESSAGES = {
    'adapter_failed': '合成來源步驟失敗', 'quality_failed': '筆數或品質檢查未通過',
    'snapshot_invalid': '來源快照不符合格式或大小限制', 'lease_lost': '執行租約失效，發布已阻擋',
    'configuration_changed': '設定已變更，發布已阻擋', 'permission_revoked': '目前權限無法確認',
    'interrupted': '先前執行中斷，不會自動重播', 'upstream_failed': '上游失敗，本步驟未執行',
    'storage_unavailable': '本機儲存暫時無法使用', 'worker_failed': '背景執行緒發生安全內部錯誤',
}
_STATUS = {'succeeded': '成功', 'failed': '失敗', 'running': '執行中', 'pending': '等待中',
           'blocked': '已阻擋', 'skipped': '略過', 'reused': '沿用成功步驟',
           'uncertain': '結果不確定', 'not_run': '尚未執行', 'ready': '待執行'}


def _today():
    return datetime.now(_TAIPEI).date().isoformat()


def _date(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValidationError()
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValidationError() from None
    if parsed.isoformat() != value or parsed > date.fromisoformat(_today()):
        raise ValidationError()
    return parsed


def _range(start, end):
    first, last = _date(start), _date(end)
    days = (last - first).days + 1
    if not 1 <= days <= MAX_BACKFILL_DAYS:
        raise ValidationError()
    return days


def _request(operation, values, previous=None, renew=False):
    desired = dict(operation=operation, values=values)
    if (not renew and isinstance(previous, dict) and previous.get('operation') == operation
            and previous.get('values') == values and isinstance(previous.get('request_id'), str)):
        return previous
    return dict(desired, request_id=uuid.uuid4().hex)


def _request_id(token, operation, values):
    if (not isinstance(token, dict) or token.get('operation') != operation or token.get('values') != values
            or not isinstance(token.get('request_id'), str) or len(token['request_id']) != 32):
        raise ValidationError()
    try:
        uuid.UUID(hex=token['request_id'])
    except ValueError:
        raise ValidationError() from None
    return token['request_id']


class _Confirmations:
    """Short-lived app-local confirmations; cancellation revokes the server token."""
    def __init__(self):
        self.pending = {}
        self.lock = threading.Lock()

    def revoke(self, proof, actor):
        with self.lock:
            item = self.pending.get(proof) if isinstance(proof, str) else None
            if item and item['actor'] == actor:
                self.pending.pop(proof, None)

    def issue(self, actor, job, start, end, request_id):
        with self.lock:
            now = time.monotonic()
            self.pending = {key: item for key, item in self.pending.items() if item['expires'] > now}
            # Opening a new preview for the same form supersedes its prior proof.
            self.pending = {key: item for key, item in self.pending.items()
                            if (item['actor'], item['request_id']) != (actor, request_id)}
            if len(self.pending) >= 1000:
                raise StateUnavailable()
            proof = uuid.uuid4().hex
            self.pending[proof] = dict(actor=actor, job=job, start=start, end=end,
                                       request_id=request_id, expires=now + 300)
            return proof

    def consume(self, proof, actor, job, start, end, request_id):
        with self.lock:
            item = self.pending.get(proof) if isinstance(proof, str) else None
            if not item or item['actor'] != actor:
                raise ValidationError()
            self.pending.pop(proof, None)
            expected = dict(actor=actor, job=job, start=start, end=end, request_id=request_id)
            if item['expires'] <= time.monotonic() or any(item[key] != value for key, value in expected.items()):
                raise ValidationError()


def _clicked(identifier, value):
    return (isinstance(value, int) and not isinstance(value, bool) and value > 0
            and set(ctx.triggered_prop_ids) == {identifier + '.n_clicks'})


def _text(value):
    return '—' if value is None else str(value)


def _when(value):
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, _TAIPEI).strftime('%Y-%m-%d %H:%M:%S')
    return _text(value)


def _failure(code):
    return '—' if not code else '{} · {}'.format(code, _ERROR_MESSAGES.get(code, '請核對批次狀態'))


def _badge(value):
    return html.Span(_STATUS.get(value, _text(value)), className='etl-badge etl-status-' +
                     (value if value in _STATUS else 'pending'))


def _table(headers, rows):
    return html.Div(html.Table([html.Thead(html.Tr([html.Th(title) for title in headers])),
                               html.Tbody([html.Tr([html.Td(value) for value in row]) for row in rows])]),
                    className='etl-table-wrap')


def _pairs(values):
    return html.Dl([html.Div([html.Dt(label), html.Dd(_text(value))]) for label, value in values],
                   className='etl-metadata')


def _jobs(jobs):
    return [html.Article([
        html.Div([html.Span('REGISTERED JOB', className='eyebrow'),
                  html.Span('排程啟用' if job.get('enabled') else '手動 / 排程停用', className='etl-badge')]),
        html.H3(job['name']), html.P(job.get('description', ''), className='subtitle'),
        html.Div(job['job_id'], className='etl-id'),
        html.Div('排程 v{} · {} 秒 · {} 個步驟'.format(job.get('version', '—'),
                 job.get('interval_seconds', '—'), len(job.get('steps', []))), className='etl-card-meta'),
    ], className='etl-job-card') for job in jobs]


def _overview(status):
    publication = status.get('publication') or {}
    running = status.get('worker_running', status.get('scheduler_running', False))
    return html.Div([
        html.Div([html.H2(status['name']), html.P(status.get('description', ''), className='subtitle')]),
        _pairs([('工作 ID', status['job_id']), ('排程版本', status['version']),
                ('排程狀態', '啟用' if status['enabled'] else '停用'),
                ('背景執行緒', '運行中' if running else '尚未啟動'),
                ('背景安全錯誤', _failure(status.get('worker_error_code'))),
                ('下次排程（台北）', _when(status.get('next_run_at'))),
                ('執行間隔', '{} 秒'.format(status['interval_seconds'])),
                ('最近發布批次', publication.get('run_id')), ('發布筆數', publication.get('row_count'))]),
        html.P('排程時區 Asia/Taipei。停用排程不會強制終止正在運行的程式；發布前仍會檢查設定與權限。', className='subtitle'),
        html.Div([html.Strong('已註冊流程與依賴'), html.Ul([html.Li('{} ← {}'.format(
            step['name'], ', '.join(step.get('depends_on') or []) or '來源 / 無上游'))
            for step in status.get('steps', [])])], className='etl-flow'),
    ])


def _run_detail(run):
    steps = run.get('steps', [])
    return html.Div([
        html.Div([html.H3('批次明細'), _badge(run.get('status'))], className='etl-section-heading'),
        _pairs([('批次 ID', run.get('run_id')), ('工作 ID', run.get('job_id')),
                ('業務日期', run.get('business_date')), ('觸發方式', run.get('trigger')),
                ('第幾次嘗試', run.get('attempt')), ('起始批次', run.get('root_run_id')),
                ('重試來源', run.get('parent_run_id')), ('開始（台北）', _when(run.get('started_at'))),
                ('完成（台北）', _when(run.get('finished_at'))), ('耗時秒數', run.get('duration_seconds')),
                ('輸出筆數', run.get('row_count')), ('安全錯誤代碼', _failure(run.get('error_code')))]),
        _table(['步驟 / 狀態', '依賴', '時間（台北） / 秒數', '筆數', '安全錯誤代碼', '沿用來源批次'], [
            [html.Div([html.Strong(step.get('name')), html.Br(), _badge(step.get('status'))]),
             ', '.join(step.get('depends_on') or []) or '—',
             html.Div([_when(step.get('started_at')), html.Br(), _when(step.get('finished_at')),
                       html.Br(), '耗時 ' + _text(step.get('duration_seconds'))]),
             _text(step.get('row_count')), _failure(step.get('error_code')), _text(step.get('reused_from_run_id'))]
            for step in steps]),
        html.Div([html.Details([html.Summary(step.get('name', '') + ' · 品質檢查'),
            _table(['檢查', '結果', '實際', '預期'], [[check.get('name'), '通過' if check.get('passed') else '未通過',
                _text(check.get('actual')), _text(check.get('expected'))] for check in step.get('checks', [])])])
            for step in steps if step.get('checks')], className='etl-quality'),
    ])


def _error(error):
    if isinstance(error, AccessDenied):
        text, color = '沒有權限執行此 ETL 操作，請重新確認登入身分。', 'danger'
    elif isinstance(error, Conflict):
        text, color = '狀態衝突：排程版本已更新、工作正在執行，或請求 ID 已用於不同操作。請重新載入設定；需另一次執行時按「建立新請求」。', 'warning'
    elif isinstance(error, NotFound):
        text, color = '找不到有權限查看的工作或批次；請重新選擇工作。', 'warning'
    elif isinstance(error, StateUnavailable):
        text, color = '儲存狀態目前無法確認。請先更新批次紀錄，避免盲目重試。', 'danger'
    else:
        text, color = '輸入或確認已失效。請檢查日期、31 天範圍及整數間隔，重新預覽後再確認。', 'warning'
    return dbc.Alert(text, color=color)


def _result(run, label):
    status = run.get('status')
    message = {'succeeded': '已成功完成。', 'failed': '批次失敗；請查看步驟與安全錯誤代碼，再決定是否重試。',
               'blocked': '依賴或品質檢查阻擋此批次，尚未發布。',
               'uncertain': '結果不確定，請先核對狀態；不可自動重試。',
               'running': '批次正在執行，請更新紀錄。'}.get(status, '請查看批次明細。')
    return dbc.Alert([html.Strong(label + '：'), message, html.Br(),
                      '批次 ' + _text(run.get('run_id')), html.Br(),
                      '重複按同一操作會取得原批次；需要新的批次時，先按「建立新請求」。'],
                     color='success' if status == 'succeeded' else 'warning')


def layout(runtime):
    user = runtime.identity()
    if not POLICY.allows(user):
        raise AccessDenied()
    service = current_app.extensions['etl_dispatch']
    jobs = service.jobs(user)
    selected = jobs[0]['job_id'] if jobs else None
    readonly = not MANAGE.allows(user)
    today = _today()
    return html.Div([
        html.Div([html.Div([html.Div('OPERATIONS / ETL', className='eyebrow'), html.H1('ETL 調度中心'),
                           html.P('管理多工作排程、批次追蹤、失敗重試與日期補跑。', className='subtitle')]),
                  dbc.Button('返回營運中心', href='/QA_portal/operations', outline=True)], className='etl-page-heading'),
        dbc.Alert('合成離線工作；無公司資料庫或外部郵件連線。排程只由 app.py 主啟動流程啟動。', color='info'),
        dbc.Alert('唯讀模式：可查看工作與批次明細；執行、排程變更、重試與補跑僅限 A 組織管理者。',
                  color='warning', is_open=readonly),
        html.Div(_jobs(jobs), id='etl-job-cards', className='etl-job-grid'),
        html.Section([
            html.Div([html.Div([dbc.Label('選擇已註冊工作', html_for='etl-job'),
                dcc.Dropdown(id='etl-job', options=[dict(label=job['name'], value=job['job_id']) for job in jobs],
                             value=selected, clearable=False)]),
                dbc.Button('更新狀態', id='etl-refresh', outline=True)], className='etl-selection'),
            html.Div(id='etl-status', **{'aria-live': 'polite'}),
        ], className='etl-panel'),
        html.Div([
            html.Section([html.H3('排程設定'), html.P('儲存時檢查版本，避免覆蓋其他管理者的變更。', className='subtitle'),
                dbc.Checkbox(id='etl-enabled', value=False, label='啟用自動排程', disabled=readonly),
                dbc.Label('執行間隔（秒，60–86400）', html_for='etl-interval'),
                dbc.Input(id='etl-interval', type='number', min=60, max=86400, step=1, value=300, disabled=readonly),
                dcc.Store(id='etl-version'),
                html.Div([dbc.Button('儲存排程', id='etl-save', disabled=readonly),
                          dbc.Button('重新載入設定', id='etl-reload', outline=True)], className='etl-actions'),
                html.Div(id='etl-save-result', **{'aria-live': 'polite'}), dcc.Store(id='etl-save-event'),
            ], className='etl-panel'),
            html.Section([html.H3('手動執行'), dbc.Label('業務日期', html_for='etl-business-date'),
                dcc.DatePickerSingle(id='etl-business-date', date=today, max_date_allowed=today,
                                     display_format='YYYY-MM-DD', disabled=readonly),
                html.Div([dbc.Button('立即執行', id='etl-run', disabled=readonly)], className='etl-actions'),
                html.Div(id='etl-run-result', **{'aria-live': 'polite'}), dcc.Store(id='etl-run-event'),
            ], className='etl-panel'),
        ], className='etl-two-column'),
        html.Section([html.H3('按日期補跑'),
            html.P('每次最多 31 個業務日期，不可選未來日期。重疊日期會保留已建立批次，失敗日期需另行重試。', className='subtitle'),
            dbc.Label('補跑範圍（含首尾日期）', html_for='etl-backfill-dates'),
            dcc.DatePickerRange(id='etl-backfill-dates', start_date=today, end_date=today,
                                max_date_allowed=today, display_format='YYYY-MM-DD', disabled=readonly),
            html.Div([dbc.Button('預覽補跑範圍', id='etl-backfill-preview', disabled=readonly)], className='etl-actions'),
            html.Div(id='etl-backfill-preview-result', **{'aria-live': 'polite'}),
            html.Div(id='etl-backfill-result', **{'aria-live': 'polite'}), dcc.Store(id='etl-backfill-proof'),
            dcc.Store(id='etl-backfill-event'),
            dbc.Modal([dbc.ModalHeader(dbc.ModalTitle('確認日期補跑'), close_button=False),
                       dbc.ModalBody(id='etl-backfill-summary'),
                       dbc.ModalFooter([dbc.Button('取消', id='etl-backfill-cancel', outline=True),
                                        dbc.Button('確認執行補跑', id='etl-backfill-confirm', color='danger', disabled=readonly)])],
                      id='etl-backfill-modal', is_open=False, centered=True, backdrop='static', keyboard=False,
                      className='portal-crud-modal etl-confirm-modal'),
        ], className='etl-panel'),
        html.Div([dbc.Button('建立新請求', id='etl-new-request', outline=True, disabled=readonly),
                  html.Small('同一表單重複點擊會使用原請求。只有更改該表單輸入或建立新請求才允許另一次操作。')],
                 className='etl-request-controls'),
        dcc.Store(id='etl-run-request', data=_request('run', [selected, today])),
        dcc.Store(id='etl-backfill-request', data=_request('backfill', [selected, today, today])),
        dcc.Store(id='etl-retry-request', data=_request('retry', [selected, None])),
        html.Section([html.H3('批次紀錄與步驟'),
            html.P('每個工作顯示最近 100 筆；未選批次時顯示最近一次。時間皆為 Asia/Taipei。', className='subtitle'),
            dbc.Label('選擇批次', html_for='etl-run-id'),
            dcc.Dropdown(id='etl-run-id', options=[], placeholder='選擇批次查看步驟、檢查與來源'),
            html.Div(id='etl-detail', **{'aria-live': 'polite'}),
            dbc.Button('重試所選失敗批次', id='etl-retry', disabled=True, className='mt-3'),
            html.Div(id='etl-retry-result', **{'aria-live': 'polite'}), dcc.Store(id='etl-retry-event'),
        ], className='etl-panel'),
        dcc.Interval(id='etl-poll', interval=10000, n_intervals=0),
    ], className='etl-workbench')


def register_callbacks(callbacks, runtime):
    from ..etl_dispatch import ETLDispatch
    server = current_app._get_current_object()
    if runtime.settings.state_path:
        database = runtime.settings.state_path + '.etl.sqlite'
    else:
        temporary = tempfile.TemporaryDirectory(prefix='portal-etl-')
        server.extensions['etl_dispatch_temporary'] = temporary
        database = os.path.join(temporary.name, 'etl.sqlite')
    service = ETLDispatch(database, runtime.identities)
    server.extensions['etl_dispatch'] = service
    confirmations = _Confirmations()
    errors = (AccessDenied, Conflict, NotFound, StateUnavailable, ValidationError, ValueError, TypeError)

    @callbacks.callback(Output('etl-enabled', 'value'), Output('etl-interval', 'value'), Output('etl-version', 'data'),
                        Input('etl-job', 'value'), Input('etl-reload', 'n_clicks'), Input('etl-save-event', 'data'),
                        callback_id='etl.select', policy=POLICY)
    def select(job, reload, saved):
        if not job:
            raise PreventUpdate
        try:
            state = service.status(runtime.identity(), job)
            return state['enabled'], state['interval_seconds'], {'job_id': job, 'version': state['version']}
        except errors:
            return no_update, no_update, None

    @callbacks.callback(Output('etl-status', 'children'), Output('etl-job-cards', 'children'), Output('etl-run-id', 'options'),
                        Input('etl-job', 'value'), Input('etl-refresh', 'n_clicks'), Input('etl-poll', 'n_intervals'),
                        Input('etl-run-event', 'data'), Input('etl-save-event', 'data'), Input('etl-backfill-event', 'data'),
                        Input('etl-retry-event', 'data'), callback_id='etl.refresh', policy=POLICY)
    def refresh(job, refresh_clicks, poll, ran, saved, filled, retried):
        try:
            user = runtime.identity()
            state = service.status(user, job)
            return _overview(state), _jobs(service.jobs(user)), [
                dict(label='{} · {} · {}'.format(run.get('business_date'), _STATUS.get(run.get('status'), run.get('status')),
                                               run['run_id']), value=run['run_id']) for run in state.get('runs', [])]
        except errors as error:
            return _error(error), no_update, []

    @callbacks.callback(Output('etl-run-request', 'data'), Output('etl-backfill-request', 'data'), Output('etl-retry-request', 'data'),
                        Input('etl-job', 'value'), Input('etl-business-date', 'date'),
                        Input('etl-backfill-dates', 'start_date'), Input('etl-backfill-dates', 'end_date'),
                        Input('etl-run-id', 'value'), Input('etl-new-request', 'n_clicks'),
                        State('etl-run-request', 'data'), State('etl-backfill-request', 'data'), State('etl-retry-request', 'data'),
                        callback_id='etl.requests', policy=POLICY)
    def requests(job, business_date, start, end, run_id, reset, run, fill, retry):
        renew = ctx.triggered_id == 'etl-new-request'
        return (_request('run', [job, business_date], run, renew),
                _request('backfill', [job, start, end], fill, renew), _request('retry', [job, run_id], retry, renew))

    @callbacks.callback(Output('etl-run-result', 'children'), Output('etl-run-event', 'data'),
                        Input('etl-run', 'n_clicks'), State('etl-job', 'value'), State('etl-business-date', 'date'),
                        State('etl-run-request', 'data'), callback_id='etl.run', policy=MANAGE, prevent_initial_call=True)
    def run(n, job, business_date, request):
        if not _clicked('etl-run', n):
            raise PreventUpdate
        try:
            _date(business_date)
            run = service.run_now(runtime.identity(), job, business_date=business_date,
                                  request_id=_request_id(request, 'run', [job, business_date]))
            return _result(run, '手動執行'), {'run_id': run['run_id']}
        except errors as error:
            return _error(error), no_update

    @callbacks.callback(Output('etl-save-result', 'children'), Output('etl-save-event', 'data'),
                        Input('etl-save', 'n_clicks'), State('etl-job', 'value'), State('etl-enabled', 'value'),
                        State('etl-interval', 'value'), State('etl-version', 'data'),
                        callback_id='etl.configure', policy=MANAGE, prevent_initial_call=True)
    def configure(n, job, enabled, interval, version):
        if not _clicked('etl-save', n):
            raise PreventUpdate
        try:
            if (not isinstance(version, dict) or version.get('job_id') != job
                    or type(version.get('version')) is not int or version['version'] < 1
                    or type(interval) is not int or not 60 <= interval <= 86400
                    or type(enabled) is not bool):
                raise ValidationError()
            result = service.configure(runtime.identity(), job, enabled, interval, expected_version=version.get('version'))
            return dbc.Alert('排程已儲存。{}'.format('背景執行緒須運行才會自動觸發。' if enabled else '已停用未來自動觸發。'),
                             color='success'), {'job_id': job, 'version': result['version']}
        except errors as error:
            return _error(error), no_update

    @callbacks.callback(Output('etl-backfill-modal', 'is_open'), Output('etl-backfill-summary', 'children'),
                        Output('etl-backfill-proof', 'data'), Output('etl-backfill-preview-result', 'children'),
                        Input('etl-backfill-preview', 'n_clicks'), Input('etl-backfill-cancel', 'n_clicks'),
                        Input('etl-job', 'value'), Input('etl-backfill-dates', 'start_date'),
                        Input('etl-backfill-dates', 'end_date'), Input('etl-backfill-request', 'data'),
                        Input('etl-backfill-event', 'data'), State('etl-backfill-proof', 'data'),
                        callback_id='etl.backfill-preview', policy=POLICY, prevent_initial_call=True)
    def preview_backfill(preview, cancel, job, start, end, request, completed, proof):
        user = runtime.identity()
        trigger = ctx.triggered_id
        try:
            if trigger != 'etl-backfill-preview':
                confirmations.revoke(proof, user['id'])
                message = dbc.Alert('補跑已取消，沒有建立新批次。', color='info') if trigger == 'etl-backfill-cancel' else ''
                return False, '', None, message
            if not _clicked('etl-backfill-preview', preview):
                confirmations.revoke(proof, user['id'])
                return False, '', None, _error(ValidationError())
            if not MANAGE.allows(user):
                raise AccessDenied()
            days = _range(start, end)
            request_id = _request_id(request, 'backfill', [job, start, end])
            state = service.status(user, job)
            confirmations.revoke(proof, user['id'])
            token = confirmations.issue(user['id'], job, start, end, request_id)
            summary = html.Div([html.P('工作：' + state['name']), html.P('日期：{} 至 {}（共 {} 天）'.format(start, end, days)),
                html.P('確認後將逐日執行合成 ETL 並可能更新最近成功發布結果。已存在的日期批次會重用；失敗批次需另按重試。'),
                html.P('確認有效期 5 分鐘；更改工作、日期或請求後須重新預覽。')])
            return True, summary, token, ''
        except errors as error:
            confirmations.revoke(proof, user['id'])
            return False, '', None, _error(error)

    @callbacks.callback(Output('etl-backfill-result', 'children'), Output('etl-backfill-event', 'data'),
                        Input('etl-backfill-confirm', 'n_clicks'), State('etl-job', 'value'),
                        State('etl-backfill-dates', 'start_date'), State('etl-backfill-dates', 'end_date'),
                        State('etl-backfill-request', 'data'), State('etl-backfill-proof', 'data'),
                        callback_id='etl.backfill', policy=MANAGE, prevent_initial_call=True)
    def backfill(confirm, job, start, end, request, proof):
        if not _clicked('etl-backfill-confirm', confirm):
            raise PreventUpdate
        user = runtime.identity()
        event = {'request_id': uuid.uuid4().hex}
        try:
            _range(start, end)
            request_id = _request_id(request, 'backfill', [job, start, end])
            try:
                confirmations.consume(proof, user['id'], job, start, end, request_id)
            except ValidationError:
                return dbc.Alert('補跑確認已失效、已取消或已使用。請先更新批次紀錄；仍需補跑時重新預覽。', color='warning'), event
            result = service.backfill(user, job, start, end, request_id=request_id)
            runs = result.get('runs', [])
            succeeded = sum(run.get('status') == 'succeeded' for run in runs)
            message = dbc.Alert('補跑已取得 {} 個日期批次：{} 成功，{} 尚未成功。請從批次紀錄查看各日結果。'.format(
                len(runs), succeeded, len(runs) - succeeded), color='success' if succeeded == len(runs) else 'warning')
            return message, {'job_id': job, 'request_id': request_id}
        except errors as error:
            confirmations.revoke(proof, user['id'])
            return _error(error), event

    @callbacks.callback(Output('etl-detail', 'children'), Output('etl-retry', 'disabled'),
                        Input('etl-job', 'value'), Input('etl-run-id', 'value'), Input('etl-poll', 'n_intervals'),
                        Input('etl-run-event', 'data'), Input('etl-backfill-event', 'data'), Input('etl-retry-event', 'data'),
                        Input('etl-refresh', 'n_clicks'), callback_id='etl.detail', policy=POLICY)
    def detail(job, run_id, poll, ran, filled, retried, refreshed):
        try:
            user = runtime.identity()
            if run_id:
                run = service.run_detail(user, run_id)
                if run.get('job_id') != job:
                    return dbc.Alert('所選批次屬於其他工作，請重新選擇批次。', color='warning'), True
            else:
                run = service.status(user, job).get('last_run')
            if not run:
                return html.Div('尚無批次。管理者可先選日期執行，或啟用排程。', className='empty-state'), True
            return _run_detail(run), not (MANAGE.allows(user) and run_id and run.get('retry_allowed'))
        except errors as error:
            return _error(error), True

    @callbacks.callback(Output('etl-retry-result', 'children'), Output('etl-retry-event', 'data'),
                        Input('etl-retry', 'n_clicks'), State('etl-job', 'value'), State('etl-run-id', 'value'),
                        State('etl-retry-request', 'data'), callback_id='etl.retry', policy=MANAGE, prevent_initial_call=True)
    def retry(n, job, run_id, request):
        if not _clicked('etl-retry', n):
            raise PreventUpdate
        try:
            user = runtime.identity()
            previous = service.run_detail(user, run_id)
            if previous.get('job_id') != job:
                raise ValidationError()
            run = service.retry(user, run_id, request_id=_request_id(request, 'retry', [job, run_id]))
            return _result(run, '失敗重試'), {'run_id': run['run_id']}
        except errors as error:
            return _error(error), no_update


SPEC = PageSpec(page_id='etl-dispatch', path='/QA_portal/etl', title='ETL 調度中心', layout=layout,
                policy=POLICY, nav_label='ETL 調度', nav_order=37, register_callbacks=register_callbacks,
                catalog_category='QA', catalog_description='多工作排程、批次步驟、失敗重試與日期補跑。',
                catalog_tags=('ETL', '排程', '重試', '補跑'))
