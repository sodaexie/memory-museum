"use strict";

const graphView = {
  query: null, data: null, selected: null, positions: new Map(), buttons: new Map(),
  requestId: 0, stale: false,
};
const nodeKinds = { person: "人物", topic: "主题", tag: "标签" };

function graphHash(query = "") {
  return query ? "#graph?" + new URLSearchParams({ q: query }) : "#graph";
}

function connectToJourney(nodes) {
  const person = nodes.find((node) => node.kind === "person")?.label ?? "";
  const keyword = nodes.find((node) => node.kind !== "person")?.label ?? "";
  return journeyHash(keyword, person);
}

function selectGraph(selection) {
  graphView.selected = selection;
  drawGraph();
  renderGraphDetail();
}

function renderGraphDetail() {
  const panel = byId("graph-detail");
  const data = graphView.data;
  const selection = graphView.selected;
  panel.replaceChildren();
  if (!data?.nodes.length) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  if (!selection) {
    panel.append(
      element("h2", "", "查看联系"),
      element("p", "muted reflection-copy", "选择图上的节点或线条，查看它们出现在哪些日记里。"),
    );
    return;
  }
  const nodesById = new Map(data.nodes.map((node) => [node.id, node]));
  const selected = selection.kind === "node" ? nodesById.get(selection.id) : selection.edge;
  const nodes = selection.kind === "node" ? [selected] : [nodesById.get(selected.from), nodesById.get(selected.to)];
  if (nodes.some((node) => !node)) return;

  panel.append(element("h2", "", nodes.map((node) => node.label).join(" ↔ ")));
  const description = selection.kind === "node"
    ? `${nodeKinds[nodes[0].kind]} · ${selected.count} 条日记`
    : `共同出现在 ${selected.count} 条日记中`;
  panel.append(
    element("p", "muted reflection-copy", description),
    link("回看心路历程 →", connectToJourney(nodes), "quiet-link graph-journey-link"),
  );

  if (selection.kind === "node") {
    const neighbors = data.edges
      .filter((edge) => edge.from === selected.id || edge.to === selected.id)
      .sort((left, right) => right.count - left.count);
    if (neighbors.length) {
      const links = element("div", "graph-neighbors");
      for (const edge of neighbors) {
        const neighbor = nodesById.get(edge.from === selected.id ? edge.to : edge.from);
        links.append(action(
          `${neighbor.label} · ${edge.count} 条`,
          () => selectGraph({ kind: "edge", edge }),
          `graph-type graph-neighbor ${neighbor.kind}`,
        ));
      }
      panel.append(element("h3", "graph-section-label", "直接关联"), links);
    }
  } else {
    const endpoints = element("div", "graph-neighbors");
    for (const node of nodes) {
      endpoints.append(action(
        `查看${node.label}`, () => selectGraph({ kind: "node", id: node.id }),
        `graph-type graph-neighbor ${node.kind}`,
      ));
    }
    panel.append(endpoints);
  }

  const sources = element("div", "graph-sources");
  for (const id of selected.entry_ids) {
    const record = data.entries[id];
    if (!record) continue;
    const item = link("", mainEntryLink(id, graphHash(graphView.query)), "graph-source");
    item.append(
      element("time", "graph-source-date", dateLabel(record.date, { year: "numeric", month: "short", day: "numeric" })),
      element("p", "", record.summary),
    );
    item.firstChild.dateTime = record.date;
    sources.append(item);
  }
  panel.append(element("h3", "graph-section-label", "日记出处"), sources);
}

function graphNodeButton(node) {
  const button = action("", () => selectGraph({ kind: "node", id: node.id }), `graph-node ${node.kind}`);
  button.title = `${node.label} · ${nodeKinds[node.kind]} · ${node.count} 条日记`;
  button.setAttribute("aria-label", button.title);
  button.append(element("span", "graph-node-mark"), element("span", "graph-node-label", node.label));
  button.firstChild.setAttribute("aria-hidden", "true");
  graphView.buttons.set(node.id, button);
  return button;
}

