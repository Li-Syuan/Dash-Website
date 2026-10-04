"""Offline one-page acceptance viewer. Evidence is data, never executable code."""
from datetime import datetime, timezone
import hashlib
import html
import math
import os
from pathlib import Path
import re
from types import SimpleNamespace

try:
    from .acceptance_core import safe_output_path, verify_artifacts
    from .acceptance_binding import clock_record
    from .acceptance_evidence import validate_performance
except ImportError:
    from acceptance_core import safe_output_path, verify_artifacts
    from acceptance_binding import clock_record
    from acceptance_evidence import validate_performance


STATUSES = {'PASSED': 'PASS', 'FAILED': 'FAIL', 'INCOMPLETE': 'INCOMPLETE',
            'INVALID': 'INVALID', 'UNRUN': 'UNRUN', 'BLOCKED': 'BLOCKED',
            'TIMED_OUT': 'TIMED_OUT', 'SKIP': 'SKIP'}
MAX_JSON = 8 * 1024 * 1024
REASONS = {
    'source-changed-since-evidence': '程式已變更；此證據不能代表目前版本。',
    'expired-evidence': '證據已超過允許期限，需產生新的驗收結果。',
    'performance-budgets-not-supplied': '效能門檻未核准。',
    'not-selected': '本次未執行此項目。',
    'unittest-unverified': '仍有略過或未驗證的測試。',
    'unittest-failed': '測試失敗；請查看下方測試名稱與原因。',
    'unittest-result': '測試結果未完整通過；個別失敗或略過原因如下。',
    'missing-report': '尚未提供可讀的驗收報告。',
}


def redact(value):
    """Conservative display masking; unknown free-form secrets cannot be inferred.

    The renderer additionally excludes raw logs, environment variables, commands,
    request/response payloads and arbitrary JSON fields entirely.
    """
    text = str(value) if value is not None else '未提供'
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----',
                  '[REDACTED KEY]', text, flags=re.S)
    text = re.sub(r'(?im)\b(?:authorization|set-cookie|cookie)\s*[:=][^\r\n]+', '[REDACTED HEADER]', text)
    text = re.sub(r'(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+', '[REDACTED AUTH]', text)
    text = re.sub(r'''(?ix)(\b(?:password|passwd|pwd|(?:client[_-]?)?secret|token|api[_-]?key|access[_-]?token|refresh[_-]?token|session(?:id)?|dsn|connection[_-]?string)\b["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;]+)''',
                  r'\1[REDACTED]', text)
    text = re.sub(r'(?i)\b[a-z][a-z0-9+.-]*://[^\s<>"\']+', '[REDACTED URL]', text)
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{12,}|github_pat_[A-Za-z0-9_]{12,}|xox[baprs]-[A-Za-z0-9-]{12,}|AKIA[A-Z0-9]{16})\b', '[REDACTED TOKEN]', text)
    text = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b', '[REDACTED JWT]', text)
    text = re.sub(r'(?i)[a-z]:[\\/]+Users[\\/]+[^\\/\s"\']+', '<user-home>', text)
    text = re.sub(r'/(?:home|Users)/[^/\s"\']+|/mnt/[a-z]/Users/[^/\s"\']+', '<user-home>', text)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u202a-\u202e\u2066-\u2069]', '', text)
    return text[:4000]


def esc(value):
    return html.escape(redact(value), quote=True)


