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
    use ratatui::layout::{Constraint, Direction, Layout};
    // 布局:[status(1)] [textarea wrap] [模式提示(1)] —— area 高度已由 render.rs desired_height 算好。
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(1), Constraint::Min(1), Constraint::Length(1)])
        .split(area);
    let [status_area, ta_area, mode_area] = [chunks[0], chunks[1], chunks[2]];

    // 状态行(orche●/session/last)
    f.render_widget(Paragraph::new(Line::from(status_spans(app))), status_area);

    // ❯ 前缀画在 ta_area 列 0;textarea 内容右移 prefix_w 对齐。
    // ponytail: prefix 固定 " ❯ "(空格+❯+空格=3 列),硬编码 prefix_w=3。
    const PREFIX_W: u16 = 3;
    f.render_widget(
        Paragraph::new(Span::styled(
            " ❯ ",
            Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD),
        )),
        Rect::new(ta_area.x, ta_area.y, PREFIX_W, 1),
    );
    // textarea 内容区:右移 PREFIX_W,宽度减(codex 式 wrap 渲染 + 精确光标)
    let ta_inner = Rect {
        x: ta_area.x + PREFIX_W,
        y: ta_area.y,
        width: ta_area.width.saturating_sub(PREFIX_W),
        height: ta_area.height,
    };
    let buf = f.buffer_mut();
    app.textarea.render(ta_inner, buf, &mut app.textarea_state);
    // 精确光标(bug3 根治):cursor_pos_with_state 算屏幕 cell → REVERSED
    if let Some((cx, cy)) = app.textarea.cursor_pos_with_state(ta_inner, &app.textarea_state) {
        if let Some(cell) = buf.cell_mut((cx, cy)) {
            cell.set_style(Style::default().add_modifier(Modifier::REVERSED));
        }
    }

    // 模式提示行
    let mode_hint = if app.insert_mode {
        "  [enter 发送 · ctrl+j 换行 · esc 退快捷键]"
    } else {
        "  [i 输入 · t/s/r/e/f/G/D/R 动作]"
    };
    f.render_widget(
        Paragraph::new(Span::styled(mode_hint, Style::default().fg(Color::DarkGray))),
        mode_area,
    );
}

/// TurnSeparator:turn 之间视觉分隔(── turn N ──,只显序号,不显内部 tick_id)。独立 fn(可复用)。
pub fn turn_separator_line(_tick_id: &str, idx: usize) -> Line<'static> {
    Line::from(Span::styled(
        format!("── turn {} ──", idx + 1),
        Style::default().fg(Color::DarkGray),
    ))
}

/// 通用事件行(Observe draw_stack 复用,逐事件平铺渲染)。
/// render_turn_stream 不用它;render.rs Observe tab 用。
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

