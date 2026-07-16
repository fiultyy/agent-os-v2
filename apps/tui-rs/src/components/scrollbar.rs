//! 滚动卷轴 + 自动换行:可复用 ScrollView(ratatui 0.28 内建 Scrollbar + Paragraph::scroll + Wrap)。
//!
//! Scrollbar 是纯指示器(0.22+ 内建,0.28-0.30 API 稳定),不驱动滚动;内容滚动靠
//! Paragraph::scroll((y,x))(元组是 (y,x)!),ScrollbarState 镜像 position 给指示器。
//! 自动换行:Paragraph::wrap(Wrap{trim}) 内建,CJK 双宽按 grapheme 断不切字(0.28 无 cjk feature,
//! unicode-width 默认)。trim=true 裁 wrap 续行**行首**空白(非行尾,源码 reflow.rs:40 证实)。
//!
//! ponytail: content_length 用未 wrap 行数(lines.len())近似——wrap 后实际行数更多,
//! 精确需 unstable-rendered-line-info feature;scrollbar 满程可能不到底,foundation 够用。

#![allow(dead_code)]

use ratatui::{
    layout::Rect,
    style::{Color, Modifier, Style},
    text::Span,
    widgets::{Block, Borders, Paragraph, Scrollbar, ScrollbarOrientation, ScrollbarState, Wrap},
    Frame,
};

/// 边框模式:None=无边框直铺;Top=顶部描边+bg(与 region_block 视觉一致);Full=全边框。
/// IT4:chat 从 bordered(bool) 升级为 border_mode,与 region_block 的 TOP+bg 描边统一。
pub enum BorderMode {
    None,
    Top,
    Full,
}

/// 可滚动视图:内容 + 偏移 + wrap/trim。render 画 Paragraph(scroll) + Scrollbar。
pub struct ScrollView {
    pub lines: Vec<ratatui::text::Line<'static>>,
    pub offset: usize,
    pub wrap: bool,
    pub trim: bool,
    /// true=Block 全边框;false=无边框,内容直铺 area(ADR-5:chat 无边框省 2 行/列)。
    /// IT4:border_mode 优先;bordered 保留作回退(既有调用 / widgets_demo)。
    pub bordered: bool,
    /// IT4 新:None/Top/Full 三态。None 时回退到 bordered 语义(向后兼容)。
    pub border_mode: Option<BorderMode>,
    /// Top/Full 模式的标题(可选)。
    pub title: Option<String>,
}

impl ScrollView {
    pub fn new(lines: Vec<ratatui::text::Line<'static>>) -> Self {
        Self { lines, offset: 0, wrap: true, trim: false, bordered: true, border_mode: None, title: None }
    }
    pub fn wrap(mut self, w: bool) -> Self {
        self.wrap = w;
        self
    }
    pub fn trim(mut self, t: bool) -> Self {
        self.trim = t;
        self
    }
    pub fn bordered(mut self, b: bool) -> Self {
        self.bordered = b;
        self
    }
    /// IT4:设 Top/Full/None 边框模式(优先于 bordered)。Top = 顶线 + bg + 可选标题,
    /// 与 render::region_block 视觉一致(Black bg + DarkGray 顶线 + Cyan bold 标题)。
    pub fn border_mode(mut self, m: BorderMode) -> Self {
        self.border_mode = Some(m);
        self
    }
    pub fn title<S: Into<String>>(mut self, t: S) -> Self {
        self.title = Some(t.into());
        self
    }
    pub fn set_content(&mut self, lines: Vec<ratatui::text::Line<'static>>) {
        self.lines = lines;
        // 不重置 offset:set_content 每帧由 draw_* 调用,重置会让滚轮刚改的偏移立刻归零(=滚不动)。
        // clamp 到新 total,内容缩短时不越界。
        let max = self.total().saturating_sub(1);
        self.offset = self.offset.min(max);
    }
    /// content_length(未 wrap 行数近似)。
    fn total(&self) -> usize {
        self.lines.len()
    }
    pub fn scroll_down(&mut self, n: usize) {
        let max = self.total().saturating_sub(1);
        self.offset = (self.offset + n).min(max);
    }
    pub fn scroll_up(&mut self, n: usize) {
        self.offset = self.offset.saturating_sub(n);
    }
    pub fn page_down(&mut self, viewport: usize) {
        self.scroll_down(viewport.max(1));
    }
    pub fn page_up(&mut self, viewport: usize) {
        self.scroll_up(viewport.max(1));
    }
    /// IT4:滚到底部(offset=total-1)。do_turn 发送后 + tail 跟随调用。
    pub fn scroll_to_bottom(&mut self) {
        self.offset = self.total().saturating_sub(1);
    }

