"""Empty structured output diagnostics and request compatibility, fictional only."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock, patch

from character import ApiCompletion
import test_activity as fixtures

DEEPSEEK = dict(fixtures.CFG, endpoint='https://api.deepseek.com/v1/chat/completions', model='deepseek-chat')


class CandidateEmptyTests(unittest.TestCase):
    setUp = fixtures.ActivityTests.setUp
    open = fixtures.ActivityTests.open
    data = fixtures.ActivityTests.data
    say = fixtures.ActivityTests.say
    topic = fixtures.ActivityTests.topic
    evaluate = fixtures.ActivityTests.evaluate

    def response(self, content='', reason='stop', reasoning=''):
        response = Mock(status_code=200)
        response.json.return_value = {'choices': [{'message': {'content': content, 'reasoning_content': reasoning},
                                                  'finish_reason': reason}]}
        return response

    def test_official_short_candidate_avoids_default_thinking_without_changing_chat_or_model(self):
        self.topic()
        cfg = deepcopy(DEEPSEEK)

        def provider(*args, **kwargs):
            payload = kwargs['json']
            # Simulate a thinking budget exhausted before final content.
            if payload.get('thinking') != {'type': 'disabled'}:
                return self.response('', 'length', 'PRIVATE_REASONING_CANARY')
            return self.response(fixtures.DRAFT)

        with patch('requests.post', side_effect=provider) as post:
            result = self.evaluate(ApiCompletion(cfg, json_object=True), backend='api')
            self.assertEqual(result['decision'], 'draft')
            post.assert_called_once()
            payload = post.call_args.kwargs['json']
            self.assertEqual(payload['thinking'], {'type': 'disabled'})
            self.assertEqual(payload['max_tokens'], 2048)
            self.assertEqual(payload['model'], 'deepseek-chat')
            self.assertEqual(cfg, DEEPSEEK)
            self.assertNotIn('PRIVATE_REASONING_CANARY', json.dumps(self.core.memory.state))

        for endpoint, structured in ((DEEPSEEK['endpoint'], False),
                                     ('https://api.deepseek.com.other.test/v1/chat/completions', True),
                                     (fixtures.CFG['endpoint'], True)):
            with self.subTest(endpoint=endpoint, structured=structured), patch('requests.post', return_value=self.response('普通文字')) as post:
                ApiCompletion(dict(DEEPSEEK, endpoint=endpoint), json_object=structured)([{'role': 'user', 'content': '虚构'}])
                payload = post.call_args.kwargs['json']
                self.assertNotIn('thinking', payload)
                self.assertEqual(payload['max_tokens'], 2048 if structured else 1024)

    def test_blank_reasoning_truncation_and_invalid_response_are_distinct_without_commit(self):
        self.topic()
        before = self.path.read_bytes(), deepcopy(self.core.memory.state)
        for content, reason, reasoning, expected in (
                ('   ', 'stop', '', '仅空白'),
                ('', 'length', 'PRIVATE_REASONING_CANARY', '输出或上下文'),
                (None, 'stop', 'PRIVATE_REASONING_CANARY', '只有推理内容'),
                ('', None, '', '未提供'),
                ('', 'content_filter', '', '拦截'),
                ('', 'tool_calls', '', '工具调用'),
                ({'PRIVATE_BODY_CANARY': 'secret'}, 'stop', '', '正文类型'),
                ('', 'PRIVATE_FINISH_CANARY', '', '未知的完成状态')):
            with self.subTest(reason=reason, expected=expected), patch('requests.post', return_value=self.response(content, reason, reasoning)) as post:
                with self.assertRaisesRegex(RuntimeError, expected) as caught:
                    self.evaluate(ApiCompletion(DEEPSEEK, json_object=True))
                error = str(caught.exception)
                self.assertNotIn('CANARY', error)
                self.assertNotIn('已暂停', error)
                self.assertLess(len(error), 500)
                post.assert_called_once()
                self.assertEqual((self.path.read_bytes(), self.core.memory.state), before)

    def test_silence_requires_explicit_json_and_history_is_not_format_example(self):
        self.topic()
        complete = Mock(return_value=fixtures.SILENT)
        result = self.evaluate(complete)
        messages = complete.call_args.args[0]
        self.assertIn('不能用空白', messages[0]['content'])
        self.assertIn('不是本次输出格式的示例', messages[0]['content'])
        current = json.loads(messages[-1]['content'])
        self.assertIn('{"decision":"silent","text":""}', current['output_requirement'])
        self.assertEqual(result['decision'], 'silent')
        self.assertEqual(self.data()['activity']['drafts'], [])


if __name__ == '__main__':
    unittest.main()
