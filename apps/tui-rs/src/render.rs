//! 渲染层(ratatui immediate-mode)。
//!
//! z-order:base panels(flow/stack/control,底层)→ overlays(预留)→ modal popups(栈顶最上)。
//! 弹窗用 Clear 擦该区域背景再画(Clear 遮罩);弹窗 Rect 支持 centered / absolute / offset。
//!
//! 保留 P1 的 draw_flow / draw_stack / draw_control / observe_lanes(真数据 lane),
//! 仅把外层 draw() 改成分层调度 + 弹窗栈叠加渲染。

use crate::components;
use crate::state::{fmt_val, trunc, App, FocusTarget, NewKind, ObserveEvent, Panel, TrackedFlow};
use crate::theme::DARK;
use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, List, ListItem, Paragraph},
    Frame,
};

/// 功能分区:ADR-5 色块 + 顶部描边(去左/右/下全边框省空间)。cyan bold 标题 + 顶线 + bg 色块。
fn region_block(_title: &'static str) -> Block<'static> {
    // title 去掉(分区标题难堪);保参数避免改 9 处调用。仅留顶线 + bg_surface 卡片层。
    Block::default()
        .borders(Borders::TOP)
        .border_style(Style::default().fg(DARK.border_accent))
        .style(Style::default().bg(DARK.bg_surface))
}

// ═══ mock flow 演示数据(P1 保留)═══════════════════════════════════

struct FNode {
    kind: &'static str,
    label: &'static str,
}
struct FTurn {
    nodes: Vec<FNode>,
    branches: Vec<FTurn>,
}
fn demo() -> FTurn {
    FTurn {
        nodes: vec![
            FNode { kind: "START", label: "build the feature" },
            FNode { kind: "TOOL", label: "plan" },
            FNode { kind: "RESULT", label: "files written" },
            FNode { kind: "TOOL", label: "test" },
            FNode { kind: "DELTA", label: "all tests pass" },
            FNode { kind: "DONE", label: "ok" },
        ],
        branches: vec![
            FTurn { nodes: vec![
                FNode{kind:"START",label:"plan"}, FNode{kind:"TOOL",label:"analyze"},
                FNode{kind:"RESULT",label:"scope"}, FNode{kind:"DONE",label:"ok"}], branches: vec![] },
            FTurn { nodes: vec![
                FNode{kind:"START",label:"edit"}, FNode{kind:"TOOL",label:"write"},
                FNode{kind:"RESULT",label:"diff"}, FNode{kind:"DONE",label:"ok"}], branches: vec![] },
        ],
    }
}

fn fnode_span(n: &FNode) -> Span<'static> {
    let (sym, color, bold) = match n.kind {
        "START" => ("● ", Color::Green, true),
        "TOOL" => ("⚒ ", Color::Blue, false),
        "RESULT" => ("◷ ", Color::Cyan, false),
        "DELTA" => ("δ ", Color::DarkGray, false),
        "DONE" => ("✓ ", Color::Magenta, true),
        _ => ("", Color::White, false),
    };
    let mut st = Style::default().fg(color);
    if bold {
        st = st.add_modifier(Modifier::BOLD);
    }
    Span::styled(format!("{}{}", sym, trunc(n.label, 16)), st)
}

fn flow_lines(t: &FTurn, depth: usize) -> Vec<Line<'static>> {
    let indent = "  ".repeat(depth);
    let mut spans: Vec<Span> = vec![Span::raw(indent.clone())];
    for (i, n) in t.nodes.iter().enumerate() {
        if i > 0 {
            spans.push(Span::raw("───").style(Style::default().fg(Color::DarkGray)));
        }
        spans.push(fnode_span(n));
    }
    let mut lines = vec![Line::from(spans)];
    for (j, b) in t.branches.iter().enumerate() {
        let conn = if j < t.branches.len() - 1 { "├─" } else { "└─" };
        let mut sub = flow_lines(b, depth + 1);
        let first = sub.remove(0);
        let mut head = vec![
            Span::raw(format!("{}   ", indent)),
            Span::raw(conn).style(Style::default().fg(Color::DarkGray)),
        ];
        head.extend(first.spans.iter().cloned());
        lines.push(Line::from(head));
        lines.extend(sub);
    }
    lines
}

// ═══ observe 真数据 lane(P1 保留 + P2 多实例)═══════════════════════

/// 把单个事件渲染成 (符号, 色, 加粗, 正文)。符号用 owned String 避开 event_type 生命周期。
fn event_glyph(e: &ObserveEvent) -> (String, Color, bool, String) {
    match e.event_type.as_str() {
        "tick_started" => ("●".to_string(), DARK.success, true, fmt_val(&e.data, "request")),
        "tool_call" => ("⚒".to_string(), DARK.accent2, false, fmt_val(&e.data, "tool_name")),
        "tool_result" => ("◷".to_string(), DARK.accent, false, fmt_val(&e.data, "result")),
        "token_delta" => ("δ".to_string(), DARK.fg_muted, false, fmt_val(&e.data, "delta_text")),
        "tick_completed" => {
            // flow_* 事件(flow.py wire 成 tick_completed,data.response 空在 flow_event/flow_payload)。
            let flow_ev = fmt_val(&e.data, "flow_event");
            // Part4:memory/orchestrate 事件(Part2/3 wire 成 tick_completed,字符串 payload)。
            // ponytail: 内联分支,第 3 类型化事件再抽通用 typed_event_body。
            if !flow_ev.is_empty() {
                ("✓".to_string(), DARK.done, true, flow_event_body(&flow_ev, &e.data))
            } else if !fmt_val(&e.data, "memory_event").is_empty() {
                ("✓".to_string(), DARK.done, true, fmt_val(&e.data, "memory_event"))
            } else if !fmt_val(&e.data, "orch_event").is_empty() {
                ("✓".to_string(), DARK.done, true, fmt_val(&e.data, "orch_event"))
            } else {
                ("✓".to_string(), DARK.done, true, fmt_val(&e.data, "response"))
            }
        }
        other => (other.to_string(), DARK.fg_muted, false, String::new()),
    }
}

/// flow_* 事件 body:从 data.flow_payload 取 node_id/response/状态(节点输出等内容)。
fn flow_event_body(flow_ev: &str, data: &std::collections::HashMap<String, serde_json::Value>) -> String {
    let p = data.get("flow_payload");
    let ps = |k: &str| p.and_then(|v| v.get(k)).and_then(|v| v.as_str()).unwrap_or("");
    match flow_ev {
        "node_completed" => {
            let nid = ps("node_id");
            let resp = ps("response");
            if resp.is_empty() { format!("{} ✓", nid) } else { format!("{}: {}", nid, resp) }
        }
        "node_started" => format!("→ {}", ps("node_id")),
        "flow_started" => "flow ▶".to_string(),
        "flow_completed" => format!("flow {}", ps("status")),
        _ => flow_ev.to_string(),
    }
}

/// 一组同 tick_id 的事件 → 一行横向 span(turn 节点 ─── 节点)。owned('static)。
fn tick_line(grp: &[&ObserveEvent]) -> Line<'static> {
    let mut spans: Vec<Span<'static>> = vec![];
    for (i, e) in grp.iter().enumerate() {
        if i > 0 {
            spans.push(Span::raw("───").style(Style::default().fg(Color::DarkGray)));
        }
        let (sym, color, bold, body) = event_glyph(e);
        let mut st = Style::default().fg(color);
        if bold {
            st = st.add_modifier(Modifier::BOLD);
        }
        let label = if body.is_empty() { sym } else { format!("{} {}", sym, trunc(&body, 20)) };
        spans.push(Span::styled(label, st));
    }
    Line::from(spans)
}

/// 多实例 lane:先按 harness_id(实例)分组,每实例一 lane 纵向叠。
/// 同实例内再按 tick_id 分组(横向节点)。ADR-5:同 sid 多 harness_id = 多实例。
/// 单实例时退化为 P1 的纯 tick lane(无前缀)。
pub fn observe_lanes(evs: &[ObserveEvent]) -> Vec<Line<'static>> {
    // 1. 按 harness_id 分实例(保持首次出现顺序)。
    let mut inst_order: Vec<String> = vec![];
    let mut by_inst: std::collections::HashMap<String, Vec<&ObserveEvent>> = std::collections::HashMap::new();
    for e in evs {
        let key = if e.harness_id.is_empty() { "__nohid__".to_string() } else { e.harness_id.clone() };
        if !by_inst.contains_key(&key) {
            inst_order.push(key.clone());
        }
        by_inst.entry(key).or_default().push(e);
    }
    let multi = inst_order.len() > 1;
    // ponytail: 单实例直接走扁平 tick lane(与 P1 一致),多实例才叠 lane。
    if !multi {
        let refs: Vec<&ObserveEvent> = evs.iter().collect();
        return flat_tick_lanes(&refs);
    }
    // 2. 多实例:每实例一 lane,带实例标签前缀。
    let mut out: Vec<Line> = vec![];
    for (idx, hid) in inst_order.iter().enumerate() {
        let Some(inst_evs) = by_inst.get(hid) else { continue };
        let tag = trunc(&hid.replace("openclaw_", "oc_").replace("claude_", "cl_"), 18);
        let mut head_spans: Vec<Span> = vec![
            Span::styled(format!("[{}] {} ", idx + 1, tag), Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
            Span::raw("» ").style(Style::default().fg(Color::DarkGray)),
        ];
        // 该实例内按 tick_id 分组,横向串。
        let lanes = flat_tick_lanes(inst_evs);
        if let Some(first) = lanes.first() {
            head_spans.extend(first.spans.iter().cloned());
            out.push(Line::from(head_spans));
            for rest in lanes.iter().skip(1) {
                let mut cont = vec![Span::raw("      "), Span::raw("» ").style(Style::default().fg(Color::DarkGray))];
                cont.extend(rest.spans.iter().cloned());
                out.push(Line::from(cont));
            }
        } else {
            out.push(Line::from(head_spans));
        }
    }
    out
}

