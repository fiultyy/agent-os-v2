//! 位置指示器:百分比 + List 高亮 + breadcrumb(ratatui 0.28 内建,零依赖)。
//!
//! 三种位置指示(全内建):
//! - 百分比文字(percent_line):"3/12 25%" —— 不用 Gauge(Gauge::percent 超 100 panic)
//! - List 高亮(highlighted_list):highlight_symbol + REVERSED + HighlightSpacing::Always
//!   (Always 防选中时列表平移抖动;默认 WhenSelected 会抖)
//! - breadcrumb:无 crate(ratatui-breadcrumb 是 0.0.0 占位),Paragraph+Span 手拼
//!
//! 陷阱(调研核实):ListState::select_previous/last 用 usize::MAX 哨兵,渲染前 selected() 返回 MAX
//! → 先 render 再读;highlight_style 覆盖 item 自身 style(只加 modifier 更稳)。

#![allow(dead_code)]

use ratatui::{
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{HighlightSpacing, List, ListItem},
};

/// 选中位置百分比行:"3/12  25%"。selected=None → "—/12  0%"。total=0 安全(分母 max(1))。
pub fn percent_line(selected: Option<usize>, total: usize) -> Line<'static> {
    let denom = total.max(1);
    let (s, pct) = match selected {
        Some(i) => {
            // 防御:selected>=total(或 select_last 哨兵 MAX 渲染前调)→ clamp,免 >100% / 溢出。
            let i = i.min(total.saturating_sub(1));
            (format!("{}/{}", i + 1, denom), (i + 1) * 100 / denom)
        }
        None => (format!("—/{}", denom), 0),
    };
    Line::from(vec![
        Span::styled(s, Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
        Span::raw(format!("  {}%", pct)),
    ])
}

/// 带 highlight 的 List(选中符号 ▶ + Always spacing 防抖 + REVERSED 选中)。
/// state 在 render_stateful_widget(list, area, &mut state) 时传(此处不借)。
pub fn highlighted_list<'a>(items: Vec<ListItem<'a>>) -> List<'a> {
    List::new(items)
        .highlight_symbol("▶ ")
        .highlight_style(Style::default().add_modifier(Modifier::REVERSED))
        .highlight_spacing(HighlightSpacing::Always)
}

/// breadcrumb:"a › b › c",active 段高亮(Cyan+BOLD),其余 dim。parts 为空返回空行。
pub fn breadcrumb(parts: &[&str], active: usize) -> Line<'static> {
    let mut spans: Vec<Span> = vec![];
    for (i, p) in parts.iter().enumerate() {
        if i > 0 {
            spans.push(Span::styled(" › ", Style::default().fg(Color::DarkGray)));
        }
        if i == active {
            spans.push(Span::styled(
                (*p).to_string(),
                Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD),
            ));
        } else {
            spans.push(Span::styled((*p).to_string(), Style::default().fg(Color::DarkGray)));
        }
    }
    Line::from(spans)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percent_none_and_some() {
        let l = percent_line(None, 12);
        let joined: String = l.spans.iter().map(|s| s.content.to_string()).collect::<Vec<_>>().join("");
        assert!(joined.contains("—/12"), "{}", joined);
        assert!(joined.contains("0%"), "{}", joined);
        let l2 = percent_line(Some(2), 12);
        let j2: String = l2.spans.iter().map(|s| s.content.to_string()).collect::<Vec<_>>().join("");
        assert!(j2.contains("3/12"), "{}", j2);
        assert!(j2.contains("25%"), "{}", j2);
    }

    #[test]
    fn percent_zero_total_safe() {
        let l = percent_line(Some(0), 0);
        let joined: String = l.spans.iter().map(|s| s.content.to_string()).collect::<Vec<_>>().join("");
        assert!(joined.contains("1/1"), "total=0 不除零:{}", joined);
    }

    #[test]
    fn breadcrumb_spans() {
        let l = breadcrumb(&["a", "b", "c"], 1);
        // a, ›, b, ›, c = 5 spans
        assert_eq!(l.spans.len(), 5);
        let joined: String = l.spans.iter().map(|s| s.content.to_string()).collect::<Vec<_>>().join("");
        assert_eq!(joined, "a › b › c");
    }

    #[test]
    fn breadcrumb_empty() {
        let l = breadcrumb(&[], 0);
        assert!(l.spans.is_empty());
    }
}
