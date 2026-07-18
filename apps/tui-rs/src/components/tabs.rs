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
    text::{Line, Span},
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
    /// 每 tab 前景色(IT6-④ 主 tag 栏配色区分)。空 vec = 全默认(不染色)。
    /// active tab 始终走 highlight_style(Yellow 反白),非 active 才取 tab_colors[i]。
    pub tab_colors: Vec<Color>,
    /// IT6-②:true=只顶线描边(region_block 同款),false=全边框(默认,顶栏 Home/Flows 用)。
    /// 顶线模式内宽=area.width(无边框列),全边框内宽=width-2。
    pub top_border: bool,
}

impl TabBar {
    pub fn new(titles: Vec<String>) -> Self {
        Self { titles, active: 0, first_visible: 0, tab_colors: vec![], top_border: false }
    }

    /// IT6-④:给每个 tab 配不同前景色(与 titles 等长)。active 仍走 Yellow 高亮。
    pub fn colors(mut self, cs: Vec<Color>) -> Self {
        self.tab_colors = cs;
        self
    }

    /// IT6-②:切顶线描边模式(右区 对话/flow tab 与 region_block 视觉一致)。
    pub fn top_border(mut self) -> Self {
        self.top_border = true;
        self
    }

    /// tab 内容区(Rect)+ 可用列宽(cap)。顶线=下移 1 行不缩列;无框(默认)=全 area 不缩列
    /// (与 render_block 无框一致:Tabs 从 area.x 渲染,hit 不偏移)。
    fn inner_and_cap(&self, area: Rect) -> (Rect, usize) {
        if self.top_border {
            (Rect::new(area.x, area.y + 1, area.width, area.height.saturating_sub(1)), area.width as usize)
        } else {
            // 无框:全宽(render_block else 无 border,Tabs 在全 area),旧版用 bordered.inner 减 2
            // → cap 比 Tabr 实际渲染窄 2 + hit 偏移 1 列(已修)。
            (area, area.width as usize)
        }
    }

    /// IT6-②:全边框(顶栏 Home/Flows,带 i/×)或顶线描边(右区 对话/flow,region_block 同款)。
    fn render_block(&self, _title: String) -> Block<'static> {
        // title 去掉(分区标题难堪);保参数避免改调用。仅顶线/边框 + bg_surface 卡片层。
        if self.top_border {
            Block::default()
                .borders(ratatui::widgets::Borders::TOP)
                .border_style(Style::default().fg(crate::theme::DARK.border_accent))
                .style(Style::default().bg(crate::theme::DARK.bg_surface))
        } else {
            // 无 border 无 bg(tabs_area 透明,显底层终端 bg):tab 紧凑无四周留白,
            // 符合"不留额外BG四周空间"。Tabs 只画 tab+padding cell,右侧空白不涂。
            Block::default()
        }
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

    /// 从 start 起窗口能容纳的 tab 数(cap = 内容区列宽)。
    /// divider 仅画在 tab 之间(首 tab 无前导,末 tab 无尾随)→ need = 首个 cw,其余 1+cw。
    /// 修复:旧实现对每个 tab 都 +1 divider(含末 tab),多算 1 列 → 少显一个 tab + 假 >。
    fn visible_count_at(&self, start: usize, area: Rect) -> usize {
        let cap = self.inner_and_cap(area).1;
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
        self.render_with_hover(f, area, None);
    }

    /// 渲染 tab 栏 + 鼠标悬停高亮(ADR-1)。
    /// hovered_col/hovered_row = Some 时,悬停的 tab 加 reversed 边框高亮。
    pub fn render_with_hover(&mut self, f: &mut Frame, area: Rect, hover: Option<(u16, u16)>) {
        let start = self.windowed_start(area);
        self.first_visible = start;
        let n = self.visible_count_at(start, area);
        // IT6-④:非 active tab 用 tab_colors[i] 染色(Home=Green/Flows=Blue/Observe=Cyan/Control=Magenta)。
        // active tab 由下方 highlight_style(Yellow 反白)接管,染色不影响命中宽度(Line 字节数不变)。
        let window: Vec<Line> = self
            .titles
            .iter()
            .enumerate()
            .skip(start)
            .take(n)
            .map(|(i, s)| {
                let color = self.tab_colors.get(i).copied();
                match color {
                    Some(c) => Line::from(Span::styled(s.as_str(), Style::default().fg(c))),
                    None => Line::from(s.as_str()),
                }
            })
            .collect();
        let local_active = self.active.saturating_sub(start).min(n.saturating_sub(1));
        let (has_left, has_right) = (start > 0, start + n < self.titles.len());
        let title = format!(
            "{}tabs{}",
            if has_left { "< " } else { "" },
            if has_right { " >" } else { "" }
        );
        Tabs::new(window)
            .block(self.render_block(title))
            .highlight_style(
                Style::default()
                    .fg(crate::theme::DARK.bg)
                    .bg(crate::theme::DARK.highlight)
                    .add_modifier(Modifier::BOLD),
            )
            .select(local_active)
            .divider("")
            .render(area, f.buffer_mut());

        // ADR-1:鼠标悬停 tab 高亮(在原生 Tabs 渲染后覆写悬停 tab 的 cell 样式)。
        if let Some((col, row)) = hover {
            if let Some(idx) = self.hit(area, col, row) {
                if idx != self.active {
                    // 算悬停 tab 的 x 范围(同 hit 逻辑),覆写该范围 cell 加 UNDERLINED。
                    let inner = self.inner_and_cap(area).0;
                    let last_visible = start + n - 1;
                    let mut x = inner.x;
                    for (i, t) in self.titles.iter().enumerate().skip(start).take(n) {
                        let cw = Self::cell_width(t) as u16;
                        let w = if i == last_visible { cw } else { cw + 1 };
                        if i == idx {
                            // 覆写悬停区域(仅 inner 行)加 BOLD 高亮(去下划线,与 DARK 风格一致)。
                            for dx in 0..w {
                                if let Some(cell) = f.buffer_mut().cell_mut((x + dx, row.max(inner.y))) {
                                    let mut s = cell.style();
                                    s = s.add_modifier(Modifier::BOLD);
                                    cell.set_style(s);
                                }
                            }
                            break;
                        }
                        x += w;
                    }
                }
            }
        }
    }