/// 扁平 tick lane(P1 行为):按 tick_id 分组,每组一行横向节点串。
/// 接 &[&ObserveEvent] 以同时服务顶层(全量)与多实例子集(借用)。
fn flat_tick_lanes(evs: &[&ObserveEvent]) -> Vec<Line<'static>> {
    let mut order: Vec<String> = vec![];
    let mut groups: std::collections::HashMap<String, Vec<&ObserveEvent>> = std::collections::HashMap::new();
    for e in evs {
        let key = if e.tick_id.is_empty() { format!("__noid_{}", order.len()) } else { e.tick_id.clone() };
        if !groups.contains_key(&key) {
            order.push(key.clone());
        }
        groups.entry(key).or_default().push(e);
    }
    let mut out: Vec<Line> = vec![];
    for k in &order {
        let Some(grp) = groups.get(k) else { continue };
        out.push(tick_line(&grp));
    }
    out
}

// ═══ base panels(P1 draw_* 保留)══════════════════════════════════

pub fn draw_flow(f: &mut Frame, area: Rect, app: &App) {
    let mut lines: Vec<Line> = vec![
        Line::from(Span::styled(
            " flow · 编排 DAG(turn 链 / 分支 / DAG on trigger_turn)".to_string(),
            Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD),
        )),
        Line::raw(""),
    ];

    if app.flows.is_empty() {
        // 无 flow:create prompt(control mode 预设 + DSL)。
        lines.push(Line::from(Span::styled(
            " (无 flow · 在 control/flow panel 按 f/g/D 创建预设 · R 运行)".to_string(),
            Style::default().fg(Color::DarkGray),
        )));
        lines.push(Line::raw(""));
        lines.push(Line::from(Span::styled(
            " 预设: f 链 A→B  ·  G 分支 A→B if cond else C  ·  D DAG A,C→B(合并)".to_string(),
            Style::default().fg(Color::Cyan),
        )));
        lines.push(Line::from(Span::styled(
            " DSL(POST /h/flows):{nodes:[{id,harness,message}],edges:[{from,to,condition?}]}".to_string(),
            Style::default().fg(Color::DarkGray),
        )));
        lines.push(Line::raw(""));
        lines.push(Line::from(Span::styled(
            " flow tab 显节点 DAG:创建 flow 后显示节点 box + 运行状态/输出明细".to_string(),
            Style::default().fg(Color::DarkGray),
        )));
        f.render_widget(Paragraph::new(lines), area);
        return;
    }

    // flow 选择条 + 当前 flow DAG。
    let cur = app.flow_cursor;
    lines.push(flow_selector_line(app));
    lines.push(Line::raw(""));

    // ADR-2:键盘聚焦 FlowsFlow 时显示聚焦标记。
    if matches!(app.focus, FocusTarget::FlowsFlow(_)) {
        lines.push(Line::from(Span::styled(
            " ▶ 键盘聚焦 flow 列表(方向键 j/k 切 flow · Enter 无鼠标也能操作)".to_string(),
            Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD),
        )));
        lines.push(Line::raw(""));
    }

    if let Some(tf) = app.current_flow() {
        lines.extend(flow_dag_lines(tf, cur));
    }
    f.render_widget(Paragraph::new(lines), area);
}

/// ADR-O1/O3:Orchestrate tab fork 谱系树渲染。
/// observe GET /sessions 客户端建树(ADR-O2)→ anchor.rs AnchorGraph 画 parent→child Braille 连线
/// + 节点状态符号(●active/✓done/⠋running/○idle)+ agent_id label + 光标高亮 + ClickMap 选中。
pub fn draw_orchestrate(f: &mut Frame, area: Rect, app: &mut App) {
    use crate::components::anchor::AnchorGraph;
    use crate::state::NodeState;

    let mut lines: Vec<Line> = vec![
        Line::from(Span::styled(
            " orchestrate · fork 谱系树(observe lineage → client build tree)".to_string(),
            Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD),
        )),
        Line::raw(""),
    ];

    let order = app.fork_tree.flat_order();
    if order.is_empty() {
        lines.push(Line::from(Span::styled(
            " (无 fork session · 在 control tab 触发 fork 或等 observe :8002 上线后按 r 刷新)".to_string(),
            Style::default().fg(Color::DarkGray),
        )));
        lines.push(Line::raw(""));
        lines.push(Line::from(Span::styled(
            " 符号: ● active · ✓ done · ⠋ running · ○ idle   ·  j/k 跨层级 · Enter 看事件流".to_string(),
            Style::default().fg(Color::Cyan),
        )));
        f.render_widget(Paragraph::new(lines), area);
        return;
    }

    // header:节点数 + 光标投影。
    let sel = app.orch_selection.clone();
    let header = match &sel {
        Some(s) => format!(
            " [{}] 节点 · 选中 {} {} (agent: {} · parent: {})",
            order.len(),
            s.state.glyph(),
            trunc(&s.session_id, 24),
            if s.agent_id.is_empty() { "-" } else { &s.agent_id },
            s.parent.as_deref().map(|p| trunc(p, 16)).unwrap_or_else(|| "root".into()),
        ),
        None => format!(" [{}] 节点 · (无选中)", order.len()),
    };
    lines.push(Line::from(Span::styled(header, Style::default().fg(Color::Yellow))));
    lines.push(Line::raw(""));

    // ── AnchorGraph:parent→child Braille 连线(ADR-O3)──────────────────
    // 布局:DFS 序每节点一行(y),x = depth * step。世界坐标对齐行/列便于 Braille 连线。
    let step = 6.0_f64;
    let max_depth = order.iter()
        .map(|sid| app.fork_tree.depth(sid))
        .max().unwrap_or(0);
    let x_max = ((max_depth + 1) as f64) * step;
    let y_max = order.len() as f64;
    let mut graph = AnchorGraph::new([0.0, x_max], [0.0, y_max]);
    // id = DFS 序行号(1-based,Braille y 反转:画布 y 向上,用 y_max-row 倒置)。
    // anchor.rs 的 anchor/edge builder 消耗 self,这里直接 push 进 Vec 字段。
    use crate::components::anchor::{Anchor, Connection};
    for (row, sid) in order.iter().enumerate() {
        let depth = app.fork_tree.depth(sid);
        let x = (depth as f64) * step + 1.0;
        let y = y_max - (row as f64) - 0.5; // 倒置:第 0 行在顶
        graph.anchors.push(Anchor { id: (row + 1) as u32, x, y });
        if let Some(node) = app.fork_tree.nodes.get(sid) {
            if let Some(p) = &node.parent {
                if let Some(prow) = order.iter().position(|s| s == p.as_str()) {
                    graph.connections.push(Connection {
                        from: (prow + 1) as u32, to: (row + 1) as u32,
                        color: Color::DarkGray, label: None,
                    });
                }
            }
        }
    }

    // 画布区(连线)占上半,text 列表(符号+label+光标)占下半。
    let [canvas_area, list_area] = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length((order.len() as u16 + 2).min(area.height / 2 + 4)), Constraint::Min(0)])
        .areas(area);
    f.render_widget(graph.canvas(), canvas_area);

    // ── text 列表:状态符号 + agent_id label + 缩进 + 光标高亮 ──────────
    // ClickMap 注册每行 1xN rect(id 700+ = DFS idx),鼠标点击选中。
    // 不 clear:顶层 draw() 已 clear(保留 999/998 quit/info 持久注册)。
    let list_x = list_area.x;
    for (row, sid) in order.iter().enumerate() {
        let node = match app.fork_tree.nodes.get(sid) { Some(n) => n, None => continue };
        let depth = app.fork_tree.depth(sid);
        let indent = "  ".repeat(depth);
        let glyph = node.state.glyph();
        let glyph_color = match node.state {
            NodeState::Active => Color::Green,
            NodeState::Done => DARK.done,
            NodeState::Running => Color::Yellow,
            NodeState::Idle => Color::DarkGray,
        };
        let agent = if node.agent_id.is_empty() { "-".to_string() } else { node.agent_id.clone() };
        let is_cur = row == app.orch_cursor;
        let marker = if is_cur { "▶ " } else { "  " };
        let line = Line::from(vec![
            Span::raw(format!("{}{} ", marker, indent)),
            Span::styled(format!("{} ", glyph), Style::default().fg(glyph_color).add_modifier(Modifier::BOLD)),
            Span::styled(agent.clone(), Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)),
            Span::styled(format!("  {}", trunc(&node.session_id, 30)), Style::default().fg(if is_cur { Color::Yellow } else { Color::DarkGray })),
        ]);
        lines.push(line);
        // ClickMap:每行 register 1-cell 高 rect。
        let y = list_area.y + (lines.len() as u16).saturating_sub(1);
        app.clickmap.register(Rect::new(list_x, y, list_area.width, 1), 700 + row);
    }

    f.render_widget(Paragraph::new(lines), list_area);
}

