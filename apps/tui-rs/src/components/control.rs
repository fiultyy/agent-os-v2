//! Control 区 Cursor 式子组件(ADR-7 第 3 批控件)。
//!
//! 独立 render fn,可复用(后续 Observe/web GUI 消费)。draw_control 组合调用。
//! - InputBar:turn_msg 输入显示 + trigger/spawn/flow 按钮(复用 ClickMap + trigger_control_button)。
//! - StatusBar:orche health(●/⚠)+ session + last turn。
//! - TurnSeparator:turn 之间视觉分隔(tick_started 开新 turn 块)。
//! - ToolCallBadge:tool_call/tool_result 视觉标识。
//! - turn stream markdown:tick_completed response 经 md_to_text 渲染。

#![allow(dead_code)]

use crate::components::markdown;
use crate::state::{fmt_val, trunc, App, ObserveEvent};
use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::Paragraph,
    Frame,
};

/// Control 按钮(label, id, base_color, action_name)。
/// id 0-7 对应 trigger_control_button。InputBar 在底栏渲染这些按钮。
pub const CONTROL_BUTTONS: [(&str, usize, Color, &str); 8] = [
    (" [t] trigger ", 0, Color::Green, "trigger"),
    (" [s] spawn   ", 1, Color::Cyan, "spawn"),
    (" [r] refresh ", 2, Color::Yellow, ""),
    (" [e] raw-exec", 3, Color::Magenta, ""),
    (" [f] chain   ", 4, Color::Blue, "create_chain"),
    (" [G] branch  ", 5, Color::Blue, "create_branch"),
    (" [D] DAG     ", 6, Color::Blue, "create_dag"),
    (" [R] run     ", 7, Color::Red, "run_flow"),
];

/// StatusBar:orche health + session + last turn。独立 render fn(可复用)。
pub fn render_status_bar(f: &mut Frame, area: Rect, app: &App) {
    let orche_hint = if app.orche_online {
        Span::styled(" ● online", Style::default().fg(Color::Green).add_modifier(Modifier::BOLD))
    } else {
        Span::styled(" ⚠ orche 离线(REST 将失败 · r 重试)", Style::default().fg(Color::Red).add_modifier(Modifier::BOLD))
    };
    let status_disp = app.turn_status.clone().unwrap_or_else(|| "(未触发)".to_string());
    let cur_sid = app.flat.get(app.cursor).map(|s| trunc(&s.session_id, 20)).unwrap_or_else(|| "(无)".to_string());
    let lines = vec![
        Line::from(vec![
            Span::styled(" CONTROL ⌘ ", Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD)),
            Span::styled("· orche", Style::default().fg(Color::DarkGray)),
            orche_hint,
        ]),
        Line::from(vec![
            Span::styled(" session ", Style::default().fg(Color::DarkGray)),
            Span::styled(cur_sid, Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
            Span::styled("  last ", Style::default().fg(Color::DarkGray)),
            Span::styled(trunc(&status_disp, 50), Style::default().fg(Color::White)),
        ]),
    ];
    f.render_widget(Paragraph::new(lines), area);
}

/// InputBar:turn_msg 输入显示 + trigger/spawn/flow 按钮行。
/// 按钮通过 ClickMap 注册(id 0-7),复用 trigger_control_button。
/// 独立 render fn(可复用)。
pub fn render_input_bar(f: &mut Frame, area: Rect, app: &mut App) {
    // 3 行:message(1) + 按钮行1(1) + 按钮行2(1)。
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(1), Constraint::Length(1), Constraint::Length(1)])
        .split(area);

    // message 输入行。
    let mode_hint = if app.insert_mode {
        "  [enter 发送 · esc 退快捷键]"
    } else {
        "  [i 输入 message · t/s/f/G/D/R/p/h/e 快捷键]"
    };
    let msg_line = Line::from(vec![
        Span::styled(" ❯ ", Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)),
        Span::styled(app.turn_msg.clone(), Style::default().fg(Color::White)),
        Span::styled("▌", Style::default().fg(Color::Cyan).add_modifier(Modifier::SLOW_BLINK)),
        Span::styled(mode_hint, Style::default().fg(Color::DarkGray)),
    ]);
    f.render_widget(Paragraph::new(msg_line), chunks[0]);

    // 按钮 2 行(每行 4 按钮):row 0 = id 0-3,row 1 = id 4-7。
    let quarter = [Constraint::Percentage(25); 4];
    let btn_rects1 = Layout::default().direction(Direction::Horizontal).constraints(quarter.clone()).split(chunks[1]);
    let btn_rects2 = Layout::default().direction(Direction::Horizontal).constraints(quarter).split(chunks[2]);

    for (i, (label, id, color, action)) in CONTROL_BUTTONS.iter().enumerate() {
        let row = i / 4;
        let col = i % 4;
        let rect = if row == 0 { btn_rects1[col] } else { btn_rects2[col] };
        app.clickmap.register(rect, *id);
        let hovered = app.mouse.in_rect(rect);
        let focused = matches!(app.focus, crate::state::FocusTarget::ControlButton(x) if x == *id);
        let loading = !action.is_empty() && app.action_loading(action);
        let style = if loading {
            Style::default().fg(Color::Black).bg(Color::Yellow).add_modifier(Modifier::RAPID_BLINK | Modifier::BOLD)
        } else if focused {
            Style::default().fg(Color::Black).bg(Color::White).add_modifier(Modifier::BOLD)
        } else if hovered {
            Style::default().fg(Color::Black).bg(*color).add_modifier(Modifier::BOLD | Modifier::UNDERLINED)
        } else {
            Style::default().fg(Color::Black).bg(*color).add_modifier(Modifier::BOLD)
        };
        let display = if focused { format!("▶{}", label) } else { label.to_string() };
        f.render_widget(Paragraph::new(display).style(style), rect);
    }
}

