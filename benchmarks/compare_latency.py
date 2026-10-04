"""Compare two immutable portal_latency JSON files; reject mismatched conditions."""
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
    before, after = [json.loads(path.read_text(encoding='utf-8'))
                     for path in (args.before, args.after)]
    for field in ('schema', 'python', 'platform', 'machine', 'sqlite', 'packages',
                  'samples', 'warmups', 'timer', 'p95_method', 'measurement_scope',
                  'seed_method', 'harness_sha256'):
        if before[field] != after[field]:
            raise SystemExit('Incomparable environment or method: ' + field)
    if len(before['datasets']) != len(after['datasets']):
        raise SystemExit('Incomparable dataset count')
    results, storage = [], []
    for old, new in zip(before['datasets'], after['datasets']):
        for field in ('active_rows', 'deleted_seed_rows', 'fixture_sha256',
                      'normalized_rows_sha256', 'filtered_rows'):
            if old[field] != new[field]:
                raise SystemExit('Incomparable fixture: ' + field)
        if set(old['measurements']) != set(new['measurements']):
            raise SystemExit('Incomparable measurement set')
        storage.append({'active_rows': old['active_rows'],
                        'before_database_bytes': old['database_bytes'],
                        'after_database_bytes': new['database_bytes'],
                        'before_import_setup_total_ms': old['setup_import']['total_ms'],
                        'after_import_setup_total_ms': new['setup_import']['total_ms'],
                        'scope': old['setup_import']['scope']})
        for name in old['measurements']:
            previous, current = old['measurements'][name], new['measurements'][name]
            record = {'active_rows': old['active_rows'], 'scenario': name}
            for metric in ('median_ms', 'p95_ms'):
                record['before_' + metric] = previous[metric]
                record['after_' + metric] = current[metric]
                record[metric + '_reduction_percent'] = 100 * (1 - current[metric] / previous[metric])
            results.append(record)
    hashes_before, hashes_after = before['source_hashes'], after['source_hashes']
    changed = [name for name in sorted(set(hashes_before) | set(hashes_after))
               if hashes_before.get(name) != hashes_after.get(name)]
    output = {'schema': 1, 'conditions_match': True,
              'before_file': args.before.name, 'after_file': args.after.name,
              'before_sha256': hashlib.sha256(args.before.read_bytes()).hexdigest(),
              'after_sha256': hashlib.sha256(args.after.read_bytes()).hexdigest(),
              'source_changes': changed, 'comparisons': results, 'storage_and_setup': storage}
    if args.output.exists():
        raise SystemExit('Refusing to overwrite existing comparison evidence')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'conditions_match': True, 'source_changes': changed,
                      'comparisons': len(results)}))


if __name__ == '__main__':
    main()
