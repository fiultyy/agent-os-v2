//! 分区:静态 Layout(ratatui 内建)+ resizable VSplit/HSplit(手写,复用 mouse.rs)。
//!
//! 静态:Layout::vertical/horizontal([Constraint::...]).spacing(u16).split/areas。
//! 0.28 Constraint 六变体:Min/Max/Length/Percentage/Ratio(u32,u32)/Fill(u16)+ from_ratios。
//!
//! resizable:零社区 crate 能锁 ratatui 0.28(ratatui-split-pane 0.0.0 空壳;ratatui-spatial-splits
//! 锁 0.30;ratatui-toolkit/ratkit 全 ^0.29)→ 手写 C:u16 pct 状态 + 手算 bar rect +
//! ClickMap 命中 bar + MouseCursor Drag 改 pct(复用 mouse.rs),~20 行。
//! ponytail: pct 须 clamp 10..=90(0 高度 rect contains 恒 false,hit-test 失效)。

#![allow(dead_code)]

use ratatui::{
    layout::Rect,
    style::{Color, Style},
    widgets::{Block, Widget},
    Frame,
};

/// 垂直可调分栏:上 pane + 水平分隔条 + 下 pane。pct=上 pane 占比(10..=90)。
pub struct VSplit {
    pub pct: u16,
}

impl VSplit {
    pub fn new(pct: u16) -> Self {
        Self { pct: pct.clamp(10, 90) }
    }
    /// [top, bar, bottom] 三个 rect(bar=1 行高分隔条)。area.height==0 时 bar 也 0(防超出邻区)。
    pub fn rects(&self, area: Rect) -> [Rect; 3] {
        let bar_h: u16 = if area.height == 0 { 0 } else { 1 };
        let usable = area.height.saturating_sub(bar_h);
        let top_h = (usable as u32 * self.pct as u32 / 100) as u16;
        let top = Rect { x: area.x, y: area.y, width: area.width, height: top_h };
        let bar = Rect { x: area.x, y: area.y + top_h, width: area.width, height: bar_h };
        let bot = Rect {
            x: area.x,
            y: bar.y + bar_h,
            width: area.width,
            height: usable - top_h,
        };
        [top, bar, bot]
    }
    /// 拖拽:dy>0(下拖)减上 pane,dy<0(上拖)加上 pane。clamp 10..=90。
    pub fn drag(&mut self, dy: i32, area: Rect) {
        let usable = (area.height as i32).max(1);
        let delta = -dy * 100 / usable;
        self.pct = (self.pct as i32 + delta).clamp(10, 90) as u16;
    }
    pub fn render<T: Widget, B: Widget>(&self, f: &mut Frame, top: T, bot: B, area: Rect) {
        let [t, bar, b] = self.rects(area);
        f.render_widget(top, t);
        f.render_widget(bot, b);
        f.render_widget(
            Block::default().style(Style::default().fg(Color::DarkGray)),
            bar,
        );
    }
}

/// 水平可调分栏:左 pane + 垂直分隔条 + 右 pane。pct=左 pane 占比(10..=90)。
pub struct HSplit {
    pub pct: u16,
}

impl HSplit {
    pub fn new(pct: u16) -> Self {
        Self { pct: pct.clamp(10, 90) }
    }
    /// [left, bar, right](bar=1 列宽分隔条)。area.width==0 时 bar 也 0(防超出邻区)。
    pub fn rects(&self, area: Rect) -> [Rect; 3] {
        let bar_w: u16 = if area.width == 0 { 0 } else { 1 };
        let usable = area.width.saturating_sub(bar_w);
        let left_w = (usable as u32 * self.pct as u32 / 100) as u16;
        let left = Rect { x: area.x, y: area.y, width: left_w, height: area.height };
        let bar = Rect { x: area.x + left_w, y: area.y, width: bar_w, height: area.height };
        let right = Rect {
            x: bar.x + bar_w,
            y: area.y,
            width: usable - left_w,
            height: area.height,
        };
        [left, bar, right]
    }
    /// 拖拽:dx>0(右拖)加左 pane。clamp 10..=90。
    pub fn drag(&mut self, dx: i32, area: Rect) {
        let usable = (area.width as i32).max(1);
        let delta = dx * 100 / usable;
        self.pct = (self.pct as i32 + delta).clamp(10, 90) as u16;
    }
    pub fn render<L: Widget, R: Widget>(&self, f: &mut Frame, left: L, right: R, area: Rect) {
        let [l, bar, r] = self.rects(area);
        f.render_widget(left, l);
        f.render_widget(right, r);
        f.render_widget(
            Block::default().style(Style::default().fg(Color::DarkGray)),
            bar,
        );
    }
}

