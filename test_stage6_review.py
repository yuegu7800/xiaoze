"""Review regressions: fictional local records, no paid or external actions."""
import json
import unittest
from unittest.mock import Mock

import test_activity as fixtures
from memory_store import empty_scene, make_record, recall


class Stage6ReviewTests(unittest.TestCase):
    setUp = fixtures.ActivityTests.setUp
    open = fixtures.ActivityTests.open
    data = fixtures.ActivityTests.data
    say = fixtures.ActivityTests.say

    def record(self, content):
        data = empty_scene()
        data["memories"].append(make_record("local-owner", "owner", "fiction-root", "preference", "fixture",
                                            content, content[:160], 0, self.now, source="local_maintainer"))
        return data

    def test_coffee_color_and_qualified_style_are_not_drink_aliases(self):
        data = self.record("不喝咖啡")
        cases = {
            "咖啡色外套怎么搭": False, "咖啡色的窗帘怎么样": False,
            "美式家具怎么选": False, "喜欢美式装修风格吗": False,
            "咖啡色外套配拿铁怎么样": True, "来杯美式": True,
            "换成美式可以吗": True, "美式怎么样": True, "美式": True,
            "想喝一杯美式咖啡": True, "美式家具和咖啡都聊聊": True,
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(bool(recall(data, "owner", query, self.now)[0]), expected)
        # The same guard applies to record content. Preserve genuine clothing
        # matches, without classifying a color preference as a drink preference.
        for content, query, expected in (
            ("喜欢咖啡色", "咖啡色怎么样", True),
            ("喜欢咖啡色外套", "喝点什么", False),
            ("喜欢美式家具", "来杯拿铁", False),
            ("喜欢美式家具", "美式家具怎么选", True),
        ):
            with self.subTest(content=content, query=query):
                self.assertEqual(bool(recall(self.record(content), "owner", query, self.now)[0]), expected)

    def test_english_function_words_do_not_create_content_matches(self):
        for content, query in (
            ("coffee and tea", "music and games"),
            ("books about history", "talk about games"),
            ("tea with milk", "chat with me"),
        ):
            with self.subTest(content=content, query=query):
                self.assertFalse(recall(self.record(content), "owner", query, self.now)[0])
        self.assertTrue(recall(self.record("coffee and tea"), "owner", "coffee please", self.now)[0])

    def test_long_matched_word_cannot_break_commit_after_generation(self):
        content = "x" * 300
        self.core._save("local-owner", self.record(content))
        complete = Mock(return_value="虚构回复，已完成")
        self.core.exchange("local-owner", "owner", content, complete)
        complete.assert_called_once()
        self.assertIn(content, complete.call_args.args[0][0]["content"])
        restored = self.open().snapshot("local-owner")
        turn = restored["turns"][-1]
        self.assertEqual(turn["reply"], "虚构回复，已完成")
        self.assertLessEqual(len(turn["recall"][0]["reason"]), 300)
        self.assertIn("fiction-root", turn["reply_trace"]["source_ids"])
        self.assertEqual(restored["memories"][0]["content"], content)
        self.core.forget_memory("local-owner", restored["memories"][0]["id"])
        self.assertFalse(self.data()["turns"][-1]["reply"])

    def test_preview_and_request_agree_on_style_false_positive(self):
        self.core._save("local-owner", self.record("不喝咖啡"))
        for query, selected in (("美式家具怎么选", False), ("来杯美式怎么样", True)):
            report = self.core.recall_preview("local-owner", "owner", query)
            self.assertEqual(bool(report["channels"]["long_memory"]), selected)
            complete = Mock(return_value="虚构回复")
            self.core.exchange("local-owner", "owner", query, complete)
            system = complete.call_args.args[0][0]["content"]
            claims = json.loads(system.split("【当前有效资料】")[1].split("【本机当前关系】")[0])["claims"]
            self.assertEqual(bool(claims), selected)


if __name__ == "__main__":
    unittest.main()
