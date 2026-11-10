"""
用当前校验规则离线检查某个目录下的抽取 JSON（不写数据库），用于评估规则变更的影响

    python -m tools.kb_pipeline.dry_validate --dir <抽取JSON目录> [--rule value_not_verbatim] [--show 20]
"""

import argparse
import collections
import json
from pathlib import Path

import psycopg2

from tools.kb_pipeline.extract import load_doc
from tools.kb_pipeline.ingest import db_dsn
from tools.kb_pipeline.validators import validate


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--env', default='dev')
    ap.add_argument('--dir', required=True)
    ap.add_argument('--rule', default='')
    ap.add_argument('--show', type=int, default=20)
    a = ap.parse_args()
    total: collections.Counter = collections.Counter()
    shown = 0
    with psycopg2.connect(db_dsn(a.env)) as conn, conn.cursor() as cur:
        for path in sorted(Path(a.dir).glob('*.json'), key=lambda p: int(p.stem)):
            doc = load_doc(cur, path.stem)
            if not doc:
                continue
            issues = validate(
                json.loads(path.read_text(encoding='utf-8')), {b['block_key']: b['text'] for b in doc.blocks}
            )
            kinds = collections.Counter(i.rule_code for i in issues)
            total.update(kinds)
            if kinds:
                print(f'{path.stem:>6} {dict(kinds)}')
            for i in issues:
                if a.rule and i.rule_code == a.rule and shown < a.show:
                    print(f'        {i.entity_ref} {i.message[:160]}')
                    shown += 1
    print('合计', dict(total))


if __name__ == '__main__':
    main()
