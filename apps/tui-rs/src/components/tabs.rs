//! tag分页:可复用 tab 栏(ratatui 0.28 原生 Tabs + 窗口分页)。
//!
//! ratatui Tabs 无原生溢出分页——标题超宽时活动 tab 会画到屏外。
//! 本控件包一层窗口切片(first_visible 偏移):活动 tab 滑出窗口时自动滚动,
//! 边缘显 < / > 提示还有更多 tab。鼠标点击 tab 用同样的 cell_width 算列偏移命中。
//!
//! 0.28 版本陷阱(已核实 ratatui-0.28.1 源码):NO HighlightStyle(那是 0.30 API),
//! highlight_style() 取 impl Into<Style>;select(usize) 是 const fn;调用 .render() 必须
//! `use ratatui::widgets::Widget`(Frame 不带 Widget 进作用域,否则 E0599)。
//! ponytail: ~40 行窗口逻辑是 lazy 正解(无社区 crate 加 tab 溢出分页)。
//!
//! 对抗验证修复(verify workflow confirmed):
//! - windowed_start 用 stale n(变宽 tab 下活动跑出窗口)→ fixed-point + visible_count_at(start)
//! - 末 tab 多算 trailing divider(ratatui 末 tab 后不画 divider)→ 末 tab 跳过 +1
//! - first_visible 私有(封装窗口状态);hit() 调 windowed_start(area) 不依赖 stale 字段

#![allow(dead_code)]

use ratatui::{
    layout::{Position, Rect},
    style::{Color, Modifier, Style},
    text::Line,
    widgets::{Block, Tabs, Widget},
    Frame,
};

/// 可复用 tab 栈:titles + 活动索引 + 窗口偏移(私有,由 render 维护)。
#[derive(Clone)]
pub struct TabBar {
    pub titles: Vec<String>,
    pub active: usize,
    /// 窗口起始索引(分页用,私有:render 维护,hit 经 windowed_start 重算)。
    first_visible: usize,
}

impl TabBar {
    pub fn new(titles: Vec<String>) -> Self {
        Self { titles, active: 0, first_visible: 0 }
    }

    pub fn next(&mut self) {
        if !self.titles.is_empty() {
            self.active = (self.active + 1) % self.titles.len();
        }
    }
    pub fn prev(&mut self) {
        if !self.titles.is_empty() {
            self.active = (self.active + self.titles.len() - 1) % self.titles.len();
        }
    }
    pub fn select(&mut self, i: usize) {
        if i < self.titles.len() {
            self.active = i;
        }
    }
    pub fn len(&self) -> usize {
        self.titles.len()
    }
    pub fn is_empty(&self) -> bool {
        self.titles.is_empty()
    }

    /// 单 tab 占用列宽(标题字符数 + 两侧 padding 各 1)。
    fn cell_width(t: &str) -> usize {
        t.chars().count() + 2
    }

    /// 从 start 起窗口能容纳的 tab 数(cap = Block 内宽)。
    /// divider 仅画在 tab 之间(首 tab 无前导,末 tab 无尾随)→ need = 首个 cw,其余 1+cw。
    /// 修复:旧实现对每个 tab 都 +1 divider(含末 tab),多算 1 列 → 少显一个 tab + 假 >。
    fn visible_count_at(&self, start: usize, area: Rect) -> usize {
        let cap = area.width.saturating_sub(2) as usize; // Block::bordered 内宽 = width - 2
        let (mut used, mut n) = (0usize, 0usize);
        for t in self.titles.iter().skip(start) {
            let cw = Self::cell_width(t);
            let need = if n == 0 { cw } else { 1 + cw }; // 首无前导 divider,其余前带 divider
            if used + need > cap {
                break;
            }
            used += need;
            n += 1;
        }
        n.max(1)
    }

    /// 找窗口起始:若活动 tab 已在当前窗口内则保持;否则从 0 扫到包含活动的最小 start。
    /// 修复:旧实现用旧 first_visible 的 n 推 start(变宽 tab 下 n 随 start 变 → stale → 活动跑出窗口)。
    fn windowed_start(&self, area: Rect) -> usize {
        let total = self.titles.len();
        if total == 0 {
            return 0;
        }
        let cur = self.first_visible.min(total - 1);
        let n_cur = self.visible_count_at(cur, area);
        if self.active >= cur && self.active < cur + n_cur {
            return cur; // 活动已在当前窗口,保持(避免无谓滚动)
        }
        // 活动跑出窗口:从 0 扫到包含活动的最小 start。
        let mut start = 0usize;
        while start < total {
            let n = self.visible_count_at(start, area);
            if self.active < start + n {
                break;
            }
            start += n.max(1);
        }
        start.min(total - 1)
    }