def stamp(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return '未提供'
    try:
        return datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    except (ValueError, OSError, OverflowError):
        return '無效時間'


def read_json(path, api):
    path = safe_output_path(path, directory=False)
    if path.stat().st_size > MAX_JSON:
        raise ValueError('evidence-json-too-large')
    value = api.strict_json(path)
    if not isinstance(value, dict):
        raise ValueError('invalid-evidence-object')
    return value


def bundle(path, api):
    path = safe_output_path(path, directory=False)
    value = read_json(path, api)
    seal = safe_output_path(path.with_suffix('.sha256'), directory=False)
    if seal.stat().st_size > 128 or seal.read_text(encoding='ascii').strip() != hashlib.sha256(path.read_bytes()).hexdigest():
        raise ValueError('report-digest-mismatch')
    if value.get('schema') != 1 or value.get('status') not in STATUSES:
        raise ValueError('invalid-report-schema-or-status')
    issues = verify_artifacts(path.parent, value.get('artifacts'))
    if issues:
        raise ValueError('artifact-integrity-failed')
    return value


def source_view(path, project, baseline, max_age, api, historical=False):
    view = dict(role='history' if historical else 'current', status='INCOMPLETE',
                reason='missing-report', data={}, trusted=False, path=None, sha256=None)
    if path is None:
        return view
    try:
        path = safe_output_path(path, directory=False)
        view.update(path=path)
        data = bundle(path, api)
        view['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        view['data'] = data
        if historical:
            names = {item['path'] for item in data['artifacts']}
            if 'clock.jsonl' not in names or clock_record(path.parent / 'clock.jsonl', api.strict_json) != data.get('clock'):
                raise ValueError('historical-clock-not-bound')
            if not isinstance(data.get('steps'), list):
                raise ValueError('invalid-historical-steps')
            status = data['status'] if data['clock']['valid'] else 'INVALID'
            view.update(status=status, trusted=True, reason='歷史紀錄；僅核對檔案完整性及時鐘，未重驗目前程式。')
        else:
            result = api.verify(SimpleNamespace(report=path, project_root=project, baseline=baseline,
                                               max_age_seconds=max_age))
            valid = result.get('evidence_valid') is True and result.get('status') in STATUSES
            view.update(status=result['status'] if valid else 'INVALID', trusted=valid,
                        reason=result.get('reason') or ('驗收證據核對完成；仍須查看略過及未驗證項目。' if valid else 'evidence-verification-failed'))
    except FileNotFoundError:
        missing_report = path is None or not Path(path).is_file()
        view.update(status='INCOMPLETE' if missing_report else 'INVALID',
                    reason='missing-report' if missing_report else 'missing-evidence-or-seal', trusted=False)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        view.update(status='INVALID', reason='evidence-invalid-or-inconsistent', trusted=False)
    return view


def bound(view, name, api):
    if not view['trusted'] or name not in {item['path'] for item in view['data'].get('artifacts', [])}:
        return None
    return read_json(view['path'].parent / name, api)


def performance(view, api):
    result = []
    if not view['trusted'] or view['data'].get('clock', {}).get('valid') is not True:
        return result
    data = view['data']
    for family, script in (('xlsx', 'large_xlsx.py'), ('latency', 'portal_latency.py')):
        try:
            before, after, compare = [bound(view, family + '_' + phase + '.json', api)
                                      for phase in ('before', 'after', 'compare')]
            if any(value is None for value in (before, after, compare)):
                continue
            for phase in ('before', 'after'):
                if compare.get(phase + '_sha256') != hashlib.sha256((view['path'].parent / (family + '_' + phase + '.json')).read_bytes()).hexdigest():
                    raise ValueError('comparison-hash-mismatch')
            checked = validate_performance(compare, family, 'compare', data.get('profile'),
                {'before': data['baseline']['files'], 'after': data['source']['files']},
                data['source']['files']['benchmarks/' + script], before=before, after=after, budgets=None)
            if checked['status'] == 'INVALID':
                raise ValueError('invalid-comparison')
            result.append(dict(family=family, rows=checked['details']['comparisons'],
                               samples=before['samples'], warmups=before['warmups'],
                               source=view['data'].get('run_id'), recorded_at=stamp(data.get('created_at')),
                               historical=view['role'] == 'history'))
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            result.append(dict(family=family, invalid=True))
    return result


def badge(status):
    label = STATUSES.get(status, 'INVALID')
    return '<span class="badge s-{}">{}</span>'.format(label.lower(), label)


def numeric_counts(view):
    if not view['trusted'] or view['status'] == 'INVALID':
        return None
    step = next((item for item in view['data'].get('steps', []) if isinstance(item, dict) and item.get('id') == 'regression'), {})
    counts = step.get('counts')
    if not isinstance(counts, dict) or any(type(counts.get(name)) is not int or counts[name] < 0
                                          for name in ('tests', 'passed', 'failures', 'errors', 'skipped')):
        return None
    return counts


def findings(view, api):
    lines = ['<p>{}</p>'.format(esc(REASONS.get(view['reason'], view['reason'])))]
    if not view['trusted']:
        return ''.join(lines) + '<p>未經驗證的成功數字不予採用；原始資料不會自動變成通過。</p>'
    data = view['data']
    for reason in data.get('invalid_reasons', [])[:20]:
        lines.append('<p>{} {}</p>'.format(badge('INVALID'), esc(reason)))
    clock = data.get('clock', {})
    if clock.get('valid') is False:
        lines.append('<p>時鐘環境無效：{} 次偏移不連續。不得以此結果認證測試通過。</p>'.format(esc(clock.get('backsteps'))))
    for item in data.get('steps', []):
        if not isinstance(item, dict) or item.get('status') in ('PASSED', 'UNRUN'):
            continue
        lines.append('<p>{} <strong>{}</strong> · {}</p>'.format(badge(item.get('status')), esc(item.get('id')),
                     esc(REASONS.get(item.get('reason'), item.get('reason')))))
    worker = bound(view, 'regression.json', api)
    if worker:
        for identifier in worker.get('failed_test_ids', [])[:30]:
            lines.append('<p class="break">{} <code>{}</code></p>'.format(badge('FAILED'), esc(identifier)))
        skipped = worker.get('skipped', [])
        if skipped:
            lines.append('<details><summary>SKIP 略過原因（{}）</summary><ul>'.format(len(skipped)))
            for row in skipped[:100]:
                if isinstance(row, dict):
                    lines.append('<li><code>{}</code><br>{}</li>'.format(esc(row.get('test')), esc(row.get('reason'))))
            lines.append('</ul></details>')
        # Interpret only fixed exception types / an exact reviewed ETL message.
        # Never embed arbitrary exception text, which can contain row payloads.
        if worker.get('failed_test_ids') and 'regression.log' in {item['path'] for item in data['artifacts']}:
            log = safe_output_path(view['path'].parent / 'regression.log', directory=False)
            if log.stat().st_size <= MAX_JSON:
                text = log.read_text(encoding='utf-8', errors='replace')
                etl_test = 'test_etl_recovery_faults.ETLRecoveryFaultTests.test_partial_backfill_restart_continues_only_unclaimed_dates'
                etl_message = 'This ETL job is already running; no overlapping attempt was started.'
                if etl_test in worker.get('failed_test_ids', []) and etl_message in text:
                    lines.append('<pre class="exception">Conflict · 原始錯誤記錄顯示租約仍有效，ETL 防重疊規則拒絕重新啟動。時鐘無效的歷史 run 不據此判為通過。</pre>')
                notes = re.findall(r'(?m)^[\w.]+(?:Error|Exception|Conflict):[^\r\n]{0,1000}', text)
                for note in notes[-8:]:
                    kind = note.split(':', 1)[0].rsplit('.', 1)[-1]
                    known = {'Conflict', 'AssertionError', 'ValueError', 'TypeError', 'RuntimeError', 'OSError', 'KeyError', 'TimeoutError'}
                    label = kind if kind in known else 'Exception'
                    explanation = '詳細內容保留於本機 raw log；此頁不嵌入自由文字例外訊息。'
                    if note == 'reporting_workspace.errors.Conflict: This ETL job is already running; no overlapping attempt was started.':
                        explanation = 'ETL 租約仍有效；防重疊規則拒絕啟動另一個 attempt。'
                    lines.append('<pre class="exception">{} · {}</pre>'.format(label, explanation))
    return ''.join(lines)


def reproduction(views, api):
    # Only reviewed registry entries and conservative unittest identifiers.
    commands = ['python -B tools/agent_acceptance.py run --profile unit',
                'python -B tools/agent_acceptance.py verify --report "<current-report.json>"',
                'python -B tools/agent_acceptance.py render-report --report "<current-report.json>" --output "output/acceptance-review-NEW.html"']
    for view in views:
        if not view['trusted']:
            continue
        worker = bound(view, 'regression.json', api)
        if worker:
            for identifier in worker.get('failed_test_ids', [])[:30]:
                if isinstance(identifier, str) and re.fullmatch(r'test_[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.test_[A-Za-z0-9_]+', identifier):
                    commands.append('python -B -m unittest discover -s tests -p "' + identifier.split('.')[0] + '.py" -v')
    return ''.join('<pre>{}</pre>'.format(esc(value)) for value in dict.fromkeys(commands))


CSS = '''
:root{color-scheme:light;--ink:#172439;--muted:#506178;--line:#dce4ed;--blue:#2359a7}
*{box-sizing:border-box}body{margin:0;background:#eef2f7;color:var(--ink);font:15px/1.65 "Segoe UI","Microsoft JhengHei",sans-serif}
main{max-width:1160px;margin:auto;padding:36px 26px 48px}header{display:flex;justify-content:space-between;gap:20px;align-items:start}
.eyebrow{color:var(--blue);font-size:12px;font-weight:700;letter-spacing:2px}h1{font-size:32px;line-height:1.3;margin:8px 0 12px}h2{font-size:21px;margin:0 0 14px}h3{font-size:16px;margin:16px 0 10px}
p{margin:9px 0}.muted,small{color:var(--muted)}nav{display:flex;flex-wrap:wrap;gap:8px 18px;margin:20px 0}a{color:var(--blue);text-underline-offset:4px}
section{background:white;border:1px solid var(--line);border-radius:14px;padding:24px;margin:18px 0;box-shadow:0 4px 14px #19395706}section:target{outline:2px solid #a8c8f3}
.badge{display:inline-block;font-size:11px;font-weight:800;letter-spacing:.5px;padding:3px 9px;border-radius:6px;background:#e7edf6;color:#334765;white-space:nowrap}
.s-pass{background:#e1f3e9;color:#176746}.s-fail,.s-invalid{background:#fce7e6;color:#9e3029}.s-skip,.s-incomplete,.s-unrun,.s-blocked,.s-timed_out{background:#fff0d5;color:#815409}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:20px 0 12px}.card{padding:15px 18px;border:1px solid var(--line);border-radius:10px}.card strong{display:block;font-size:30px;line-height:1.3;margin-top:6px}.card span{font-size:12px;color:var(--muted)}
.legend{display:flex;flex-wrap:wrap;gap:12px;margin-top:16px}.callout{border-left:4px solid #d69b39;padding:10px 16px;background:#fff7e9;border-radius:0 7px 7px 0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.grid section{margin:0}code,pre{font:12px/1.65 Consolas,monospace;overflow-wrap:anywhere;white-space:pre-wrap}pre{background:#f3f6fa;border:1px solid var(--line);padding:12px;border-radius:8px}details{margin:12px 0;border-top:1px solid var(--line);padding-top:12px}summary{cursor:pointer;font-weight:600;color:var(--blue)}summary:focus-visible,a:focus-visible{outline:3px solid #397acf;outline-offset:3px}ul{padding-left:22px}li{margin:9px 0}.break,.source{overflow-wrap:anywhere}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:right;padding:10px 8px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}th{background:#f5f7fa;font-weight:600}th:first-child,td:first-child{text-align:left}.historical{border-left:3px solid #a7b6c9;padding-left:16px;margin-top:22px}.foot{font-size:12px;color:var(--muted)}
@media(max-width:650px){main{padding:20px 12px}header{display:block}h1{font-size:26px}section{padding:18px}.cards{grid-template-columns:repeat(2,1fr)}.grid{display:block}.grid section{margin:18px 0}.badge{font-size:10px}th,td{padding:8px 6px}nav{font-size:13px}}
@media print{body{background:white}main{padding:0;max-width:none}section{box-shadow:none;break-inside:avoid}nav{display:none}details{display:block}pre{white-space:pre-wrap}.table-wrap{overflow:visible}}
'''


def render(current, histories, api):
    views = [current] + histories
    counts = numeric_counts(current)
    number = lambda name: str(counts[name]) if counts else '—'
    failed = str(counts['failures'] + counts['errors']) if counts else '—'
    now = stamp(datetime.now(timezone.utc).timestamp())
    out = ['<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">',
           '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; script-src \'none\'; connect-src \'none\'; img-src \'none\'; base-uri \'none\'; form-action \'none\'">',
           '<meta name="referrer" content="no-referrer"><title>Dash QA｜一頁驗收報告</title><style>', CSS, '</style></head><body><main>',
           '<header><div><div class="eyebrow">DASH QA PORTAL · LOCAL ACCEPTANCE</div><h1>一頁驗收報告</h1><p class="muted">失敗原因、重現步驟、效能差異與未驗證項目。</p></div><div><small>生成時間</small><br><strong>', esc(now), '</strong></div></header>',
           '<nav aria-label="報告章節"><a href="#overview">目前驗收</a><a href="#findings">原因與歷史</a><a href="#performance">效能差異</a><a href="#reproduce">重現步驟</a><a href="#sources">證據來源</a><a href="#limits">未驗證項目</a></nav>',
           '<section id="overview" data-evidence-role="current"><h2>目前驗收 ', badge(current['status']), '</h2><p>',
           esc(REASONS.get(current['reason'], current['reason'])), '</p><div class="cards">']
    for label, value, desc in [('PASS', number('passed'), '已驗證的回歸通過'), ('FAIL', failed, '失敗與錯誤'),
                               ('SKIP', number('skipped'), '略過，不能視為通過'), ('TOTAL', number('tests'), '僅本次回歸，不累加歷史')]:
        out.append('<div class="card"><span>{}</span><strong>{}</strong><small>{}</small></div>'.format(label, value, desc))
    out += ['</div><div id="status-legend" class="legend" aria-label="狀態說明">',
            ''.join(badge(key) for key in ('PASSED', 'FAILED', 'SKIP', 'INVALID', 'INCOMPLETE')),
            '</div><p class="foot">PASS 通過 · FAIL 失敗 · SKIP 略過 · INVALID 證據或環境無效 · INCOMPLETE 尚未驗證完整。缺少證據不等於通過。</p></section>',
            '<section id="findings"><h2>失敗原因與歷史紀錄</h2>', findings(current, api)]
    for view in histories:
        out += ['<article class="historical" data-evidence-role="history"><h3>歷史證據 ', badge(view['status']), '</h3><p class="muted">',
                esc(view['data'].get('run_id')), ' · ', esc(stamp(view['data'].get('created_at'))), '</p>', findings(view, api), '</article>']
    out += ['</section><section id="performance"><h2>效能前後差異 ', badge('INCOMPLETE'), '</h2>',
            '<div class="callout">15% 耗時／10% RSS 門檻未批准，本報告不啟用。既有量測只供比較；至少需要 3 次獨立基線、核准政策及核准後候選，不能據此判定效能通過。</div>']
    measurements = [item for view in views for item in performance(view, api)]
    if not measurements:
        out.append('<p>未取得可核對的成對量測；效能未驗證。</p>')
    for item in measurements:
        family = item['family']
        if item.get('invalid'):
            out.append('<p>{} {} 比較證據不一致；不展示數值。</p>'.format(badge('INVALID'), esc(family)))
            continue
        out += ['<h3>{}</h3>'.format('大型 XLSX 匯出' if family == 'xlsx' else 'Portal 延遲'),
                '<p class="muted">{} · {} · 每組 {} 樣本／{} warmups；前後各 1 次 invocation。</p>'.format(
                    '歷史實測，非本輪執行' if item['historical'] else '本次紀錄', esc(item['recorded_at']), item['samples'], item['warmups'])]
        if family == 'latency':
            out.append('<details><summary>查看全部延遲情境（毫秒，非瀏覽器延遲）</summary>')
        out.append('<div class="table-wrap" tabindex="0" role="region" aria-label="效能比較表"><table><thead><tr><th>資料／情境</th><th>Before</th><th>After</th><th>差異</th></tr></thead><tbody>')
        for row in item['rows']:
            pairs = [('median_wall_ms', '耗時中位數 ms', 1), ('median_peak_rss_bytes', '峰值 RSS MiB', 1048576)] if family == 'xlsx' else [('median_ms', row['scenario'] + ' ms', 1)]
            for metric, label, divisor in pairs:
                before, after = row['before_' + metric] / divisor, row['after_' + metric] / divisor
                out.append('<tr><td>{:,} · {}</td><td>{:,.3f}</td><td>{:,.3f}</td><td>{:+.2f}%</td></tr>'.format(
                    row.get('rows', row.get('active_rows')), esc(label), before, after, 100 * (after / before - 1)))
        out.append('</tbody></table></div>')
        if family == 'latency':
            out.append('</details>')
    out += ['<p class="foot">正數代表耗時或記憶體增加。這些是描述性差異，不推論改善、穩定性或效能合格。</p></section>',
            '<section id="reproduce"><h2>重現步驟</h2><p>在經審查的專案、核准的 Python 環境中手動執行。先替換尖括號路徑；此頁不執行任何命令。歷史 WSL 時鐘無效時，先取得核准的正常環境，不直接重跑。</p>',
            reproduction(views, api), '<details><summary>準備新的效能證據</summary><pre>python -B tools/agent_acceptance.py run --profile performance-baseline --baseline "&lt;frozen-baseline&gt;"</pre><p>獨占時段下分別執行三次；保留全部結果。先檢查基線噪聲及核准政策，再量測候選。操作細節見 docs/acceptance/FOLLOWUP.md。</p></details></section>',
            '<section id="sources"><h2>證據來源與生成資訊</h2><p>SHA256 僅用於檢查本機資料一致性，並非簽章或獨立認證。歷史結果不取代目前驗收。</p>']
    for view in views:
        data = view['data']
        out += ['<details class="source"><summary>{} · {}</summary>'.format('目前' if view['role'] == 'current' else '歷史', esc(data.get('run_id', 'missing'))),
                '<p>檔案：<code>{}</code></p>'.format(esc((view['path'].parent.name + '/' + view['path'].name) if view['path'] else '未提供')),
                '<p>報告 SHA256：<code>{}</code></p>'.format(esc(view['sha256'])),
                '<p>程式摘要：<code>{}</code></p>'.format(esc(data.get('source', {}).get('digest'))),
                '<p>執行時間：{} → {}</p>'.format(esc(stamp(data.get('created_at'))), esc(stamp(data.get('finished_at')))),
                '<p>環境：Python {} · {} · {}</p>'.format(esc(data.get('environment', {}).get('python')), esc(data.get('environment', {}).get('platform')), esc(data.get('profile'))),
                '<p>核對範圍：{}</p></details>'.format('原始檔案及時鐘紀錄；未重驗歷史程式' if view['role'] == 'history' else '既有驗收 verify；含目前程式、runtime、artifact 與固定入口')]
    out += ['</section><section id="limits"><h2>未驗證項目與限制</h2><ul>']
    unverified = current['data'].get('unverified', []) if current['trusted'] and current['status'] != 'INVALID' else list(api.REQUIRED)
    out += ['<li>{} <code>{}</code> 尚未取得目前版本的通過證據。</li>'.format(badge('INCOMPLETE'), esc(name)) for name in unverified]
    out += ['<li>略過測試不是通過；歷史 HTTP 測試不是瀏覽器操作。</li>',
            '<li>Python 3.8 語法檢查不是執行證據；需要可用且時鐘正常的核准環境。</li>',
            '<li>15%／10% 政策未批准；效能樣本及独立基線不足，不認證效能。</li>',
            '<li>合成測試不認證公司 Oracle／LDAP／SMTP；沒有連接正式資料或外部副作用。</li>',
            '<li>此頁不含完整 raw log、環境變數或任意 JSON 欄位。常見憑證格式已遮罩；分享前仍應檢查自訂錯誤文字。</li>',
            '</ul></section><p class="foot">離線 HTML · 無 JavaScript、外部資源或命令執行 · 本輪僅本地審查，未推送／上傳／部署。</p></main></body></html>']
    return ''.join(out)


def generate(args, api):
    project = safe_output_path(args.project_root, directory=True)
    output = safe_output_path(args.output, directory=False)
    allowed = safe_output_path(project / 'output')
    if output.suffix.lower() != '.html' or allowed not in output.parents or output.exists():
        raise ValueError('report-output-must-be-new-html-under-project-output')
    if output.is_reserved() or any(':' in part for part in output.relative_to(allowed).parts):
        raise ValueError('report-output-device-or-alternate-stream-refused')
    baseline = safe_output_path(args.baseline, directory=True) if args.baseline is not None else None
    paths = args.history_report or []
    if len(paths) > 8:
        raise ValueError('at-most-eight-history-reports')
    current = source_view(args.report, project, baseline, args.max_age_seconds, api)
    histories = [source_view(path, project, baseline, args.max_age_seconds, api, historical=True) for path in paths]
    try:
        text = render(current, histories, api)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        # A malformed optional producer must never prevent a fail-closed page.
        current.update(status='INVALID', trusted=False, reason='malformed-render-evidence', data={})
        for view in histories:
            view.update(status='INVALID', trusted=False, reason='malformed-render-evidence', data={})
        text = render(current, histories, api)
    output.parent.mkdir(parents=True, exist_ok=True)
    safe_output_path(output, directory=False)
    fd = os.open(str(output), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(text.encode('utf-8'))
    status = current['status']
    return dict(status=status, exit_code=api.EXIT[status], generated=True, report=str(output),
                sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(),
                whole_project_verified=current['trusted'] and status == 'PASSED',
                historical_reports=len(histories), commands_executed=False)
