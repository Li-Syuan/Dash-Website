"""Version preview and constrained report wizard for the offline QA adapter.

No identity, policy, SQL, or provider reference is accepted from browser state.
"""
import json
import os
from datetime import datetime, timezone

from dash import Input, Output, State, ctx, dcc, html, dash_table, no_update
import dash_mantine_components as dmc

from .legacy_crud import QSL_FIELDS, LegacyCrudError, InvalidPreview


def _panel(title, children):
    return dmc.Card([dmc.Title(title, order=3), dmc.Space(h=12)] + children,
                    withBorder=True, shadow='sm', radius='md', style={'marginBottom': 20})


def _display(value):
    return '（空白）' if value in (None, '') else str(value)


def render_diff(preview):
    return html.Div([
        dmc.Text('ID {} · 目前版本 {} → 確認後產生新版本'.format(preview['record_id'], preview['expected_version']), weight=600),
        dmc.Text('確認只套用此預覽；若又改了表單，請重新按「預覽修改差異」。取消或重新預覽不會寫入資料。', size='sm', color='dimmed'),
        html.Table([
            html.Thead(html.Tr([html.Th('欄位'), html.Th('修改前'), html.Th('修改後')])),
            html.Tbody([html.Tr([html.Td(item['field']), html.Td(_display(item['before'])), html.Td(_display(item['after']))])
                        for item in preview['diff']]),
        ], style={'width': '100%', 'borderCollapse': 'collapse'}),
        dmc.Text('沒有欄位差異。' if not preview['diff'] else '請核對差異後確認。', size='sm'),
    ])


def revision_panel():
    return _panel('歷史版本 / 安全還原', [
        dmc.Text('只顯示目前資料表的授權歷史。還原會新增版本，保留舊版本與操作人紀錄。歷史查看及還原需要維護權限。', size='sm'),
        dmc.NumberInput(id='qa-history-id', label='查詢歷史的資料 ID（包含已刪除資料）', min=1, value=1),
        dmc.Button('載入歷史版本', id='qa-history-load', mt=8),
        html.Div(id='qa-history-status', role='status'),
        dash_table.DataTable(id='qa-history-table', data=[], columns=[
            {'name': label, 'id': key} for label, key in [('版本', 'version'), ('操作', 'action'), ('操作人', 'actor'),
                                                        ('時間 UTC', 'time'), ('變更欄位', 'fields'), ('還原來源版本', 'source_version')]],
                             page_size=10, style_table={'overflowX': 'auto'}, style_cell={'textAlign': 'left', 'padding': 8}),
        dmc.Select(id='qa-history-version', label='選取要還原的歷史版本', data=[], value=None),
        dmc.Button('預覽還原差異', id='qa-restore-preview', mt=8),
        dcc.Store(id='qa-history-current', storage_type='memory'),
        dcc.Store(id='qa-restore-token', storage_type='memory'),
        html.Div(id='qa-restore-diff', role='status'),
        dmc.Group([dmc.Button('確認還原為新版本', id='qa-restore-confirm', color='orange'),
                   dmc.Button('取消還原', id='qa-restore-cancel', variant='outline')], spacing='sm', mt=8),
    ])


