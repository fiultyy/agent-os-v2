//! 渲染层(ratatui immediate-mode)。
//!
//! z-order:base panels(flow/stack/control,底层)→ overlays(预留)→ modal popups(栈顶最上)。
//! 弹窗用 Clear 擦该区域背景再画(Clear 遮罩);弹窗 Rect 支持 centered / absolute / offset。
//!
//! 保留 P1 的 draw_flow / draw_stack / draw_control / observe_lanes(真数据 lane),
//! 仅把外层 draw() 改成分层调度 + 弹窗栈叠加渲染。

use crate::components;
use crate::state::{fmt_val, trunc, App, ObserveEvent, Panel};
use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{List, ListItem, Paragraph},
    Frame,
};

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

// ═══ observe 真数据 lane(P1 保留)══════════════════════════════════

pub fn observe_lanes(evs: &[ObserveEvent]) -> Vec<Line<'static>> {
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
        let mut spans: Vec<Span> = vec![];
        for (i, e) in grp.iter().enumerate() {
            if i > 0 {
                spans.push(Span::raw("───").style(Style::default().fg(Color::DarkGray)));
            }
            let (sym, color, bold, body) = match e.event_type.as_str() {
                "tick_started" => ("●", Color::Green, true, fmt_val(&e.data, "request")),
                "tool_call" => ("⚒", Color::Blue, false, fmt_val(&e.data, "tool_name")),
                "tool_result" => ("◷", Color::Cyan, false, fmt_val(&e.data, "result")),
                "token_delta" => ("δ", Color::DarkGray, false, fmt_val(&e.data, "delta_text")),
                "tick_completed" => ("✓", Color::Magenta, true, fmt_val(&e.data, "response")),
                other => (other, Color::DarkGray, false, String::new()),
            };
            let mut st = Style::default().fg(color);
            if bold {
                st = st.add_modifier(Modifier::BOLD);
            }
            let label = if body.is_empty() { sym.to_string() } else { format!("{} {}", sym, trunc(&body, 20)) };
            spans.push(Span::styled(label, st));
        }
        out.push(Line::from(spans));
    }
    out
}

pub fn observe_lane(evs: &[ObserveEvent]) -> Line<'static> {
    observe_lanes(evs)
        .into_iter()
        .next()
        .unwrap_or_else(|| Line::raw("(no events)"))
}

// ═══ base panels(P1 draw_* 保留)══════════════════════════════════

pub fn draw_flow(f: &mut Frame, area: Rect, app: &App) {
    let mut lines: Vec<Line> = vec![
        Line::from(Span::styled(
            " flow · 横向轨道流(turn 节点 → 时间轴)".to_string(),
            Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD),
        )),
        Line::raw(""),
    ];
    match app.events.get("openclaw/agent:main:main") {
        Some(evs) => {
            lines.push(Line::from(Span::styled(
                format!(" observe 真实 turn · openclaw/{} · {} events", crate::state::CLAW_SESSION, evs.len()),
                Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD),
            )));
            lines.push(Line::raw(""));
            lines.extend(observe_lanes(evs));
        }
        None => {
            lines.push(Line::from(Span::styled(
                " (observe 不可达 · mock demo fallback)".to_string(),
                Style::default().fg(Color::DarkGray),
            )));
            lines.push(Line::raw(""));
            let t = demo();
            lines.extend(flow_lines(&t, 0));
        }
    }
    f.render_widget(Paragraph::new(lines), area);
}

pub fn draw_stack(f: &mut Frame, area: Rect, app: &App) {
    let h = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(35), Constraint::Percentage(65)])
        .split(area);
    let mut items: Vec<ListItem> = vec![];
    let mut harnesses: Vec<String> = app.sessions.sessions_by_harness.keys().cloned().collect();
    harnesses.sort();
    let mut ci = 0;
    for hs in &harnesses {
        let n = app.sessions.sessions_by_harness.get(hs).map(|v| v.len()).unwrap_or(0);
        items.push(ListItem::new(format!(" ▾ {} · {}", hs, n)).style(Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD)));
        if let Some(ss) = app.sessions.sessions_by_harness.get(hs) {
            for s in ss {
                let label = trunc(&s.session_id, 22);
                let (prefix, st) = if ci == app.cursor {
                    ("▸ ", Style::default().fg(Color::White).bg(Color::Blue).add_modifier(Modifier::BOLD))
                } else {
                    ("  ", Style::default().fg(Color::White))
                };
                items.push(ListItem::new(format!("   {}{}", prefix, label)).style(st));
                ci += 1;
            }
        }
        items.push(ListItem::new(""));
    }
    f.render_widget(List::new(items), h[0]);

    let key = app.flat.get(app.cursor).map(|s| format!("{}/{}", s.harness_type, s.session_id)).unwrap_or_default();
    let mut ev_lines: Vec<Line> = vec![
        Line::from(Span::styled(" turn stream".to_string(), Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD))),
        Line::raw(""),
    ];
    if let Some(evs) = app.events.get(&key) {
        for e in evs {
            ev_lines.push(stack_event_line(e));
        }
    }
    f.render_widget(Paragraph::new(ev_lines), h[1]);
}

