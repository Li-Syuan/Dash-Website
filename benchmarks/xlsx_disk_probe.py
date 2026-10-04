"""Untimed diagnostic of owned XLSX temporary-file sizes and cleanup.

This instrumentation is deliberately separate from large_xlsx.py timing/RSS
samples. It never enumerates unrelated temporary files or records file paths.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

from large_xlsx import run_child, service_for, source_hashes


def probe(request):
    import openpyxl
    from openpyxl.worksheet._writer import ALL_TEMP_FILES, WorksheetWriter
    original_file = tempfile.TemporaryFile
    original_cleanup = WorksheetWriter.cleanup
    owned = []
    phases = []
    xml_sizes = []
    before_xml = set(ALL_TEMP_FILES)

    def phase(name, xml_bytes=0):
        sizes = []
        for entry in owned:
            if entry.file.closed:
                sizes.append(0)
            else:
                entry.file.flush()
                sizes.append(os.fstat(entry.file.fileno()).st_size)
        phases.append({'phase': name, 'owned_file_bytes': sizes,
                       'worksheet_xml_bytes': xml_bytes,
                       'total_observed_bytes': sum(sizes) + xml_bytes})

    class ObservedFile:
        def __init__(self, *args, **kwargs):
            self.file = original_file(*args, **kwargs)
            self.mode = kwargs.get('mode', 'w+b')
            self.final_bytes = None
            owned.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file.flush()
            self.final_bytes = os.fstat(self.file.fileno()).st_size
            phase('before_owned_file_close')
            self.file.close()

        def __getattr__(self, name):
            return getattr(self.file, name)

        def __iter__(self):
            return iter(self.file)

    def cleanup(writer):
        size = os.path.getsize(writer.out)
        xml_sizes.append(size)
        phase('before_worksheet_cleanup', size)
        return original_cleanup(writer)

    service, actor = service_for(Path(request['source']), Path(request['database']), request['rows'])
    try:
        with patch('tempfile.TemporaryFile', ObservedFile), patch.object(WorksheetWriter, 'cleanup', cleanup):
            content = service.export_xlsx(actor)
        assert content.startswith(b'PK')
        assert all(entry.file.closed for entry in owned)
        assert set(ALL_TEMP_FILES) == before_xml
        return {'rows': request['rows'], 'xlsx_bytes': len(content),
                'owned_files': [{'role': 'snapshot' if 't' in entry.mode else 'archive',
                                 'bytes': entry.final_bytes, 'closed': entry.file.closed} for entry in owned],
                'worksheet_xml_bytes': xml_sizes, 'observed_phases': phases,
                'peak_observed_temp_bytes': max(item['total_observed_bytes'] for item in phases),
                'cleanup': 'all owned handles closed and openpyxl temporary-file registry unchanged',
                'scope': 'untimed phase observations; not an OS-wide disk high-water counter; final returned bytes are RAM'}
    finally:
        service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--rows', type=int, nargs='+', default=[100000, 200000])
    parser.add_argument('--child', action='store_true')
    args = parser.parse_args()
    if args.child:
        print(json.dumps(probe(json.load(sys.stdin))))
        return
    if args.output is None or args.output.exists() or any(n <= 50000 or n > 500000 for n in args.rows):
        parser.error('Choose a new output and 50,001..500,000 rows')
    source = args.source_root.resolve()
    hashes = source_hashes(source)
    result = {'schema': 1, 'diagnostic': 'separate untimed temp-disk observation',
              'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'source_hashes': hashes, 'datasets': []}
    with tempfile.TemporaryDirectory(prefix='xlsx-disk-probe-') as directory:
        for size in args.rows:
            request = {'source': str(source), 'database': str(Path(directory) / ('data-{}.sqlite'.format(size))), 'rows': size}
            setup = run_child('seed', request)
            completed = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--child'],
                input=json.dumps(request), text=True, capture_output=True, timeout=600)
            if completed.returncode or completed.stderr.strip():
                raise RuntimeError('Disk probe child failed: ' + completed.stderr.splitlines()[-1])
            dataset = json.loads(completed.stdout)
            dataset['normalized_fixture_sha256'] = setup['normalized_fixture_sha256']
            result['datasets'].append(dataset)
            print(json.dumps({'rows': size, 'peak_observed_temp_bytes': dataset['peak_observed_temp_bytes'],
                              'cleanup': 'passed'}), flush=True)
    if source_hashes(source) != hashes:
        raise RuntimeError('Source changed during diagnostic')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
