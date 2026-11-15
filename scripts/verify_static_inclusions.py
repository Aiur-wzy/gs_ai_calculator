"""Recalculate explicit native plans against a read-only DB using production APIs.

Example: python scripts/verify_static_inclusions.py --db-path data/genshin.db
         --output-dir outputs/static-verification-new --plan path/to/native.json
The output directory must not exist; historical results are never overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / 'src').is_dir():
    sys.path.insert(0, str(ROOT / 'src'))

from genshin_app.equipment import load_plan, run_plan_calc
from genshin_db.equipment_queries import equipment_read_session


def file_hash(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db-path', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--plan', required=True, type=Path, action='append')
    args = parser.parse_args()
    db = args.db_path.resolve(strict=True)
    if args.output_dir.exists():
        parser.error('输出目录必须不存在，以保留历史验证资料')
    plans = [(path.resolve(strict=True), load_plan(path)['plan']) for path in args.plan]
    before = file_hash(db)
    args.output_dir.mkdir(parents=True)
    records = []
    with equipment_read_session(db):
        for index, (path, plan) in enumerate(plans):
            out = run_plan_calc(plan, db)
            name = f'{index:02d}-{path.stem}.calculated.json'
            artifact = args.output_dir / name
            artifact.write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')
            records.append({'input_path': str(path), 'input_sha256': file_hash(path),
                'output_file': name, 'output_sha256': file_hash(artifact),
                'static_source_count': len(out['result'].get('static_inclusion_compilations', [])),
                'hit_count': len(out['result'].get('hits', [])), 'summary': out['result'].get('summary')})
    after = file_hash(db)
    report = {'database_path': str(db), 'database_sha256_before': before, 'database_sha256_after': after,
              'database_unchanged': before == after, 'cases': records}
    (args.output_dir / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False))
    if before != after:
        raise SystemExit('数据库在只读验证期间发生外部变化；本次验证不得记为通过')


if __name__ == '__main__':
    main()