fn stack_event_line(e: &ObserveEvent) -> Line<'static> {
    let (tag, color, body) = match e.event_type.as_str() {
        "tick_started" => ("START", Color::Green, fmt_val(&e.data, "request")),
        "tool_call" => ("TOOL▸", Color::Blue, fmt_val(&e.data, "tool_name")),
        "tool_result" => ("TOOL◂", Color::Blue, fmt_val(&e.data, "result")),
        "tick_completed" => ("DONE ", Color::Magenta, fmt_val(&e.data, "response")),
        "token_delta" => ("δ", Color::DarkGray, fmt_val(&e.data, "delta_text")),
        other => (other, Color::DarkGray, String::new()),
    };
    Line::from(vec![
        Span::styled(format!(" {} ", tag), Style::default().fg(Color::Black).bg(color).add_modifier(Modifier::BOLD)),
        Span::raw(format!(" {}", trunc(&body, 60))),
    ])
}

pub fn draw_control(f: &mut Frame, area: Rect, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(6), Constraint::Min(1)])
        .split(area);

    let status_disp = app.turn_status.clone().unwrap_or_else(|| "(未触发)".to_string());
    let bar = vec![
        Line::from(Span::styled(
            " CONTROL · orchestrator 原语".to_string(),
            Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD),
        )),
        Line::from(vec![
            Span::styled(" session   ", Style::default().fg(Color::DarkGray)),
            Span::styled(crate::state::CLAW_SESSION.to_string(), Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
        ]),
        Line::from(vec![
            Span::styled(" message   ", Style::default().fg(Color::DarkGray)),
            Span::styled(format!("\"{}\"", app.turn_msg), Style::default().fg(Color::Cyan)),
            Span::styled("  [t 触发 turn]", Style::default().fg(Color::Green)),
        ]),
        Line::from(vec![
            Span::styled(" last turn ", Style::default().fg(Color::DarkGray)),
            Span::styled(trunc(&status_disp, 80), Style::default().fg(Color::White)),
        ]),
        Line::raw(""),
    ];
    f.render_widget(Paragraph::new(bar), chunks[0]);

    let mut lane_lines: Vec<Line> = vec![Line::from(Span::styled(
        " flow lane · openclaw 真实 turn(tick_started → token_delta → tick_completed)".to_string(),
        Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD),
    ))];
    lane_lines.push(Line::raw(""));
    match app.events.get("openclaw/agent:main:main") {
        Some(evs) => {
            lane_lines.push(observe_lane(evs));
            lane_lines.push(Line::raw(""));
            lane_lines.push(Line::from(Span::styled(
                format!(" ({} events)", evs.len()),
                Style::default().fg(Color::DarkGray),
            )));
        }
        None => lane_lines.push(Line::from(Span::raw("(observe 不可达,按 r 重试)").style(Style::default().fg(Color::DarkGray)))),
    }
    f.render_widget(Paragraph::new(lane_lines), chunks[1]);
}

// ═══ 分层 draw:base → overlay → modal popup 栈 ═══════════════════

/// 顶层 draw:分层调度。
pub fn draw(f: &mut Frame, app: &mut App) {
    let area = f.area();
    app.size = (area.width, area.height);

    // z-order layer 0:base panel(标题栏 + 当前 panel + 提示栏)
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(1), Constraint::Min(1), Constraint::Length(1)])
        .split(area);

    let title = Paragraph::new(format!(
        " v2 harness-bridge(ratatui · P2 分层)· {} · kitty={} · [tab/e/p/?,右键 menu]",
        app.panel.label(),
        app.term.protocol.label(),
    ))
    .style(Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD));
    f.render_widget(title, chunks[0]);

    match app.panel {
        Panel::Flow => draw_flow(f, chunks[1], app),
        Panel::Stack => draw_stack(f, chunks[1], app),
        Panel::Control => draw_control(f, chunks[1], app),
    }

    let hint = Paragraph::new(format!(
        " tab 切视图 · c control · j/k 选 session · t turn · e raw exec · p 弹窗 · ? help · 右键 menu · q quit{}",
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
}
