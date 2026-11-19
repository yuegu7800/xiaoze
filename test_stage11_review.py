"""Stage-eleven policy regressions, fictional data and controllable clocks."""
from datetime import timedelta
import unittest
from unittest.mock import Mock

import test_auto_opening as fixtures


class AutoReviewTests(unittest.TestCase):
    setUp = fixtures.AutoOpeningTests.setUp
    open = fixtures.AutoOpeningTests.open
    data = fixtures.AutoOpeningTests.data
    say = fixtures.AutoOpeningTests.say
    topic = fixtures.AutoOpeningTests.topic
    setup_auto = fixtures.AutoOpeningTests.setup_auto
    idle = fixtures.AutoOpeningTests.idle
    generate = fixtures.AutoOpeningTests.generate

    def test_local_recheck_block_does_not_claim_model_silence_or_cooldown(self):
        self.setup_auto()
        self.idle()
        ready, _ = self.core.candidate_precheck("local-owner", "owner", "private")
        self.assertTrue(ready)
        run = self.policy.begin(self.core.ticket())
        # A clock-driven expiry between precheck and worker does not move the
        # epoch. Actual evaluation must recheck, and no provider is attempted.
        self.clock.wall += timedelta(days=8)
        before = self.path.read_bytes(), self.core._epoch
        complete = Mock()
        result = self.core.evaluate_candidate("local-owner", "owner", "private", complete,
            ticket=run.ticket, guard=lambda: self.policy.guard(run))
        complete.assert_not_called()
        self.assertEqual(result["decision"], "silent")
        self.assertNotIn("commit_ticket", result)
        self.policy.finish(run, result)
        self.assertEqual(self.policy.note, result["reason"])
        self.assertNotIn("冷却", self.policy.note)
        self.assertNotIn("模型", self.policy.note)
        self.assertEqual((self.path.read_bytes(), self.core._epoch), before)
        self.assertEqual(self.policy.budget.attempt_total, 0)


if __name__ == "__main__":
    unittest.main()
