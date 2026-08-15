import contextlib
import io
import json
import os
import subprocess
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

    def test_env_file_is_loaded_before_configuration(self):
        configured_dir = Path(self.temp_dir.name) / "configured-state"
        env_file = Path(self.temp_dir.name) / "config.env"
        env_file.write_text(
            "TECH_RADAR_PASS_THRESHOLD=99\n"
            f"TECH_RADAR_DATA_DIR={configured_dir}\n",
            encoding="utf-8",
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("TECH_RADAR_")
        }
        env["TECH_RADAR_ENV_FILE"] = str(env_file)

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json, tech_radar as r; "
                    "print(json.dumps({'threshold': r.PASS_THRESHOLD, "
                    "'data_dir': str(r.DATA_DIR)}))"
                ),
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        config = json.loads(result.stdout)

        self.assertEqual(config["threshold"], 99)
        self.assertEqual(Path(config["data_dir"]), configured_dir)

    def test_seen_items_are_not_reintroduced_to_fill_batch(self):
        original_load_state = self.radar.load_state
        original_collect_items = self.radar.collect_items
        item = self.item()
        try:
            self.radar.load_state = lambda: {
                "seen": [item["id"]],
                "seen_urls": [item["url"]],
            }
            self.radar.collect_items = lambda: [item]
            result = self.radar.get_unseen_batch(3)
        finally:
            self.radar.load_state = original_load_state
            self.radar.collect_items = original_collect_items

        self.assertEqual(result, [])

    def test_hosting_url_does_not_influence_scoring(self):
        item = self.item(
            title="plain-tool",
            description="",
            url="https://github.com/example/plain-tool",
            topics=[],
        )
        text = self.radar.scoring_text(item)

        self.assertNotIn("github.com", text)
        self.assertEqual(self.radar.calc_relevance(text), 0)
        self.assertEqual(self.radar.calc_engineering(text), 0)
        self.assertEqual(self.radar.calc_novelty(text), 0)

    def test_github_topics_are_preserved(self):
        item = self.radar.github_item(
            {
                "id": 1,
                "full_name": "example/plain-tool",
                "description": "",
                "html_url": "https://github.com/example/plain-tool",
                "stargazers_count": 10,
                "topics": ["mcp", "ai-agent"],
            },
            "new",
        )

        self.assertEqual(item["topics"], ["mcp", "ai-agent"])

    def test_hn_points_use_hn_maturity_path(self):
        item = self.radar.hn_item(
            {
                "objectID": "42",
                "title": "Agent CLI",
                "points": 100,
                "created_at": "2026-01-03T00:00:00Z",
            }
        )
        now = datetime(2026, 1, 3, tzinfo=timezone.utc)
        expected = self.radar.log_score(100, 500.0, 70.0)

        self.assertEqual(item["points"], 100)
        self.assertNotIn("stars", item)
        self.assertAlmostEqual(self.radar.calc_maturity(item, now), expected)
        self.assertIn("▲100", self.radar.meta_text(item))

    def test_show_reuses_existing_pending_batch(self):
        original_pending = self.radar.PENDING_FILE
        original_parse_args = self.radar.parse_args
        original_get_unseen_batch = self.radar.get_unseen_batch
        try:
            self.radar.PENDING_FILE = Path(self.temp_dir.name) / "pending.json"
            self.radar.write_pending(
                [self.radar.public_item(self.item())],
                "01:00 UTC",
            )
            self.radar.parse_args = lambda: self.radar.argparse.Namespace(
                commit=False,
                show=True,
                json=True,
                max=5,
                no_pending=False,
                refresh=False,
            )

            def fail_if_refetched(_max_items):
                raise AssertionError("pending batch should be reused")

            self.radar.get_unseen_batch = fail_if_refetched
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = self.radar.main()
        finally:
            self.radar.PENDING_FILE = original_pending
            self.radar.parse_args = original_parse_args
            self.radar.get_unseen_batch = original_get_unseen_batch

        self.assertEqual(exit_code, 0)
        self.assertIn("repo:1", output.getvalue())


if __name__ == "__main__":
    unittest.main()
