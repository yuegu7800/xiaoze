"""Fictional candidate-format compatibility; no provider calls or real stores."""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

import test_activity as fixtures
from activity import decode_candidate
from character import ApiCompletion


class CandidateFormatTests(unittest.TestCase):
    setUp = fixtures.ActivityTests.setUp
    open = fixtures.ActivityTests.open
    data = fixtures.ActivityTests.data
    say = fixtures.ActivityTests.say
    topic = fixtures.ActivityTests.topic
    evaluate = fixtures.ActivityTests.evaluate

    def test_one_complete_json_fence_is_a_draft_not_an_external_action(self):
        self.topic()
        before = deepcopy(self.data()["turns"])
        complete = Mock(return_value='```json\n{"decision":"draft","text":"聊聊纸船结尾？"}\n```')
        result = self.evaluate(complete)
        self.assertEqual(result["decision"], "draft")
        self.assertEqual(complete.call_count, 1)
        self.assertEqual(self.data()["turns"], before)
        self.assertEqual(self.data()["activity"]["drafts"][-1]["text"], "聊聊纸船结尾？")

    def test_invalid_output_has_safe_reason_and_does_not_commit_or_retry(self):
        self.topic()
        before = self.path.read_bytes(), deepcopy(self.core.memory.state)
        for value, reason in [('PRIVATE_CANARY普通文字', '不是JSON对象'),
                              ('{"decision":"draft","text":"hi","target":"PRIVATE_CANARY"}', '字段不符合'),
                              ('{"decision":"draft","text":""}', '草稿正文')]:
            with self.subTest(reason=reason):
                complete = Mock(return_value=value)
                with self.assertRaisesRegex(RuntimeError, reason) as error:
                    self.evaluate(complete)
                self.assertNotIn('PRIVATE_CANARY', str(error.exception))
                self.assertEqual(complete.call_count, 1)
                self.assertEqual((self.path.read_bytes(), self.core.memory.state), before)

    def test_wrappers_do_not_allow_extra_text_blocks_or_duplicate_fields(self):
        for value in ['说明\n```json\n{"decision":"silent","text":""}\n```',
                      '```json\n{"decision":"silent","text":""}\n```\n还有一句',
                      '```json\n{"decision":"silent","text":""}\n```\n```json\n{}\n```',
                      '{"decision":"silent","decision":"draft","text":"hi"}']:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                decode_candidate(value)
        self.assertIsNone(decode_candidate('```JSON\n{"decision":"silent","text":""}\n```'))

    def test_json_transport_fixes_provider_that_otherwise_returns_prose(self):
        self.topic()
        cfg = deepcopy(fixtures.CFG)
        before = self.path.read_bytes(), deepcopy(self.core.memory.state)

        def provider(*args, **kwargs):
            payload = kwargs['json']
            content = (fixtures.DRAFT if payload.get('response_format') == {'type': 'json_object'}
                       else '孤月大人，纸船的结尾后来有新想法了吗？')
            response = Mock(status_code=200)
            response.json.return_value = {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]}
            return response

        with patch('requests.post', side_effect=provider) as post:
            with self.assertRaisesRegex(RuntimeError, '不是JSON对象'):
                self.evaluate(ApiCompletion(cfg))
            post.assert_called_once()
            self.assertNotIn('response_format', post.call_args.kwargs['json'])
            self.assertEqual((self.path.read_bytes(), self.core.memory.state), before)
            post.reset_mock()
            result = self.evaluate(ApiCompletion(cfg, json_object=True), backend='api')
            post.assert_called_once()
            self.assertEqual(result['decision'], 'draft')
            payload = post.call_args.kwargs['json']
            self.assertEqual(payload['response_format'], {'type': 'json_object'})
            self.assertEqual(payload['model'], cfg['model'])
            self.assertIn('JSON', payload['messages'][0]['content'])
            self.assertEqual(post.call_args.args[0], cfg['endpoint'])
            self.assertEqual(cfg, fixtures.CFG)
            self.assertEqual(self.data()['turns'], before[1]['scenes']['local-owner']['turns'])

    def test_json_mode_rejections_bad_content_and_truncation_fail_once(self):
        self.topic()
        before = self.path.read_bytes(), deepcopy(self.core.memory.state)
        for status, content, finish, error in (
                (400, 'PRIVATE_CANARY', 'stop', 'JSON 对象模式'),
                (422, 'PRIVATE_CANARY', 'stop', 'JSON 对象模式'),
                (200, 'PRIVATE_CANARY', 'stop', '不是JSON对象'),
                (200, fixtures.DRAFT, 'length', '未完成'),
                (200, '{"decision":"draft","decision":"silent","text":""}', 'stop', '重复字段')):
            response = Mock(status_code=status, text='PRIVATE_CANARY')
            response.json.return_value = {'choices': [{'message': {'content': content}, 'finish_reason': finish}]}
            with self.subTest(status=status, finish=finish), patch('requests.post', return_value=response) as post:
                with self.assertRaisesRegex(RuntimeError, error) as caught:
                    self.evaluate(ApiCompletion(fixtures.CFG, json_object=True))
                self.assertNotIn('PRIVATE_CANARY', str(caught.exception))
                post.assert_called_once()
                self.assertEqual((self.path.read_bytes(), self.core.memory.state), before)

    def test_json_silence_is_not_saved_as_draft_or_retried(self):
        self.topic()
        response = Mock(status_code=200)
        response.json.return_value = {'choices': [{'message': {'content': fixtures.SILENT}, 'finish_reason': 'stop'}]}
        with patch('requests.post', return_value=response) as post:
            result = self.evaluate(ApiCompletion(fixtures.CFG, json_object=True))
            self.assertEqual(result['decision'], 'silent')
            self.assertEqual(self.data()['activity']['drafts'], [])
            post.assert_called_once()


if __name__ == '__main__':
    unittest.main()
