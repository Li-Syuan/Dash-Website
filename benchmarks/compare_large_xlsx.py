"""Compare large XLSX raw samples only when methodology and fixtures match."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new output; evidence is immutable')
    before, after = [json.loads(path.read_text(encoding='utf-8'))
                     for path in (args.before, args.after)]
    for field in ('schema', 'python', 'platform', 'machine', 'packages',
                  'harness_sha256', 'samples', 'warmups', 'timer', 'memory_scope',
                  'warmup_scope', 'p95_method', 'fixture_method', 'default_cap_unchanged'):
        if before[field] != after[field]:
            raise SystemExit('Incomparable conditions: ' + field)
    if len(before['datasets']) != len(after['datasets']):
        raise SystemExit('Incomparable dataset count')
    comparisons = []
    for old, new in zip(before['datasets'], after['datasets']):
        if old['rows'] != new['rows'] or old['setup']['normalized_fixture_sha256'] != new['setup']['normalized_fixture_sha256']:
            raise SystemExit('Incomparable fixture')
        expected = old['setup']['normalized_fixture_sha256']
        for dataset in (old, new):
            if dataset['default_cap_check']['status'] != 'expected-rejection':
                raise SystemExit('Default cap did not reject oversized data')
            for sample in dataset['samples'] + dataset['warmups']:
                if sample['validation']['normalized_rows_sha256'] != expected:
                    raise SystemExit('Semantic output mismatch')
        row = {'rows': old['rows']}
        for key in ('median_wall_ms', 'p95_wall_ms', 'median_peak_rss_bytes',
                    'p95_peak_rss_bytes', 'median_peak_commit_bytes', 'p95_peak_commit_bytes'):
            if key in old and key in new:
                row['before_' + key] = old[key]
                row['after_' + key] = new[key]
                row[key + '_reduction_percent'] = 100 * (1 - new[key] / old[key])
        comparisons.append(row)
    old_hashes, new_hashes = before['source_hashes'], after['source_hashes']
    result = {'schema': 1, 'conditions_match': True,
              'before_file': args.before.name, 'after_file': args.after.name,
              'before_sha256': hashlib.sha256(args.before.read_bytes()).hexdigest(),
              'after_sha256': hashlib.sha256(args.after.read_bytes()).hexdigest(),
              'source_changes': [name for name in sorted(set(old_hashes) | set(new_hashes))
                                 if old_hashes.get(name) != new_hashes.get(name)],
              'comparisons': comparisons}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
