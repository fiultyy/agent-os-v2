//! 渲染层(ratatui immediate-mode)。
//!
//! z-order:base panels(flow/stack/control,底层)→ overlays(预留)→ modal popups(栈顶最上)。
//! 弹窗用 Clear 擦该区域背景再画(Clear 遮罩);弹窗 Rect 支持 centered / absolute / offset。
//!
//! 保留 P1 的 draw_flow / draw_stack / draw_control / observe_lanes(真数据 lane),
//! 仅把外层 draw() 改成分层调度 + 弹窗栈叠加渲染。

use crate::components;
use crate::state::{fmt_val, trunc, App, FocusTarget, ObserveEvent, Panel, TrackedFlow};
use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, List, ListItem, Paragraph},
    Frame,
};

/// 功能分区边框:Control 右堆叠各 pane + 左大纲的可见边界。cyan bold 标题 + 全边框。
fn region_block(title: &str) -> Block<'static> {
    Block::default()
        .borders(Borders::ALL)
        .title(Line::from(Span::styled(
            title.to_string(),
            Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD),
        )))
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
        "tick_started" => ("●".to_string(), Color::Green, true, fmt_val(&e.data, "request")),
        "tool_call" => ("⚒".to_string(), Color::Blue, false, fmt_val(&e.data, "tool_name")),
        "tool_result" => ("◷".to_string(), Color::Cyan, false, fmt_val(&e.data, "result")),
        "token_delta" => ("δ".to_string(), Color::DarkGray, false, fmt_val(&e.data, "delta_text")),
        "tick_completed" => ("✓".to_string(), Color::Magenta, true, fmt_val(&e.data, "response")),
        other => (other.to_string(), Color::DarkGray, false, String::new()),
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
            " ── fallback:observe 真实 turn lane(openclaw 单 session)──".to_string(),
            Style::default().fg(Color::DarkGray),
        )));
        match app.events.get("openclaw/agent:main:main") {
            Some(evs) => lines.extend(observe_lanes(evs)),
            None => {
                let t = demo();
                lines.extend(flow_lines(&t, 0));
            }
        }
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
        "running" => ("⠋".to_string(), Color::Green),
        "completed" => ("✓".to_string(), Color::Magenta),
        "failed" => ("✗".to_string(), Color::Red),
        "skipped" => ("⊘".to_string(), Color::DarkGray),
        _ => ("○".to_string(), Color::Yellow), // pending / idle
    }
}

