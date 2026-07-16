//! Control 区 Cursor 式子组件(ADR-1/ADR-2/ADR-7 第 3 批控件)。
//!
//! 独立 render fn,可复用(后续 Observe/web GUI 消费)。draw_control 组合调用。
//! - InputBar:turn_msg 输入显示(ADR-1:textarea 无按钮,动作走键盘)。
//! - status_spans:footer 状态(orche●/session/last),ADR-2 取代独立 StatusBar。
//! - TurnSeparator:turn 之间视觉分隔(tick_started 开新 turn 块)。
//! - chat turn 行样式:user/assistant 前缀 + 工具调用弱化。
//! - turn stream markdown:tick_completed response 经 md_to_text 渲染。

#![allow(dead_code)]

use crate::components::markdown;
use crate::state::{fmt_val, trunc, App, ObserveEvent};
use ratatui::{
    layout::Rect,
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::Paragraph,
    Frame,
};

/// Control 按钮(label, id, base_color, action_name)。
/// ADR-1:按钮已从 InputBar 删除(动作走键盘)。本常量保留供 key 路由参考(id→action 映射)。
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

/// ADR-2 footer 状态 spans:orche●(online/离线) · session(cursor sid) · last(turn_status)。
/// 单行 Vec<Span>,供 Footer::render 与按键提示同行(宽度自适应折叠由 Footer 负责)。
/// 逻辑与 render_status_bar 同源,但输出 spans 而非独立 2 行 widget。
pub fn status_spans(app: &App) -> Vec<Span<'static>> {
    let orche = if app.orche_online {
        Span::styled(" ●", Style::default().fg(Color::Green).add_modifier(Modifier::BOLD))
    } else {
        Span::styled(" ⚠offline", Style::default().fg(Color::Red).add_modifier(Modifier::BOLD))
    };
    let cur_sid = app
        .flat
        .get(app.cursor)
        .map(|s| trunc(&s.session_id, 16))
        .unwrap_or_else(|| "(无)".to_string());
    let status_disp = app
        .turn_status
        .clone()
        .unwrap_or_else(|| "(未触发)".to_string());
    vec![
        orche,
        Span::styled("  session ", Style::default().fg(Color::DarkGray)),
        Span::styled(cur_sid, Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
        Span::styled("  last ", Style::default().fg(Color::DarkGray)),
        Span::styled(trunc(&status_disp, 40), Style::default().fg(Color::White)),
    ]
}

/// InputBar(IT6-③:整合 StatusBar):底部输入区多行 = [状态行] + [输入行] + [模式提示行]。
/// 原 render_status_bar 的独立 2 行 status 已并入此处顶行,右区无独立 statusbar 省空间。
/// area = region_block(" 输入 · message ") 的 inner(已剥顶线);行 0=状态、行 1=输入、行 2=模式。
pub fn render_input_bar(f: &mut Frame, area: Rect, app: &mut App) {
    // 行 0:状态(orche●/session/last),复用 status_spans 单行。
    let status_line = Line::from(status_spans(app));
    // 行 1:输入(❯ + turn_msg + ▌ 光标)。
    let msg_line = Line::from(vec![
        Span::styled(" ❯ ", Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)),
        Span::styled(app.turn_msg.clone(), Style::default().fg(Color::White)),
        Span::styled("▌", Style::default().fg(Color::Cyan).add_modifier(Modifier::SLOW_BLINK)),
    ]);
    // 行 2:模式提示。
    let mode_hint = if app.insert_mode {
        "  [enter 发送 · esc 退快捷键]"
    } else {
        "  [i 输入 · t/s/r/e/f/G/D/R 动作]"
    };
    let mode_line = Line::from(Span::styled(mode_hint, Style::default().fg(Color::DarkGray)));
    f.render_widget(Paragraph::new(vec![status_line, msg_line, mode_line]), area);
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

/// chat turn 行样式:Cursor 式 user/assistant 前缀 + 工具调用弱化(ADR:Control 对话卷轴)。
/// - tick_started request → user 行(USER ▸)。
/// - tick_completed response → assistant 行(ASSISTANT ▸ + md 多行,首行带前缀)。
/// - tool_call/tool_result → 弱化(DarkGray + 缩进,不抢主对话视觉)。

/// user turn 行:tick_started 的 request message 渲染为 `❯ USER ▸ request`。
pub fn chat_user_line(e: &ObserveEvent) -> Line<'static> {
    let request = fmt_val(&e.data, "request");
    Line::from(vec![
        Span::styled("❯ ", Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)),
        Span::styled("USER ", Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)),
        Span::styled("▸ ", Style::default().fg(Color::DarkGray)),
        Span::raw(trunc(&request, 80)),
    ])
}

/// assistant turn 行:tick_completed response 渲染为 md 多行,首行带 `ASSISTANT ▸` 前缀。
/// 后续行缩进(对齐 md_response_lines 的 3 空格缩进)。
pub fn chat_assistant_lines(e: &ObserveEvent) -> Vec<Line<'static>> {
    let response = fmt_val(&e.data, "response");
    let prefix_spans: Vec<Span<'static>> = vec![
        Span::styled("✦ ", Style::default().fg(Color::Magenta).add_modifier(Modifier::BOLD)),
        Span::styled("ASSISTANT ", Style::default().fg(Color::Magenta).add_modifier(Modifier::BOLD)),
        Span::styled("▸ ", Style::default().fg(Color::DarkGray)),
    ];
    if response.is_empty() {
        return vec![Line::from(prefix_spans)];
    }
    let text = markdown::md_to_text(&response);
    let mut out: Vec<Line> = Vec::with_capacity(text.lines.len());
    for (i, line) in text.lines.into_iter().enumerate() {
        let mut spans: Vec<Span<'static>> = if i == 0 {
            prefix_spans.clone()
        } else {
            vec![Span::raw("   ")] // 后续行缩进,对齐 md_response_lines
        };
        spans.extend(line.spans);
        out.push(Line::from(spans));
    }
    out
}

/// 工具调用弱化行:tool_call/tool_result 用 DarkGray 缩进呈现(不抢主对话视觉)。
pub fn chat_tool_line(e: &ObserveEvent) -> Line<'static> {
    let (glyph, tag, body) = match e.event_type.as_str() {
        "tool_call" => ("⚒", "tool", fmt_val(&e.data, "tool_name")),
        "tool_result" => ("◷", "result", fmt_val(&e.data, "result")),
        _ => return stack_event_line(e),
    };
    Line::from(vec![
        Span::styled(
            format!("    {} {} ", glyph, tag),
            Style::default().fg(Color::DarkGray).add_modifier(Modifier::DIM),
        ),
        Span::styled(trunc(&body, 60), Style::default().fg(Color::DarkGray)),
    ])
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
