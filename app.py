"""Run the private diary interface on the local loopback address."""

import argparse
import json
import tempfile
import threading
import webbrowser
from collections import Counter
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from associations import build_graph
from journey import assess, timeline
from journal import (
    DEMO_TEXT, MAX_TOTAL_IMAGE_BYTES, TOPICS, AnalysisBusy, InvalidInput, JournalStore, MissingEntry,
    ModelUnavailable, analyze, detail, entry_summary, find_entries, seed_demo,
)
from model_settings import ModelSettings
from progress import cached_threads, organize_progress, progress_sources, seed_demo_progress

ROOT = Path(__file__).resolve().parent
APP_NAME = "心迹"
DATA_PARENT = Path.home() / "Library" / "Application Support"
TIMEZONE = ZoneInfo("Asia/Shanghai")
MAX_ENTRY_REQUEST_BYTES = 4 * ((MAX_TOTAL_IMAGE_BYTES + 2) // 3) + 128 * 1024
STATIC = {
    "/": ("index.html", "text/html"),
    "/app.js": ("app.js", "text/javascript"),
    "/attachments.js": ("attachments.js", "text/javascript"),
    "/graph.js": ("graph.js", "text/javascript"),
    "/model-settings.js": ("model-settings.js", "text/javascript"),
    "/progress.js": ("progress.js", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
}


def default_data():
    current = DATA_PARENT / APP_NAME
    legacy = DATA_PARENT / "日记管家"
    return legacy if legacy.is_dir() and not current.exists() else current


def today():
    return datetime.now(TIMEZONE).date()


class JournalServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, port, store, settings):
        super().__init__(("127.0.0.1", port), Handler)
        self.store = store
        self.settings = settings
        self.origins = {
            f"http://127.0.0.1:{self.server_port}",
            f"http://localhost:{self.server_port}",
        }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Avoid writing search queries or identifiers to terminal logs.
        pass

    def answer(self, status, body, content_type):
        self.send_response(status)
        if not content_type.startswith("image/"):
            content_type += "; charset=utf-8"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def reply(self, value, status=200):
        self.answer(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json")

    def check_origin(self, write=False):
        host_origin = "http://" + self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if (
            host_origin not in self.server.origins
            or (origin is not None and origin != host_origin)
            or fetch_site == "cross-site"
            or (write and self.headers.get_content_type() != "application/json")
        ):
            self.reply({"error": "请从心迹的本机页面打开。"}, 403)
            return False
        return True

    def body(self, path):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            limit = MAX_ENTRY_REQUEST_BYTES if path == "/api/entries" else 65536
            if length > limit:
                raise InvalidInput("提交的内容太大。请减少图片或压缩后再试。")
            if length <= 0:
                raise ValueError
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except InvalidInput:
            raise
        except (ValueError, UnicodeError) as exc:
            raise InvalidInput("提交的内容无法读取。") from exc

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def dispatch(self, write):
        if not self.check_origin(write):
            return
        target = urlsplit(self.path)
        try:
            if write:
                self.post(target.path, self.body(target.path))
            else:
                self.get(target.path, parse_qs(target.query))
        except InvalidInput as exc:
            self.reply({"error": str(exc)}, 400)
        except MissingEntry as exc:
            self.reply({"error": str(exc)}, 404)
        except AnalysisBusy as exc:
            self.reply({"error": str(exc)}, 409)
        except ModelUnavailable as exc:
            self.reply({"error": str(exc)}, 503)
        except OSError:
            self.reply({"error": "暂时无法读写本地日记。请检查目录权限和剩余空间。"}, 500)

    def get(self, path, query):
        store = self.server.store
        settings = self.server.settings
        if path in STATIC:
            filename, content_type = STATIC[path]
            self.answer(200, (ROOT / "web" / filename).read_bytes(), content_type)
        elif path == "/api/status":
            config = settings.current()
            self.reply({**settings.to_model(config).status(), "provider": config["provider"],
                        "auto_analyze": config["auto_analyze"]})
        elif path == "/api/model-settings":
            self.reply(settings.public())
        elif path == "/api/home":
            entries = store.entries()
            people = Counter(person for entry in entries for person in entry["people"])
            recorded_dates = list(dict.fromkeys(entry["date"] for entry in entries))
            recent_dates = set(recorded_dates[:5])
            self.reply({
                "today": today().isoformat(), "demo": settings.demo,
                "sample": DEMO_TEXT if settings.demo else "",
                "recent": [entry_summary(entry) for entry in entries if entry["date"] in recent_dates],
                "count": len(entries), "people_count": len(people),
                "memory_count": len(store.all_memories()),
            })
        elif path == "/api/entries":
            entries = store.entries()
            people = Counter(person for entry in entries for person in entry["people"])
            self.reply({
                "entries": find_entries(entries, query.get("q", [""])[0][:200], query.get("kind", ["all"])[0]),
                "people": people.most_common(), "topics": list(TOPICS),
            })
        elif path == "/api/graph":
            subject = query.get("q", [""])[0].strip()[:80]
            self.reply(build_graph(store.entries(), subject))
        elif path == "/api/journey/progress":
            self.journey_progress()
        elif path == "/api/journey":
            subject = query.get("q", [""])[0].strip()[:140]
            person = query.get("person", [""])[0].strip()[:60]
            self.reply({"records": timeline(store, subject, person)})
        elif path == "/api/memories":
            self.reply({"memories": store.all_memories()})
        elif path.startswith("/api/entry/"):
            entry_id, separator, filename = path.removeprefix("/api/entry/").partition("/images/")
            if separator:
                image, content_type = store.get_image(entry_id, filename)
                self.answer(200, image, content_type)
            else:
                self.reply(detail(store, store.get(entry_id)))
        else:
            self.reply({"error": "这里没有日记内容。"}, 404)

    def post(self, path, payload):
        store = self.server.store
        settings = self.server.settings
        if path == "/api/entries":
            entry = store.save(
                payload.get("text"), payload.get("date", today().isoformat()),
                images=payload.get("images", []), links=payload.get("links", []),
            )
            self.reply(detail(store, entry), 201)
        elif path == "/api/journey/progress":
            self.journey_progress(payload)
        elif path == "/api/journey":
            self.reply(assess(store, settings.to_model(settings.current()), payload))
        elif path == "/api/model-settings":
            self.reply(settings.save(payload))
        elif path == "/api/model-settings/test":
            self.reply(settings.test(payload))
        elif path == "/api/model-settings/clear-key":
            self.reply(settings.clear_key())
        elif path.startswith("/api/analyze/"):
            entry_id = path.removeprefix("/api/analyze/")
            config = settings.current()
            if payload.get("automatic") is True and not config["auto_analyze"]:
                self.reply({"entry": detail(store, store.get(entry_id)),
                            "notice": "原文已存本机。需要 AI 整理时，可以打开这条日记并手动开始。"})
                return
            entry, notice = analyze(
                store, settings.to_model(config), entry_id, payload.get("refresh") is True,
            )
            self.reply({"entry": entry, "notice": notice})
        elif path.startswith("/api/labels/"):
            entry = store.save_labels(path.removeprefix("/api/labels/"), payload)
            self.reply(detail(store, entry))
        elif path == "/api/memories":
            self.reply(store.save_memory(payload), 201)
        elif path.startswith("/api/memories/"):
            memory_id = path.removeprefix("/api/memories/")
            if payload.get("delete") is True:
                store.delete_memory(memory_id)
                self.reply({"deleted": True})
            else:
                self.reply(store.save_memory(payload, memory_id))
        else:
            self.reply({"error": "这里没有可执行的操作。"}, 404)

    def journey_progress(self, payload=None):
        store = self.server.store
        settings = self.server.settings
        config = settings.current()
        external = not settings.demo and config["provider"] != "ollama"
        current_date = today()
        sources = progress_sources(store, current_date)
        threads = None
        automatic = False
        if payload is None and sources:
            threads = cached_threads(store, sources)
            automatic = threads is None and not external and (
                settings.demo or settings.to_model(config).status()["mode"] == "ready"
            )
        elif payload is not None:
            requested_automatic = payload.get("automatic") is True
            if requested_automatic and external:
                raise InvalidInput("需要点击「用 API 整理」才能发送近期摘录。")
            if sources:
                model = settings.to_model(config)
                threads = organize_progress(store, model, sources, requested_automatic)
        self.reply({
            "today": current_date.isoformat(), "has_entries": bool(sources),
            "threads": threads, "external": external, "automatic": automatic,
        })


def start(args, directory):
    store = JournalStore(directory)
    settings = ModelSettings(directory, args.model, args.demo)
    if args.demo:
        seed_demo(store, today())
        seed_demo_progress(store, today())
    try:
        server = JournalServer(args.port, store, settings)
    except PermissionError as exc:
        raise SystemExit("当前环境不能监听本机端口。请从 Mac 终端运行心迹。") from exc
    except OSError as exc:
        raise SystemExit("端口不可用。请关闭已运行的心迹，或用 --port 指定另一个端口。") from exc
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"{'心迹（示例模式）' if args.demo else APP_NAME}  {url}", flush=True)
    print(f"{'虚构日记临时目录' if args.demo else '本地日记目录'}：{directory}", flush=True)
    print("按 Control+C 停止。", flush=True)
    if args.open:
        threading.Timer(0.35, webbrowser.open, [url]).start()
    with server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n已关闭心迹。")


def main():
    parser = argparse.ArgumentParser(description="心迹 · 本地日记助手")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=default_data())
    parser.add_argument("--model", default="qwen3:4b", help="未保存模型设置时使用的本地 Ollama 模型")
    parser.add_argument("--open", action="store_true", help="打开浏览器")
    parser.add_argument("--demo", action="store_true", help="在隔离的临时目录演示虚构日记")
    args = parser.parse_args()
    if args.demo:
        with tempfile.TemporaryDirectory(prefix="journal-demo-") as directory:
            start(args, Path(directory))
    else:
        start(args, args.data_dir.expanduser().resolve())


if __name__ == "__main__":
    main()