def wizard_panel():
    fields = list(QSL_FIELDS)
    return _panel('報表建立精靈', [
        dmc.Text('選擇核准的合成資料來源，逐步設定欄位、條件及分組。定義只包含設定，不接受 SQL 或 Python。', size='sm'),
        dcc.Store(id='qb-step', data=0), dcc.Store(id='qb-preview-definition'), dcc.Store(id='qb-saved-version'),
        dmc.Text(id='qb-step-label', children='步驟 1 / 4：資料来源', weight=600, mt=12),
        html.Div([
            dmc.Select(id='qb-source', label='資料來源', data=[{'value': 'synthetic-qsl', 'label': 'QSL 合成供應商資料'}], value='synthetic-qsl'),
        ], id='qb-source-panel'),
        html.Div([
            dmc.MultiSelect(id='qb-columns', label='報表欄位（依選取順序）', data=fields,
                            value=['Vendor_Code', 'Vendor_Name', 'Country', 'City'], searchable=True),
        ], id='qb-columns-panel', style={'display': 'none'}),
        html.Div([
            dmc.SimpleGrid([
                dmc.Select(id='qb-filter-field', label='篩選欄位（可不選）', data=fields, clearable=True),
                dmc.Select(id='qb-filter-op', label='條件', data=[{'value': 'contains', 'label': '包含（字面文字）'}, {'value': 'eq', 'label': '完全相等'}], value='contains'),
                dmc.TextInput(id='qb-filter-value', label='篩選值', value=''),
            ], cols=3, spacing='md'),
            dmc.MultiSelect(id='qb-group', label='分組計數（最多 3 欄，選取後報表欄位改為分組欄位＋筆數）', data=fields, value=[]),
            dmc.NumberInput(id='qb-limit', label='預覽筆數上限（1–500）', value=100, min=1, max=500),
        ], id='qb-options-panel', style={'display': 'none'}),
        html.Div([
            dmc.Button('產生 / 更新預覽', id='qb-preview'),
            html.Div(id='qb-preview-status', role='status', style={'marginTop': 8}),
            dash_table.DataTable(id='qb-table', data=[], columns=[], page_size=10,
                                 style_table={'overflowX': 'auto'}, style_cell={'textAlign': 'left', 'padding': 8}),
            dmc.TextInput(id='qb-name', label='報表定義名稱', value='我的 QSL 報表'),
            dmc.Button('儲存已預覽定義', id='qb-save', mt=8),
        ], id='qb-preview-panel', style={'display': 'none'}),
        dmc.Group([dmc.Button('上一步', id='qb-back', variant='outline'), dmc.Button('下一步', id='qb-next')], spacing='sm', mt=12),
        html.Div(id='qb-nav-status', role='status'),
        dmc.Divider(my='md'),
        dmc.Group([dmc.Button('列出我的已存報表', id='qb-list'), dmc.Button('載入所選定義', id='qb-load')], spacing='sm'),
        dmc.Select(id='qb-saved-id', label='已保存的報表定義', data=[], value=None),
        html.Div(id='qb-save-status', role='status'),
    ])