    /// 渲染:按 border_mode(None 回退 bordered)画 Block(Top=顶线+bg+标题 / Full=全边框 /
    /// None=无边框直铺)+ Paragraph(scroll+wrap) + 右侧 Scrollbar。
    pub fn render(&mut self, f: &mut Frame, area: Rect) {
        // mode=None 回退 bordered 兼容(既有 widgets_demo / observe_scroll.bordered=false)。
        let use_top = matches!(self.border_mode, Some(BorderMode::Top));
        let use_full = matches!(self.border_mode, Some(BorderMode::Full))
            || (self.border_mode.is_none() && self.bordered);
        let inner = if use_top {
            // 与 render::region_block 同款:DarkGray 顶线 + Black bg + Cyan bold 标题。
            let mut block = Block::default()
                .borders(Borders::TOP)
                .border_style(Style::default().fg(Color::DarkGray))
                .style(Style::default().bg(Color::Black));
            if let Some(t) = &self.title {
                block = block.title(ratatui::text::Line::from(Span::styled(
                    t.clone(),
                    Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD),
                )));
            }
            let inner = block.inner(area);
            f.render_widget(block, area);
            inner
        } else if use_full {
            let block = Block::default().borders(Borders::ALL);
            let inner = block.inner(area);
            f.render_widget(block, area);
            inner
        } else {
            area
        };

        // >65535 行时 offset as u16 截断(65537→1)→ clamp u16::MAX(ScrollbarState 仍保留 usize position)。
        let scroll_y = self.offset.min(u16::MAX as usize) as u16;
        let mut para = Paragraph::new(self.lines.clone()).scroll((scroll_y, 0));
        if self.wrap {
            para = para.wrap(Wrap { trim: self.trim });
        }
        f.render_widget(para, inner);

        // Scrollbar(纯指示器):position 镜像 offset,content_length 近似。
        // viewport_content_length 省略:ratatui fallback 用 track 高度(= inner.height),与显式传等价。
        let mut st = ScrollbarState::new(self.total()).position(self.offset);
        let sb = Scrollbar::new(ScrollbarOrientation::VerticalRight)
            .begin_symbol(Some("↑"))
            .end_symbol(Some("↓"));
        f.render_stateful_widget(sb, inner, &mut st);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ratatui::text::Line;

    #[test]
    fn scroll_clamps_to_total() {
        let mut v = ScrollView::new(vec![Line::from("a"); 5]);
        v.scroll_down(100);
        assert_eq!(v.offset, 4, "clamps to total-1");
        v.scroll_up(10);
        assert_eq!(v.offset, 0, "clamps to 0");
    }

    #[test]
    fn empty_safe() {
        let mut v = ScrollView::new(vec![]);
        v.scroll_down(10);
        v.scroll_up(10);
        assert_eq!(v.offset, 0);
        assert_eq!(v.total(), 0);
    }

    #[test]
    fn page_scroll() {
        let mut v = ScrollView::new(vec![Line::from("a"); 20]);
        v.page_down(5);
        assert_eq!(v.offset, 5);
        v.page_up(3);
        assert_eq!(v.offset, 2);
    }

    /// set_content 不再重置 offset(draw_* 每帧调,重置会让滚轮失效)。仅 clamp 到新 total。
    #[test]
    fn set_content_preserves_offset() {
        let mut v = ScrollView::new(vec![Line::from("a"); 20]);
        v.scroll_down(5);
        assert_eq!(v.offset, 5);
        v.set_content(vec![Line::from("a"); 20]); // 模拟 draw_* 每帧重灌同样内容
        assert_eq!(v.offset, 5, "offset must survive per-frame set_content");
        // 内容缩短时 clamp,不越界。
        v.set_content(vec![Line::from("a"); 3]);
        assert_eq!(v.offset, 2, "offset clamps to new total-1");
    }

    /// IT4:scroll_to_bottom 锁到 total-1(do_turn 发送后 + tail 跟随调用)。
    #[test]
    fn scroll_to_bottom_locks_max() {
        let mut v = ScrollView::new(vec![Line::from("a"); 30]);
        v.scroll_down(3);
        assert_eq!(v.offset, 3);
        v.scroll_to_bottom();
        assert_eq!(v.offset, 29, "locks to total-1");
        // 空内容安全:total=0 → saturating_sub → 0。
        let mut e = ScrollView::new(vec![]);
        e.scroll_to_bottom();
        assert_eq!(e.offset, 0);
    }

    /// IT4:border_mode/title builder 链式设置。
    #[test]
    fn border_mode_title_builders() {
        let v = ScrollView::new(vec![])
            .border_mode(BorderMode::Top)
            .title(" 对话 ");
        assert!(matches!(v.border_mode, Some(BorderMode::Top)));
        assert_eq!(v.title.as_deref(), Some(" 对话 "));
        // None 回退 bordered=false → 直铺;bordered=true → 全边框。
        let n = ScrollView::new(vec![]).bordered(false);
        assert!(n.border_mode.is_none() && !n.bordered);
    }
}
