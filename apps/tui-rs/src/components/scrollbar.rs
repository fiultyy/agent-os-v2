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
    widgets::{Block, Borders, Paragraph, Scrollbar, ScrollbarOrientation, ScrollbarState, Wrap},
    Frame,
};

/// 可滚动视图:内容 + 偏移 + wrap/trim。render 画 Paragraph(scroll) + Scrollbar。
pub struct ScrollView {
    pub lines: Vec<ratatui::text::Line<'static>>,
    pub offset: usize,
    pub wrap: bool,
    pub trim: bool,
    /// true=Block 全边框(默认);false=无边框,内容直铺 area(ADR-5:chat 无边框省 2 行/列)。
    pub bordered: bool,
}

impl ScrollView {
    pub fn new(lines: Vec<ratatui::text::Line<'static>>) -> Self {
        Self { lines, offset: 0, wrap: true, trim: false, bordered: true }
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

    /// 渲染:bordered 时画 Block 全边框 + Paragraph(scroll+wrap) + 右侧 Scrollbar;
    /// 否则不画 Block,inner=area 直接渲染 Paragraph + Scrollbar。
    pub fn render(&mut self, f: &mut Frame, area: Rect) {
        let inner = if self.bordered {
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
}
