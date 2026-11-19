import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from moments import MomentsDrafts


class MomentsDraftTests(unittest.TestCase):
    def test_draft_uses_only_explicit_topic_and_records_unpublished_status(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            persona = root / "persona.txt"
            persona.write_text("阿澄，正常聊天。", encoding="utf-8")
            for name in ("memory.json", "group-memory.json"):
                (root / name).write_text("PRIVATE-NOT-FOR-MOMENTS", encoding="utf-8")
            generator = MomentsDrafts({"endpoint": "https://example.test/chat/completions", "model": "test", "api_key": "synthetic"},
                                      persona, root / "drafts.json")
            response = Mock(status_code=200)
            response.json.return_value = {"choices": [{"message": {"content": "今天的小想法，留到明天也不一定会变聪明。"}}]}
            with patch("requests.post", return_value=response) as post:
                generator.generate("轻松的小想法")
            self.assertNotIn("PRIVATE-NOT-FOR-MOMENTS", json.dumps(post.call_args.kwargs["json"]))
            saved = json.loads(generator.path.read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["status"], "draft")
            self.assertFalse(saved[0]["published"])

    def test_generation_failure_does_not_save_a_draft(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            persona = root / "persona.txt"
            persona.write_text("阿澄", encoding="utf-8")
            generator = MomentsDrafts({"endpoint": "https://example.test/chat/completions", "model": "test", "api_key": "synthetic"},
                                      persona, root / "drafts.json")
            with patch("requests.post", return_value=Mock(status_code=500)):
                with self.assertRaises(RuntimeError):
                    generator.generate()
            self.assertFalse(generator.path.exists())


if __name__ == "__main__":
    unittest.main()
