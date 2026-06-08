from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_researcher import db
from ai_researcher.discord_bot import make_rate_custom_id, parse_component_custom_id
from ai_researcher.notifier import notify_report
from ai_researcher.preferences import apply_preference_adjustments, record_feedback
from ai_researcher.utils import utc_now


class FeedbackTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "research.db"
        self.conn = db.connect(self.db_path)
        db.init_db(self.conn)
        self._seed_article("article-1")

    def tearDown(self) -> None:
        self.conn.close()
        self.temp_dir.cleanup()

    def test_feedback_tables_are_created(self) -> None:
        tables = {
            row["name"]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        self.assertIn("article_feedback", tables)
        self.assertIn("article_feedback_events", tables)

    def test_record_feedback_upserts_latest_and_appends_events(self) -> None:
        record_feedback(
            self.conn,
            report_key="daily:2026-06-08",
            article_id="article-1",
            discord_user_id="user-1",
            rating="刺さる",
        )
        record_feedback(
            self.conn,
            report_key="daily:2026-06-08",
            article_id="article-1",
            discord_user_id="user-1",
            rating="追う",
            comment="継続して追いたい",
        )

        latest = self.conn.execute("SELECT * FROM article_feedback").fetchone()
        self.assertEqual(latest["rating"], "追う")
        self.assertIn("継続して追いたい", latest["comment"])

        events = self.conn.execute("SELECT COUNT(*) AS count FROM article_feedback_events").fetchone()
        self.assertEqual(events["count"], 2)

    def test_preference_adjustment_is_inactive_before_threshold_and_capped_after(self) -> None:
        candidate = self._candidate("candidate-1")
        for index in range(19):
            record_feedback(
                self.conn,
                report_key=f"daily:2026-06-{index + 1:02d}",
                article_id="article-1",
                discord_user_id=f"user-{index}",
                rating="刺さる",
            )

        adjusted, context = apply_preference_adjustments(self.conn, [candidate])
        self.assertFalse(context["active"])
        self.assertEqual(adjusted[0]["preference_adjustment"], 0.0)
        self.assertEqual(adjusted[0]["preference_score"], candidate["score"])

        record_feedback(
            self.conn,
            report_key="daily:2026-06-20",
            article_id="article-1",
            discord_user_id="user-19",
            rating="刺さる",
        )
        adjusted, context = apply_preference_adjustments(self.conn, [candidate])
        self.assertTrue(context["active"])
        self.assertGreater(adjusted[0]["preference_adjustment"], 0)
        self.assertLessEqual(adjusted[0]["preference_adjustment"], 0.7)

    def test_discord_custom_id_round_trip(self) -> None:
        custom_id = make_rate_custom_id("daily:2026-06-08", "abc123")
        parsed = parse_component_custom_id(custom_id)
        self.assertIsNotNone(parsed)
        self.assertLessEqual(len(custom_id), 100)
        self.assertEqual(parsed.action, "rate")
        self.assertEqual(parsed.report_key, "daily:2026-06-08")
        self.assertEqual(parsed.article_id, "abc123")

    def test_notify_report_uses_webhook_when_bot_is_unset(self) -> None:
        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.test/webhook"}, clear=True):
            with patch("ai_researcher.notifier.post_discord_webhook") as post_webhook:
                post_webhook.return_value = {"status": "sent", "http_status": 204}
                result = notify_report(
                    [self._candidate("article-1")],
                    period="daily",
                    label="2026-06-08",
                    report_path="reports/daily/2026-06-08.md",
                    candidates=1,
                    selected=1,
                    newly_adopted=1,
                    config={"notifications": {"discord": {"enabled": True}}},
                    report_key="daily:2026-06-08",
                )

        self.assertEqual(result["status"], "sent")
        post_webhook.assert_called_once()

    def test_notify_report_posts_feedback_card_when_bot_is_set(self) -> None:
        env = {"DISCORD_BOT_TOKEN": "token", "DISCORD_CHANNEL_ID": "channel"}
        with patch.dict(os.environ, env, clear=True):
            with patch("ai_researcher.notifier.post_discord_bot_message") as post_bot:
                post_bot.side_effect = [
                    {"status": "sent", "http_status": 200, "message_id": "summary"},
                    {"status": "sent", "http_status": 200, "message_id": "article"},
                ]
                result = notify_report(
                    [self._candidate("article-1")],
                    period="daily",
                    label="2026-06-08",
                    report_path="reports/daily/2026-06-08.md",
                    candidates=1,
                    selected=1,
                    newly_adopted=1,
                    config={"notifications": {"discord": {"enabled": True}}},
                    report_key="daily:2026-06-08",
                )

        self.assertEqual(result["status"], "sent")
        self.assertEqual(post_bot.call_count, 2)
        article_payload = post_bot.call_args_list[1].args[2]
        self.assertEqual(article_payload["components"][0]["components"][0]["type"], 3)
        self.assertEqual(article_payload["components"][1]["components"][0]["label"], "コメント")
        self.assertEqual(article_payload["components"][1]["components"][1]["style"], 5)

    def _seed_article(self, article_id: str) -> None:
        db.seed_source(
            self.conn,
            {
                "id": "source-1",
                "name": "Example Source",
                "type": "rss",
                "url": "https://example.com/feed.xml",
                "category": "official",
                "priority": 1.0,
                "max_items": 10,
            },
        )
        db.upsert_article(
            self.conn,
            {
                "id": article_id,
                "source_id": "source-1",
                "url": f"https://example.com/{article_id}",
                "title": "LLM agent release",
                "summary": "A practical LLM agent article.",
                "author": "Example",
                "published_at": utc_now(),
                "fetched_at": utc_now(),
                "score": 4.0,
                "relevance": 1,
                "reasons": ["matched: LLM"],
                "matched_keywords": ["LLM"],
                "tags": ["agent"],
                "cluster_key": article_id,
                "raw": {},
            },
        )
        self.conn.commit()

    def _candidate(self, article_id: str) -> dict[str, object]:
        return {
            "id": article_id,
            "source_id": "source-1",
            "source_name": "Example Source",
            "source_category": "official",
            "source_priority": 1.0,
            "url": f"https://example.com/{article_id}",
            "title": "LLM agent release",
            "summary": "A practical LLM agent article.",
            "score": 4.0,
            "matched_keywords": json.dumps(["LLM"]),
            "tags": json.dumps(["agent"]),
            "selection_reason": "Useful for agent engineering.",
        }


if __name__ == "__main__":
    unittest.main()
