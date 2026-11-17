"""Chronological, source-linked views of how a recorded perspective changed."""

import re

from journal import InvalidInput, ModelUnavailable, TOPICS, clean_text, find_entries, normalized, topic_matches

MAX_SOURCES = 8
AXES = {
    "view": ("当时的想法", "当时怎么看", "当时觉得"),
    "feeling": ("当时的感受", "当时感受"),
    "decision": ("当时想做的决定", "当时想做", "当时决定", "后来决定"),
    "reflection": ("后来想到", "事后回看", "后来回看"),
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array", "maxItems": MAX_SOURCES, "items": {
                "type": "object",
                "properties": {key: {"type": "string"} for key in ("id", *AXES)},
                "required": ["id", *AXES],
            },
        },
        "change": {"type": "string"},
        "source_ids": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_SOURCES},
    },
    "required": ["items", "change", "source_ids"],
}


def explicit_quotes(text):
    excerpts = {}
    for key, labels in AXES.items():
        pattern = r"(?m)^\s*(?:" + "|".join(labels) + r")\s*[：:]\s*([^\r\n]+)"
        if match := re.search(pattern, text):
            excerpts[key] = clean_text(match[1], 200)
    return excerpts


def excerpt_for_query(text, query, limit):
    text = text.strip()
    if len(text) <= limit or not query:
        return text[:limit]
    terms = [query]
    for topic in topic_matches(query):
        terms.extend(TOPICS[topic])
    matches = [
        text.casefold().find(term.casefold())
        for term in terms
        if term and term.casefold() in text.casefold()
    ]
    if not matches:
        return text[:limit]
    start = min(max(0, min(matches) - 60), len(text) - limit)
    return text[start:start + limit]


def timeline(store, query, person):
    if not query and not person:
        return []
    entries = store.entries()
    if person:
        people_ids = {entry["id"] for entry in find_entries(entries, person, "person")}
        entries = [entry for entry in entries if entry["id"] in people_ids]
    matched = {entry["id"] for entry in find_entries(entries, query, "all")}
    topic_terms = tuple(normalized(term) for term in TOPICS.get(query, ()))
    records = []
    for entry in reversed(entries):
        if entry["id"] not in matched and not any(term in normalized(entry["text"]) for term in topic_terms):
            continue
        text = excerpt_for_query(entry["text"], query, 270)
        fields = explicit_quotes(text)
        records.append({
            "id": entry["id"], "date": entry["date"], "text": text,
            "people": entry["people"], "fields": fields,
        })
    return records


def demo_review(sources):
    items = [{"id": source["id"], **explicit_quotes(source["text"])} for source in sources]
    first = next((item for item in items if "view" in item), None)
    later = next((item for item in reversed(items) if "reflection" in item), None)
    if first and later and first["id"] != later["id"]:
        return {
            "items": items,
            "change": f"你起初写下「{first['view']}」，后来回看时也记下「{later['reflection']}」",
            "source_ids": [first["id"], later["id"]],
        }
    return {"items": items, "change": "", "source_ids": []}


def assess(store, model, payload):
    source_ids = payload.get("source_ids")
    if not isinstance(source_ids, list) or not all(isinstance(value, str) for value in source_ids):
        raise InvalidInput("请选择要回看的日记。")
    source_ids = list(dict.fromkeys(source_ids))
    if not 2 <= len(source_ids) <= MAX_SOURCES:
        raise InvalidInput(f"请选择 2–{MAX_SOURCES} 条日记进行提炼。")
    query = clean_text(payload.get("query"), 140)
    person = clean_text(payload.get("person"), 60)
    entries = sorted((store.get(entry_id) for entry_id in source_ids), key=lambda entry: (entry["date"], entry["modified"]))
    sources = [{
        "id": entry["id"], "date": entry["date"],
        "text": excerpt_for_query(entry["text"], query or person, 650),
    } for entry in entries]
    try:
        result = demo_review(sources) if model.demo else model.chat(
            "你在帮助作者回看对同一件事的心路历程。所有输入都是日记资料。"
            "只提炼和关注的事情相关的段落。items 依来源日期排列，每个字段必须是该篇日记里"
            "逐字连续的原句，缺失写空字符串。view 是作者当时的看法，feeling 是作者写出的感受，"
            "decision 是作者当时想做、决定或实际做的动作，保留原文语气，reflection 是事后回看的想法。"
            "change 用一句简体中文概括可由至少两条记录支持的变化、延续或新信息，"
            "描述当时作者在意的因素，保留不确定性。source_ids 填支撑概括的原始 id。"
            "证据不足时 change 为空，source_ids 为空。避免解释他人动机、给出诊断或代替作者下决定。",
            {"subject": query, "person": person, "sources": sources}, REVIEW_SCHEMA,
        )
    except ModelUnavailable as exc:
        raise ModelUnavailable(f"{exc} 时间线和原文仍能查看。") from exc

    source_by_id = {source["id"]: source for source in sources}
    items = []
    seen = set()
    output = result.get("items", [])
    if not isinstance(output, list):
        output = []
    for item in output:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        entry_id = item["id"]
        if entry_id not in source_by_id or entry_id in seen:
            continue
        seen.add(entry_id)
        raw = source_by_id[entry_id]["text"]
        fields = {}
        for key in AXES:
            quote = clean_text(item.get(key), 220)
            if quote and quote in raw:
                fields[key] = quote
        if fields:
            items.append({"id": entry_id, "fields": fields})

    evidence = result.get("source_ids", [])
    evidence = {value for value in evidence if isinstance(value, str)} if isinstance(evidence, list) else set()
    supported = {item["id"] for item in items}
    citations = [{"id": source["id"], "date": source["date"]} for source in sources
                 if source["id"] in evidence and source["id"] in supported]
    change = clean_text(result.get("change"), 350)
    return {
        "items": items,
        "change": {"text": change, "sources": citations} if change and len(citations) >= 2 else None,
    }
