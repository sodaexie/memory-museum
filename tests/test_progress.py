import json
import tempfile
import unittest
from datetime import date
from unittest.mock import Mock

from journal import JournalStore
from progress import MAX_NOTES, cached_threads, organize_progress, progress_signature, progress_sources, validate_threads


class ProgressTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = JournalStore(temp.name)
        self.today = date(2026, 11, 17)

    def test_source_window_gives_other_days_a_slot(self):
        earlier = self.store.save("昨天联系了房东。", "2026-11-16")
        for i in range(MAX_NOTES + 1):
            self.store.save(f"今天第 {i} 条记录", "2026-11-17")
        self.store.save("很久以前的记录", "2026-10-17")
        self.store.save("未来日期的记录", "2026-11-18")
        sources = progress_sources(self.store, self.today)
        self.assertEqual(len(sources), MAX_NOTES)
        self.assertEqual(sources[0]["id"], earlier["id"])
        self.assertEqual({source["date"] for source in sources}, {"2026-11-16", "2026-11-17"})

    def test_grounds_and_orders_separate_highlights_on_the_same_day(self):
        early = self.store.save("模拟面试后，发现可以先说结论。", "2026-11-15")
        late = self.store.save("早上收到面试邀请。下午通过了之前的面试。", "2026-11-17")
        sources = progress_sources(self.store, self.today)
        result = {"threads": [
            {"title": "秋招", "moments": [
                {"source_id": late["id"], "quote": "下午通过了之前的面试。", "label": "通过面试", "kind": "highlight"},
                {"source_id": early["id"], "quote": "发现可以先说结论。", "label": "学会先说结论", "kind": "turning_point"},
                {"source_id": late["id"], "quote": "早上收到面试邀请。", "label": "收到面试邀请", "kind": "highlight"},
                {"source_id": late["id"], "quote": "已经收到录用。", "label": "收到录用", "kind": "highlight"},
            ]},
            {"title": "只在一天出现的随记", "moments": [
                {"source_id": late["id"], "quote": "早上收到面试邀请。", "label": "普通记录", "kind": "note"},
            ]},
        ]}
        (thread,) = validate_threads(result, sources)
        moments = thread["moments"]
        self.assertEqual([item["label"] for item in moments], ["学会先说结论", "收到面试邀请", "通过面试"])
        self.assertEqual([item["kind"] for item in moments], ["turning_point", "highlight", "highlight"])
        self.assertEqual([item["date"] for item in moments], ["2026-11-15", "2026-11-17", "2026-11-17"])

    def test_a_single_day_can_start_a_growth_timeline(self):
        diary = self.store.save("第一次鼓起勇气投了简历。", "2026-11-17")
        sources = progress_sources(self.store, self.today)
        model = Mock(demo=False)
        model.chat.return_value = {"threads": [{"title": "秋招", "moments": [
            {"source_id": diary["id"], "quote": diary["text"], "label": "第一次投简历", "kind": "highlight"},
        ]}]}
        (thread,) = organize_progress(self.store, model, sources)
        self.assertEqual(thread["moments"][0]["id"], diary["id"])

    def test_cached_task_format_is_ignored(self):
        self.store.save("收到秋招面试邀请。", "2026-11-17")
        sources = progress_sources(self.store, self.today)
        (self.store.metadata / "progress.json").write_text(json.dumps({
            "signature": progress_signature(sources), "threads": [{"title": "秋招", "steps": []}],
        }), encoding="utf-8")
        self.assertIsNone(cached_threads(self.store, sources))

    def test_cache_reuses_automatic_results_until_recent_notes_change(self):
        self.store.save("第一天", "2026-11-16")
        self.store.save("第二天", "2026-11-17")
        sources = progress_sources(self.store, self.today)
        model = Mock(demo=False)
        model.chat.return_value = {"threads": []}
        self.assertEqual(organize_progress(self.store, model, sources, automatic=True), [])
        self.assertEqual(organize_progress(self.store, model, sources, automatic=True), [])
        model.chat.assert_called_once()
        self.store.save("第二天再补一条", "2026-11-17")
        self.assertIsNone(cached_threads(self.store, progress_sources(self.store, self.today)))