/// TurnSeparator:turn 之间视觉分隔(── turn N ──)。独立 fn(可复用)。
pub fn turn_separator_line(tick_id: &str, idx: usize) -> Line<'static> {
    let label = if tick_id.is_empty() {
        format!("── turn {} ──", idx + 1)
    } else {
        format!("── turn {} · {} ──", idx + 1, trunc(tick_id, 16))
    };
    Line::from(Span::styled(label, Style::default().fg(Color::DarkGray)))
}

/// ToolCallBadge:tool_call/tool_result 视觉标识行。独立 fn(可复用)。
/// tool_call → ⚒ TOOL▸ tool_name;tool_result → ◷ TOOL◂ result。
pub fn tool_call_badge_line(e: &ObserveEvent) -> Line<'static> {
    let (glyph, tag, body, color) = match e.event_type.as_str() {
        "tool_call" => ("⚒", "TOOL▸", fmt_val(&e.data, "tool_name"), Color::Blue),
        "tool_result" => ("◷", "TOOL◂", fmt_val(&e.data, "result"), Color::Cyan),
        _ => return stack_event_line(e),
    };
    Line::from(vec![
        Span::styled(format!(" {} {} ", glyph, tag), Style::default().fg(Color::Black).bg(color).add_modifier(Modifier::BOLD)),
        Span::raw(format!(" {}", trunc(&body, 60))),
    ])
}

/// 通用事件行(含 tool_call/tool_result 完整匹配)。
/// pub 供 render.rs Observe draw_stack 复用(消除重复,两处共用)。
pub fn stack_event_line(e: &ObserveEvent) -> Line<'static> {
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

/// tick_completed response markdown 渲染:把 response 字段当 markdown 渲染(多行 Text)。
/// 复用 components/markdown.rs md_to_text。返回 Vec<Line>(已缩进 + 着色)。
pub fn md_response_lines(e: &ObserveEvent) -> Vec<Line<'static>> {
    let response = fmt_val(&e.data, "response");
    if response.is_empty() {
        return vec![];
    }
    let text = markdown::md_to_text(&response);
    let mut out: Vec<Line> = Vec::new();
    for line in text.lines {
        // 每行前缀缩进(对话内容视觉层级)。
        let mut spans = vec![Span::raw("   ")];
        spans.extend(line.spans);
        out.push(Line::from(spans));
    }
    out
}

/// 把 cursor session 的 turn stream 渲染成 Lines。
/// 按 tick_started 分组,每组:TurnSeparator + 事件行(tool_call/result 用 badge,token_delta 内联,
/// tick_completed response 用 md 渲染)。复用 TurnSeparator + ToolCallBadge + md_response_lines。
pub fn render_turn_stream(evs: &[ObserveEvent]) -> Vec<Line<'static>> {
    let mut out: Vec<Line> = vec![];
    let mut turn_idx: usize = 0;
    let mut cur_tick: String = String::new();

    for e in evs {
        // tick_started 开新 turn 块(仅当 tick_id 不同 —— F1 修复:原只查 empty 不比较 tick_id,防御连续相同 tick_id)。
        if e.event_type == "tick_started" && e.tick_id != cur_tick {
            if !cur_tick.is_empty() {
                turn_idx += 1;
            }
            cur_tick = e.tick_id.clone();
            out.push(turn_separator_line(&e.tick_id, turn_idx));
        }
        if e.event_type == "tick_started" {
            // tick_started 本身也一行(request message)。
            out.push(stack_event_line(e));
            continue;
        }
        if e.event_type == "tool_call" || e.event_type == "tool_result" {
            out.push(tool_call_badge_line(e));
        } else if e.event_type == "tick_completed" {
            // response 用 md 渲染(多行)。
            let md_lines = md_response_lines(e);
            if md_lines.is_empty() {
                out.push(stack_event_line(e));
            } else {
                out.extend(md_lines);
            }
        } else {
            out.push(stack_event_line(e));
        }
    }
    // 无任何 turn_started 事件:按原 flat 渲染(兜底)。
    if out.is_empty() {
        for e in evs {
            out.push(stack_event_line(e));
        }
    }
    out
}
