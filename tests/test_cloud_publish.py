import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import cloud_publish


class CloudPublishTests(unittest.TestCase):
    def item(self):
        return {
            "id": "repo:1",
            "type": "trend",
            "emoji": "📈",
            "score": 88.5,
            "stars": 321,
            "points": 0,
            "name": "signal-labs/agent-runtime",
            "title": "Agent runtime",
            "description": "A practical agent automation runtime.",
            "url": "https://github.com/signal-labs/agent-runtime",
        }

    def test_build_payload_uses_shanghai_report_date(self):
        instant = datetime(2026, 8, 14, 23, 20, tzinfo=timezone.utc)
        with mock.patch.object(cloud_publish.radar, "utc_now", return_value=instant):
            payload = cloud_publish.build_payload([self.item()])

        self.assertEqual(payload["report_date"], "2026-08-15")
        self.assertEqual(payload["timezone"], "Asia/Shanghai")
        self.assertEqual(payload["items"][0]["id"], "repo:1")

    def test_render_markdown_handles_empty_report(self):
        markdown = cloud_publish.render_markdown(
            {
                "report_date": "2026-08-15",
                "items": [],
            }
        )
        self.assertIn("今日暂无新的高信号技术动态", markdown)

    def test_publish_writes_latest_archive_and_commits_state(self):
        with tempfile.TemporaryDirectory() as directory:
            report_dir = Path(directory) / "reports"
            item = self.item()
            instant = datetime(2026, 8, 14, 23, 20, tzinfo=timezone.utc)

            with mock.patch.object(cloud_publish, "REPORT_DIR", report_dir), mock.patch.object(
                cloud_publish.radar, "get_unseen_batch", return_value=[item]
            ), mock.patch.object(
                cloud_publish.radar, "utc_now", return_value=instant
            ), mock.patch.object(
                cloud_publish.radar, "commit_items"
            ) as commit_items, mock.patch.object(
                cloud_publish.radar, "clear_pending"
            ) as clear_pending:
                payload = cloud_publish.publish()

            latest = json.loads(
                (report_dir / "latest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(latest, payload)
            self.assertTrue(
                (report_dir / "archive" / "2026-08-15.md").exists()
            )
            commit_items.assert_called_once_with([item])
            clear_pending.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
