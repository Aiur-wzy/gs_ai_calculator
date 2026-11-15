"""Real source/CLI/HTTP unit projections, without claiming gameplay dispatch."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
import zlib
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from genshin_db.runtime_queries import query_runtime, runtime_capabilities
from genshin_db.service import GenshinHTTPServer

PREFIX = 'BinOutput/Ability/Temp/AvatarAbilities/ConfigAbility_Avatar_'
# Observation limit for these seven engineering cases, not a public CLI contract.
ENGINEERING_SUMMARY_LIMIT_BYTES = 16 * 1024
CASES = (
    ('ayaka-five', 'Ayaka', '/12/Default/GPPBBDEICKP/DropBall_Base/EMGBJJOIENB/0/PAPFFKOKPML/0', 2022, 5, 'cryo'),
    ('ayaka-four', 'Ayaka', '/12/Default/GPPBBDEICKP/DropBall_Base/EMGBJJOIENB/0/MLJNNELMEFL/0', 2022, 4, 'cryo'),
    ('kazuha-tap', 'Kazuha', '/3/Default/GPPBBDEICKP/Avatar_Kazuha_ElementalArt_DropBall/EMGBJJOIENB/0', 2021, 3, 'anemo'),
    ('kazuha-hold', 'Kazuha', '/3/Default/GPPBBDEICKP/Avatar_Kazuha_ElementalArt_DropBall/EMGBJJOIENB/1', 2021, 4, 'anemo'),
    ('kokomi-success', 'Kokomi', '/14/Default/GPPBBDEICKP/Avatar_Kokomi_ElementalArt_ElemBall/EMGBJJOIENB/0/MLJNNELMEFL/0', 2018, 1, 'hydro'),
    ('shenhe-tap', 'Shenhe', '/3/Default/GPPBBDEICKP/ElementalArt_Bullet_ElemBall/EMGBJJOIENB/0', 2022, 3, 'cryo'),
    ('shenhe-hold', 'Shenhe', '/6/Default/GPPBBDEICKP/ElementalArt_ElemBall/EMGBJJOIENB/0', 2022, 4, 'cryo'),
)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fp(value):
    return hashlib.sha256(canonical(value).encode('utf8')).hexdigest()


def write(path, value):
    with path.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def pointer(raw, path):
    for token in path.split('/')[1:]:
        token = token.replace('~1', '/').replace('~0', '~')
        raw = raw[int(token)] if isinstance(raw, list) else raw[token]
    return raw


def main(args):
    db, out = args.data_dir / 'genshin.db', args.output_dir
    assert not out.exists(), 'Use a fresh output directory; previous evidence is preserved'
    out.mkdir(parents=True)
    before, checks, observations = sha(db), [], []

    def check(name, value):
        checks.append({'name': name, 'passed': bool(value)})
        assert value, name

    try:
        caps = runtime_capabilities()
        check('self describing operation', 'energy_generation_profile' in caps['actions'])
        check('index6 source requirement', caps['energy_generation_profile']['minimum_indexer_version'] == '6')
        write(out / 'capabilities.json', caps)
        requests = []
        with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            for name, character, operation, _material, _count, _element in CASES:
                rows = [r for r in conn.execute(
                    "SELECT reference_id,json_pointer FROM runtime_reference WHERE version_line='release' AND source_path=? AND field_name='configID'",
                    (PREFIX + character + '.json',)) if r[1].rsplit('/', 1)[0] == operation]
                assert len(rows) == 1, (name, rows)
                requests.append({'id': name, 'request': {'action': 'energy_generation_profile', 'origin_reference_id': rows[0][0]}})
        request = {'action': 'batch', 'version_line': 'release', 'items': requests}
        started = time.perf_counter()
        result = query_runtime(db, request)
        observations.append({'operation': 'seven_origin_batch', 'wall_seconds': time.perf_counter() - started,
                             'canonical_bytes': len(canonical(result).encode('utf8'))})
        write(out / 'seven-source-projections.json', result)
        check('seven conditional source leaves return completely', result['status'] == 'complete' and result['counts']['ok'] == 7)
        by_name = {row['id']: row['data'] for row in result['items']}
        sources = {}
        with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            def source(path):
                if path not in sources:
                    row = conn.execute("SELECT payload_blob,source_sha256 FROM runtime_source WHERE version_line='release' AND source_path=?", (path,)).fetchone()
                    raw_bytes = zlib.decompress(row[0])
                    assert hashlib.sha256(raw_bytes).hexdigest() == row[1]
                    sources[path] = json.loads(raw_bytes), row[1]
                return sources[path]

            for name, character, operation, material, count, element in CASES:
                data = by_name[name]
                projection = data['reference_projection']
                check(name + ' local relationship and units ready', data['status'] == data['source_status'] == projection['status'] == 'ready')
                check(name + ' no whole skill or dispatch claim', data['whole_source_status'] == 'partial' and data['execution_support'] == 'not_evaluated' and data['parent_semantics']['status'] == 'not_evaluated')
                check(name + ' independent expected units', projection['count'] == count and projection['unit']['element'] == element and projection['particle_type'] == 'particle')
                check(name + ' reference evidence only', projection['evidence_tier'] == 'reference_implementation' and projection['mapping_status'] == 'standard_energy_model_projection')
                origin = data['origin']
                check(name + ' exact selected operation', origin['operation']['raw_pointer'] == operation)
                raw_parent, _ = source(PREFIX + character + '.json')
                check(name + ' complete operation bytes', origin['operation']['raw'] == pointer(raw_parent, operation) and origin['operation']['node_sha256'] == fp(pointer(raw_parent, operation)))
                nodes = [origin['parent'], *data['material']['candidates']]
                if origin['owner'] is not None:
                    nodes.append(origin['owner'])
                for node in nodes:
                    raw, source_sha = source(node['source_path'])
                    expected = pointer(raw, node['json_pointer'])
                    payload = node['payload'] if 'payload' in node else node['record']
                    assert payload == expected and node['source_sha256'] == source_sha and node['node_sha256'] == fp(expected)
                check(name + ' all full nodes independently match raw DB bytes', True)
                selected = next(n for n in data['material']['candidates'] if n['definition']['definition_id'] == data['material']['selected_definition_id'])
                material_raw = selected.get('payload', selected.get('record'))
                check(name + ' exact material and unfilled useOnGain', material_raw['id'] == material and 'useOnGain' not in material_raw)
                check(name + ' response hash and bounded complete data', data['content_fingerprint'] == fp({k: v for k, v in data.items() if k != 'content_fingerprint'}) and len(canonical(data).encode('utf8')) <= 262144)
        check('same parent material retains separate leaves', by_name['ayaka-five']['source_fingerprint'] != by_name['ayaka-four']['source_fingerprint'])
        check('no numeric branch aggregation', [by_name[k]['reference_projection']['count'] for k in ('ayaka-five', 'ayaka-four')] == [5, 4])
        check('modifier origin retains owner Ability', all(v['origin']['owner'] is not None for v in by_name.values()))
        http_state = out / 'http-state'
        http_state.mkdir()
        with closing(sqlite3.connect(http_state / 'genshin.db')) as conn, conn:
            conn.execute('CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT)')
        server = GenshinHTTPServer(http_state, port=0)
        server.db_path = db
        worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        worker.start()
        try:
            req = urllib.request.Request(server.base_url + '/v1/runtime/query', data=canonical(requests[0]['request']).encode('utf8'), headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=120) as response:
                payload = json.load(response)
                check('real HTTP identical source', response.status == 200 and payload['data']['source_fingerprint'] == by_name['ayaka-five']['source_fingerprint'])
                check('real HTTP complete body identical', canonical(payload['data']) == canonical(by_name['ayaka-five']))
                check('real HTTP independently recomputed content hash', payload['data']['content_fingerprint'] == fp({k: v for k, v in payload['data'].items() if k != 'content_fingerprint'}))
            write(out / 'http-response.json', payload)
        finally:
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()
        prefixes = [('source', [sys.executable, '-X', 'utf8', '-m', 'genshin_app'])]
        if args.cli_exe:
            prefixes.append(('package', [str(args.cli_exe)]))
        for label, prefix in prefixes:
            req_path, dest = out / (label + '-request.json'), out / (label + '-files')
            write(req_path, request)
            started = time.perf_counter()
            proc = subprocess.run([*prefix, '--data-dir', str(args.data_dir), 'db', 'runtime', '--input', str(req_path), '--output-dir', str(dest), '--summary'], cwd=ROOT, capture_output=True, encoding='utf8', timeout=180, check=False)
            observations.append({'operation': label + '_batch_delivery', 'wall_seconds': time.perf_counter() - started,
                                 'stdout_bytes': len(proc.stdout.encode('utf8')), 'stderr_bytes': len(proc.stderr.encode('utf8'))})
            (out / (label + '.stdout.json')).write_text(proc.stdout, 'utf8')
            (out / (label + '.stderr.txt')).write_text(proc.stderr, 'utf8')
            check(label + ' CLI complete and short', proc.returncode == 0
                  and len(proc.stdout.encode('utf8')) <= ENGINEERING_SUMMARY_LIMIT_BYTES and not proc.stderr)
            index = json.loads((dest / 'index.json').read_text('utf8'))
            check(label + ' complete index', index['status'] == 'complete' and len(index['items']) == 7
                  and {item['id'] for item in index['items']} == {item['id'] for item in requests})
            for item in index['items']:
                path = (dest / item['path']).resolve()
                assert path.is_relative_to(dest) and sha(path) == item['file_sha']
                saved = json.loads(path.read_text('utf8'))['data']
                assert saved['source_fingerprint'] == by_name[item['id']]['source_fingerprint']
                assert canonical(saved) == canonical(by_name[item['id']])
                assert saved['content_fingerprint'] == fp({k: v for k, v in saved.items() if k != 'content_fingerprint'})
            check(label + ' each full small file matches source', True)
        if args.old_data_dir:
            old_db = args.old_data_dir / 'genshin.db'
            old_sha = sha(old_db)
            with closing(sqlite3.connect(old_db.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
                state = json.loads(conn.execute("SELECT validation_json FROM data_component WHERE component='runtime_config' AND version_line='release'").fetchone()[0])
                check('old compatibility database really is index5', state['indexer_version'] == '5')
            old = query_runtime(args.old_data_dir / 'genshin.db', {'action': 'batch', 'items': [requests[0], {'id': 'old-q', 'request': {'action': 'action_resource_profile', 'character_id': 10000002, 'slot': 'burst'}}]})
            write(out / 'old-index-query.json', old)
            check('index5 upgrade is item scoped', old['status'] == 'partial' and old['items'][0]['error']['reason'] == 'material_index_upgrade_required' and old['items'][1]['status'] == 'ok')
            check('old compatibility database unchanged', sha(old_db) == old_sha)
            observations.append({'operation': 'old_index5_compatibility', 'database': str(old_db), 'database_sha256': old_sha})
        if args.legacy_cli_exe:
            req_path = out / 'legacy-request.json'
            write(req_path, {'action': 'action_resource_profile', 'character_id': 10000002, 'slot': 'burst'})
            proc = subprocess.run([str(args.legacy_cli_exe), '--data-dir', str(args.data_dir), 'db', 'runtime', '--input', str(req_path)], cwd=ROOT, capture_output=True, encoding='utf8', timeout=120, check=False)
            (out / 'legacy.stderr.json').write_text(proc.stderr, 'utf8')
            (out / 'legacy.stdout.txt').write_text(proc.stdout, 'utf8')
            check('0.19 rejects unsupported index6 cleanly', proc.returncode == 1 and json.loads(proc.stderr)['reason'] == 'unsupported_schema' and not proc.stdout)
            observations.append({'operation': 'legacy_executable_rejects_index6', 'exe': str(args.legacy_cli_exe), 'exe_sha256': sha(args.legacy_cli_exe)})
        check('database bytes unchanged', sha(db) == before)
        receipt = {'status': 'passed', 'scope': 'conditional unit projection only; no gameplay execution', 'passed': len(checks), 'checks': checks,
                   'database_sha256': before, 'snapshot': result['snapshot'], 'cli_exe': str(args.cli_exe) if args.cli_exe else None,
                   'exe_sha256': sha(args.cli_exe) if args.cli_exe else None, 'observations': observations,
                   'measurement_scope': 'single local run, not a repeated performance benchmark; no peak RSS captured'}
        write(out / 'verification.json', receipt)
        print(canonical({'status': 'passed', 'checks': len(checks), 'output': str(out)}), flush=True)
    except Exception as exc:
        write(out / 'verification-failed.json', {'checks': checks, 'error': repr(exc), 'database_sha256_before': before, 'database_sha256_after': sha(db)})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('data-dir', 'output-dir', 'cli-exe', 'old-data-dir', 'legacy-cli-exe'):
        parser.add_argument('--' + name, type=Path, required=name in ('data-dir', 'output-dir'))
    args = parser.parse_args()
    for name, value in vars(args).items():
        if value is not None:
            setattr(args, name, value.resolve())
    if not args.output_dir.is_relative_to(ROOT / 'outputs'):
        parser.error('Evidence must remain inside workspace outputs')
    main(args)
