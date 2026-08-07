import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TechRadarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        os.environ["TECH_RADAR_DATA_DIR"] = cls.temp_dir.name
        import tech_radar

        cls.radar = tech_radar

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()
        os.environ.pop("TECH_RADAR_DATA_DIR", None)

    def item(self, **overrides):
        base = {
            "id": "repo:1",
            "type": "trend",
            "title": "Agent browser automation runtime",
            "name": "signal-labs/agent-runtime",
            "description": "A CLI and MCP server for agentic browser automation.",
            "url": "https://github.com/signal-labs/agent-runtime",
            "stars": 500,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
        }
        base.update(overrides)
        return base

    def test_high_signal_engineering_item_passes(self):
        now = datetime(2026, 1, 3, tzinfo=timezone.utc)
        score = self.radar.score_item(self.item(), now)
        self.assertGreaterEqual(score, self.radar.PASS_THRESHOLD)

    def test_toy_item_is_capped_below_candidate_threshold(self):
        now = datetime(2026, 1, 3, tzinfo=timezone.utc)
        score = self.radar.score_item(
            self.item(title="Toy MCP demo", description="A beginner tutorial sample"), now
        )
        self.assertLess(score, self.radar.CANDIDATE_THRESHOLD)

    def test_dedupe_keeps_one_item_per_url(self):
        first = self.item(id="repo:1")
        duplicate = self.item(id="repo:2", type="new")
        unique = self.item(id="repo:3", url="https://github.com/example/other")
        result = self.radar.dedupe_items([first, duplicate, unique])
        self.assertEqual({item["url"] for item in result}, {first["url"], unique["url"]})

    def test_commit_items_updates_state_in_isolated_directory(self):
        original_state = self.radar.STATE_FILE
        try:
            self.radar.STATE_FILE = Path(self.temp_dir.name) / "state.json"
            self.radar.commit_items([self.item()])
            state = self.radar.load_state()
        finally:
            self.radar.STATE_FILE = original_state
        self.assertEqual(state["seen"], ["repo:1"])
        self.assertEqual(state["seen_urls"], ["https://github.com/signal-labs/agent-runtime"])


if __name__ == "__main__":
    unittest.main()