/// 把 markdown 文本渲染成带缩进的 Lines(assistant cell 复用)。
fn md_indented_lines(response: &str, prefix_spans: &[Span<'static>]) -> Vec<Line<'static>> {
    if response.is_empty() {
        return vec![Line::from(prefix_spans.to_vec())];
    }
    let text = markdown::md_to_text(response);
    text.lines
        .into_iter()
        .enumerate()
        .map(|(i, line)| {
            let mut spans: Vec<Span<'static>> = if i == 0 {
                prefix_spans.to_vec()
            } else {
                vec![Span::raw("   ")] // 续行缩进 2 空格宽(3 char 视觉对齐前缀)
            };
            spans.extend(line.spans);
            Line::from(spans)
        })
        .collect()
}

/// IT6 codex cell 模型:把 cursor session 的事件流渲染成 Lines。
///
/// 不再逐事件渲染。按 tick_id 分组为 turn,每个 turn = 一组 cells:
/// - user cell:tick_started 的 request → 一行 `> request`(> Cyan bold)。
/// - tool cells:同 tick 内 tool_call + 紧随的 tool_result 配对合并为一行 `▸ tool_name result`(三角 DarkGray dim)。
/// - assistant cell:同 tick 的 token_delta 累积,或优先用 tick_completed.response;渲染为 md cell(• Magenta dim,首行带前缀,续行缩进)。
///   token_delta 绝不逐行渲染(避免碎片/乱序)。
/// - turn 间用空行分隔(不用 dash 线)。
///
/// 无 tick_started 事件时按 flat stack 兜底(逐事件 stack_event_line)。
pub fn render_turn_stream(evs: &[ObserveEvent]) -> Vec<Line<'static>> {
    use std::collections::HashMap as Map;

    // 1) 按 tick_id 分组(保持首次出现顺序)。无 tick_id 的事件并入 "" 桶兜底。
    let mut order: Vec<String> = Vec::new();
    let mut buckets: Map<String, Vec<&ObserveEvent>> = Map::new();
    for e in evs {
        let key = if e.tick_id.is_empty() { String::new() } else { e.tick_id.clone() };
        if !buckets.contains_key(&key) {
            order.push(key.clone());
        }
        buckets.entry(key).or_default().push(e);
    }

    // 全无 tick_id 桶:flat 兜底。
    let any_tick = evs.iter().any(|e| !e.tick_id.is_empty());
    if !any_tick {
        return evs.iter().map(stack_event_line).collect();
    }

    let mut out: Vec<Line> = Vec::new();
    for (i, tick) in order.iter().enumerate() {
        let group = match buckets.get(tick) {
            Some(g) => g,
            None => continue,
        };
        // turn 间纯空行分隔(首个 turn 前不加)。不加 turn 标识,干净。
        if i > 0 {
            out.push(Line::raw(""));
        }

        // 2) user cell:首个 tick_started 的 request。
        let mut user_rendered = false;
        // 3) tool 配对:tool_call + 紧随的同 tick tool_result 合并为一行。
        // 4) assistant cell:token_delta 累积 / tick_completed.response(优先)。
        // 收集 assistant 文本(tick_completed.response 优先,否则累积 token_delta)。
        let mut assistant_text = String::new();
        let mut assistant_from_response = false;
        for e in group {
            if e.event_type == "tick_completed" {
                let resp = fmt_val(&e.data, "response");
                if !resp.is_empty() {
                    assistant_text = resp;
                    assistant_from_response = true;
                }
            }
        }
        // 无 response 才累积 token_delta(流式中途或无 finalize)。
        if !assistant_from_response {
            for e in group {
                if e.event_type == "token_delta" {
                    assistant_text.push_str(&fmt_val(&e.data, "delta_text"));
                }
            }
        }

        // 渲染 user + tool(按事件顺序;tool_call/result 配对合并)。
        let mut j = 0;
        while j < group.len() {
            let e = group[j];
            if e.event_type == "tick_started" && !user_rendered {
                let request = fmt_val(&e.data, "request");
                out.push(Line::from(vec![
                    Span::styled("> ", Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)),
                    Span::styled(trunc(&request, 120), Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)),
                ]));
                user_rendered = true;
                j += 1;
                continue;
            }
            if e.event_type == "tool_call" {
                let tool_name = fmt_val(&e.data, "tool_name");
                // 找紧随的 tool_result(同 tick)。
                let mut result = String::new();
                let mut k = j + 1;
                while k < group.len() {
                    if group[k].event_type == "tool_result" {
                        result = fmt_val(&group[k].data, "result");
                        break;
                    }
                    if group[k].event_type == "tool_call" {
                        break; // 下一个 call,本 call 无 result
                    }
                    k += 1;
                }
                let body = if result.is_empty() {
                    tool_name.clone()
                } else {
                    format!("{} → {}", tool_name, result)
                };
                out.push(Line::from(vec![
                    Span::styled("▸ ", Style::default().fg(Color::DarkGray).add_modifier(Modifier::DIM)),
                    Span::styled(trunc(&body, 100), Style::default().fg(Color::DarkGray).add_modifier(Modifier::DIM)),
                ]));
                j = if result.is_empty() { j + 1 } else { k + 1 };
                continue;
            }
            if e.event_type == "tool_result" {
                // 孤立 result(无配对 call):单独行。
                let result = fmt_val(&e.data, "result");
                out.push(Line::from(vec![
                    Span::styled("◂ ", Style::default().fg(Color::DarkGray).add_modifier(Modifier::DIM)),
                    Span::styled(trunc(&result, 100), Style::default().fg(Color::DarkGray).add_modifier(Modifier::DIM)),
                ]));
                j += 1;
                continue;
            }
            j += 1;
        }

        // 5) assistant cell(若有文本)。
        if !assistant_text.is_empty() {
            let prefix: Vec<Span<'static>> = vec![
                Span::styled("• ", Style::default().fg(Color::Magenta).add_modifier(Modifier::DIM)),
            ];
            out.extend(md_indented_lines(&assistant_text, &prefix));
        }
    }

    if out.is_empty() {
        for e in evs {
            out.push(stack_event_line(e));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::ObserveEvent;
    use std::collections::HashMap;

    fn ev(t: &str, tick: &str, kv: &[(&str, &str)]) -> ObserveEvent {
        let mut d = HashMap::new();
        for (k, v) in kv {
            d.insert((*k).to_string(), serde_json::Value::String((*v).to_string()));
        }
        ObserveEvent {
            event_type: t.to_string(),
            tick_id: tick.to_string(),
            harness_id: "h".to_string(),
            data: d,
            event_id: String::new(),
        }
    }

    /// 把 Line 的所有 spans 拼成一个 String(忽略样式)。
    fn spans_str(l: &Line) -> String {
        l.spans.iter().map(|s| s.content.as_ref()).collect::<String>()
    }

    /// IT6 core:token_delta 必须合并为单个 assistant cell,绝不逐行渲染。
    #[test]
    fn token_delta_merged_into_one_assistant_cell() {
        let evs = vec![
            ev("tick_started", "t1", &[("request", "hi")]),
            ev("token_delta", "t1", &[("delta_text", "Hel")]),
            ev("token_delta", "t1", &[("delta_text", "lo")]),
            ev("token_delta", "t1", &[("delta_text", " world")]),
        ];
        let out = render_turn_stream(&evs);
        let joined = out.iter().map(spans_str).collect::<Vec<_>>().join("\n");
        // 合并后的完整文本出现在某行(可能被 md 包裹,但必须连续)。
        assert!(joined.contains("Hello world"), "token_delta 未合并: {}", joined);
        // 绝不能出现碎片行。
        assert!(!joined.contains("δ"), "token_delta 被逐行渲染(stack_event_line): {}", joined);
        // 仅 1 个 assistant cell(• 前缀出现 1 次)。
        let dots = joined.matches('•').count();
        assert_eq!(dots, 1, "期望 1 个 assistant cell,实际 {}: {}", dots, joined);
    }

    /// tick_completed.response 优先于 token_delta 累积。
    #[test]
    fn response_preferred_over_delta_accumulation() {
        let evs = vec![
            ev("tick_started", "t1", &[("request", "q")]),
            ev("token_delta", "t1", &[("delta_text", "PARTIAL")]),
            ev("tick_completed", "t1", &[("response", "FINAL")]),
        ];
        let out = render_turn_stream(&evs);
        let joined = out.iter().map(spans_str).collect::<Vec<_>>().join("\n");
        assert!(joined.contains("FINAL"), "缺 response: {}", joined);
        assert!(!joined.contains("PARTIAL"), "response 未优先,delta 残留: {}", joined);
    }

    /// tool_call + tool_result 配对合并为一行(三角前缀)。
    #[test]
    fn tool_call_result_paired_into_one_line() {
        let evs = vec![
            ev("tick_started", "t1", &[("request", "run it")]),
            ev("tool_call", "t1", &[("tool_name", "bash")]),
            ev("tool_result", "t1", &[("result", "ok")]),
            ev("tick_completed", "t1", &[("response", "done")]),
        ];
        let out = render_turn_stream(&evs);
        let joined = out.iter().map(spans_str).collect::<Vec<_>>().join("\n");
        // 合并行:tool_name → result。
        assert!(joined.contains("bash") && joined.contains("ok"), "tool 未合并: {}", joined);
        // 三角前缀(▸)出现 1 次。
        let tri = joined.matches('▸').count();
        assert_eq!(tri, 1, "期望 1 个 tool cell,实际 {}: {}", tri, joined);
    }

    /// turn 间用空行分隔,不用 dash 线作主分隔(仍可有轻量 tick 头)。
    #[test]
    fn turns_separated_by_blank_line() {
        let evs = vec![
            ev("tick_started", "t1", &[("request", "a")]),
            ev("tick_completed", "t1", &[("response", "A")]),
            ev("tick_started", "t2", &[("request", "b")]),
            ev("tick_completed", "t2", &[("response", "B")]),
        ];
        let out = render_turn_stream(&evs);
        let joined = out.iter().map(spans_str).collect::<Vec<_>>().join("\n");
        // 两 turn 之间有空行。
        assert!(joined.contains("\n\n"), "turn 间无空行: {}", joined);
        // 两 turn 内容都在。
        assert!(joined.contains("a") && joined.contains("B"), "内容丢失: {}", joined);
    }

    /// user 前缀 > (Cyan bold)。
    #[test]
    fn user_prefix_greater_than() {
        let evs = vec![ev("tick_started", "t1", &[("request", "hello")])];
        let out = render_turn_stream(&evs);
        let joined = out.iter().map(spans_str).collect::<Vec<_>>().join("\n");
        assert!(joined.contains("> hello"), "user 前缀不符: {}", joined);
    }

    /// 无 tick_id 事件:flat 兜底(逐事件 stack_event_line)。
    #[test]
    fn no_tick_falls_back_to_flat_stack() {
        let evs = vec![
            ev("tick_started", "", &[("request", "x")]),
            ev("tick_completed", "", &[("response", "y")]),
        ];
        let out = render_turn_stream(&evs);
        // flat:至少 2 行(每事件一行),且无空行分隔。
        assert!(out.len() >= 2, "flat 兜底行数不足: {}", out.len());
    }
}

