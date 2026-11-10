"""
题录 → 首版临床问题（Q1–Q5）关键词标签（kb_record_question，method=keyword）：
每个问题的第一个条件（主题词）必须出现在题名中，其余条件可出现在题名或摘要中；
摘要里顺带提到的主题（如“增生性息肉”“腺瘤检出率”）不打标签。
用于挑选深抽文献，不用于限制检索；精确度、召回尚未人工实测。规则改动后提高 RULE_VERSION 重跑，旧标签被替换。

用法（ruoyi-fastapi-backend 目录下）：
    python -m tools.kb_pipeline.qtags --env dev [--dry-run]
"""

import argparse
import re
from collections import Counter

import psycopg2
from psycopg2.extras import execute_values

from tools.kb_pipeline.ingest import db_dsn

RULE_VERSION = 'kw-v2'


def rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


# 每个问题：所有条件（AND）都命中才打标签；单个条件内为“或”；第一个条件只匹配题名
RULES: dict[str, list[re.Pattern]] = {
    'Q1': [
        rx(
            r'polypectomy|adenoma removal|removal of (colorectal )?(adenomas?|polyps?)|surveillance colonoscop|'
            r'post-?polypectomy|follow-?up colonoscop|息肉切除|腺瘤切除|内镜切除术后|监测|随访'
        ),
        rx(
            r'metachronous|recurren|advanced (neoplasia|adenoma)|high-?risk adenoma|'
            r'colorectal cancer (incidence|risk|mortality)|interval cancer|异时性|复发|再发|进展期|癌变风险'
        ),
    ],
    'Q2': [
        rx(
            r'piecemeal|\bEPMR\b|\bpEMR\b|分片|分块切除|endoscopic mucosal resection|\bEMR\b|laterally spreading|'
            r'\bLSTs?\b|\bLNPCPs?\b|large (non-?pedunculated|sessile)|侧向发育|黏膜切除'
        ),
        rx(r'recurren|residual|scar|surveillance|复发|残留|瘢痕|随访'),
    ],
    'Q3': [
        rx(
            r'bowel (preparation|cleansing)|Boston bowel|\bBBPS\b|Ottawa (bowel|scale)|adenoma detection rate|'
            r'withdrawal time|ca?ecal intubation|quality indicator|interval (colorectal )?cancer|'
            r'post-?colonoscopy colorectal cancer|\bPCCRC\b|肠道准备|腺瘤检出率|退镜时间|盲肠插管|结肠镜质量'
        ),
    ],
    'Q4': [
        rx(
            r'elderly|older (adults|patients|persons)|aged (75|80|85)|\b(75|80|85) years|octogenarian|nonagenarian|'
            r'life expectancy|comorbidit|高龄|老年|预期寿命'
        ),
        rx(r'surveillance|colonoscop|polypectomy|stop|discontinu|随访|监测|结肠镜|肠镜'),
    ],
    'Q5': [
        rx(r'serrated|sessile serrated|\bSSA/?Ps?\b|\bSSLs?\b|\bTSAs?\b|hyperplastic polyp|锯齿状|增生性息肉'),
    ],
}


def tag(title: str, abstract: str) -> list[str]:
    full = f'{title} {abstract}'
    return [q for q, (first, *rest) in RULES.items() if first.search(title) and all(c.search(full) for c in rest)]


def main() -> None:
    ap = argparse.ArgumentParser(description='题录 Q1–Q5 关键词标签')
    ap.add_argument('--env', default='dev')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    conn = psycopg2.connect(db_dsn(a.env))
    counts: Counter = Counter()
    try:
        with conn, conn.cursor() as cur:
            cur.execute('SELECT code, eq_id FROM kb_evidence_question')
            eq = dict(cur.fetchall())
            cur.execute("SELECT record_id, coalesce(title, ''), coalesce(abstract, '') FROM lit_record")
            rows = []
            for record_id, title, abstract in cur.fetchall():
                for q in tag(title, abstract):
                    rows.append((record_id, eq[q], 'keyword', None, RULE_VERSION))
                    counts[q] += 1
            counts['records_tagged'] = len({r[0] for r in rows})
            if not a.dry_run:
                cur.execute("DELETE FROM kb_record_question WHERE method = 'keyword'")
                execute_values(
                    cur,
                    'INSERT INTO kb_record_question(record_id, eq_id, method, confidence, model) VALUES %s',
                    rows,
                )
            else:
                conn.rollback()
    finally:
        conn.close()
    print(('（试运行）' if a.dry_run else '') + str(dict(counts)))


if __name__ == '__main__':
    main()