    /// 渲染 tab 栏(原生 Tabs + 窗口切片 + < / > 边缘提示)。
    pub fn render(&mut self, f: &mut Frame, area: Rect) {
        let start = self.windowed_start(area);
        self.first_visible = start;
        let n = self.visible_count_at(start, area);
        let window: Vec<Line> = self
            .titles
            .iter()
            .skip(start)
            .take(n)
            .map(|s| Line::from(s.as_str()))
            .collect();
        let local_active = self.active.saturating_sub(start).min(n.saturating_sub(1));
        let (has_left, has_right) = (start > 0, start + n < self.titles.len());
        let title = format!(
            "{}tabs{}",
            if has_left { "< " } else { "" },
            if has_right { " >" } else { "" }
        );
        Tabs::new(window)
            .block(Block::bordered().title(title))
            .highlight_style(
                Style::default()
                    .fg(Color::Black)
                    .bg(Color::Yellow)
                    .add_modifier(Modifier::BOLD),
            )
            .select(local_active)
            .divider("│")
            .render(area, f.buffer_mut());
    }

    /// 鼠标点击命中:返回点中的 tab 全局索引(窗口偏移已加回)。
    /// 用 windowed_start(area) 算当前窗口(不依赖 stale first_visible,resize 后也一致)。
    /// 末 tab 无尾随 divider → hit 范围不含尾部空列(修复:旧实现末 tab 多占 1 列)。
    pub fn hit(&self, area: Rect, col: u16, row: u16) -> Option<usize> {
        if !area.contains(Position { x: col, y: row }) {
            return None;
        }
        let inner = Block::bordered().inner(area);
        if col < inner.x || row < inner.y || row >= inner.y + inner.height {
            return None;
        }
        let start = self.windowed_start(area);
        let n = self.visible_count_at(start, area);
        let last_visible = start + n - 1;
        let mut x = inner.x;
        for (i, t) in self.titles.iter().enumerate().skip(start).take(n) {
            let cw = Self::cell_width(t) as u16;
            let w = if i == last_visible { cw } else { cw + 1 }; // 末 tab 无尾随 divider
            if col >= x && col < x + w {
                return Some(i);
            }
            x += w;
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn next_wraps_and_prev_wraps() {
        let mut b = TabBar::new(vec!["a".into(), "b".into(), "c".into()]);
        b.next();
        b.next();
        b.next();
        assert_eq!(b.active, 0, "next wraps to 0 after cycling 3");
        b.prev();
        assert_eq!(b.active, 2, "prev wraps to last");
    }

    #[test]
    fn select_clamps_out_of_range() {
        let mut b = TabBar::new(vec!["a".into(), "b".into()]);
        b.select(99);
        assert_eq!(b.active, 0, "out-of-range select ignored");
        b.select(1);
        assert_eq!(b.active, 1);
    }

    #[test]
    fn hit_respects_window_offset_and_border() {
        let mut b = TabBar::new(vec!["alpha".into(), "beta".into(), "gamma".into()]);
        b.active = 2; // gamma
        b.first_visible = 1; // 窗口在 beta,gamma(windowed_start 保持:active=2 在 [1,3))
        let area = Rect::new(0, 0, 50, 3);
        assert_eq!(b.hit(area, 5, 0), None, "border row miss");
        let h = b.hit(area, 3, 1);
        assert!(h.is_some() && h.unwrap() >= 1, "inner click hits a visible tab (idx>=1): {:?}", h);
        assert_eq!(b.hit(area, 60, 1), None, "outside area miss");
    }

    #[test]
    fn empty_tabbar_is_safe() {
        let mut b = TabBar::new(vec![]);
        b.next();
        b.prev();
        b.select(0);
        assert_eq!(b.active, 0);
        assert!(b.is_empty());
    }

    /// 变宽 tab + 窄 area:每个 active 都必须落在渲染窗口 [start, start+n) 内(修复 stale-n)。
    #[test]
    fn windowed_start_keeps_active_visible_variable_width() {
        let titles = vec![
            "FLOW".into(), "STACK".into(), "CONTROL".into(),
            "ANCHOR".into(), "MOUSE".into(), "POPUP".into(),
        ];
        for active in 0..titles.len() {
            let mut b = TabBar::new(titles.clone());
            b.active = active;
            b.first_visible = 0;
            // 多个窄宽度逐个测(触发分页)。
            for &w in &[14u16, 17, 20, 27, 36, 44, 60] {
                let area = Rect::new(0, 0, w, 3);
                let start = b.windowed_start(area);
                let n = b.visible_count_at(start, area);
                assert!(
                    start <= active && active < start + n,
                    "active={} w={} not in window [{},{}+{}={})", active, w, start, start, n, start + n
                );
            }
        }
    }

    /// 末 tab 无尾随 divider:visible_count 在边界多容纳一个 tab(修复 phantom divider)。
    #[test]
    fn visible_count_no_phantom_divider_on_last() {
        let b = TabBar::new(vec!["FLOW".into(), "STACK".into()]); // cw 6,7;cap 需 = 6+7+1(div)=14
        // area width 16 → cap=14 → 两 tab 实占 6+1+7=14 ≤ 14 应容纳 2(旧实现 +1 末 divider → 15>14 只显 1)。
        let n = b.visible_count_at(0, Rect::new(0, 0, 16, 3));
        assert_eq!(n, 2, "no phantom divider on last tab → both fit at width 16");
    }
}
