import tempfile
import unittest
from pathlib import Path

from yc_code_agent.state import StateStore


class StateStoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.temporary.name) / "state.db")

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_session_round_trip(self):
        messages = [{"role": "user", "content": "hello"}]
        self.store.save_session("demo", messages)
        self.assertEqual(self.store.load_session("demo"), messages)

    def test_goal_queue_is_fifo_and_durable(self):
        first = self.store.enqueue("first")
        second = self.store.enqueue("second")
        claimed = self.store.claim_next()
        self.assertEqual((claimed["id"], claimed["text"]), (first, "first"))
        self.store.finish(first, success=True)
        self.assertEqual(self.store.claim_next()["id"], second)
        self.assertEqual(self.store.list_goals()[0]["status"], "done")


if __name__ == "__main__":
    unittest.main()
