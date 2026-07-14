//! md 渲染:pulldown-cmark 0.13 + 手写 Event→Style 映射(零 ratatui 版本耦合)。
//!
//! 候选 crate(tui-markdown 0.3.8 / markdown-tui-explorer / ratkit)全锁 ratatui ≥0.29:
//! tui-markdown runtime 依赖 ratatui-core 0.1,from_str() 返回 ratatui_core::text::Text,
//! 与本项目 ratatui 0.28 的 ratatui::text::Text 跨 crate 不兼容,Paragraph::new 拒绝
//! (与 ratatui-interact 0.30-lock 同源,更隐蔽)。且 tui-markdown 不支持表格/链接/图片。
//! pulldown-cmark 是纯 CommonMark 解析器,零 ratatui 耦合,覆盖全部元素(含表格)。
//!
//! 0.13 陷阱:Tag/TagEnd 拆分(旧 0.11 示例 Event::End(Tag::Heading) 不编译);
//! CowStr<'a> 借用输入,出 Text<'static> 须 .into_owned()。
//! 表格 defer(需 +30 行状态机收集 TableHead/Row/Cell 对齐,本版覆盖其余元素)。

#![allow(dead_code)]

use pulldown_cmark::{Event, HeadingLevel, Options, Parser, Tag, TagEnd};
use ratatui::{
    style::{Color, Modifier, Style},
    text::{Line, Span, Text},
};

/// markdown 字符串 → ratatui Text<'static>(标题/粗/斜/删除线/代码/列表/引用/链接/段落)。
pub fn md_to_text(md: &str) -> Text<'static> {
    let mut opts = Options::empty();
    opts.insert(Options::ENABLE_TABLES);
    opts.insert(Options::ENABLE_STRIKETHROUGH);

    let mut lines: Vec<Line<'static>> = vec![];
    let mut cur: Vec<Span<'static>> = vec![];
    let mut style = Style::new();
    // link fg 保存/恢复:扁平 style 的 fg 是 Option<Color> 单值覆盖,嵌套(heading 内 link)
    // 时 End 若 fg(Reset) 会丢外层 heading fg → 用栈存 field 前值,End 恢复。
    let mut fg_stack: Vec<Option<Color>> = vec![];
    let mut in_quote = false; // blockquote 续行(多段/SoftBreak)补 │ 前缀

    for ev in Parser::new_ext(md, opts) {
        match ev {
            Event::Start(Tag::Heading { level, .. }) => {
                style = style.add_modifier(Modifier::BOLD).fg(heading_color(level));
            }
            Event::Start(Tag::Strong) => style = style.add_modifier(Modifier::BOLD),
            Event::Start(Tag::Emphasis) => style = style.add_modifier(Modifier::ITALIC),
            Event::Start(Tag::Strikethrough) => style = style.add_modifier(Modifier::CROSSED_OUT),
            Event::Start(Tag::CodeBlock(_)) => style = style.bg(Color::DarkGray),
            Event::Start(Tag::Item) => cur.push(Span::raw("• ")),
            Event::Start(Tag::BlockQuote(_)) => {
                in_quote = true;
                cur.push(Span::styled("│ ", Style::new().fg(Color::DarkGray)))
            }
            Event::Start(Tag::Link { .. }) => {
                fg_stack.push(style.fg);
                style = style.fg(Color::Cyan).add_modifier(Modifier::UNDERLINED);
            }
            Event::Start(Tag::Paragraph) | Event::Start(Tag::List(_)) => {}
            Event::Text(t) => cur.push(Span::styled(t.to_string(), style)),
            Event::Code(t) => cur.push(Span::styled(t.to_string(), style.bg(Color::DarkGray))),
            Event::SoftBreak | Event::HardBreak => {
                flush_cur(&mut cur, &mut lines);
                if in_quote {
                    cur.push(Span::styled("│ ", Style::new().fg(Color::DarkGray)));
                }
            }
            Event::End(TagEnd::Heading(_)) => {
                style = Style::new();
                flush_cur(&mut cur, &mut lines);
            }
            Event::End(TagEnd::Paragraph) => flush_cur(&mut cur, &mut lines),
            Event::End(TagEnd::Item) => flush_cur(&mut cur, &mut lines),
            Event::End(TagEnd::CodeBlock) => {
                style = Style::new();
                flush_cur(&mut cur, &mut lines);
            }
            Event::End(TagEnd::Strong) => style = style.remove_modifier(Modifier::BOLD),
            Event::End(TagEnd::Emphasis) => style = style.remove_modifier(Modifier::ITALIC),
            Event::End(TagEnd::Strikethrough) => style = style.remove_modifier(Modifier::CROSSED_OUT),
            Event::End(TagEnd::BlockQuote(_)) => {
                in_quote = false;
                flush_cur(&mut cur, &mut lines);
            }
            Event::End(TagEnd::Link) => {
                if let Some(prev) = fg_stack.pop() {
                    style.fg = prev;
                }
                style = style.remove_modifier(Modifier::UNDERLINED);
            }
            _ => {}
        }
    }
    flush_cur(&mut cur, &mut lines);
    Text::from(lines)
}