/// flow 选择条:[cur+1/N] flow_xxxx · status · nodes M/edges K。
fn flow_selector_line(app: &App) -> Line<'static> {
    let n = app.flows.len();
    let cur = app.flow_cursor + 1;
    let mut spans: Vec<Span> = vec![
        Span::styled(format!(" [{}/{}] ", cur, n), Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
    ];
    if let Some(tf) = app.current_flow() {
        let st = tf.status.as_ref().map(|s| s.status.as_str()).unwrap_or("unknown");
        let st_color = match st {
            "running" => Color::Green,
            "completed" => Color::Magenta,
            "failed" => Color::Red,
            _ => Color::DarkGray,
        };
        spans.push(Span::styled(trunc(&tf.flow_id, 20), Style::default().fg(Color::Cyan)));
        spans.push(Span::raw(" · "));
        spans.push(Span::styled(st.to_string(), Style::default().fg(st_color).add_modifier(Modifier::BOLD)));
        spans.push(Span::styled(
            format!(" · {} nodes · {} edges", tf.def.nodes.len(), tf.def.edges.len()),
            Style::default().fg(Color::DarkGray),
        ));
    }
    spans.push(Span::styled("  [j/k 切 flow · R run]", Style::default().fg(Color::DarkGray)));
    Line::from(spans)
}

/// 把一个 TrackedFlow 渲染成 DAG 拓扑 + 每 node 状态。
/// 分层:BFS 从入度 0 节点起,每层一行横向;边标 ── 或条件(field op value)。
fn flow_dag_lines(tf: &TrackedFlow, _cur: usize) -> Vec<Line<'static>> {
    let def = &tf.def;
    let status = tf.status.as_ref();

    // BFS 分层:level[node] = max(level[predecessor]) + 1。
    let mut level: std::collections::HashMap<&str, usize> = std::collections::HashMap::new();
    // 入度
    let mut indeg: std::collections::HashMap<&str, usize> = def.nodes.iter().map(|n| (n.id.as_str(), 0)).collect();
    for e in &def.edges {
        *indeg.entry(e.to.as_str()).or_insert(0) += 1;
    }
    let mut queue: Vec<&str> = indeg.iter().filter(|(_, d)| **d == 0).map(|(k, _)| *k).collect();
    queue.sort();
    for &s in &queue {
        level.insert(s, 0);
    }
    // ponytail: 简化拓扑排序——反复扫描直到稳定(DAG 无环,flow.py 已有超时兜底)。
    loop {
        let mut progressed = false;
        for e in &def.edges {
            if let Some(&lf) = level.get(e.from.as_str()) {
                let entry = level.entry(e.to.as_str()).or_insert(0);
                if lf + 1 > *entry {
                    *entry = lf + 1;
                    progressed = true;
                }
            }
        }
        if !progressed {
            break;
        }
    }
    // 任何未被赋值的节点(孤立)放 level 0。
    for n in &def.nodes {
        level.entry(n.id.as_str()).or_insert(0);
    }
    let max_level = level.values().copied().max().unwrap_or(0);

    let mut out: Vec<Line> = vec![];
    for lvl in 0..=max_level {
        let mut layer_nodes: Vec<&crate::state::FlowNode> =
            def.nodes.iter().filter(|n| level.get(n.id.as_str()).copied() == Some(lvl)).collect();
        layer_nodes.sort_by_key(|n| n.id.as_str());
        if layer_nodes.is_empty() {
            continue;
        }
        // 每层一行横向 node box(─ 分隔);多 lane(DAG 并行)同一层并排。
        let mut spans: Vec<Span> = vec![Span::styled(format!(" L{} ", lvl), Style::default().fg(Color::DarkGray))];
        for (i, n) in layer_nodes.iter().enumerate() {
            if i > 0 {
                spans.push(Span::raw("   ").style(Style::default().fg(Color::DarkGray)));
            }
            spans.push(node_span(n, status));
        }
        out.push(Line::from(spans));

        // 边层:从本层 node 出发的边,列出 to + 条件标。
        for n in &layer_nodes {
            let outs: Vec<&crate::state::FlowEdge> = def.edges.iter().filter(|e| e.from == n.id).collect();
            if outs.is_empty() {
                continue;
            }
            let mut edge_spans: Vec<Span> = vec![
                Span::raw("     "),
                Span::styled(format!("  {} ", trunc(&n.id, 8)), Style::default().fg(Color::DarkGray)),
            ];
            for (i, e) in outs.iter().enumerate() {
                if i > 0 {
                    edge_spans.push(Span::raw("   ").style(Style::default().fg(Color::DarkGray)));
                }
                let cond = e.condition.as_ref().map(|c| format!(" if {} {} \"{}\"", c.field, c.op, trunc(&c.value, 12))).unwrap_or_default();
                edge_spans.push(Span::raw(format!("──▶{}{}", trunc(&e.to, 10), cond)).style(Style::default().fg(Color::Blue)));
            }
            out.push(Line::from(edge_spans));
        }
    }

    // node 状态明细表(从 GET /h/flows/{id} 的 status.nodes)。
    if let Some(st) = status {
        out.push(Line::raw(""));
        out.push(Line::from(Span::styled(
            format!(" node 状态(flow={}):", st.status),
            Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD),
        )));
        let mut ids: Vec<&String> = st.nodes.keys().collect();
        ids.sort();
        for id in ids {
            let ns = &st.nodes[id];
            let (g, gc) = node_status_glyph(&ns.status);
            out.push(Line::from(vec![
                Span::raw("   "),
                Span::styled(g, Style::default().fg(gc).add_modifier(Modifier::BOLD)),
                Span::styled(format!(" {:<8}", trunc(id, 8)), Style::default().fg(Color::White)),
                Span::styled(format!(" {:<10}", ns.status), Style::default().fg(gc)),
                Span::styled(format!("  {}", trunc(&ns.response, 40)), Style::default().fg(Color::DarkGray)),
            ]));
        }
    }
    out
}

/// node box:状态符号 + id + message 摘要。状态色来自 status.nodes(无 status = idle)。
fn node_span(n: &crate::state::FlowNode, status: Option<&crate::state::FlowStatus>) -> Span<'static> {
    let st_str = status.and_then(|s| s.nodes.get(&n.id)).map(|x| x.status.as_str()).unwrap_or("idle");
    let (glyph, color) = node_status_glyph(st_str);
    let label = format!("[{} {}] {}", glyph, trunc(&n.id, 6), trunc(&n.message, 18));
    Span::styled(label, Style::default().fg(color).add_modifier(Modifier::BOLD))
}

/// node 状态 → (符号 owned, 色)。pending=idle,running,done,failed,skipped。
fn node_status_glyph(status: &str) -> (String, Color) {
    match status {
        "running" => ("⠋".to_string(), DARK.success),
        "completed" => ("✓".to_string(), DARK.done),
        "failed" => ("✗".to_string(), DARK.error),
        "skipped" => ("⊘".to_string(), DARK.fg_muted),
        _ => ("○".to_string(), DARK.highlight), // pending / idle
    }
}