/// N-pane 垂直可扩展堆叠:Vec<u16> pcts 按比例垂直分(pane 上下堆叠)。N>=2。
/// pcts 按比例分(不要求和 100,按 pct/sum);各 pct clamp 10..=90。pane 间 1 行分隔条。
/// ponytail: 后续 push pane(加 flow/属性)= pcts.push;架构不变。
pub struct VerticalStack {
    pub pcts: Vec<u16>,
}

impl VerticalStack {
    pub fn new(pcts: Vec<u16>) -> Self {
        assert!(pcts.len() >= 2, "VerticalStack needs N>=2 panes");
        Self { pcts: pcts.into_iter().map(|p| p.clamp(10, 90)).collect() }
    }

    /// N pane Rect(pane 间留 1 行分隔条)。返 len==pcts.len()。
    pub fn rects(&self, area: Rect) -> Vec<Rect> {
        let n = self.pcts.len();
        let sep_h: u16 = if area.height == 0 { 0 } else { 1 };
        let total_sep = sep_h * (n.saturating_sub(1)) as u16;
        let usable = area.height.saturating_sub(total_sep);
        let sum: u32 = self.pcts.iter().map(|&p| p as u32).sum::<u32>().max(1);
        let mut out = Vec::with_capacity(n);
        let mut y = area.y;
        for (i, &p) in self.pcts.iter().enumerate() {
            let h = if i == n - 1 {
                // 末 pane 吃余量(避免舍入缝隙)。
                area.y + area.height - y
            } else {
                (usable as u32 * p as u32 / sum) as u16
            };
            out.push(Rect { x: area.x, y, width: area.width, height: h });
            y += h + sep_h;
        }
        out
    }

    /// N-1 分隔条 Rect(pane 间 1 行高,供鼠标拖拽命中)。
    pub fn separators(&self, area: Rect) -> Vec<Rect> {
        let panes = self.rects(area);
        let sep_h: u16 = if area.height == 0 { 0 } else { 1 };
        panes
            .windows(2)
            .map(|w| Rect {
                x: area.x,
                y: w[0].y + w[0].height,
                width: area.width,
                height: sep_h,
            })
            .collect()
    }