fn flush_cur(cur: &mut Vec<Span<'static>>, lines: &mut Vec<Line<'static>>) {
    if !cur.is_empty() {
        lines.push(Line::from(std::mem::take(cur)));
    }
}

fn heading_color(level: HeadingLevel) -> Color {
    match level {
        HeadingLevel::H1 => Color::Yellow,
        HeadingLevel::H2 => Color::Cyan,
        HeadingLevel::H3 => Color::Green,
        HeadingLevel::H4 => Color::Magenta,
        HeadingLevel::H5 => Color::Blue,
        HeadingLevel::H6 => Color::DarkGray,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn md_parses_heading_para_list() {
        let text = md_to_text("# Title\n\nplain text here\n\n- item one\n- item two\n");
        assert!(text.lines.len() >= 3, "produces heading+para+items: {}", text.lines.len());
        let joined: String = text
            .lines
            .iter()
            .flat_map(|l| l.spans.iter())
            .map(|s| s.content.to_string())
            .collect::<Vec<_>>()
            .join("");
        assert!(joined.contains("Title"), "heading text: {}", joined);
        assert!(joined.contains("item one"), "list item: {}", joined);
        assert!(joined.contains("• "), "list bullet: {}", joined);
    }

    #[test]
    fn md_bold_and_code() {
        let text = md_to_text("**bold** and `code`");
        let joined: String = text
            .lines
            .iter()
            .flat_map(|l| l.spans.iter())
            .map(|s| s.content.to_string())
            .collect::<Vec<_>>()
            .join("");
        assert!(joined.contains("bold"), "{}", joined);
        assert!(joined.contains("code"), "{}", joined);
    }

    #[test]
    fn md_empty_safe() {
        let text = md_to_text("");
        assert!(text.lines.is_empty() || text.lines.iter().all(|l| l.spans.is_empty()));
    }

    #[test]
    fn md_blockquote_multiline_prefix() {
        // 回归:多行 blockquote 每行应有 │ 前缀(in_quote flag 在 SoftBreak 补前缀)。
        let text = md_to_text("> line one\n> line two\n> line three\n");
        let bars: usize = text
            .lines
            .iter()
            .flat_map(|l| l.spans.iter())
            .filter(|s| s.content.contains('│'))
            .count();
        assert_eq!(bars, 3, "each blockquote line has │ prefix: got {}", bars);
    }

    #[test]
    fn md_link_text_and_surrounding() {
        // 回归:link 文本 + 前后文本都在(fg_stack 恢复外层,结构不丢)。
        let text = md_to_text("see [the docs](https://x) for more");
        let joined: String = text
            .lines
            .iter()
            .flat_map(|l| l.spans.iter())
            .map(|s| s.content.to_string())
            .collect::<Vec<_>>()
            .join("");
        assert!(joined.contains("the docs"), "link text: {}", joined);
        assert!(joined.contains("see"), "text before link: {}", joined);
        assert!(joined.contains("for more"), "text after link: {}", joined);
    }
}
