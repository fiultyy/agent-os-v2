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
    widgets::{Block, Borders, Paragraph, Scrollbar, ScrollbarOrientation, ScrollbarState},
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
    /// follow_tail:render 时自动追底(显最后一页);false=用户自由滚。
    pub follow_tail_flag: bool,
    /// 是否画右侧 Scrollbar 柱状指示器。false=不画(用 footer N/M 代位置)。
    pub show_scrollbar: bool,
    /// wrap 后 display 行缓存(key=width);set_content 内容变时失效。避免每帧 wrap_line。
    wrap_cache: Option<(usize, Vec<ratatui::text::Line<'static>>)>,
}

impl ScrollView {
    pub fn new(lines: Vec<ratatui::text::Line<'static>>) -> Self {
        Self { lines, offset: 0, wrap: true, trim: false, bordered: true, border_mode: None, title: None, follow_tail_flag: true, show_scrollbar: true, wrap_cache: None }
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
        // 内容同(每帧 render_turn_stream 缓存 clone 同内容)→ 不更新 lines + 保 wrap_cache;
        // 内容变 → 更新 + 失效 wrap_cache(render 重建)。避免每帧 wrap_line。
        if self.lines != lines {
            self.lines = lines;
            self.wrap_cache = None;
        }
    }
    /// content_length(未 wrap 行数近似;scroll 按键/scroll_to_bottom 用)。
    fn total(&self) -> usize {
        self.lines.len()
    }
    /// 按 width 字符流折行(unicode-width,保留 span style)。render 用同逻辑 slice 显示 →
    /// 行数精确,scroll 能到真底。ponytail: 不 word-break(英文词可拆);CJK 每字可断精确。
    /// 旧版用 ratatui Paragraph::Wrap 显示但自算行数,word-break 不一致 → 滚不到真底。
    fn wrap_line(line: &ratatui::text::Line<'static>, width: usize) -> Vec<ratatui::text::Line<'static>> {
        if width == 0 {
            return vec![line.clone()];
        }
        let mut out: Vec<ratatui::text::Line<'static>> = Vec::new();
        let mut cur_spans: Vec<ratatui::text::Span<'static>> = Vec::new();
        let mut cur_w: usize = 0;
        for span in &line.spans {
            let style = span.style;
            let mut chunk = String::new();
            for ch in span.content.chars() {
                let cw = unicode_width::UnicodeWidthChar::width(ch).unwrap_or(1);
                if cur_w + cw > width && (!chunk.is_empty() || !cur_spans.is_empty()) {
                    if !chunk.is_empty() {
                        cur_spans.push(ratatui::text::Span::styled(std::mem::take(&mut chunk), style));
                    }
                    out.push(ratatui::text::Line::from(std::mem::take(&mut cur_spans)));
                    cur_w = 0;
                }
                chunk.push(ch);
                cur_w += cw;
            }
            if !chunk.is_empty() {
                cur_spans.push(ratatui::text::Span::styled(chunk, style));
            }
        }
        out.push(ratatui::text::Line::from(cur_spans));
        if out.is_empty() {
            vec![ratatui::text::Line::from("")]
        } else {
            out
        }
    }
    pub fn scroll_down(&mut self, n: usize) {
        // 不 clamp:total() 是未 wrap 行数,clamp 会卡在 wrap 真底之上(向下滚不动)。
        // render 时按 wrap_total + 视口高 clamp(每帧)。saturating_add 防 overflow。
        self.offset = self.offset.saturating_add(n);
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
    /// follow_tail:render 时若 true,自动追底(offset=total-viewport_height)。
    /// 内容不超视口 → offset=0(从顶向下增长);超视口 → 显最后一页(新内容可见)。
    /// ScrollUp/PgUp 置 false(自由滚);ScrollDown/PgDn/do_turn 置 true(追新)。
    pub fn follow_tail(&mut self, follow: bool) {
        self.follow_tail_flag = follow;
    }
    /// 是否画右侧 Scrollbar(默认 true;control 对话区设 false 用 footer N/M 代位置)。
    pub fn show_scrollbar(mut self, s: bool) -> Self {
        self.show_scrollbar = s;
        self
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

        // follow_tail:内容超视口 → 显最后一页(offset=total-viewport_height,新内容可见)。
        // 内容不超视口 → offset=0(从顶向下增长,不跳底)。用户 ScrollUp/PgUp → follow=false(自由滚)。
        // 自己折行(保留 span style)+ slice 显示:折行与显示同逻辑 → 行数精确,scroll 到真底。
        // 不用 ratatui Paragraph::Wrap(其 word-break 与自算行数不一致 → 滚不到真底)。
        let width = inner.width as usize;
        // wrap_cache:width 同 + 内容未变(set_content 失效)→ 复用,避免每帧 flat_map(wrap_line)。
        if self.wrap_cache.as_ref().map_or(true, |(w, _)| *w != width) {
            let d: Vec<ratatui::text::Line<'static>> = if self.wrap {
                self.lines.iter().flat_map(|l| Self::wrap_line(l, width)).collect()
            } else {
                self.lines.clone()
            };
            self.wrap_cache = Some((width, d));
        }
        let display: &[ratatui::text::Line<'static>] = self.wrap_cache.as_ref().unwrap().1.as_slice();
        let total = display.len();
        let max_off = total.saturating_sub(inner.height as usize);
        if self.follow_tail_flag {
            self.offset = max_off;
        } else {
            self.offset = self.offset.min(max_off);  // 用户自由滚不超真底
        }
        let visible: Vec<ratatui::text::Line<'static>> = display
            .iter()
            .skip(self.offset)
            .take(inner.height.max(1) as usize)
            .cloned()
            .collect();
        f.render_widget(Paragraph::new(visible), inner);

        // Scrollbar(纯指示器):position 镜像 offset,content_length = 折行后精确总数。
        // show_scrollbar=false 时跳过(control 对话区改用 footer N/M 代位置)。
        if self.show_scrollbar {
            let mut st = ScrollbarState::new(total)
                .position(self.offset)
                .viewport_content_length(inner.height as usize);
            let sb = Scrollbar::new(ScrollbarOrientation::VerticalRight)
                .begin_symbol(Some("↑"))
                .end_symbol(Some("↓"));
            f.render_stateful_widget(sb, inner, &mut st);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ratatui::text::Line;

    #[test]
    fn scroll_down_unclamped_up_saturates() {
        let mut v = ScrollView::new(vec![Line::from("a"); 5]);
        v.scroll_down(100);
        // scroll_down 不 clamp(未 wrap total 会卡 wrap 真底);render 按 wrap_total+视口 clamp
        assert_eq!(v.offset, 100, "scroll_down 自由增,render 时 clamp");
        v.scroll_up(10);
        assert_eq!(v.offset, 90, "scroll_up 减");
        v.scroll_up(1000);
        assert_eq!(v.offset, 0, "scroll_up saturating 到 0");
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

    /// wrap_line:按 width 字符流折行(长行/CJK/空行),保留 span。render slice 显示同逻辑。
    #[test]
    fn wrap_line_long_cjk_empty() {
        assert_eq!(ScrollView::wrap_line(&Line::from("a".repeat(100)), 20).len(), 5);
        assert_eq!(ScrollView::wrap_line(&Line::from("中".repeat(10)), 10).len(), 2);
        assert_eq!(ScrollView::wrap_line(&Line::from(""), 10).len(), 1);
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
        // set_content 不 clamp(render 才按 wrap_total + 视口 clamp);offset 跨 set_content 保留
        assert_eq!(v.offset, 5, "offset preserved across set_content (render clamps)");
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