/// IT3 ④:Observe 改折叠树 + 查看面板 + 跳转按钮。
/// 上半 = 折叠树(组 header ▾/▸+名+count,点击 toggle;展开显 session 行,点击查看);
/// 下半 = 选中 session(observe_view_cursor)的事件流(ScrollView);
/// 跳转 Control 用独立按钮(id 400)→ jump_to_control(不再 session 点击自动跳)。
/// clickmap id 段:400=跳转按钮、500+组、600+session(flat idx)。
pub fn draw_stack(f: &mut Frame, area: Rect, app: &mut App) {
    app.observe_area = area;

    // 上下分屏:上半折叠树(固定够用的高度),下半选中 session 事件流。
    // ponytail: 上半按内容行数动态(组数+展开 session 数 +4 标题/提示),clamp 3..area 的 60%。
    let mut harnesses: Vec<String> = app.sessions.sessions_by_harness.keys().cloned().collect();
    harnesses.sort();
    let tree_rows = harnesses.len() // 组 header
        + harnesses.iter().map(|h| {
            if app.observe_collapsed.contains(h) { 0 }
            else { app.sessions.sessions_by_harness.get(h).map(|v| v.len()).unwrap_or(0) }
        }).sum::<usize>()
        + harnesses.len().max(1) // 组间空行
        + 4; // 标题/提示
    let top_h = (tree_rows as u16).clamp(4, (area.height as u16 * 6 / 10).max(4));
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(top_h), Constraint::Min(4), Constraint::Length(1)])
        .split(area);
    let tree_area = chunks[0];
    let view_area = chunks[1];
    let jump_area = chunks[2];

    // ── 上半:折叠树 ──
    let tree_block = region_block(" Observe · session 树(点组折叠 · 点 session 查看) ");
    let tree_inner = tree_block.inner(tree_area);
    f.render_widget(tree_block, tree_area);

    let mut tree_lines: Vec<Line> = vec![];
    // (row_in_tree_inner, flat_idx) 供 session 行 clickmap 注册;组 header clickmap 另记。
    let mut session_marks: Vec<(u16, usize)> = vec![];
    let mut group_marks: Vec<(u16, usize)> = vec![]; // (row, gi)
    let mut row: u16 = 0;
    let mut fi = 0usize;
    for (gi, hs) in harnesses.iter().enumerate() {
        if gi > 0 {
            tree_lines.push(Line::raw(""));
            row = row.saturating_add(1);
        }
        let n = app.sessions.sessions_by_harness.get(hs).map(|v| v.len()).unwrap_or(0);
        let collapsed = app.observe_collapsed.contains(hs);
        let arrow = if collapsed { "▸" } else { "▾" };
        let (tag, color) = crate::state::harness_tag(hs);
        group_marks.push((row, gi));
        tree_lines.push(Line::from(vec![
            Span::styled(format!(" {} ", tag), Style::default().fg(color).add_modifier(Modifier::BOLD)),
            Span::styled(format!("{} ", arrow), Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
            Span::styled(hs.clone(), Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD)),
            Span::styled(format!(" ({})", n), Style::default().fg(Color::DarkGray)),
        ]));
        row = row.saturating_add(1);
        if !collapsed {
            if let Some(ss) = app.sessions.sessions_by_harness.get(hs) {
                for s in ss {
                    let is_view = app.observe_view_cursor == Some(fi);
                    let inst = app.instance_count(&s.harness_type, &s.session_id);
                    let multi_tag = if inst >= 2 { format!(" ×{}", inst) } else { String::new() };
                    let st = if is_view {
                        Style::default().fg(Color::White).bg(Color::Blue).add_modifier(Modifier::BOLD)
                    } else {
                        Style::default().fg(Color::White)
                    };
                    session_marks.push((row, fi));
                    tree_lines.push(Line::from(vec![
                        Span::styled(format!("    ▸ {}", trunc(&s.session_id, 22)), st),
                        Span::styled(multi_tag, Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
                    ]));
                    row = row.saturating_add(1);
                    fi += 1;
                }
            }
        } else {
            fi += n; // 折叠:fi 前进保持 flat 对齐
        }
    }
    if app.flat.is_empty() {
        tree_lines.push(Line::from(Span::styled(
            " (无 session · r 刷新)".to_string(),
            Style::default().fg(Color::DarkGray),
        )));
    }
    f.render_widget(Paragraph::new(tree_lines), tree_inner);

    // clickmap:组 header(id 500+gi)、session 行(id 600+flat_idx)。
    for (r, gi) in &group_marks {
        let y = tree_inner.y + r;
        if y < tree_inner.y + tree_inner.height {
            app.clickmap.register(Rect::new(tree_inner.x, y, tree_inner.width, 1), 500 + gi);
        }
    }
    for (r, fidx) in &session_marks {
        let y = tree_inner.y + r;
        if y < tree_inner.y + tree_inner.height {
            app.clickmap.register(Rect::new(tree_inner.x, y, tree_inner.width, 1), 600 + fidx);
        }
    }

    // ── 下半:选中 session 事件流(observe_view_cursor 指向的 session)──
    let view_block = region_block(" Observe · 事件流(选中 session · 点击树中 session 查看) ");
    let view_inner = view_block.inner(view_area);
    f.render_widget(view_block, view_area);
    let view_lines: Vec<Line> = match app.observe_view_cursor.and_then(|i| app.flat.get(i)) {
        Some(s) => {
            let key = format!("{}/{}", s.harness_type, s.session_id);
            let mut out = vec![Line::from(vec![
                Span::styled(format!(" ▸ {} ", trunc(&s.session_id, 28)),
                    Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)),
                Span::styled(format!("({}) ", s.harness_type), Style::default().fg(Color::DarkGray)),
            ])];
            match app.events.get(&key) {
                Some(evs) => {
                    if evs.is_empty() {
                        out.push(Line::from(Span::styled(
                            "   (无事件)".to_string(),
                            Style::default().fg(Color::DarkGray),
                        )));
                    }
                    for e in evs {
                        let (tag, color, body) = event_log_parts(e);
                        out.push(Line::from(vec![
                            Span::styled(format!(" {} ", tag),
                                Style::default().fg(Color::Black).bg(color).add_modifier(Modifier::BOLD)),
                            Span::raw(format!(" {}", trunc(&body, 60))),
                        ]));
                    }
                }
                None => out.push(Line::from(Span::styled(
                    "   (observe 不可达 · r 刷新)".to_string(),
                    Style::default().fg(Color::DarkGray),
                ))),
            }
            out
        }
        None => vec![Line::from(Span::styled(
            " (未选 session · 上方树点击 session 查看其事件流)".to_string(),
            Style::default().fg(Color::DarkGray),
        ))],
    };
    app.observe_scroll.set_content(view_lines);
    // view_inner 已在 region_block 边框内,scroll 不再叠自己的边框(免双框)。
    app.observe_scroll.bordered = false;
    app.observe_scroll.render(f, view_inner);

    // ── 跳转 Control 按钮(id 400):整行宽,点击 → jump_to_control(view_cursor)──
    let has_view = app.observe_view_cursor.is_some();
    let style = if has_view {
        Style::default().fg(Color::Black).bg(Color::Green).add_modifier(Modifier::BOLD)
    } else {
        Style::default().fg(Color::DarkGray)
    };
    let label = if has_view { " →Control (跳转 · 查看选中 session 的 Control 面板) " } else { " →Control (先选 session) " };
    f.render_widget(Paragraph::new(label).style(style), jump_area);
    app.clickmap.register(jump_area, 400);
}

/// 事件 → (tag, color, body)用于卷轴日志行(ADR-3 Observe 卷轴)。复用 glyph 语义。
fn event_log_parts(e: &ObserveEvent) -> (String, Color, String) {
    use crate::state::fmt_val;
    match e.event_type.as_str() {
        "tick_started" => ("START".to_string(), DARK.success, fmt_val(&e.data, "request")),
        "tool_call" => ("TOOL▸".to_string(), DARK.accent2, fmt_val(&e.data, "tool_name")),
        "tool_result" => ("TOOL◂".to_string(), DARK.accent, fmt_val(&e.data, "result")),
        "tick_completed" => ("DONE ".to_string(), DARK.done, fmt_val(&e.data, "response")),
        "token_delta" => ("δ".to_string(), DARK.fg_muted, fmt_val(&e.data, "delta_text")),
        other => (other.to_string(), DARK.fg_muted, String::new()),
    }
}