    /// 鼠标点击命中:返回点中的 tab 全局索引(窗口偏移已加回)。
    /// 用 windowed_start(area) 算当前窗口(不依赖 stale first_visible,resize 后也一致)。
    /// 末 tab 无尾随 divider → hit 范围不含尾部空列(修复:旧实现末 tab 多占 1 列)。
    pub fn hit(&self, area: Rect, col: u16, row: u16) -> Option<usize> {
        if !area.contains(Position { x: col, y: row }) {
            return None;
        }
        let inner = self.inner_and_cap(area).0;
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
    fn hit_respects_window_offset_no_border() {
        let mut b = TabBar::new(vec!["alpha".into(), "beta".into(), "gamma".into()]);
        b.active = 2; // gamma
        b.first_visible = 1; // 窗口在 beta,gamma(windowed_start 保持:active=2 在 [1,3))
        let area = Rect::new(0, 0, 50, 3);
        // 无框:row 0 是 tab 内容行(非边框),col 3 命中窗口内 tab(idx>=1)。
        let h = b.hit(area, 3, 0);
        assert!(h.is_some() && h.unwrap() >= 1, "row0 hits a visible tab (idx>=1): {:?}", h);
        // row/col 越界 miss。
        assert_eq!(b.hit(area, 5, 3), None, "row outside area miss");
        assert_eq!(b.hit(area, 60, 0), None, "col outside area miss");
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

    /// IT6-②:top_border 模式下,顶行(row 0)=描边行不算 tab 内容;内容从 row 1 起,
    /// 且无左右边框列(全宽)。hit 在 row 0 应 miss,row 1 点到 tab 文本应命中。
    #[test]
    fn top_border_hit_skips_top_line_uses_full_width() {
        let b = TabBar::new(vec!["aaa".into(), "bbb".into()]).top_border();
        let area = Rect::new(0, 0, 20, 2); // 顶线 row0 + tab 内容 row1
        // row 0 = 顶线描边,不算 tab 内容。
        assert_eq!(b.hit(area, 1, 0), None, "top border row not a tab");
        // row 1,col 0..4 = "aaa"(cw=5:3 chars+2 pad)→ 命中 idx 0。
        assert_eq!(b.hit(area, 1, 1), Some(0), "first tab hit at row1 (full width, no border col)");
        // "bbb" 从 col 6 起(cw 5 + 1 divider)。col 7 命中 idx 1。
        assert_eq!(b.hit(area, 7, 1), Some(1), "second tab hit");
        // cap = full width(20)无边框列 → 两 tab 都在窗口内。
        assert_eq!(b.visible_count_at(0, area), 2, "top_border: full width cap fits both");
    }

    /// 无框(默认)与顶线模式 cap 都 = area.width(全宽,不缩列);区别仅在 y 偏移(顶线下移 1 行)。
    #[test]
    fn inner_and_cap_both_full_width() {
        let area = Rect::new(0, 0, 10, 3);
        let full = TabBar::new(vec!["a".into()]);
        let topb = TabBar::new(vec!["a".into()]).top_border();
        let (inner_full, cap_full) = full.inner_and_cap(area);
        let (inner_top, cap_top) = topb.inner_and_cap(area);
        assert_eq!(cap_full, 10, "无框 cap = 全宽 10");
        assert_eq!(cap_top, 10, "顶线 cap = 全宽 10");
        assert_eq!(inner_full.y, 0, "无框 y 不偏移");
        assert_eq!(inner_top.y, 1, "顶线 y 下移 1(顶线行)");
    }

    /// IT6-④:colors builder 设置 tab_colors;active tab 不受影响(仍 Yellow)。
    #[test]
    fn colors_builder_sets_per_tab_colors() {
        let b = TabBar::new(vec!["Home".into(), "Flows".into()])
            .colors(vec![Color::Green, Color::Blue]);
        assert_eq!(b.tab_colors, vec![Color::Green, Color::Blue]);
        // active 默认 0,染色不影响 active 索引。
        assert_eq!(b.active, 0);
    }
}
