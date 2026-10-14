"""Combine actual offline outputs and assisted semantic ratings, without exact-string scoring."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import hashlib
from pathlib import Path

SCORES = ('meaning', 'terminology', 'speech_act', 'naturalness', 'aggression')


def result_digest(row):
    identity = {key: row.get(key) for key in ('id', 'source', 'source_language', 'target_language', 'history', 'status', 'output')}
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def accepted(row):
    return (row['meaning'] == row['speech_act'] == row['aggression'] == 2
            and row['terminology'] >= 1 and row['naturalness'] >= 1)


def high_quality(row):
    return all(row[key] == 2 for key in SCORES)


def load_reviews(paths):
    result = {}
    for path in paths:
        for row in read_rows(path):
            if not isinstance(row.get('id'), str) or any(
                    type(row.get(key)) is not int or row[key] not in (0, 1, 2) for key in SCORES):
                raise ValueError(f'Invalid semantic rating in {path}: {row.get("id")}')
            if row['id'] in result:
                raise ValueError(f'Duplicate semantic rating: {row["id"]}')
            result[row['id']] = row
    return result


def _quality(rows, reviews):
    ids = set(rows)
    graded = ids & set(reviews)
    return dict(attempted=len(ids), returned=sum(row['status'] == 'ok' for row in rows.values()),
                statuses=dict(Counter(row['status'] for row in rows.values())), reviewed=len(graded),
                accepted=sum(rows[i]['status'] == 'ok' and accepted(reviews[i]) for i in graded),
                high_quality=sum(rows[i]['status'] == 'ok' and high_quality(reviews[i]) for i in graded),
                unreviewed=sorted(ids - graded),
                reference_issues=sorted(i for i in graded if reviews[i].get('reference_issue')))


def source_identity(row):
    if any(not isinstance(row.get(key), str) or not row[key].strip()
           for key in ('source', 'source_language', 'target_language')):
        raise ValueError(f'Invalid source identity: {row.get("id")}')
    if (row['source_language'], row['target_language']) not in (('ru', 'zh'), ('zh', 'ru')):
        raise ValueError(f'Invalid language identity: {row.get("id")}')
    history = row.get('history', [])
    if not isinstance(history, list) or any(not isinstance(text, str) or not text.strip() for text in history):
        raise ValueError(f'Invalid history identity: {row.get("id")}')
    return row['source'], row['source_language'], row['target_language'], tuple(history)


def compare(cases, baseline_rows, improved_rows, baseline_reviews, improved_reviews):
    case_by_id = {row['id']: row for row in cases}
    baseline_rows, improved_rows = list(baseline_rows), list(improved_rows)
    baseline = {row['id']: row for row in baseline_rows}
    improved = {row['id']: row for row in improved_rows}
    if (set(baseline) | set(improved)) - set(case_by_id):
        raise ValueError('Attempted IDs absent from corpus')
    identities = {case_id: source_identity(row) for case_id, row in case_by_id.items()}
    for rows in (baseline_rows, improved_rows):
        for row in rows:
            if source_identity(row) != identities[row['id']]:
                raise ValueError(f'Result source/language/history identity differs from corpus: {row["id"]}')
    if set(baseline_reviews) - set(baseline) or set(improved_reviews) - set(improved):
        raise ValueError('Reviewed IDs absent from attempted outputs')
    # Ratings are about concrete source/output pairs, not a reusable case ID.
    # A retry or a pipeline change must not inherit stale semantic scores.
    baseline_reviews = {i: row for i, row in baseline_reviews.items()
                        if row.get('result_sha256') == result_digest(baseline[i])}
    improved_reviews = {i: row for i, row in improved_reviews.items()
                        if row.get('result_sha256') == result_digest(improved[i])}
    paired = set(baseline_reviews) & set(improved_reviews)

    def pair_summary(ids):
        ids = paired & set(ids)
        before = {i for i in ids if baseline[i]['status'] == 'ok' and accepted(baseline_reviews[i])}
        after = {i for i in ids if improved[i]['status'] == 'ok' and accepted(improved_reviews[i])}
        return dict(count=len(ids), baseline_accepted=len(before), improved_accepted=len(after),
                    baseline_high_quality=sum(baseline[i]['status'] == 'ok' and high_quality(baseline_reviews[i]) for i in ids),
                    improved_high_quality=sum(improved[i]['status'] == 'ok' and high_quality(improved_reviews[i]) for i in ids),
                    recovered=sorted(after - before), regressed=sorted(before - after))

    groups = {}
    for case in cases:
        key = f'{case["domain"]}:{case["source_language"]}>{case["target_language"]}:{case["length"]}'
        groups.setdefault(key, []).append(case['id'])
    return dict(corpus_count=len(cases), baseline=_quality(baseline, baseline_reviews),
                improved=_quality(improved, improved_reviews), paired=pair_summary(case_by_id),
                by_stratum={key: pair_summary(ids) for key, ids in sorted(groups.items())})


def ratio(numerator, denominator):
    return f'{numerator}/{denominator}（{numerator / denominator:.0%}）' if denominator else '尚未评分'


def render_markdown(result, baseline_metrics, improved_metrics):
    paired = result['paired']
    lines = ['# 本地翻译 600 条评测', '',
             '样本为原创模拟聊天：200 条粗话、400 条 Dota 信息，包含短、中、长句及俄中双向翻译。',
             '语义评分由助手逐条核对，属于辅助审查，不是俄罗斯母语玩家盲测，也不是实战聊天准确率。', '',
             '“可用”要求意思、否定/问句/人称、攻击强度正确，游戏术语和表达至少清楚；“优质”要求五项全部合格。',
             '返回译文、保留数字、与参考译文文字相同，都不能单独证明翻译正确。', '',
             '| 检查 | 修改前 | 修改后 |', '|---|---:|---:|',
             f'| 实际测试 | {result["baseline"]["attempted"]} 条 | {result["improved"]["attempted"]} 条 |',
             f'| 同一批已评分样本可用 | {ratio(paired["baseline_accepted"], paired["count"])} | {ratio(paired["improved_accepted"], paired["count"])} |',
             f'| 同一批已评分样本优质 | {ratio(paired["baseline_high_quality"], paired["count"])} | {ratio(paired["improved_high_quality"], paired["count"])} |']
    for title, field, divisor, suffix in (
            ('模型翻译耗时中位数', 'successful_model_latency_seconds', 1, ' 秒'),
            ('模型翻译耗时 P95', 'successful_model_latency_seconds', 1, ' 秒'),
            ('模型进程内存峰值', 'peak_owned_rss_bytes', 1024 ** 3, ' GiB')):
        values = []
        for metrics in (baseline_metrics, improved_metrics):
            value = metrics.get(field)
            if isinstance(value, dict):
                value = value.get('p95' if 'P95' in title else 'median')
            values.append(f'{value / divisor:.2f}{suffix}' if isinstance(value, (int, float)) else '未完成')
        lines.append(f'| {title} | {values[0]} | {values[1]} |')
    lines += ['', '耗时包含当次提示处理；固定为 CPU 2 线程、GPU 0。首次加载、电脑负载、缓存和测试顺序会影响耗时。', '',
              '| 类别 / 方向 / 长度 | 已评分配对 | 修改前可用 | 修改后可用 |', '|---|---:|---:|---:|']
    for key, group in result['by_stratum'].items():
        label = key.replace('profanity', '粗话').replace('dota', 'Dota').replace('ru>zh', '俄→中').replace('zh>ru', '中→俄')
        label = label.replace('short', '短').replace('medium', '中').replace('long', '长')
        lines.append(f'| {label} | {group["count"]} | {ratio(group["baseline_accepted"], group["count"])} | {ratio(group["improved_accepted"], group["count"])} |')
    lines += ['', f'由不可用变可用：{len(paired["recovered"])} 条；出现回退：{len(paired["regressed"])} 条。',
              f'修改前未评分：{len(result["baseline"]["unreviewed"])} 条；修改后未评分：{len(result["improved"]["unreviewed"])} 条。',
              '存在参考答案或原文歧义的样本单独标记，允许符合原文的其他译法，不按逐字匹配计分。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=Path(__file__).resolve().parents[1] / 'eval' / 'chat600')
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--improved', type=Path, required=True)
    parser.add_argument('--reviews', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cases = [row for path in sorted(args.corpus.glob('*.jsonl')) for row in read_rows(path)]
    result = compare(cases, read_rows(args.baseline / 'results.jsonl'), read_rows(args.improved / 'results.jsonl'),
                     load_reviews(sorted(args.reviews.glob(args.baseline.name + '-*.jsonl'))),
                     load_reviews(sorted(args.reviews.glob(args.improved.name + '-*.jsonl'))))
    metrics = [json.loads((directory / 'summary.json').read_text(encoding='utf-8')) for directory in (args.baseline, args.improved)]
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'quality.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output / 'report.md').write_text(render_markdown(result, *metrics), encoding='utf-8')
    print(json.dumps({'baseline_attempted': result['baseline']['attempted'], 'improved_attempted': result['improved']['attempted'],
                      'paired_reviewed': result['paired']['count'], 'report': str(args.output / 'report.md')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