pub fn draw_control(f: &mut Frame, area: Rect, app: &mut App) {
    use crate::components::control;
    use crate::state::harness_tag;

    // 窄大纲(色块组标签 + session 列)| 右主区(StatusBar + 右tab + body + 输入栏)。
    app.control_area = area; // 缓存供 mouse drag/hit。
    let [left, bar, right] = app.control_split.rects(area);
    // HSplit bar:单线分隔(border_accent 竖线,替代空白间隔;可拖拽调大纲宽)。
    for y in bar.y..bar.bottom() {
        if let Some(cell) = f.buffer_mut().cell_mut((bar.x, y)) {
            cell.set_char('│').set_style(Style::default().fg(DARK.border_accent));
        }
    }

    // ── 左区:region_block 边框内水平切 [色块列(2) | session 列] ──
    let left_block = region_block(" 大纲 ");
    let left_inner = left_block.inner(left);
    f.render_widget(left_block, left);
    let left_split = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Length(2), Constraint::Min(1)])
        .split(left_inner);
    let tag_col = left_split[0];
    let sess_col = left_split[1];

    let mut harnesses: Vec<String> = app.sessions.sessions_by_harness.keys().cloned().collect();
    harnesses.sort();
    app.control_groups = harnesses.clone();
    let cursor_group = app.flat.get(app.cursor).map(|s| s.harness_type.clone());

    // IT2 节点 C:大纲侧顶部 [+] new 按钮(clickmap id=300)。
    // 占 sess_col 第 0 行;sessions 列表渲染到下移 1 行的子区域(不重叠)。
    let new_rect = Rect::new(sess_col.x, sess_col.y, sess_col.width.min(10), 1);
    {
        let hovered = app.mouse.in_rect(new_rect);
        let style = if hovered {
            Style::default().fg(Color::Black).bg(Color::Green).add_modifier(Modifier::BOLD)
        } else if app.action_loading("new_btn") {
            Style::default().fg(Color::Black).bg(Color::Magenta).add_modifier(Modifier::BOLD)
        } else {
            Style::default().fg(Color::Green).add_modifier(Modifier::BOLD)
        };
        f.render_widget(
            Paragraph::new("[+] new").style(style),
            new_rect,
        );
        app.clickmap.register(new_rect, 300);
    }

    // IT3 ③:Control 左大纲改成真折叠树。
    //   - 每组一行 header(色块列 + ▾/▸ + 名+count),id 200+ 点击 toggle_group。
    //   - control_collapsed 含该组 → 只画 header,跳过 session 行;否则画 header + session 行。
    //   - 组间留空行分隔。session 行 id 100+(flat idx)→ 选 cursor。
    // IT2 节点 C:sessions 从 sess_col.y+1 起([+] new 占第 0 行)。
    let list_area = Rect {
        y: sess_col.y + 1,
        height: sess_col.height.saturating_sub(1),
        ..sess_col
    };
    let mut items: Vec<ListItem> = vec![];
    let mut ci = 0usize; // flat 索引(与 app.flat 对齐)
    let mut row_idx: u16 = 0; // 相对 list_area 内偏移
    // (gi, header_row_idx):组 header 所在行(供色块列对齐)。
    let mut group_header_rows: Vec<u16> = vec![];
    let sid_cap = sess_col.width.saturating_sub(8).max(4) as usize;
    for (gi, hs) in harnesses.iter().enumerate() {
        // 组间空行分隔(首组除外)。
        if gi > 0 {
            items.push(ListItem::new(""));
            row_idx = row_idx.saturating_add(1);
        }
        // 组 header 行:▾/▸ + 名 + (count)。
        group_header_rows.push(row_idx);
        let ss = app.sessions.sessions_by_harness.get(hs).map(|v| v.len()).unwrap_or(0);
        let collapsed = app.control_collapsed.contains(hs);
        let arrow = if collapsed { "▸" } else { "▾" };
        let is_cursor_grp = cursor_group.as_deref() == Some(hs.as_str());
        let hdr_st = if is_cursor_grp {
            Style::default().fg(Color::Black).bg(Color::Yellow).add_modifier(Modifier::BOLD)
        } else {
            Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD)
        };
        items.push(ListItem::new(ratatui::text::Line::from(vec![
            Span::styled(format!("{} ", arrow), hdr_st),
            Span::styled(hs.clone(), hdr_st),
            Span::styled(format!(" ({})", ss), Style::default().fg(Color::DarkGray)),
        ])));
        // header 整行注册 clickmap(色块列 200+ 不再用;header 点击 toggle)。点击范围 = header 行整宽。
        let hdr_rect = Rect::new(list_area.x, list_area.y + row_idx, list_area.width, 1);
        app.clickmap.register(hdr_rect, 200 + gi);
        row_idx = row_idx.saturating_add(1);

        // 展开态:画 session 行;折叠态:跳过。
        if !collapsed {
            if let Some(ss_list) = app.sessions.sessions_by_harness.get(hs) {
                for s in ss_list {
                    let sid = trunc(&s.session_id, sid_cap);
                    let inst = app.instance_count(&s.harness_type, &s.session_id);
                    let ev_key = format!("{}/{}", s.harness_type, s.session_id);
                    let ev_n = app.events.get(&ev_key).map(|e| e.len()).unwrap_or(0);
                    let multi_tag = if inst >= 2 { format!("×{}", inst) } else { String::new() };
                    let row_rect = Rect::new(list_area.x, list_area.y + row_idx, list_area.width, 1);
                    let hovered = app.mouse.in_rect(row_rect);
                    let is_cursor = ci == app.cursor;
                    let (prefix, st) = if is_cursor && app.action_loading("session") {
                        ("  ▸", Style::default().fg(Color::White).bg(Color::Magenta).add_modifier(Modifier::BOLD))
                    } else if is_cursor {
                        ("  ▸", Style::default().fg(Color::White).bg(Color::Blue).add_modifier(Modifier::BOLD))
                    } else if hovered {
                        ("   ", Style::default().fg(Color::Black).bg(Color::Yellow))
                    } else {
                        ("   ", Style::default().fg(Color::White))
                    };
                    let multi_color = if inst >= 2 { Color::Yellow } else { Color::DarkGray };
                    let line = ratatui::text::Line::from(vec![
                        Span::styled(format!("{}{}", prefix, sid), st),
                        Span::styled(multi_tag, Style::default().fg(multi_color).add_modifier(Modifier::BOLD)),
                        Span::styled(format!(" {}", ev_n), Style::default().fg(Color::DarkGray)),
                    ]);
                    items.push(ListItem::new(line));
                    app.clickmap.register(row_rect, 100 + ci);
                    ci += 1;
                    row_idx = row_idx.saturating_add(1);
                }
            }
        } else {
            // 折叠态:ci 仍要前进(保持 flat 索引对齐,虽然不画但 cursor 索引语义不变)。
            if let Some(ss_list) = app.sessions.sessions_by_harness.get(hs) {
                ci += ss_list.len();
            }
        }
    }
    if app.flat.is_empty() {
        items.push(ListItem::new(Line::from(Span::styled(
            "(无 session · r 刷新)", Style::default().fg(Color::DarkGray),
        ))));
    }
    f.render_widget(List::new(items), list_area);

    // 色块列:对齐到每组 header 行(group_header_rows),光标组反白。
    // IT3 ③:色块仍画(视觉组标签),但点击行为已由 header 行 clickmap 200+ 接管(toggle)。
    // 色块 rect 与 header 行重叠 → 同 id 200+ 注册(header rect 已覆盖,这里仅视觉)。
    for (gi, hs) in harnesses.iter().enumerate() {
        let (tag, color) = harness_tag(hs);
        let is_cursor = cursor_group.as_deref() == Some(hs.as_str());
        let y_off = *group_header_rows.get(gi).unwrap_or(&0);
        let block_rect = Rect::new(tag_col.x, tag_col.y + 1 + y_off, 2, 1);
        if block_rect.y < tag_col.y + tag_col.height {
            let style = if is_cursor {
                Style::default().bg(color).fg(Color::Black).add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(color).add_modifier(Modifier::BOLD)
            };
            f.render_widget(
                Paragraph::new(tag).style(style).alignment(ratatui::layout::Alignment::Center),
                block_rect,
            );
        }
    }

    // 左|右分隔条(resizable)。
    f.render_widget(
        ratatui::widgets::Block::default().style(Style::default().fg(Color::DarkGray)),
        bar,
    );

    // ── 右区(IT6-③ 状态栏整合进 input):右TabBar(顶线) + body(按 tab) + 输入+状态 ──
    // 去掉独立 2 行 StatusBar,orche●/session/last 并入底部输入区省空间,右区 = tabs+body+input。
    // IT7 ①:输入区高度动态 = base 3(状态+输入+提示) + max(0, textarea.line_count()-1) 额外行,cap 8。
    // bug1 根治:area 高度按 wrap 后行数(desired_height),非逻辑行 line_count。
    // ponytail: input_inner 宽 ≈ right.width-2(border);垂直 layout input 占右区全宽,估算够用。
    let dh = app.textarea.desired_height(right.width.saturating_sub(2).max(1));
    // input_h = dh(wrap 行)+ status(1)+ mode(1)+ border(2)= dh+4(render_input_bar 在 inner 上 [1,Min,1])
    let input_h = (dh + 4).min(12);
    let right_chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(1), Constraint::Min(1), Constraint::Length(input_h)])
        .split(right);
    let [tabs_area, body_area, input_area] = [right_chunks[0], right_chunks[1], right_chunks[2]];

    let hover = if app.mouse.visible { Some((app.mouse.x, app.mouse.y)) } else { None };
    app.control_right_tabs.render_with_hover(f, tabs_area, hover);
    app.right_tab_area = tabs_area;

    let key = app.flat.get(app.cursor)
        .map(|s| format!("{}/{}", s.harness_type, s.session_id))
        .unwrap_or_default();

    // body:对话=chat 卷轴(主)、flow=lane+DAG、属性=props。
    match app.control_right_tabs.active {
        0 => {
            let (mut ev_lines, n_turns) = if let Some(evs) = app.events.get(&key) {
                // B1 缓存:cursor session key + last event 签名不变 → 复用(消除每帧 render_turn_stream 重建)。
                // 签名用 last event (event_type, event_id) 替代 count(evs.len()):drain_ws cap=200 后
                // remove(0)+push 使 len 恒 200 → count 不变但内容推进 → 旧 lines 永复用致对话冻结(#1)。
                // last event 在 cap 推进/新事件到达时必变 → 强制重建;REST 全量 replace 同样失效。
                let sig = evs.last().map(|e| (e.event_type.clone(), e.event_id.clone()));
                let need = app.cached_turn_lines.as_ref().map_or(true, |c| c.0 != key || c.1 != sig);
                if need {
                    let (lines, n) = control::render_turn_stream(evs);
                    app.cached_turn_lines = Some((key.clone(), sig, lines.clone(), n));
                    (lines, n)
                } else {
                    let c = app.cached_turn_lines.as_ref().unwrap();
                    (c.2.clone(), c.3)
                }
            } else if key.is_empty() {
                (vec![Line::from(Span::styled(
                    " (无 cursor session · 左大纲点选 session 或色块)", Style::default().fg(Color::DarkGray),
                ))], 0)
            } else {
                (vec![Line::from(Span::styled(
                    " (observe 不可达 · r 刷新)", Style::default().fg(Color::DarkGray),
                ))], 0)
            };
            app.control_turn_count = n_turns;
            // optimistic pending 行(cache 外,每帧 spinner 变):本地立即回显 + 跑马灯特效,
            // drain_ws 收 orche 事件清 pending_turn 后自动消失(被真实 user message 行替代)。
            // per-session:只显 cursor session 的 pending(切到别的 session 不串显 spinner)。
            let cur_key = app.flat.get(app.cursor)
                .map(|s| format!("{}/{}", s.harness_type, s.session_id));
            if let Some((pk, msg)) = app.pending_turn.as_ref() {
                if cur_key.as_deref() == Some(pk.as_str()) {
                    use crate::components::control::SPINNER;
                    let sp = SPINNER[app.spinner_frame % SPINNER.len()];
                    ev_lines.push(Line::from(vec![
                        Span::styled(format!("{} ", sp),
                            Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)),
                        Span::styled(msg.clone(), Style::default().fg(Color::White)),
                    ]));
                }
            }
            // 流式 token_delta 累积行(紧跟 pending;openclaw turn 边收边显,native 不发故空)。
            // 每帧变(token 累积),cache 外(同 pending_turn);tick_completed 清 buffer + response 进 events 替代。
            if let Some(txt) = cur_key.as_deref().and_then(|k| app.streaming_text.get(k)) {
                if !txt.is_empty() {
                    use crate::components::control::SPINNER;
                    let sp = SPINNER[app.spinner_frame % SPINNER.len()];
                    let prefix = vec![Span::styled(
                        format!("{} ", sp),
                        Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD),
                    )];
                    // markdown 渲染 streaming(半截容错;md_to_text 解析未闭合语法当普通文本,
                    // 完整 response 来后 tick_completed 用同款 md_to_text 替换,视觉一致)。
                    ev_lines.extend(crate::components::control::md_indented_lines(txt, &prefix));
                }
            }
            app.control_chat_scroll.set_content(ev_lines);
            // ScrollView.render 内部按 follow_tail_flag 自动追底(内容超视口→最后一页,不超→从顶)。
            app.control_chat_scroll.follow_tail_flag = app.chat_follow_tail;
            app.control_chat_scroll.render(f, body_area);
        }
        1 => {
            // flow tab 纯节点 DAG(不混 cursor session 对话 lanes);选 flow 显示节点内容。
            let mut flow_lines_v: Vec<Line> = vec![];
            if let Some(tf) = app.current_flow() {
                flow_lines_v.extend(flow_dag_lines(tf, app.flow_cursor));
            } else {
                flow_lines_v.push(Line::from(Span::styled(
                    " (无 flow · 右键 Create Chain/Branch/DAG 或 f/G/D)", Style::default().fg(Color::DarkGray),
                )));
            }
            f.render_widget(Paragraph::new(flow_lines_v).block(region_block(" flow · 节点 DAG ")), body_area);
        }
        _ => {
            let prop_lines = render_props_lines(app);
            f.render_widget(Paragraph::new(prop_lines).block(region_block(" 属性 · cursor session ")), body_area);
        }
    }

    // 输入栏(恒显,含 8 按钮)。
    let input_block = region_block(" 输入 · message ");
    let input_inner = input_block.inner(input_area);
    f.render_widget(input_block, input_area);
    control::render_input_bar(f, input_inner, app);
}



