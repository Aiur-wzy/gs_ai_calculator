"""CLI batch review publication: prepare all bytes before one atomic commit."""
from __future__ import annotations

import hashlib
from pathlib import Path

from genshin_app.calculation_review_batch import _review_calculation_file
from genshin_app.equipment.files import publish_files
from genshin_app.query_cache import protect_inputs
from genshin_app.review_batch_json import (
    BATCH_BYTES,
    REQUEST_BYTES,
    embed_json,
    encode,
    read_json,
)


def command(args):
    from genshin_app.cli import CLIUsageError
    if not args.calculated or not args.request:
        raise CLIUsageError('equipment review-batch需要--calculated PATH与--request PATH')
    if args.output:
        protect_inputs([args.output], [args.calculated, args.request, Path(args.data_dir) / 'genshin.db'])
    request = read_json(args.request, REQUEST_BYTES)

    def finish(data, budget):
        content = encode(data, budget, maximum=BATCH_BYTES)
        data_bytes = content
        if args.output:
            path = str(Path(args.output).resolve())
            budget.charge((len(content) + 255) // 256)
            files = {'published_paths': [path], 'file_shas': {path: hashlib.sha256(content).hexdigest()}}
            data = {**data, 'files': files}
            addition = b',"files":' + encode(files, budget, maximum=BATCH_BYTES) + b'}'
            if len(content) - 1 + len(addition) > BATCH_BYTES:
                from genshin_app.equipment.schema import PlanError
                raise PlanError('批量响应含文件回执超过字节上限', 'review_size_limit')
            budget.charge((len(content) + len(addition) + 255) // 256)
            data_bytes = content[:-1] + addition
        if args.pretty:
            raw = encode({'code': 0, 'message': 'ok', 'data': data}, budget,
                         maximum=BATCH_BYTES - 1, pretty=True, compact=False)
        else:
            raw = embed_json({'code': 0, 'message': 'ok'}, 'data', data_bytes, budget, maximum=BATCH_BYTES - 1)
        rendered = raw.decode('utf-8')
        if args.output:
            publish_files([(args.output, content)], overwrite=args.overwrite,
                          input_paths=[args.calculated, args.request, Path(args.data_dir) / 'genshin.db'])
        return rendered

    rendered = _review_calculation_file(args.calculated, request, finish=finish)
    print(rendered)