/// ADR-3(第八轮):Observe tab 卷轴 UI。所有 session 事件流连续滚动(日志总览),
/// 不分 session 树 | turn stream HSplit(改全屏 ScrollView 大卷轴)。
/// 每 session 一个 header 块 + 其事件行(session 标签前缀)。点 header/session → 跳 Control(ADR-2)。
/// WS 数据源不变(observe service 不改,drain_ws 更新 app.events)。
pub fn draw_stack(f: &mut Frame, area: Rect, app: &mut App) {
    app.observe_area = area; // 缓存供 events 鼠标命中(分隔条拖拽虽去,保留兼容)

    // 收集所有 session 事件 → 连续行(header 块 + 事件行)。按 flat 顺序(已排序)。
    // 跟踪每 session header 在总行列表中的索引(供 clickmap 算屏幕 y,考虑 scroll offset)。
    let mut all_lines: Vec<Line> = vec![
        Line::from(Span::styled(
            " OBSERVE · 事件卷轴(所有 session 连续滚动 · 点 header 跳 Control · Enter 跳 Control)".to_string(),
            Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD),
        )),
        Line::raw(""),
    ];
    // (header_line_idx, flat_idx) 对:header 行在全量 lines 中的索引 + 对应 flat 索引。
    let mut header_marks: Vec<(usize, usize)> = vec![];

    for (fi, s) in app.flat.iter().enumerate() {
        let key = format!("{}/{}", s.harness_type, s.session_id);
        let evs = app.events.get(&key);
        let ev_n = evs.map(|e| e.len()).unwrap_or(0);
        let inst = app.instance_count(&s.harness_type, &s.session_id);
        let sid_tag = trunc(&s.session_id, 24);
        let multi_tag = if inst >= 2 { format!(" ×{}", inst) } else { String::new() };

        // header 行(session 标签 + 事件数 + 多实例)。
        let is_cursor = fi == app.cursor;
        let st = if is_cursor {
            Style::default().fg(Color::White).bg(Color::Blue).add_modifier(Modifier::BOLD)
        } else {
            Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)
        };
        header_marks.push((all_lines.len(), fi));
        all_lines.push(Line::from(vec![
            Span::styled(format!(" ▸ {} ", sid_tag), st),
            Span::styled(format!("({}) ", s.harness_type), Style::default().fg(Color::DarkGray)),
            Span::styled(format!("{}ev", ev_n), Style::default().fg(Color::Yellow)),
            Span::styled(
                if inst >= 2 { format!("  {}", multi_tag) } else { String::new() },
                Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD),
            ),
        ]));

        // 事件行:每事件一行,session 标签前缀(日志风格)。
        if let Some(evs) = evs {
            for e in evs {
                let (tag, color, body) = event_log_parts(e);
                let prefix = format!(" [{}] ", trunc(&s.session_id, 10));
                all_lines.push(Line::from(vec![
                    Span::styled(prefix, Style::default().fg(Color::DarkGray)),
                    Span::styled(format!(" {} ", tag), Style::default().fg(Color::Black).bg(color).add_modifier(Modifier::BOLD)),
                    Span::raw(format!(" {}", trunc(&body, 50))),
                ]));
            }
        } else {
            all_lines.push(Line::from(Span::styled(
                "   (observe 不可达 · r 刷新)".to_string(),
                Style::default().fg(Color::DarkGray),
            )));
        }
        all_lines.push(Line::raw("")); // session 间空行
    }

    if app.flat.is_empty() {
        all_lines.push(Line::from(Span::styled(
            " (无 session · r 刷新)".to_string(),
            Style::default().fg(Color::DarkGray),
        )));
    }

    app.observe_scroll.set_content(all_lines);
    app.observe_scroll.render(f, area);

    // clickmap:为可见 header 行注册(flat 索引)供鼠标点击跳 Control。
    // ScrollView render 用 Block(全 borders),inner = area 四周缩 1;offset 是行偏移。
    // header 屏幕行 y = inner.y + (header_line_idx - offset)(落在 inner 内才注册)。
    // ponytail: scroll offset 下 clickmap 仅 header 行精确(事件行不注册,日志卷轴点 header 足够)。
    let inner = ratatui::widgets::Block::default().borders(ratatui::widgets::Borders::ALL).inner(area);
    let offset = app.observe_scroll.offset;
    for (line_idx, flat_idx) in &header_marks {
        if *line_idx < offset {
            continue;
        }
        let screen_y = inner.y + (*line_idx - offset) as u16;
        if screen_y >= inner.y + inner.height {
            continue; // 屏幕外
        }
        let row_rect = Rect::new(inner.x, screen_y, inner.width, 1);
        app.clickmap.register(row_rect, *flat_idx);
    }
}

/// 事件 → (tag, color, body)用于卷轴日志行(ADR-3 Observe 卷轴)。复用 glyph 语义。
fn event_log_parts(e: &ObserveEvent) -> (String, Color, String) {
    use crate::state::fmt_val;
    match e.event_type.as_str() {
        "tick_started" => ("START".to_string(), Color::Green, fmt_val(&e.data, "request")),
        "tool_call" => ("TOOL▸".to_string(), Color::Blue, fmt_val(&e.data, "tool_name")),
        "tool_result" => ("TOOL◂".to_string(), Color::Blue, fmt_val(&e.data, "result")),
        "tick_completed" => ("DONE ".to_string(), Color::Magenta, fmt_val(&e.data, "response")),
        "token_delta" => ("δ".to_string(), Color::DarkGray, fmt_val(&e.data, "delta_text")),
        other => (other.to_string(), Color::DarkGray, String::new()),
    }
}