def register_enhancements(app, server, policy, directory, user_resolver):
    from .report_builder import ReportBuilderService
    from .legacy_policy import require
    service = server.extensions['qa_demo_crud']
    builder = ReportBuilderService(os.path.join(directory, 'report_builder.sqlite'), service, lambda user: policy)
    server.extensions['qa_demo_builder'] = builder

    def usage_event(event):
        # Optional app-integrated telemetry: actual successful actions only.
        # Standalone adapter remains functional without the operations service.
        operations = server.extensions.get('operations_service')
        runtime = server.extensions.get('workspace')
        if operations is not None and runtime is not None:
            try:
                operations.record_usage(runtime.identity(), 'qsl-workspace', event)
            except Exception:
                # A committed save must not be reported as failed because a
                # secondary, privacy-minimized analytics store is unavailable.
                server.logger.warning('operations_usage_write_failed')

    @app.callback(Output('qa-history-status', 'children'), Output('qa-history-table', 'data'),
                  Output('qa-history-version', 'data'), Output('qa-history-current', 'data'),
                  Output('qa-restore-token', 'data'), Output('qa-restore-diff', 'children'),
                  Input('qa-history-load', 'n_clicks'), Input('qa-restore-preview', 'n_clicks'),
                  Input('qa-restore-confirm', 'n_clicks'), Input('qa-restore-cancel', 'n_clicks'),
                  State('qa-history-id', 'value'), State('qa-history-version', 'value'),
                  State('qa-history-current', 'data'), State('qa-restore-token', 'data'), prevent_initial_call=True)
    def revision_action(load, preview, confirm, cancel, record_id, source_version, current_version, token):
        user = user_resolver()
        try:
            require(user, policy, 'crud')
            action = ctx.triggered_id
            next_token, diff = no_update, no_update
            message = '歷史版本已載入。'
            if action == 'qa-restore-preview':
                if token:
                    try:
                        service.discard_preview(user, token)
                    except InvalidPreview:
                        pass
                prepared = service.preview_restore(user, record_id, source_version, expected_version=current_version)
                next_token, diff = prepared['token'], render_diff(prepared)
                message = '尚未還原，請檢查差異後確認。'
            elif action == 'qa-restore-confirm':
                result = service.confirm_restore(user, token)
                next_token, diff = None, '已還原為新版本 {}。請重新查詢主表以更新顯示。'.format(result.version)
                message = diff
            elif action == 'qa-restore-cancel':
                if token:
                    try:
                        service.discard_preview(user, token)
                    except InvalidPreview:
                        pass
                next_token, diff = None, '已取消；未還原資料。'
            elif action == 'qa-history-load':
                if token:
                    try:
                        service.discard_preview(user, token)
                    except InvalidPreview:
                        pass
                next_token, diff = None, ''
            else:
                raise ValueError('未知操作。')
            history = service.history(user, record_id)
            rows = [dict(version=item['version'], action=item['action'], actor=item['actor'],
                         time=datetime.fromtimestamp(item['at'], timezone.utc).isoformat(),
                         fields='、'.join(item['changed_fields']), source_version=item.get('source_version')) for item in history]
            options = [{'value': str(item['version']), 'label': '版本 {} · {}'.format(item['version'], item['action'])} for item in history]
            version = history[0]['version'] if history else None
            return message, rows, options, version, next_token, diff
        except (LegacyCrudError, PermissionError, ValueError, TypeError):
            return '操作未完成：請確認權限、資料 ID 與版本，重新載入後再試。', [], [], no_update, no_update, no_update

    def definition(source, columns, field, op, value, groups, limit):
        groups = groups or []
        return dict(schema_version=1, source=source, columns=groups if groups else columns,
                    filters=[{'field': field, 'op': op, 'value': value or ''}] if field else [],
                    group_by=groups, aggregate='count' if groups else None, limit=limit)

    config_states = [State('qb-source', 'value'), State('qb-columns', 'value'), State('qb-filter-field', 'value'),
                     State('qb-filter-op', 'value'), State('qb-filter-value', 'value'), State('qb-group', 'value'), State('qb-limit', 'value')]

    @app.callback(Output('qb-step', 'data'), Output('qb-step-label', 'children'),
                  Output('qb-source-panel', 'style'), Output('qb-columns-panel', 'style'),
                  Output('qb-options-panel', 'style'), Output('qb-preview-panel', 'style'), Output('qb-nav-status', 'children'),
                  Input('qb-next', 'n_clicks'), Input('qb-back', 'n_clicks'), State('qb-step', 'data'), *config_states,
                  prevent_initial_call=True)
    def navigate(nxt, back, step, source, columns, field, op, value, groups, limit):
        try:
            builder.sources(user_resolver())
            if not isinstance(step, int) or isinstance(step, bool) or step not in range(4):
                raise ValueError()
            if ctx.triggered_id == 'qb-next':
                builder.validate(user_resolver(), definition(source, columns, field, op, value, groups, limit))
                step = min(3, step + 1)
            elif ctx.triggered_id == 'qb-back':
                step = max(0, step - 1)
            else:
                raise ValueError()
            label = ['資料來源', '報表欄位', '篩選與分組', '預覽與保存'][step]
            return (step, '步驟 {} / 4：{}'.format(step + 1, label)) + tuple({} if i == step else {'display': 'none'} for i in range(4)) + ('',)
        except (LegacyCrudError, PermissionError, ValueError, TypeError):
            return (no_update,) * 6 + ('請確認登入權限與欄位設定。',)

    @app.callback(Output('qb-preview-status', 'children'), Output('qb-table', 'columns'), Output('qb-table', 'data'),
                  Output('qb-preview-definition', 'data'), Input('qb-preview', 'n_clicks'), *config_states,
                  prevent_initial_call=True)
    def preview_report(clicks, source, columns, field, op, value, groups, limit):
        try:
            result = builder.preview(user_resolver(), definition(source, columns, field, op, value, groups, limit))
            usage_event('report_view')
            message = '符合 {} 筆來源資料，顯示 {} 列{}。'.format(result['matched_rows'], len(result['rows']), '（已達顯示上限）' if result['truncated'] else '')
            return message, [{'name': name, 'id': name} for name in result['columns']], result['rows'], result['definition']
        except (LegacyCrudError, PermissionError, ValueError, TypeError):
            return '預覽未完成：請確認權限或設定；來源超量時請縮小篩選範圍。', [], [], None

    @app.callback(Output('qb-save-status', 'children'), Output('qb-saved-id', 'data'), Output('qb-saved-version', 'data'),
                  Input('qb-save', 'n_clicks'), Input('qb-list', 'n_clicks'), State('qb-name', 'value'),
                  State('qb-preview-definition', 'data'), State('qb-saved-version', 'data'), *config_states,
                  prevent_initial_call=True)
    def save_report(save, listing, name, previewed, version, source, columns, field, op, value, groups, limit):
        user = user_resolver()
        try:
            next_version = no_update
            message = '已載入你自己的報表定義。'
            if ctx.triggered_id == 'qb-save':
                normalized = builder.validate(user, definition(source, columns, field, op, value, groups, limit))
                if normalized != previewed:
                    raise ValueError('Preview required')
                # Re-run bounded source authorization before saving. A report definition is not a data snapshot.
                builder.preview(user, normalized)
                expected = version.get('version') if isinstance(version, dict) and version.get('name') == name else None
                saved = builder.save(user, name, normalized, expected_version=expected)
                usage_event('selfservice_save')
                next_version = {'name': saved['name'], 'version': saved['version']}
                message = '已保存「{}」版本 {}。'.format(saved['name'], saved['version'])
            elif ctx.triggered_id != 'qb-list':
                raise ValueError()
            records = builder.list_definitions(user)
            return message, [{'value': str(row['id']), 'label': '{} · v{}'.format(row['name'], row['version'])} for row in records], next_version
        except (LegacyCrudError, PermissionError, ValueError, TypeError):
            return '保存 / 載入未完成：請先預覽目前設定；同名報表請先載入最新版本再修改，並確認維護權限。', no_update, no_update

    @app.callback(Output('qb-source', 'value'), Output('qb-columns', 'value'), Output('qb-filter-field', 'value'),
                  Output('qb-filter-op', 'value'), Output('qb-filter-value', 'value'), Output('qb-group', 'value'),
                  Output('qb-limit', 'value'), Output('qb-name', 'value'),
                  Output('qb-saved-version', 'data', allow_duplicate=True),
                  Output('qb-preview-definition', 'data', allow_duplicate=True),
                  Output('qb-save-status', 'children', allow_duplicate=True),
                  Output('qb-table', 'data', allow_duplicate=True), Output('qb-table', 'columns', allow_duplicate=True),
                  Output('qb-preview-status', 'children', allow_duplicate=True), Input('qb-load', 'n_clicks'),
                  State('qb-saved-id', 'value'), prevent_initial_call=True)
    def load_report(clicks, selected):
        try:
            if not isinstance(selected, str) or not selected.isascii() or not selected.isdigit() or len(selected) > 19:
                raise ValueError()
            saved = builder.load(user_resolver(), int(selected))
            spec = saved['definition']
            # This compact UI exposes one filter; refuse to silently drop definitions from richer clients.
            if len(spec['filters']) > 1:
                raise ValueError()
            filt = spec['filters'][0] if spec['filters'] else {}
            return (spec['source'], spec['columns'], filt.get('field'), filt.get('op', 'contains'), filt.get('value', ''),
                    spec['group_by'], spec['limit'], saved['name'], {'name': saved['name'], 'version': saved['version']}, None,
                    '已載入定義。請進入步驟 4 重新預覽。', [], [], '已載入新定義，尚未預覽。')
        except (LegacyCrudError, PermissionError, ValueError, TypeError):
            return (no_update,) * 10 + ('無法載入：請選取你自己的報表；此精簡精靈支援單一篩選條件。', [], [], '請重新預覽。')