function graphLayout() {
  const container = byId("graph-surface");
  const width = container.clientWidth;
  const height = container.clientHeight;
  const graph = graphView.data;
  if (!graph?.nodes.length || !width || !height) return;
  const compact = width < 450;
  const centerX = width / 2;
  const centerY = height / 2;
  const bounds = (point) => {
    const inset = point.halfWidth + 14;
    point.x = Math.max(inset, Math.min(width - inset, point.x));
    point.y = Math.max(25, Math.min(height - 25, point.y));
  };

  const points = graph.nodes.map((node, index) => {
    const angle = index * 2.39996 - Math.PI / 2;
    const ratio = graph.nodes.length === 1 ? 0 : Math.sqrt((index + 0.2) / graph.nodes.length);
    return {
      id: node.id,
      x: centerX + Math.cos(angle) * ratio * width * 0.39,
      y: centerY + Math.sin(angle) * ratio * height * 0.39,
      halfWidth: graphView.buttons.get(node.id).offsetWidth / 2,
    };
  });
  const positions = new Map(points.map((point) => [point.id, point]));
  const connections = graph.edges.map((edge) => [positions.get(edge.from), positions.get(edge.to), edge.count]);

  for (let step = 0; step < 110; step++) {
    const cooling = 0.7 * (1 - step / 130);
    for (const point of points) {
      point.dx = (centerX - point.x) * 0.018;
      point.dy = (centerY - point.y) * 0.018;
    }
    for (const [left, right, count] of connections) {
      const dx = right.x - left.x;
      const dy = right.y - left.y;
      const distance = Math.hypot(dx, dy) || 1;
      const ideal = Math.max(left.halfWidth + right.halfWidth + 24, compact ? 82 : 122);
      const pull = (distance - ideal) * (0.018 + Math.min(count, 3) * 0.004) / distance;
      left.dx += dx * pull;
      left.dy += dy * pull;
      right.dx -= dx * pull;
      right.dy -= dy * pull;
    }
    for (let index = 0; index < points.length; index++) {
      for (let next = index + 1; next < points.length; next++) {
        const left = points[index];
        const right = points[next];
        const dx = right.x - left.x || 0.01;
        const dy = right.y - left.y || 0.01;
        const distance = Math.max(1, Math.hypot(dx, dy));
        const repulsion = (compact ? 460 : 900) / (distance * distance);
        const horizontalOverlap = left.halfWidth + right.halfWidth + 11 - Math.abs(dx);
        const verticalOverlap = 36 - Math.abs(dy);
        let pushX = dx * repulsion;
        let pushY = dy * repulsion;
        if (horizontalOverlap > 0 && verticalOverlap > 0) {
          if (horizontalOverlap < verticalOverlap) pushX += Math.sign(dx) * horizontalOverlap * 0.37;
          else pushY += Math.sign(dy) * verticalOverlap * 0.37;
        }
        left.dx -= pushX;
        left.dy -= pushY;
        right.dx += pushX;
        right.dy += pushY;
      }
    }
    for (const point of points) {
      point.x += Math.max(-15, Math.min(15, point.dx)) * cooling;
      point.y += Math.max(-15, Math.min(15, point.dy)) * cooling;
      bounds(point);
    }
  }
  graphView.positions = positions;
  for (const [id, point] of positions) {
    const button = graphView.buttons.get(id);
    button.style.left = `${point.x}px`;
    button.style.top = `${point.y}px`;
  }
  const canvas = byId("graph-lines");
  const ratio = Math.min(window.devicePixelRatio || 1, 3);
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  canvas.getContext("2d").setTransform(ratio, 0, 0, ratio, 0, 0);
  drawGraph();
}

function edgeIsRelevant(edge, selection) {
  if (selection.kind === "edge") return edge.from === selection.edge.from && edge.to === selection.edge.to;
  return edge.from === selection.id || edge.to === selection.id;
}