pub fn draw_control(f: &mut Frame, area: Rect, app: &mut App) {
    use crate::components::control;
    use crate::state::harness_tag;

    // 窄大纲(色块组标签 + session 列)| 右主区(StatusBar + 右tab + body + 输入栏)。
    app.control_area = area; // 缓存供 mouse drag/hit。
    let [left, bar, right] = app.control_split.rects(area);

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

    // session 列:flat 顺序(已按 harness 排序),去组头,组间空行。
    // 记录每组首 session 行(group_start),供色块对齐渲染到该行。
    let mut items: Vec<ListItem> = vec![];
    let mut ci = 0usize;
    let mut row_idx: u16 = 0;
    let mut group_start: Vec<u16> = vec![];
    let sid_cap = sess_col.width.saturating_sub(6).max(4) as usize;
    for hs in &harnesses {
        let mut group_first = true;
        if let Some(ss) = app.sessions.sessions_by_harness.get(hs) {
            for s in ss {
                if ci > 0 && group_first {
                    items.push(ListItem::new(""));
                    row_idx = row_idx.saturating_add(1);
                }
                if group_first {
                    group_start.push(row_idx);
                    group_first = false;
                }
                let sid = trunc(&s.session_id, sid_cap);
                let inst = app.instance_count(&s.harness_type, &s.session_id);
                let ev_key = format!("{}/{}", s.harness_type, s.session_id);
                let ev_n = app.events.get(&ev_key).map(|e| e.len()).unwrap_or(0);
                let multi_tag = if inst >= 2 { format!("×{}", inst) } else { String::new() };
                let row_rect = Rect::new(sess_col.x, sess_col.y + row_idx, sess_col.width, 1);
                let hovered = app.mouse.in_rect(row_rect);
                let is_cursor = ci == app.cursor;
                let (prefix, st) = if is_cursor {
                    ("▸", Style::default().fg(Color::White).bg(Color::Blue).add_modifier(Modifier::BOLD))
                } else if hovered {
                    (" ", Style::default().fg(Color::Black).bg(Color::Yellow))
                } else {
                    (" ", Style::default().fg(Color::White))
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
        if group_first {
            group_start.push(row_idx); // 空组:占位保持与 harnesses 等长
        }
    }
    if app.flat.is_empty() {
        items.push(ListItem::new(Line::from(Span::styled(
            "(无 session · r 刷新)", Style::default().fg(Color::DarkGray),
        ))));
    }
    f.render_widget(List::new(items), sess_col);

    // 色块列:对齐到每组首 session 行(group_start),光标组反白;clickmap id 200+group_idx。
    for (gi, hs) in harnesses.iter().enumerate() {
        let (tag, color) = harness_tag(hs);
        let is_cursor = cursor_group.as_deref() == Some(hs.as_str());
        let y_off = *group_start.get(gi).unwrap_or(&0);
        let block_rect = Rect::new(tag_col.x, tag_col.y + y_off, 2, 1);
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
            app.clickmap.register(block_rect, 200 + gi);
        }
    }

    // 左|右分隔条(resizable)。
    f.render_widget(
        ratatui::widgets::Block::default().style(Style::default().fg(Color::DarkGray)),
        bar,
    );

    // ── 右区:StatusBar(恒显) + 右TabBar + body(按 tab) + 输入栏(恒显) ──
    let right_chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(2), Constraint::Length(3), Constraint::Min(1), Constraint::Length(5)])
        .split(right);
    let [status_area, tabs_area, body_area, input_area] = [right_chunks[0], right_chunks[1], right_chunks[2], right_chunks[3]];

    control::render_status_bar(f, status_area, app);

    let hover = if app.mouse.visible { Some((app.mouse.x, app.mouse.y)) } else { None };
    app.control_right_tabs.render_with_hover(f, tabs_area, hover);
    app.right_tab_area = tabs_area;

    let key = app.flat.get(app.cursor)
        .map(|s| format!("{}/{}", s.harness_type, s.session_id))
        .unwrap_or_default();

    // body:对话=chat 卷轴(主)、flow=lane+DAG、属性=props。
    match app.control_right_tabs.active {
        0 => {
            let ev_lines = if let Some(evs) = app.events.get(&key) {
                control::render_turn_stream(evs)
            } else if key.is_empty() {
                vec![Line::from(Span::styled(
                    " (无 cursor session · 左大纲点选 session 或色块)", Style::default().fg(Color::DarkGray),
                ))]
            } else {
                vec![Line::from(Span::styled(
                    " (observe 不可达 · r 刷新)", Style::default().fg(Color::DarkGray),
                ))]
            };
            app.control_chat_scroll.set_content(ev_lines);
            app.control_chat_scroll.render(f, body_area);
        }
        1 => {
            let mut flow_lines_v: Vec<Line> = vec![];
            if let Some(evs) = app.events.get(&key) {
                if !evs.is_empty() {
                    flow_lines_v.extend(observe_lanes(evs));
                }
            }
            if let Some(tf) = app.current_flow() {
                flow_lines_v.push(Line::raw(""));
                flow_lines_v.extend(flow_dag_lines(tf, app.flow_cursor));
            } else if flow_lines_v.is_empty() {
                flow_lines_v.push(Line::from(Span::styled(
                    " (无 flow · f/G/D 创建预设)", Style::default().fg(Color::DarkGray),
                )));
            }
            f.render_widget(Paragraph::new(flow_lines_v).block(region_block(" flow · lane+DAG ")), body_area);
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

/// Home dashboard 三块真数据(ADR-1):session 总览 + flow 状态 + cursor 摘要。
/// read-only 读 App 业务字段,用 position::percent_line 显活跃指标。
pub fn draw_home(f: &mut Frame, area: Rect, app: &App) {
    use crate::components::position;

    // ── block 1: session 总览(harness 分组 + 多实例 ×N)──
    let mut session_lines: Vec<Line> = vec![Line::from(Span::styled(
        " Sessions · harness 分组".to_string(),
        Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD),
    ))];

    let mut harnesses: Vec<&String> = app.sessions.sessions_by_harness.keys().collect();
    harnesses.sort();
    let total_sessions = app.flat.len();
    for hs in &harnesses {
        let n = app.sessions.sessions_by_harness.get(*hs).map(|v| v.len()).unwrap_or(0);
        // 多实例标记:该 harness 下有多少 session 是 ×N 多实例。
        let multi_count = app.sessions.sessions_by_harness.get(*hs).map(|ss| {
            ss.iter().filter(|s| app.instance_count(&s.harness_type, &s.session_id) >= 2).count()
        }).unwrap_or(0);
        let multi_tag = if multi_count > 0 {
            format!("  (×N multi: {})", multi_count)
        } else { String::new() };
        session_lines.push(Line::from(vec![
            Span::styled(format!("  ▾ {:<14}", hs), Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD)),
            Span::raw(format!(" {} sessions{}", n, multi_tag)),
        ]));
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

    // ── block 2: flow 状态(running/completed/failed 计数)──
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
        Line::from(Span::styled(
            " Flows · 状态".to_string(),
            Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD),
        )),
        Line::from(vec![
            Span::styled("  ● running   ", Style::default().fg(Color::Green).add_modifier(Modifier::BOLD)),
            Span::raw(format!("{}", running)),
        ]),
        Line::from(vec![
            Span::styled("  ✓ completed ", Style::default().fg(Color::Magenta).add_modifier(Modifier::BOLD)),
            Span::raw(format!("{}", completed)),
        ]),
        Line::from(vec![
            Span::styled("  ✗ failed    ", Style::default().fg(Color::Red).add_modifier(Modifier::BOLD)),
            Span::raw(format!("{}", failed)),
        ]),
        Line::from(vec![
            Span::styled("  ○ pending   ", Style::default().fg(Color::Yellow)),
            Span::raw(format!("{}", pending)),
        ]),
        Line::raw(""),
        Line::from(vec![
            Span::styled("  total ", Style::default().fg(Color::DarkGray)),
            Span::styled(format!("{}", app.flows.len()), Style::default().fg(Color::White).add_modifier(Modifier::BOLD)),
        ]),
    ];

    // ── block 3: cursor session 摘要(最近 turn_status + 事件数)──
    let cur_session = app.flat.get(app.cursor);
    let cur_sid = cur_session.map(|s| trunc(&s.session_id, 24)).unwrap_or_else(|| "(无)".to_string());
    let cur_ht = cur_session.map(|s| s.harness_type.as_str()).unwrap_or("—");
    let cur_key = cur_session.map(|s| format!("{}/{}", s.harness_type, s.session_id)).unwrap_or_default();
    let event_count = app.events.get(&cur_key).map(|e| e.len()).unwrap_or(0);
    let inst_n = cur_session.map(|s| app.instance_count(&s.harness_type, &s.session_id)).unwrap_or(0);
    let turn_disp = app.turn_status.clone().unwrap_or_else(|| "(未触发)".to_string());

    let cursor_lines = vec![
        Line::from(Span::styled(
            " Cursor session".to_string(),
            Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD),
        )),
        Line::from(vec![
            Span::styled("  session  ", Style::default().fg(Color::DarkGray)),
            Span::styled(cur_sid, Style::default().fg(Color::White).add_modifier(Modifier::BOLD)),
            Span::styled(format!("  ({})", cur_ht), Style::default().fg(Color::DarkGray)),
        ]),
        Line::from(vec![
            Span::styled("  events   ", Style::default().fg(Color::DarkGray)),
            Span::styled(format!("{}", event_count), Style::default().fg(Color::Yellow)),
            Span::styled(
                if inst_n >= 2 { format!("  ×{} instances", inst_n) } else { String::new() },
                Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD),
            ),
        ]),
        Line::from(vec![
            Span::styled("  last turn", Style::default().fg(Color::DarkGray)),
            Span::styled(format!(" {}", trunc(&turn_disp, 50)), Style::default().fg(Color::White)),
        ]),
    ];

    // 三块布局:左右分栏(session 总览 | flow 状态)+ 底部 cursor 摘要全宽。
    let cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
        .split(area);
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(1), Constraint::Length(7)])
        .split(cols[0]);

    f.render_widget(Paragraph::new(session_lines), rows[0]);
    f.render_widget(Paragraph::new(flow_lines), cols[1]);
    f.render_widget(Paragraph::new(cursor_lines), rows[1]);
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
        .constraints([Constraint::Length(3), Constraint::Min(1), Constraint::Length(1)])
        .split(area);

    // 顶栏:TabBar 渲染(ADR-1:传鼠标位置做悬停高亮)+ 缓存 tab_area 供鼠标 hit。
    let hover = if app.mouse.visible { Some((app.mouse.x, app.mouse.y)) } else { None };
    app.tabbar.render_with_hover(f, chunks[0], hover);
    app.tab_area = chunks[0];

    // ADR-2:键盘焦点在 TabBar 时,在 tab 栏底部加 ▶ 聚焦标记。
    if matches!(app.focus, FocusTarget::TabBar) {
        let focus_rect = Rect::new(chunks[0].x, chunks[0].y, 1, chunks[0].height);
        if let Some(cell) = f.buffer_mut().cell_mut((focus_rect.x, focus_rect.y + focus_rect.height.saturating_sub(1))) {
            let mut s = cell.style();
            s = s.fg(Color::Yellow).add_modifier(Modifier::BOLD);
            cell.set_char('▶').set_style(s);
        }
    }

    // 顶层统一 clear clickmap(不依赖各 panel 互斥 clear;Flows 等无 clickmap 的 tab 也 clean,修 minor 2/3)。
    app.clickmap.clear();

    // 主区:按 active tab 分发(ADR-1)。
    match app.panel {
        Panel::Home => draw_home(f, chunks[1], app),
        Panel::Flows => draw_flow(f, chunks[1], app),
        Panel::Observe => draw_stack(f, chunks[1], app),
        Panel::Control => draw_control(f, chunks[1], app),
    }

    // 底栏 hint。
    let hint = Paragraph::new(format!(
        " Tab/1-4 切 tab · 鼠标点 tab · j/k 选(flow panel 切 flow)· t turn · f/G/D 创建 flow · R 运行 · s spawn · p 弹窗 · ? help · q quit{}",
        if app.term.hint.is_empty() { String::new() } else { format!("  ⚠ {}", app.term.hint) },
    ))
    .style(Style::default().fg(Color::DarkGray));
    f.render_widget(hint, chunks[2]);

    // z-order layer 1:overlay(Kitty 图片预览——有图形协议才画)。ponytail: 无图片资源时跳过。
    if app.term.image_ok {
        components::render_image_preview(f, area, &app.term);
    }

    // z-order layer 2:modal popup 栈(栈顶最上)。每个弹窗 Clear 遮罩 + Block + 正文。
    for p in app.popups.iter_mut() {
        components::render_popup(f, area, p);
    }

    // 帧末:鼠标光标(ADR-2:最后渲染,黑底黄字高亮)。
    app.mouse.render(f);
}
