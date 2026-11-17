import tempfile
import unittest
from datetime import date

from associations import MAX_VISIBLE_NODES, build_graph
from journal import JournalStore, seed_demo


class AssociationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = JournalStore(self.temp.name)

    def test_demo_links_repeated_mentions_to_original_diaries(self):
        seed_demo(self.store, date(2026, 11, 16))
        graph = build_graph(self.store.entries(), "小A")
        self.assertEqual(graph["focus"], "person:小a")
        edge = next(edge for edge in graph["edges"] if {edge["from"], edge["to"]} == {"person:小a", "topic:约定变化"})
        self.assertEqual(edge["count"], 2)
        self.assertEqual(
            [graph["entries"][entry_id]["date"] for entry_id in edge["entry_ids"]],
            ["2026-11-13", "2026-11-07"],
        )
        self.assertEqual(build_graph(self.store.entries(), "无关词")["nodes"], [])

    def test_events_in_one_diary_do_not_invent_a_relationship(self):
        text = "小 A 改约。小 B 帮我整理了新房。"
        entry = self.store.save(text, "2026-11-16")
        events = [
            {"people": ["小 A"], "tags": ["改约"], "topic": "约定变化", "quote": "小 A 改约。"},
            {"people": ["小 B"], "tags": ["搬家"], "topic": "支持与关心", "quote": "小 B 帮我整理了新房。"},
        ]
        self.store.update_metadata(entry["id"], {"events": events + [events[0]]}, source_text=text)
        graph = build_graph(self.store.entries())
        self.assertFalse(any({edge["from"], edge["to"]} == {"person:小a", "person:小b"} for edge in graph["edges"]))
        edge = next(edge for edge in graph["edges"] if {edge["from"], edge["to"]} == {"person:小a", "tag:改约"})
        self.assertEqual(edge["entry_ids"], [entry["id"]])
        self.assertEqual(edge["count"], 1)

    def test_manual_changes_replace_old_graph_labels(self):
        entry = self.store.save("小 A 改约", "2026-11-16")
        self.store.save_labels(entry["id"], {"people": ["小程"], "tags": ["改约"]})
        graph = build_graph(self.store.entries())
        self.assertEqual({node["id"] for node in graph["nodes"]}, {"person:小程", "tag:改约"})
        self.assertEqual(graph["edges"][0]["entry_ids"], [entry["id"]])

    def test_search_reaches_keywords_outside_the_overview(self):
        for index in range(30):
            entry = self.store.save(f"关键词{index:02}", "2026-11-16")
            self.store.save_labels(entry["id"], {"people": [], "tags": [f"关键词{index:02}"]})
        entries = self.store.entries()
        overview = build_graph(entries)
        self.assertEqual(overview["total"], 30)
        self.assertEqual(len(overview["nodes"]), MAX_VISIBLE_NODES)
        self.assertNotIn("tag:关键词29", {node["id"] for node in overview["nodes"]})
        self.assertEqual(build_graph(entries, "关键词29")["focus"], "tag:关键词29")