/// 属性区:cursor session 详情(sid/harness/实例/事件数/last turn)。读 app 业务字段,不改业务方法。
/// 标题由 region_block 边框给出,本函数只返回字段行。
fn render_props_lines(app: &App) -> Vec<Line<'static>> {
    let mut lines: Vec<Line> = vec![];
    let Some(s) = app.flat.get(app.cursor) else {
        lines.push(Line::from(Span::styled(
            " (无 session · r 刷新)".to_string(),
            Style::default().fg(Color::DarkGray),
        )));
        return lines;
    };
    let inst = app.instance_count(&s.harness_type, &s.session_id);
    let ev_key = format!("{}/{}", s.harness_type, s.session_id);
    let ev_n = app.events.get(&ev_key).map(|e| e.len()).unwrap_or(0);
    let turn_disp = app.turn_status.clone().unwrap_or_else(|| "(未触发)".to_string());
    lines.push(Line::from(vec![
        Span::styled(" sid     ", Style::default().fg(Color::DarkGray)),
        Span::styled(trunc(&s.session_id, 30), Style::default().fg(Color::White).add_modifier(Modifier::BOLD)),
    ]));
    lines.push(Line::from(vec![
        Span::styled(" harness ", Style::default().fg(Color::DarkGray)),
        Span::styled(s.harness_type.clone(), Style::default().fg(Color::Yellow)),
    ]));
    lines.push(Line::from(vec![
        Span::styled(" 实例    ", Style::default().fg(Color::DarkGray)),
        Span::styled(format!("{}", inst), Style::default().fg(if inst >= 2 { Color::Yellow } else { Color::White }).add_modifier(Modifier::BOLD)),
        Span::styled(if inst >= 2 { "  (×N multi)" } else { "" }, Style::default().fg(Color::DarkGray)),
    ]));
    lines.push(Line::from(vec![
        Span::styled(" 事件    ", Style::default().fg(Color::DarkGray)),
        Span::styled(format!("{}", ev_n), Style::default().fg(Color::Yellow)),
    ]));
    lines.push(Line::from(vec![
        Span::styled(" last    ", Style::default().fg(Color::DarkGray)),
        Span::styled(trunc(&turn_disp, 40), Style::default().fg(Color::White)),
    ]));
    lines
}

// ═══ Home 占位面板 ════════════════════════════════════════════════

/// Home dashboard 三张结构化卡片(ADR-5 region_block:TOP 描边 + bg + cyan 标题)。
/// 布局:[Sessions 50% | Flows 50%](上半) + [Cursor Session 全宽](下半)。
/// read-only 读 App 业务字段。cursor 所在 harness 组高亮。
/// Home tab 已移除(默认 Control),保留供 --dump 演示/未来用。
#[allow(dead_code)]
pub fn draw_home(f: &mut Frame, area: Rect, app: &App) {
    use crate::components::position;
    use crate::state::harness_tag;

    // 上半左右分 [Sessions | Flows],下半全宽 [Cursor]。
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(1), Constraint::Length(7)])
        .split(area);
    let top = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
        .split(rows[0]);

    // ── 卡片 1(左上):Sessions · harness 分组,色块 + 组名 + 计数 + multi;cursor 组高亮 ──
    let cursor_ht = app.flat.get(app.cursor).map(|s| s.harness_type.as_str()).unwrap_or("");
    let mut harnesses: Vec<&String> = app.sessions.sessions_by_harness.keys().collect();
    harnesses.sort();
    let total_sessions = app.flat.len();
    let mut session_lines: Vec<Line> = Vec::new();
    for hs in &harnesses {
        let n = app.sessions.sessions_by_harness.get(*hs).map(|v| v.len()).unwrap_or(0);
        let multi_count = app.sessions.sessions_by_harness.get(*hs).map(|ss| {
            ss.iter().filter(|s| app.instance_count(&s.harness_type, &s.session_id) >= 2).count()
        }).unwrap_or(0);
        let multi_tag = if multi_count > 0 {
            format!("  ×{} multi", multi_count)
        } else { String::new() };
        let (tag, color) = harness_tag(hs);
        let is_cur = *hs == cursor_ht;
        let mut spans = vec![
            Span::styled(format!(" {} ", tag), Style::default().fg(Color::Black).bg(color).add_modifier(Modifier::BOLD)),
            Span::raw(" "),
            Span::styled(
                format!("{:<14}", hs),
                Style::default().fg(if is_cur { Color::Yellow } else { Color::White })
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw(format!(" {} sessions{}", n, multi_tag)),
        ];
        if is_cur {
            spans.push(Span::styled("  ◀ cursor", Style::default().fg(Color::Yellow)));
        }
        session_lines.push(Line::from(spans));
    }
    if total_sessions == 0 {
        session_lines.push(Line::from(Span::styled(
            "  (无 session · 按 r 刷新)".to_string(),
            Style::default().fg(Color::DarkGray),
        )));
    }
    session_lines.push(Line::raw(""));
    session_lines.push(position::percent_line(
        if total_sessions > 0 { Some(app.cursor) } else { None },
        total_sessions,
    ));
    f.render_widget(
        Paragraph::new(session_lines).block(region_block(" Sessions · harness 分组 ")),
        top[0],
    );

    // ── 卡片 2(右上):Flows · 4 状态计数(running/completed/failed/pending)+ total ──
    let mut running = 0;
    let mut completed = 0;
    let mut failed = 0;
    let mut pending = 0;
    for tf in &app.flows {
        match tf.status.as_ref().map(|s| s.status.as_str()).unwrap_or("pending") {
            "running" => running += 1,
            "completed" => completed += 1,
            "failed" => failed += 1,
            _ => pending += 1,
        }
    }
    let flow_lines = vec![
        Line::from(vec![
            Span::styled(" ● ", Style::default().fg(Color::Green).add_modifier(Modifier::BOLD)),
            Span::styled("running  ", Style::default().fg(Color::Green)),
            Span::styled(format!("{}", running), Style::default().fg(Color::White).add_modifier(Modifier::BOLD)),
        ]),
        Line::from(vec![
            Span::styled(" ✓ ", Style::default().fg(Color::Magenta).add_modifier(Modifier::BOLD)),
            Span::styled("completed ", Style::default().fg(Color::Magenta)),
            Span::styled(format!("{}", completed), Style::default().fg(Color::White).add_modifier(Modifier::BOLD)),
        ]),
        Line::from(vec![
            Span::styled(" ✗ ", Style::default().fg(Color::Red).add_modifier(Modifier::BOLD)),
            Span::styled("failed   ", Style::default().fg(Color::Red)),
            Span::styled(format!("{}", failed), Style::default().fg(Color::White).add_modifier(Modifier::BOLD)),
        ]),
        Line::from(vec![
            Span::styled(" ○ ", Style::default().fg(Color::Yellow)),
            Span::styled("pending  ", Style::default().fg(Color::Yellow)),
            Span::styled(format!("{}", pending), Style::default().fg(Color::White)),
        ]),
        Line::raw(""),
        Line::from(vec![
            Span::styled(" total ", Style::default().fg(Color::DarkGray)),
            Span::styled(format!("{}", app.flows.len()), Style::default().fg(Color::White).add_modifier(Modifier::BOLD)),
        ]),
    ];
    f.render_widget(
        Paragraph::new(flow_lines).block(region_block(" Flows · 状态 ")),
        top[1],
    );

    // ── 卡片 3(下半全宽):Cursor session 摘要(2 行紧凑:sid/harness/实例 + events/last)──
    let cur_session = app.flat.get(app.cursor);
    let cur_sid = cur_session.map(|s| trunc(&s.session_id, 30)).unwrap_or_else(|| "(无)".to_string());
    let cur_ht = cur_session.map(|s| s.harness_type.as_str()).unwrap_or("—");
    let cur_key = cur_session.map(|s| format!("{}/{}", s.harness_type, s.session_id)).unwrap_or_default();
    let event_count = app.events.get(&cur_key).map(|e| e.len()).unwrap_or(0);
    let inst_n = cur_session.map(|s| app.instance_count(&s.harness_type, &s.session_id)).unwrap_or(0);
    let turn_disp = app.turn_status.clone().unwrap_or_else(|| "(未触发)".to_string());

    let cursor_lines = vec![
        Line::from(vec![
            Span::styled(" sid     ", Style::default().fg(Color::DarkGray)),
            Span::styled(cur_sid, Style::default().fg(Color::White).add_modifier(Modifier::BOLD)),
            Span::styled(format!("   ({})", cur_ht), Style::default().fg(Color::DarkGray)),
            Span::styled(
                if inst_n >= 2 { format!("   ×{} instances", inst_n) } else { String::new() },
                Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD),
            ),
        ]),
        Line::from(vec![
            Span::styled(" events  ", Style::default().fg(Color::DarkGray)),
            Span::styled(format!("{}", event_count), Style::default().fg(Color::Yellow)),
            Span::styled("   last ", Style::default().fg(Color::DarkGray)),
            Span::styled(trunc(&turn_disp, 40), Style::default().fg(Color::White)),
        ]),
    ];
    f.render_widget(
        Paragraph::new(cursor_lines).block(region_block(" Cursor Session ")),
        rows[1],
    );
}