    /// 拖 pane_idx 与 pane_idx+1 之间的分隔条:dy>0(下拖)减上 pane 加下 pane。
    /// 改 pcts[pane_idx]/pcts[pane_idx+1](和守恒),各 clamp 10..=90。
    pub fn drag(&mut self, pane_idx: usize, dy: i32, area: Rect) {
        if pane_idx + 1 >= self.pcts.len() {
            return;
        }
        let usable = (area.height as i32).max(1);
        let sum: i32 = self.pcts[pane_idx] as i32 + self.pcts[pane_idx + 1] as i32;
        // dy>0 下拖:上 pane 减少。
        let delta = -dy * sum / usable;
        let mut a = self.pcts[pane_idx] as i32 + delta;
        // clamp a 10..=sum-10,b 取余保持和守恒。
        a = a.max(10).min(sum - 10);
        let b = sum - a;
        self.pcts[pane_idx] = a as u16;
        self.pcts[pane_idx + 1] = b as u16;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn vsplit_rects_half() {
        let v = VSplit::new(50);
        let [t, bar, b] = v.rects(Rect::new(0, 0, 40, 21));
        assert_eq!(t.height, 10, "top = 20*50/100");
        assert_eq!(bar.y, 10);
        assert_eq!(bar.height, 1);
        assert_eq!(b.height, 10, "bottom = 20-10");
    }

    #[test]
    fn vsplit_clamps_pct() {
        assert_eq!(VSplit::new(200).pct, 90);
        assert_eq!(VSplit::new(0).pct, 10);
        assert_eq!(VSplit::new(50).pct, 50);
    }

    #[test]
    fn vsplit_drag_clamps() {
        let mut v = VSplit::new(50);
        v.drag(-1000, Rect::new(0, 0, 40, 21)); // 上拖大量 → 加上 pane
        assert_eq!(v.pct, 90, "clamps to 90");
        v.drag(1000, Rect::new(0, 0, 40, 21)); // 下拖大量 → 减上 pane
        assert_eq!(v.pct, 10, "clamps to 10");
    }

    #[test]
    fn hsplit_rects_half() {
        let h = HSplit::new(50);
        let [l, bar, r] = h.rects(Rect::new(0, 0, 81, 10));
        assert_eq!(l.width, 40, "left = 80*50/100");
        assert_eq!(bar.x, 40);
        assert_eq!(r.width, 40, "right = 80-40");
    }

    #[test]
    fn hsplit_drag_clamps() {
        let mut h = HSplit::new(50);
        h.drag(1000, Rect::new(0, 0, 81, 10)); // 右拖大量 → 加左 pane
        assert_eq!(h.pct, 90);
        h.drag(-1000, Rect::new(0, 0, 81, 10)); // 左拖大量 → 减左 pane
        assert_eq!(h.pct, 10);
    }

    #[test]
    fn split_zero_area_safe() {
        // 0 高/宽区域:bar 也 0,不超出邻区。
        let [t, bar, b] = VSplit::new(50).rects(Rect::new(0, 0, 10, 0));
        assert_eq!(bar.height, 0);
        assert_eq!(t.height + b.height, 0);
        let [l, bar, r] = HSplit::new(50).rects(Rect::new(0, 0, 0, 10));
        assert_eq!(bar.width, 0);
        assert_eq!(l.width + r.width, 0);
    }

    // ── VerticalStack N-pane ──
    #[test]
    fn vstack_n2_rects_and_seps() {
        let v = VerticalStack::new(vec![50, 50]);
        let panes = v.rects(Rect::new(0, 0, 40, 21));
        assert_eq!(panes.len(), 2, "N=2 → 2 panes");
        assert_eq!(panes[0].height + panes[1].height, 20, "2 panes + 1 sep = 21");
        let seps = v.separators(Rect::new(0, 0, 40, 21));
        assert_eq!(seps.len(), 1, "N=2 → 1 separator");
        assert_eq!(seps[0].y, panes[0].y + panes[0].height);
        assert_eq!(seps[0].height, 1);
    }

    #[test]
    fn vstack_n3_rects_and_seps() {
        let v = VerticalStack::new(vec![60, 30, 10]);
        let panes = v.rects(Rect::new(0, 0, 40, 32)); // usable = 32 - 2 sep = 30
        assert_eq!(panes.len(), 3, "N=3 → 3 panes");
        // 60/100 * 30 = 18, 30/100 * 30 = 9, 末 pane 吃余 = 3
        assert_eq!(panes[0].height, 18);
        assert_eq!(panes[1].height, 9);
        assert_eq!(panes[2].height, 3);
        let seps = v.separators(Rect::new(0, 0, 40, 32));
        assert_eq!(seps.len(), 2, "N=3 → 2 separators");
    }

    #[test]
    fn vstack_drag_clamps() {
        let mut v = VerticalStack::new(vec![50, 50]);
        v.drag(0, 1000, Rect::new(0, 0, 40, 21)); // 下拖大量 → 上 pane 减到 min(10)
        assert_eq!(v.pcts[0], 10);
        assert_eq!(v.pcts[1], 90, "和守恒 = 100");
        v.drag(0, -1000, Rect::new(0, 0, 40, 21)); // 上拖大量 → 上 pane 加到 max(90)
        assert_eq!(v.pcts[0], 90);
        assert_eq!(v.pcts[1], 10);
    }

    #[test]
    fn vstack_zero_area_safe() {
        let v = VerticalStack::new(vec![50, 50]);
        let panes = v.rects(Rect::new(0, 0, 10, 0));
        let seps = v.separators(Rect::new(0, 0, 10, 0));
        assert_eq!(panes.len(), 2);
        assert_eq!(seps.len(), 1);
        assert_eq!(seps[0].height, 0, "0 高度 area → sep 0");
        assert_eq!(panes[0].height + panes[1].height, 0);
    }
}
