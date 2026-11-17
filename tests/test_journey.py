import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app import default_data
from journal import DemoModel, InvalidInput, JournalStore, ModelUnavailable, seed_demo
from journey import assess, timeline


class JourneyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = JournalStore(self.temp.name)
        seed_demo(self.store, date(2026, 11, 16))
        self.records = timeline(self.store, "约定变化", "小A")

    def payload(self):
        return {"query": "改约", "person": "小 A", "source_ids": [item["id"] for item in reversed(self.records)]}

    def test_timeline_is_chronological_and_keeps_relevant_later_context(self):
        self.assertEqual([record["date"] for record in self.records], ["2026-11-07", "2026-11-10", "2026-11-13"])
        self.assertIn("他好像没把", self.records[0]["fields"]["view"])
        self.assertIn("照顾我", self.records[1]["fields"]["reflection"])
        self.assertIn("留出时间", self.records[2]["fields"]["reflection"])
        self.assertEqual(timeline(self.store, "改约", "小周")[0]["date"], "2026-11-05")
        self.assertEqual(timeline(self.store, "", ""), [])

    def test_demo_synthesis_references_two_real_diaries(self):
        review = assess(self.store, DemoModel(), self.payload())
        self.assertEqual([source["date"] for source in review["change"]["sources"]], ["2026-11-07", "2026-11-13"])
        for item in review["items"]:
            for quote in item["fields"].values():
                self.assertIn(quote, self.store.get(item["id"])["text"])

    def test_fabricated_excerpts_and_unselected_citations_are_rejected(self):
        sources = self.payload()["source_ids"]

        class InvalidModel:
            demo = False

            def chat(self, instruction, data, schema):
                return {
                    "items": [
                        {"id": sources[0], "view": "日记里没有这句话"},
                        {"id": sources[1], "reflection": data["sources"][1]["text"]},
                    ],
                    "change": "未经足够证据支持的想法",
                    "source_ids": [sources[0], sources[1], "invalid"],
                }

        review = assess(self.store, InvalidModel(), self.payload())
        self.assertEqual([item["id"] for item in review["items"]], [sources[1]])
        self.assertIsNone(review["change"])

    def test_model_error_still_allows_reading_the_chronological_records(self):
        class OfflineModel:
            demo = False

            def chat(self, instruction, data, schema):
                raise ModelUnavailable("offline")

        with self.assertRaisesRegex(ModelUnavailable, "时间线和原文仍能查看"):
            assess(self.store, OfflineModel(), self.payload())
        self.assertEqual(timeline(self.store, "约定变化", "小A"), self.records)
        with self.assertRaises(InvalidInput):
            assess(self.store, DemoModel(), {"source_ids": [self.records[0]["id"]]})

    def test_new_name_preserves_legacy_default_data_path(self):
        parent = Path(self.temp.name)
        with patch("app.DATA_PARENT", parent):
            self.assertEqual(default_data(), parent / "心迹")
            (parent / "日记管家").mkdir()
            self.assertEqual(default_data(), parent / "日记管家")