// ═══ 分层 draw:顶栏 TabBar → 主区 panel → 底栏 hint → 弹窗栈 → MouseCursor ═══

/// 顶层 draw:分层调度。
pub fn draw(f: &mut Frame, app: &mut App) {
    let area = f.area();
    app.size = (area.width, area.height);

    // 确保 tabbar.active 与 panel 同步(键盘切 panel 后 tab 高亮一致)。
    app.sync_tab_from_panel();

    // 顶栏 TabBar(3) / 主区(Min)/ 底栏 hint(1)。
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(1), Constraint::Min(1), Constraint::Length(1)])
        .split(area);

    // 顶栏:TabBar 渲染(ADR-1:传鼠标位置做悬停高亮)+ 缓存 tab_area 供鼠标 hit。
    let hover = if app.mouse.visible { Some((app.mouse.x, app.mouse.y)) } else { None };
    app.tabbar.render_with_hover(f, chunks[0], hover);
    app.tab_area = chunks[0];

    // status(tab 右侧,info 按钮左):Flows R/C/F 计数 + cursor sid(opencode 紧凑)。
    let (mut fr, mut fc, mut ff) = (0u32, 0u32, 0u32);
    for tf in &app.flows {
        match tf.status.as_ref().map(|s| s.status.as_str()).unwrap_or("") {
            "running" => fr += 1,
            "completed" => fc += 1,
            "failed" => ff += 1,
            _ => {}
        }
    }
    let cur_sid = app.flat.get(app.cursor).map(|s| trunc(&s.session_id, 16)).unwrap_or_default();
    let st_line = Line::from(vec![
        Span::styled(" Flows ", Style::default().fg(DARK.fg_muted)),
        Span::styled(format!("R{} ", fr), Style::default().fg(DARK.success)),
        Span::styled(format!("C{} ", fc), Style::default().fg(DARK.done)),
        Span::styled(format!("F{} ", ff), Style::default().fg(DARK.error)),
        Span::styled(format!("· {}", cur_sid), Style::default().fg(DARK.fg_muted)),
    ]);
    let st_w = (st_line.width() as u16).min(chunks[0].width.saturating_sub(16));
    if st_w > 0 {
        let st_area = Rect::new(chunks[0].right().saturating_sub(st_w as u16 + 8), chunks[0].y + 1, st_w as u16, 1);
        f.render_widget(Paragraph::new(st_line).style(Style::default().bg(DARK.bg_surface)), st_area);
    }

    // ADR-2:键盘焦点 TabBar 标记(▶)已移除(多余,与 DARK 风格不搭)。

    // ADR-3:顶栏右端 i(id998→open_props)/×(id999→quit_requested)按钮。
    // IT5 ①:clickmap 区域覆盖顶栏全高(top.height=3:边框行+tab 行+边框行),
    // 防止点 tab 行(row 1)时 miss id 999/998 被当 tab 点击。文本仍渲染在 row 0(原 3x1 色块)。
    let top = chunks[0];
    let quit_hit = Rect::new(top.right().saturating_sub(3), top.y, 3, top.height);
    let info_hit = Rect::new(top.right().saturating_sub(6), top.y, 3, top.height);
    // 视觉色块仍只在 row 0(边框行),避免覆盖 tab 文本。
    let quit_rect = Rect::new(top.right().saturating_sub(3), top.y, 3, 1);
    let info_rect = Rect::new(top.right().saturating_sub(6), top.y, 3, 1);
    f.render_widget(
        Paragraph::new(" × ").style(Style::default().fg(Color::Black).bg(Color::Red).add_modifier(Modifier::BOLD))
            .alignment(ratatui::layout::Alignment::Center),
        quit_rect,
    );
    f.render_widget(
        Paragraph::new(" i ").style(Style::default().fg(Color::Black).bg(Color::Cyan).add_modifier(Modifier::BOLD))
            .alignment(ratatui::layout::Alignment::Center),
        info_rect,
    );

    // 顶层统一 clear clickmap(不依赖各 panel 互斥 clear;Flows 等无 clickmap 的 tab 也 clean,修 minor 2/3)。
    // IT5 ①:clear 移到 999/998 register 之前——否则顶栏按钮注册被清掉,鼠标命中失效。
    app.clickmap.clear();
    // 999/998 注册在 clear 之后,持久到下一帧;rect 在顶栏(top.y..),与主区 tree(Chunks[1])无重叠。
    app.clickmap.register(quit_hit, 999);
    app.clickmap.register(info_hit, 998);

    // 主区:按 active tab 分发(ADR-1)。
    match app.panel {
        Panel::Flows => draw_flow(f, chunks[1], app),
        Panel::Observe => draw_stack(f, chunks[1], app),
        Panel::Control => draw_control(f, chunks[1], app),
        Panel::Orchestrate => draw_orchestrate(f, chunks[1], app),
    }

    // 底栏 hint。
    // ADR-O4:Orchestrate tab 从 registry 动态派生原语 hint(灰显/高亮);其余 tab 全局硬编码。
    let hint_text = match app.panel {
        Panel::Orchestrate => format!(
            "{}· j/k 跨层级 · 1-4 切 tab · q quit{}",
            app.orch_primitive_hint(),
            if app.term.hint.is_empty() { String::new() } else { format!("  ⚠ {}", app.term.hint) },
        ),
        _ => format!(
            " Tab/1-4 切 tab · 鼠标点 tab · j/k 选(flow panel 切 flow)· t turn · f/G/D 创建 flow · R 运行 · s spawn · p 弹窗 · ? help · q quit{}",
            if app.term.hint.is_empty() { String::new() } else { format!("  ⚠ {}", app.term.hint) },
        ),
    };
    let hint = Paragraph::new(hint_text).style(Style::default().fg(Color::DarkGray));
    f.render_widget(hint, chunks[2]);

    // z-order layer 1:overlay(Kitty 图片预览)IT3 ② 已废——终端能力并入 i 弹窗(open_props)。
    // render_image_preview 函数保留(dead_code),draw() 不再调。

    // z-order layer 2:modal popup 栈(栈顶最上)。每个弹窗 Clear 遮罩 + Block + 正文。
    // IT2 节点 C:new/delete 弹窗 body 按 state 实时刷新(picker 选中行/自由输入)。
    update_action_popup_bodies(app);
    update_context_menu_body(app);
    for p in app.popups.iter_mut() {
        components::render_popup(f, area, p);
    }
    // IT7 ②:new 弹窗渲染后 area 已回填 → 注册可点击区到 popup_clickmap。
    register_new_popup_clickmap(app);
    register_context_menu_clickmap(app);
    register_delete_popup_clickmap(app);

    // 帧末:鼠标光标(ADR-2:最后渲染,黑底黄字高亮)。
    app.mouse.render(f);
}

