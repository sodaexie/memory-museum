"""Build keyword associations from the local journal's recorded labels."""

from collections import defaultdict
from itertools import combinations

from journal import entry_summary, entry_topics, normalized

MAX_VISIBLE_NODES = 24


def keyword_group(people, topics, tags):
    topics = [topic for topic in topics if topic != "其他"]
    topic_labels = {normalized(topic) for topic in topics}
    keywords = {}
    for kind, labels in (
        ("person", people),
        ("topic", topics),
        ("tag", [tag for tag in tags if normalized(tag) not in topic_labels]),
    ):
        for label in labels:
            key = normalized(label)
            if key:
                keywords.setdefault(f"{kind}:{key}", (kind, label))
    return keywords


def entry_groups(entry):
    events = entry["events"]
    if events and not (entry["manual_people"] or entry["manual_topics"]):
        return [
            keyword_group(event["people"], [event["topic"]], event["tags"])
            for event in events
        ]
    return [keyword_group(entry["people"], sorted(entry_topics(entry)), entry["tags"])]


def build_graph(entries, query=""):
    keywords = {}
    node_records = defaultdict(dict)
    edge_records = defaultdict(dict)
    for entry in entries:
        entry_id = entry["id"]
        for group in entry_groups(entry):
            for key, label in group.items():
                keywords.setdefault(key, label)
                node_records[key][entry_id] = None
            for pair in combinations(sorted(group), 2):
                edge_records[pair][entry_id] = None

    def rank(key):
        kind, label = keywords[key]
        return -len(node_records[key]), ("person", "topic", "tag").index(kind), normalized(label)

    needle = normalized(query)
    ranked = sorted(keywords, key=rank)
    matches = [key for key in ranked if needle and needle in normalized(keywords[key][1])]
    matches.sort(key=lambda key: normalized(keywords[key][1]) != needle)
    if needle:
        neighbors = {}
        match_keys = set(matches)
        for (left, right), record_ids in edge_records.items():
            if left in match_keys and right not in match_keys:
                neighbors[right] = max(neighbors.get(right, 0), len(record_ids))
            if right in match_keys and left not in match_keys:
                neighbors[left] = max(neighbors.get(left, 0), len(record_ids))
        ranked = matches + sorted(neighbors, key=lambda key: (-neighbors[key], rank(key)))

    keys = ranked[:MAX_VISIBLE_NODES]
    visible = set(keys)
    nodes = [
        {
            "id": key, "kind": keywords[key][0], "label": keywords[key][1],
            "count": len(node_records[key]), "entry_ids": list(node_records[key]),
        }
        for key in keys
    ]
    edges = [
        {"from": left, "to": right, "count": len(record_ids), "entry_ids": list(record_ids)}
        for (left, right), record_ids in edge_records.items()
        if left in visible and right in visible
    ]
    referenced = {entry_id for node in nodes for entry_id in node["entry_ids"]}
    return {
        "nodes": nodes, "edges": edges,
        "entries": {entry["id"]: entry_summary(entry) for entry in entries if entry["id"] in referenced},
        "focus": matches[0] if matches else None,
        "total": len(ranked),
    }