function drawGraph() {
  const data = graphView.data;
  if (!data?.nodes.length || !graphView.positions.size) return;
  const canvas = byId("graph-lines");
  const context = canvas.getContext("2d");
  const style = getComputedStyle(canvas);
  const activeColor = style.getPropertyValue("--line-active").trim();
  const mutedColor = style.getPropertyValue("--line-muted").trim();
  const defaultColor = style.getPropertyValue("--line-default").trim();
  context.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  const selected = graphView.selected;
  const emphasized = new Set();
  for (const edge of data.edges) {
    if (!selected || edgeIsRelevant(edge, selected)) {
      emphasized.add(edge.from);
      emphasized.add(edge.to);
    }
  }
  if (selected?.kind === "node") emphasized.add(selected.id);
  const orderedEdges = selected
    ? [...data.edges].sort((left, right) => Number(edgeIsRelevant(left, selected)) - Number(edgeIsRelevant(right, selected)))
    : data.edges;
  for (const edge of orderedEdges) {
    const left = graphView.positions.get(edge.from);
    const right = graphView.positions.get(edge.to);
    if (!left || !right) continue;
    const active = selected && edgeIsRelevant(edge, selected);
    context.strokeStyle = active ? activeColor : selected ? mutedColor : defaultColor;
    context.lineWidth = (active ? 1.6 : 0.9) + Math.min(2, Math.max(0, edge.count - 1)) * 0.42;
    context.beginPath();
    context.moveTo(left.x, left.y);
    context.lineTo(right.x, right.y);
    context.stroke();
  }
  for (const [id, button] of graphView.buttons) {
    const active = selected?.kind === "node" && selected.id === id;
    button.classList.toggle("is-muted", Boolean(selected) && !emphasized.has(id));
    button.classList.toggle("is-active", Boolean(active));
    button.setAttribute("aria-pressed", String(Boolean(active)));
  }
}

function selectGraphLine(event) {
  if (!graphView.data) return;
  const rect = event.currentTarget.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  let closest = null;
  let nearest = 9;
  for (const edge of graphView.data.edges) {
    const left = graphView.positions.get(edge.from);
    const right = graphView.positions.get(edge.to);
    if (!left || !right) continue;
    const dx = right.x - left.x;
    const dy = right.y - left.y;
    const part = Math.max(0, Math.min(1, ((x - left.x) * dx + (y - left.y) * dy) / (dx * dx + dy * dy || 1)));
    const distance = Math.hypot(x - left.x - part * dx, y - left.y - part * dy);
    if (distance < nearest) {
      nearest = distance;
      closest = edge;
    }
  }
  selectGraph(closest ? { kind: "edge", edge: closest } : null);
}

async function loadGraph(query = "") {
  byId("graph-query").value = query;
  if (graphView.query === query && graphView.data && !graphView.stale) {
    graphLayout();
    return;
  }
  const requestId = ++graphView.requestId;
  const empty = byId("graph-empty");
  const surface = byId("graph-surface");
  empty.textContent = "正在整理关键词之间的联系…";
  empty.hidden = false;
  surface.hidden = true;
  byId("graph-detail").hidden = true;
  const data = await request("/api/graph?" + new URLSearchParams({ q: query }));
  if (requestId !== graphView.requestId) return;
  graphView.query = query;
  graphView.data = data;
  graphView.stale = false;
  graphView.positions = new Map();
  graphView.buttons = new Map();
  graphView.selected = data.focus ? { kind: "node", id: data.focus } : null;
  byId("graph-nodes").replaceChildren(...data.nodes.map(graphNodeButton));
  byId("graph-count").textContent = data.total > data.nodes.length
    ? `显示 ${data.nodes.length} / ${data.total} 个节点 · 可搜索其余关键词`
    : `${data.nodes.length} 个节点 · ${data.edges.length} 条联系`;
  empty.textContent = query
    ? "没有这个关键词。试试日记里的人名、主题或标签。"
    : "写下或标记一则日记后，这里就会开始长出关联。";
  empty.hidden = Boolean(data.nodes.length);
  surface.hidden = !data.nodes.length;
  if (data.nodes.length) graphLayout();
  renderGraphDetail();
}

function initGraph() {
  byId("graph-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const query = byId("graph-query").value.trim();
    const hash = graphHash(query);
    if (location.hash === hash) loadGraph(query).catch(displayError);
    else location.hash = hash;
  });
  byId("graph-lines").addEventListener("click", selectGraphLine);
  new ResizeObserver(graphLayout).observe(byId("graph-surface"));
}