/// IT2 节点 C / IT7 ②:new 弹窗 body 实时刷新(反映 picker 选中行 / cc 自由输入)。
/// 在 render_popup 之前调,改 top_popup body 让用户看到当前选中。
/// IT7 ②:统一布局——行 0 = [claw]/[cc] harness 按钮,行末 = [Create]/[Cancel] 按钮,
/// 中间为 picker 候选行。clickmap id 见 register_new_popup_clickmap。
fn update_action_popup_bodies(app: &mut App) {
    use ratatui::style::{Color, Style};
    use ratatui::text::{Line, Span};
    let is_new_top = app.popups.last().map(|p| p.id == "new").unwrap_or(false);
    if !is_new_top {
        return;
    }
    let cyan_btn = |s: String| Span::styled(s, Style::default().fg(Color::Black).bg(Color::Cyan));
    let sep = || Span::styled("│", Style::default().fg(Color::Cyan));
    let mut lines: Vec<Line<'static>> = vec![];
    // 行 0:harness 按钮(700/701),色块 + 公用描边分隔。
    let claw_mark = if matches!(app.new_popup, Some(NewKind::Claw)) { "[x]claw" } else { "[ ]claw" };
    let cc_mark = if matches!(app.new_popup, Some(NewKind::Cc)) { "[x]cc" } else { "[ ]cc" };
    let ao_mark = if matches!(app.new_popup, Some(NewKind::AoV2)) { "[x]ao" } else { "[ ]ao" };
    lines.push(Line::from(vec![
        cyan_btn(format!(" {} ", claw_mark)),
        sep(),
        cyan_btn(format!(" {} ", cc_mark)),
        sep(),
        cyan_btn(format!(" {} ", ao_mark)),
        Span::raw("  (c/d/o)"),
    ]));
    match app.new_popup {
        None => lines.push(Line::from("(选类型后显候选)")),
        Some(NewKind::Claw) => {
            lines.push(Line::from("claw agents:"));
            for (i, a) in app.new_candidates.iter().enumerate() {
                let mark = if i == app.new_idx { "▸" } else { " " };
                lines.push(Line::from(format!("{} {}", mark, a)));
            }
        }
        Some(NewKind::Cc) => {
            lines.push(Line::from(format!("cc cwd: [{}]", app.new_cc_input)));
            for (i, c) in app.new_candidates.iter().enumerate() {
                let mark = if i == app.new_idx { "▸" } else { " " };
                lines.push(Line::from(format!("{} {}", mark, c)));
            }
        }
        Some(NewKind::AoV2) => {
            // ADR-3:列出 registry agents 让用户选(default 预选 + ★ 标记)。
            // 风格匹配 claw/cc picker(▸ 选中 + 候选行)。空候选 = orche 不可达。
            lines.push(Line::from("agent-os-v2 agents:"));
            if app.new_ao2_agents.is_empty() {
                lines.push(Line::from("(无候选——orche :8001 不可达?重开 tab 重试)"));
            } else {
                for (i, a) in app.new_ao2_agents.iter().enumerate() {
                    let mark = if i == app.new_idx { "▸" } else { " " };
                    let star = if a.default { " ★" } else { "" };
                    lines.push(Line::from(format!("{} {}{}  [{}]", mark, a.name, star, a.id)));
                }
            }
        }
    }
    // 末行:Create/Cancel(790/791),色块 + 公用描边。
    lines.push(Line::from(vec![
        Span::raw(" "),
        cyan_btn(" Create ".into()),
        sep(),
        cyan_btn(" Cancel ".into()),
    ]));
    if let Some(p) = app.popups.last_mut() {
        p.height = (lines.len() as u16 + 4).clamp(8, 22);
        p.body_lines = lines;
        p.body = vec![];
    }
}

/// IT7 ②:new 弹窗渲染后注册可点击区到 popup_clickmap。
/// 弹窗 area 已回填(tui-popup render_ref 后);body 行 i 位于 area.y+1+i(跳 title 边框)。
/// id: 700=claw 701=cc 710+i=claw agent 720+i=cc cwd 790=Create 791=Cancel。
fn register_new_popup_clickmap(app: &mut App) {
    app.popup_clickmap.clear();
    let is_new_top = app.popups.last().map(|p| p.id == "new").unwrap_or(false);
    if !is_new_top {
        return;
    }
    let Some(area) = app.popups.last().and_then(|p| p.state.area().as_ref().copied()) else {
        return;
    };
    let inner_x = area.x + 1; // 跳左竖边框
    let inner_w = area.width.saturating_sub(2);
    // body 行索引(相对弹窗):row 0 = harness 按钮,1 = header,2.. = 候选,末行 = Create/Cancel。
    let body_lines = app.popups.last().map(|p| p.body_lines.clone()).unwrap_or_default();
    let body_len = body_lines.len();
    let row_y = |i: usize| -> u16 { area.y + 1 + i as u16 }; // title 占顶边框,首行 = area.y+1
    // 行 0:[x]claw  [x]cc —— 按文本长度切两段(claw 段前半,cc 段后半)。
    if body_len >= 1 {
        let y = row_y(0);
        app.popup_clickmap.register(Rect::new(inner_x, y, 8, 1), 700); // [x]claw
        app.popup_clickmap.register(Rect::new(inner_x + 9, y, 6, 1), 701); // [x]cc
        app.popup_clickmap.register(Rect::new(inner_x + 17, y, 7, 1), 702); // [x]ao
    }
    // 候选行:header 在 row 1,候选从 row 2 起。claw→710+i,cc→720+i。
    let cand_start = 2usize;
    for (i, _line) in body_lines.iter().enumerate().skip(cand_start) {
        if i + 1 >= body_len {
            break; // 末行是 Create/Cancel
        }
        let y = row_y(i);
        let id = match app.new_popup {
            Some(NewKind::Claw) => 710 + (i - cand_start),
            Some(NewKind::Cc) => 720 + (i - cand_start),
            // ADR-3:AoV2 候选行注册 730+i(与 claw 710/cc 720 同模式)。header 行
            // i=cand_start-1 不含(被 i>=cand_start skip 过滤)。
            Some(NewKind::AoV2) => 730 + (i - cand_start),
            None => continue,
        };
        app.popup_clickmap.register(Rect::new(inner_x, y, inner_w, 1), id);
    }
    // 末行:[Create] [Cancel]。
    if body_len >= 1 {
        let last_y = row_y(body_len - 1);
        app.popup_clickmap.register(Rect::new(inner_x + 1, last_y, 8, 1), 790); // [Create]
        app.popup_clickmap.register(Rect::new(inner_x + 10, last_y, 8, 1), 791); // [Cancel]
    }
}

/// 栈顶 id="ctx" 时:菜单项 ▸ 标记 selected + 末行 [x] close。
fn update_context_menu_body(app: &mut App) {
    let is_ctx_top = app.popups.last().map(|p| p.id == "ctx").unwrap_or(false);
    if !is_ctx_top {
        return;
    }
    let Some(cm) = app.context_menu.as_ref() else { return; };
    let mut lines: Vec<String> = cm.items.iter().enumerate().map(|(i, (label, _))| {
        let mark = if i == cm.selected { "▸" } else { " " };
        format!("{} {}", mark, label)
    }).collect();
    lines.push(" [x] close".into());
    if let Some(p) = app.popups.last_mut() {
        p.height = (lines.len() as u16 + 4).clamp(7, 22);
        p.body = lines;
    }
}

/// 栈顶 id="ctx" 时:注册菜单项(900+i)+ close(800)到 popup_clickmap。
/// 不 clear:register_new_popup_clickmap 每帧已 clear;ctx 栈顶时 new 不注册,clean。
fn register_context_menu_clickmap(app: &mut App) {
    let is_ctx_top = app.popups.last().map(|p| p.id == "ctx").unwrap_or(false);
    if !is_ctx_top {
        return;
    }
    let Some(area) = app.popups.last().and_then(|p| p.state.area().as_ref().copied()) else { return; };
    let Some(cm) = app.context_menu.as_ref() else { return; };
    let inner_x = area.x + 1;
    let inner_w = area.width.saturating_sub(2);
    for i in 0..cm.items.len() {
        let y = area.y + 1 + i as u16;
        app.popup_clickmap.register(Rect::new(inner_x, y, inner_w, 1), 900 + i);
    }
    let close_y = area.y + 1 + cm.items.len() as u16;
    app.popup_clickmap.register(Rect::new(inner_x, close_y, 10, 1), 800);
}

/// 栈顶 delete 弹窗:注册 [y]确认(600)/ [N]取消(601),对齐 delete_popup_body 行 1。
fn register_delete_popup_clickmap(app: &mut App) {
    let is_del_top = app.popups.last().map(|p| p.id == "delete").unwrap_or(false);
    if !is_del_top {
        return;
    }
    let Some(area) = app.popups.last().and_then(|p| p.state.area().as_ref().copied()) else { return; };
    let inner_x = area.x + 1;
    let btn_y = area.y + 2; // 跳 title 边框(行 0)+ "删除?"(行 1),按钮在行 2
    // [y]确认 10 列(含中文宽)│ [N]取消 10 列,对齐 delete_popup_body Line 1 的 span 位置。
    app.popup_clickmap.register(Rect::new(inner_x, btn_y, 10, 1), 600);
    app.popup_clickmap.register(Rect::new(inner_x + 11, btn_y, 10, 1), 601);
}
