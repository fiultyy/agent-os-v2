//! Footer 单行状态栏(codex 式,ADR-1 第 3 批控件)。
//!
//! 一行:左边 hints(快捷键提示," · " join)+ 右边 status spans(orche●/session/last)。
//! 窄宽(area.width 小)时从右向左省略 hints(优先保 status)。无边框,整体 bg Color::Black。

#![allow(dead_code)]

use ratatui::{
    layout::{Alignment, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::Paragraph,
    Frame,
};

/// 渲染 footer 单行。
///
/// - `hints`:快捷键提示切片(左)," · " join。
/// - `status`:状态 spans(右,如 control::status_spans(app))。
/// - 窄宽:从右向左省略 hints(优先保 status 全显)。
///
/// ponytail: 窄宽省略用 width 估算(每个 hint 字符数 + " · ");精确像素宽度需 unicode-width,
/// 当前用 .chars().count() 近似(ASCII hint 够用,CJK 略偏宽——保守省略更多,不溢出)。
pub fn render(f: &mut Frame, area: Rect, hints: &[&str], status: Vec<Span>) {
    if area.height == 0 {
        return;
    }

    // status 宽度估算(char count)。
    let status_width: usize = status.iter().map(|s| s.content.chars().count()).sum();

    // hints 用 " · " join,从右向左省略直到 hints + status + 1 间隔 <= area.width。
    let mut visible_hints: Vec<&str> = hints.to_vec();
    let sep = " · ";
    loop {
        let hint_str = visible_hints.join(sep);
        let hint_w = hint_str.chars().count();
        let need = hint_w + status_width + 2; // +2 间隔缓冲
        if need <= area.width as usize || visible_hints.is_empty() {
            break;
        }
        visible_hints.pop(); // 从右(最新/最不重要)省略
    }

    let hint_str = visible_hints.join(sep);
    let hint_w = hint_str.chars().count();
    let pad = (area.width as usize).saturating_sub(hint_w + status_width);
    let mut spans: Vec<Span> = vec![Span::styled(
        hint_str,
        Style::default().fg(Color::DarkGray),
    )];
    if pad > 0 {
        spans.push(Span::raw(" ".repeat(pad)));
    }
    spans.extend(status);

    let _ = pad; // suppress unused when status empty

    let line = Line::from(spans).style(Style::default().bg(Color::Black));
    let para = Paragraph::new(line)
        .style(Style::default().bg(Color::Black))
        .alignment(Alignment::Left);
    f.render_widget(para, area);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_hints_status_only() {
        // render 不 panic 即可(TestBackend)。
        let backend = ratatui::backend::TestBackend::new(40, 1);
        let mut terminal = ratatui::Terminal::new(backend).unwrap();
        terminal
            .draw(|f| {
                render(f, Rect::new(0, 0, 40, 1), &[], vec![Span::raw(" ● online")]);
            })
            .unwrap();
    }

    #[test]
    fn narrow_area_omits_hints() {
        // 极窄宽度:hints 应被省略到空,不 panic,不溢出。
        let backend = ratatui::backend::TestBackend::new(5, 1);
        let mut terminal = ratatui::Terminal::new(backend).unwrap();
        terminal
            .draw(|f| {
                render(
                    f,
                    Rect::new(0, 0, 5, 1),
                    &["enter 发送", "esc 退快捷键", "t trigger"],
                    vec![Span::raw(" ● ok")],
                );
            })
            .unwrap();
    }
}
