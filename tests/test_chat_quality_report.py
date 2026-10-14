"""Semantic review accounting must not confuse returned output with quality."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('quality_report', Path(__file__).parents[1] / 'tools' / 'report_chat_evaluation.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def grade(case_id, **changes):
    row = dict(id=case_id, meaning=2, terminology=2, speech_act=2, naturalness=2,
               aggression=2, accepted=True, high_quality=True)
    row.update(changes)
    return row


def bound_grade(row, **changes):
    return grade(row['id'], result_sha256=report.result_digest(row), **changes)


def corpus_case(case_id='a', **changes):
    row = dict(id=case_id, domain='dota', source_language='ru', target_language='zh',
               length='short', source='не жми байбек')
    row.update(changes)
    return row


def result_row(case_id='a', **changes):
    row = dict(id=case_id, source='не жми байбек', source_language='ru', target_language='zh',
               status='ok', output='别买活。')
    row.update(changes)
    return row


class QualityReportTests(unittest.TestCase):
    def test_acceptance_is_derived_from_scores_not_reviewer_boolean(self):
        self.assertFalse(report.accepted(grade('bad', meaning=0)))
        self.assertFalse(report.accepted(grade('bad', aggression=0)))
        self.assertTrue(report.accepted(grade('good', naturalness=1, high_quality=False)))
        self.assertFalse(report.high_quality(grade('good', naturalness=1)))

    def test_paired_comparison_uses_same_ids_and_reports_ungraded_outputs(self):
        cases = [corpus_case(i) for i in ('a', 'b', 'c')]
        rows = [result_row(i) for i in ('a', 'b', 'c')]
        result = report.compare(cases, rows, rows, {'a': bound_grade(rows[0]), 'b': bound_grade(rows[1], meaning=0)}, {'a': bound_grade(rows[0])})
        self.assertEqual(result['paired']['count'], 1)
        self.assertEqual(result['baseline']['reviewed'], 2)
        self.assertEqual(result['improved']['unreviewed'], ['b', 'c'])
        self.assertEqual(result['baseline']['returned'], 3)
        self.assertEqual(result['baseline']['accepted'], 1)

    def test_failed_latest_output_cannot_reuse_old_full_marks(self):
        case = corpus_case()
        old = result_row()
        latest = result_row(status='timeout', output='')
        result = report.compare([case], [old, latest], [], {'a': bound_grade(old)}, {})
        self.assertEqual(result['baseline']['returned'], 0)
        self.assertEqual(result['baseline']['accepted'], 0)
        self.assertEqual(result['baseline']['reviewed'], 0)
        self.assertEqual(result['baseline']['unreviewed'], ['a'])

    def test_changed_successful_output_requires_a_new_rating(self):
        case = corpus_case()
        old = result_row()
        changed = result_row(output='快买活。')
        result = report.compare([case], [changed], [], {'a': bound_grade(old)}, {})
        self.assertEqual(result['baseline']['accepted'], 0)
        self.assertEqual(result['baseline']['reviewed'], 0)

    def test_invalid_or_duplicate_grades_cannot_inflate_accuracy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reviews.jsonl'
            path.write_text('{"id":"a","meaning":9}\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                report.load_reviews([path])
            import json
            path.write_text('\n'.join(json.dumps(grade('a')) for _ in range(2)), encoding='utf-8')
            with self.assertRaises(ValueError):
                report.load_reviews([path])

    def test_review_ids_must_belong_to_attempted_corpus(self):
        case = corpus_case()
        with self.assertRaises(ValueError):
            report.compare([case], [result_row()], [], {'bogus': grade('bogus')}, {})

    def test_identical_id_cannot_pair_changed_source_language_or_history(self):
        case = corpus_case(history=['байбек нужен'])
        good = result_row(history=['байбек нужен'])
        for changes in (dict(source='жми байбек'), dict(source_language='zh'),
                        dict(target_language='ru'), dict(history=['байбек не нужен']), dict(history=[])):
            changed = result_row(history=['байбек нужен'])
            changed.update(changes)
            for baseline, improved in (([changed], [good]), ([good], [changed])):
                with self.subTest(changes=changes, baseline=baseline), self.assertRaisesRegex(ValueError, 'identity'):
                    report.compare([case], baseline, improved, {'a': bound_grade(baseline[0])}, {'a': bound_grade(improved[0])})

    def test_every_attempt_is_checked_even_if_latest_source_matches(self):
        case = corpus_case()
        changed = result_row(source='жми байбек')
        latest = result_row()
        with self.assertRaisesRegex(ValueError, 'identity'):
            report.compare([case], [changed, latest], [], {'a': bound_grade(latest)}, {})

    def test_missing_source_identity_fields_are_rejected(self):
        for key in ('source', 'source_language', 'target_language'):
            row = result_row()
            del row[key]
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'identity'):
                report.compare([corpus_case()], [row], [], {'a': bound_grade(row)}, {})

    def test_invalid_history_type_cannot_be_scored(self):
        row = result_row(history='байбек нужен')
        with self.assertRaisesRegex(ValueError, 'identity'):
            report.compare([corpus_case(history=['байбек нужен'])], [row], [], {'a': bound_grade(row)}, {})

    def test_omitted_history_and_explicit_empty_history_are_equivalent(self):
        row = result_row(history=[])
        result = report.compare([corpus_case()], [row], [row], {'a': bound_grade(row)}, {'a': bound_grade(row)})
        self.assertEqual(result['paired']['count'], 1)


if __name__ == '__main__':
    unittest.main()
