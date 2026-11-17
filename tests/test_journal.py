import base64
import json
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from journal import (
    DEMO_TEXT, AnalysisBusy, DemoModel, InvalidInput, JournalStore, MissingEntry, ModelUnavailable, Ollama,
    analyze, detail, find_entries, seed_demo,
)


class BrokenModel(DemoModel):
    def extract(self, text):
        raise ModelUnavailable("model offline")


class FabricatingModel(DemoModel):
    def extract(self, text):
        output = super().extract(text)
        valid = output["events"][0]
        valid["people"].append("杜撰的人")
        valid["feeling"] = "杜撰的感受"
        output["events"].append({**valid, "quote": "杜撰的原文"})
        return output

    def advise(self, text, sources, memories):
        return {"text": "来源错误的建议。", "source_ids": ["2000-01-01-000000000000"]}


class CapturingModel(DemoModel):
    def advise(self, text, sources, memories):
        self.memories = memories
        return super().advise(text, sources, memories)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = JournalStore(self.temporary.name)
        self.today = date(2026, 11, 16)

    def save(self, text=DEMO_TEXT):
        return self.store.save(text, self.today.isoformat())

    def test_original_is_saved_before_failed_analysis(self):
        text = "  小 A 改约。\r\n我很生气。\n"
        entry = self.save(text)
        with self.assertRaises(ModelUnavailable):
            analyze(self.store, BrokenModel(), entry["id"])
        self.assertEqual(text.encode(), (self.store.originals / f"{entry['id']}.md").read_bytes())
        self.assertEqual(self.store.get(entry["id"])["analysis"], "pending")
        self.assertEqual(self.store.running, set())
        self.assertEqual(analyze(self.store, DemoModel(), entry["id"])[0]["analysis"], "ready")

    def test_recall_has_real_quotes_and_original_links(self):
        seed_demo(self.store, self.today)
        entry, notice = analyze(self.store, DemoModel(), self.save()["id"])
        self.assertIsNone(notice)
        self.assertEqual(entry["people"], ["小 A"])
        self.assertEqual(len(entry["related"]), 3)
        for source in entry["related"]:
            self.assertIn(source["quote"], self.store.get(source["id"])["text"])
            self.assertNotEqual(source["id"], entry["id"])
        self.assertEqual(len(entry["advice"]["sources"]), 2)
        referenced = entry["advice"]["sources"][0]
        (self.store.originals / f"{referenced['id']}.md").write_text("旧记录被修改了。", encoding="utf-8")
        self.assertIsNone(detail(self.store, self.store.get(entry["id"]))["advice"])

    def test_search_can_find_a_person_topic_or_literal_text(self):
        seed_demo(self.store, self.today)
        entries = self.store.entries()
        self.assertEqual(len(find_entries(entries, "小A", "person")), 3)
        self.assertEqual(len(find_entries(entries, "出门前取消", "topic")), 3)
        self.assertEqual(len(find_entries(entries, "把手机放进口袋", "text")), 1)
        self.assertEqual(find_entries(entries, "田径比赛", "all"), [])

    def test_model_results_cannot_fabricate_citations_or_named_people(self):
        seed_demo(self.store, self.today)
        entry, _ = analyze(self.store, FabricatingModel(), self.save()["id"])
        self.assertEqual(len(entry["events"]), 1)
        self.assertEqual(entry["people"], ["小 A"])
        self.assertEqual(entry["events"][0]["feeling"], "")
        self.assertIsNone(entry["advice"])

    def test_manual_people_and_tags_are_preserved_on_refresh(self):
        entry = self.save()
        updated = self.store.save_labels(entry["id"], {"people": ["阿星"], "tags": ["支持与关心", "同事"]})
        self.assertEqual(updated["analysis"], "pending")
        self.assertEqual(updated["topics"], ["支持与关心"])
        self.assertEqual(updated["people"], ["阿星"])
        updated, _ = analyze(self.store, DemoModel(), entry["id"], refresh=True)
        self.assertEqual(updated["people"], ["阿星"])
        self.assertEqual(updated["tags"], ["同事"])
        self.assertEqual(updated["topics"], ["支持与关心"])
        self.assertEqual(find_entries(self.store.entries(), "改约", "topic"), [])

    def test_memory_can_be_linked_edited_considered_and_deleted(self):
        seed_demo(self.store, self.today)
        model = CapturingModel()
        old = self.store.entries()[0]
        memory = self.store.save_memory({"text": "初稿", "source_ids": [old["id"]]})
        edited = self.store.save_memory({"text": "这是我确认的经验。", "source_ids": [old["id"]]}, memory["id"])
        self.assertEqual(edited["id"], memory["id"])
        analyze(self.store, model, self.save()["id"])
        self.assertIn(edited["text"], model.memories)
        self.store.delete_memory(memory["id"])
        self.assertNotIn(edited, self.store.all_memories())

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(InvalidInput):
            self.store.save("写一件事", "2026-02-30")
        with self.assertRaises(MissingEntry):
            self.store.get("../other")
        with self.assertRaises(InvalidInput):
            self.store.save(" ", self.today.isoformat())

    def test_files_remain_readable_after_restart(self):
        entry = self.save()
        analyze(self.store, DemoModel(), entry["id"])
        restarted = JournalStore(Path(self.temporary.name))
        self.assertEqual(restarted.get(entry["id"])["text"], DEMO_TEXT)
        self.assertEqual(restarted.get(entry["id"])["analysis"], "ready")
        metadata = json.loads((restarted.metadata / f"{entry['id']}.json").read_text(encoding="utf-8"))
        self.assertIn("events", metadata)

    def test_attachments_survive_restart_and_links_can_be_searched(self):
        photo = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l2sAAAAASUVORK5CYII="
        url = "https://example.com/a-post"
        entry = self.store.save("", self.today.isoformat(), images=[{"name": "照片.png", "data": photo}], links=[url, url])
        stored = JournalStore(self.temporary.name)
        self.assertEqual(stored.get(entry["id"])["attachments"], {
            "images": [{"name": "照片.png", "file": "1.png"}],
            "links": [url],
        })
        self.assertEqual(stored.get_image(entry["id"], "1.png"), (base64.b64decode(photo), "image/png"))
        self.assertEqual(find_entries(stored.entries(), "example.com", "all")[0]["id"], entry["id"])
        self.assertEqual(find_entries(stored.entries(), "example.com", "text"), [])
        with self.assertRaises(InvalidInput):
            analyze(stored, DemoModel(), entry["id"])
        with self.assertRaises(MissingEntry):
            stored.get_image(entry["id"], "../1.png")
        self.assertEqual(self.save()["attachments"], {"images": [], "links": []})

    def test_failed_attachment_save_leaves_no_partial_entry(self):
        with self.assertRaises(InvalidInput):
            self.store.save("正文", self.today.isoformat(), links=["file:///tmp/item"])
        not_an_image = base64.b64encode(b"<svg>not a supported image</svg>").decode()
        with self.assertRaises(InvalidInput):
            self.store.save("正文", self.today.isoformat(), images=[{"data": not_an_image}])
        with patch("journal.atomic_write", side_effect=[None, OSError("磁盘写入失败")]):
            with self.assertRaises(OSError):
                self.store.save("正文", self.today.isoformat(), links=["https://example.com"])
        self.assertEqual(list(self.store.attachments.iterdir()), [])
        self.assertEqual(self.store.entries(), [])

    def test_external_original_edit_invalidates_older_summary(self):
        entry = self.save()
        analyze(self.store, DemoModel(), entry["id"])
        path = self.store.originals / f"{entry['id']}.md"
        path.write_text(DEMO_TEXT + "\n事后发现，对方其实提前发过消息。", encoding="utf-8")
        updated = self.store.get(entry["id"])
        self.assertIn("事后发现", updated["text"])
        self.assertEqual(updated["analysis"], "pending")
        self.assertEqual(updated["summary"], "")

    def test_ollama_adapter_builds_a_local_structured_request(self):
        model = Ollama()
        expected = DemoModel().extract(DEMO_TEXT)
        response = {"done": True, "message": {"content": json.dumps(expected, ensure_ascii=False)}}
        with patch.object(model.opener, "open", return_value=io.BytesIO(json.dumps(response).encode())) as send:
            self.assertEqual(model.extract(DEMO_TEXT), expected)
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "qwen3:4b")
        self.assertFalse(payload["stream"])
        self.assertIn("events", payload["format"]["required"])

    def test_label_edit_during_advice_preserves_the_edit(self):
        seed_demo(self.store, self.today)
        entry = self.save()
        store = self.store

        class EditingModel(DemoModel):
            def advise(self, text, sources, memories):
                store.save_labels(entry["id"], {"people": ["手动更新"], "tags": []})
                return super().advise(text, sources, memories)

        with self.assertRaises(AnalysisBusy):
            analyze(store, EditingModel(), entry["id"])
        saved = store.get(entry["id"])
        self.assertEqual(saved["people"], ["手动更新"])
        self.assertEqual(saved["analysis"], "ready")
        self.assertIsNone(saved["advice"])
