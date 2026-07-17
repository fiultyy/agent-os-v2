//! 鼠标光标 + 鼠标点击:可复用 MouseCursor(悬停指示器)+ ClickMap<T>(点击命中)。
//!
//! MouseCursor:crossterm 0.28.1 EnableMouseCapture 发 ?1003h(any-event tracking,已核实
//! crossterm-0.28.1/src/event.rs:327)→ MouseEventKind::Moved 在无按键悬停时也会触发。
//! track() 喂所有鼠标事件(Moved/Down/Drag 都带 column/row),render() 在所有 widget 画完后
//! 用 buffer_mut.get_mut(x,y) 黑底黄字高亮单格(保留底层 glyph,不擦内容)。
//!
//! ClickMap<T>:register(Rect,id)+hit(col,row)。用 ratatui 0.28 原生 Rect::contains(Position)
//! (rect.rs:212 const fn)。ratatui-interact 0.5.3 虽是已装 dep,但它锁 ratatui 0.30 + crossterm
//! 0.29(Cargo.lock 两套 ratatui 共存),Rect 类型不兼容 → 不能用于 0.28 hit-test。原生 ClickMap
//! 是 lazy 正解,非重造(interact 的 0.30 Rect 用不了)。
//! ponytail: ClickMap hit() 是 O(n) 线扫;<100 region 够用,超 500 上空间哈希。

#![allow(dead_code)]

use crossterm::event::{MouseButton, MouseEvent, MouseEventKind};
use ratatui::{
    layout::{Position, Rect},
    style::Color,
    Frame,
};

// ── 鼠标光标指示器 ──────────────────────────────────────────────────

/// 鼠标光标指示器:跟踪鼠标位置,在帧最后高亮单格。
#[derive(Default, Clone, Copy)]
pub struct MouseCursor {
    pub x: u16,
    pub y: u16,
    pub visible: bool,
}

impl MouseCursor {
    /// 喂每个 crossterm MouseEvent:悬停(Moved)+ 左键(Down/Drag)都锁位置。
    /// 注意:tmux/screen 多路复用器可能吞 ?1003h 悬停(降级 ?1002h),其下 Moved 稀疏,
    /// 光标仅在点击/拖拽时更新——这是终端行为非 crossterm bug。
    pub fn track(&mut self, m: MouseEvent) {
        match m.kind {
            MouseEventKind::Moved
            | MouseEventKind::Down(MouseButton::Left)
            | MouseEventKind::Drag(MouseButton::Left) => {
                self.x = m.column;
                self.y = m.row;
                self.visible = true;
            }
            _ => {}
        }
    }

    /// 在所有 widget 画完后调用,把光标画在最上层(黑底黄字,保留底层 glyph)。
    pub fn render(&self, f: &mut Frame) {
        if !self.visible {
            return;
        }
        if let Some(cell) = f.buffer_mut().cell_mut((self.x, self.y)) {
            cell.set_fg(Color::Black);
            cell.set_bg(Color::Yellow);
        }
    }

    /// 光标是否在某区域内(给命中判断用)。
    pub fn in_rect(&self, r: Rect) -> bool {
        r.contains(Position { x: self.x, y: self.y })
    }
}

// ── 鼠标点击命中 ────────────────────────────────────────────────────

/// 一个可点击区域:Rect + 调用方 id。
#[derive(Clone)]
pub struct ClickRegion<T: Clone> {
    pub area: Rect,
    pub id: T,
}

/// 点击命中表:register(Rect,id)+ hit(col,row)。先注册的优先(重叠时)。
/// contains 右/下边界排他(x < right, y < bottom),符合终端 cell 语义。
pub struct ClickMap<T: Clone> {
    regions: Vec<ClickRegion<T>>,
}

impl<T: Clone> ClickMap<T> {
    pub fn new() -> Self {
        Self { regions: Vec::new() }
    }
    pub fn clear(&mut self) {
        self.regions.clear();
    }
    pub fn register(&mut self, area: Rect, id: T) {
        self.regions.push(ClickRegion { area, id });
    }
    /// 左键命中测试:返回首个包含 (col,row) 的 region id。先注册的优先。
    /// ponytail: O(n) 线扫区域 contains——区域 hit-test 本质难 O(1)(重叠区域查询,
    /// HashMap 点查不适用);n=按钮+session 项几十个 + 鼠标点击低频,O(n) 可接受。
    /// 升级路径:n 增大时改按 row 分桶(Vec<Vec<region>>)缩扫描集。
    pub fn hit(&self, col: u16, row: u16) -> Option<&T> {
        let p = Position { x: col, y: row };
        self.regions.iter().find(|r| r.area.contains(p)).map(|r| &r.id)
    }
    pub fn len(&self) -> usize {
        self.regions.len()
    }
    pub fn is_empty(&self) -> bool {
        self.regions.is_empty()
    }
}

impl<T: Clone> Default for ClickMap<T> {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crossterm::event::KeyModifiers;

    fn ev(kind: MouseEventKind, col: u16, row: u16) -> MouseEvent {
        MouseEvent { kind, column: col, row, modifiers: KeyModifiers::empty() }
    }

    #[test]
    fn cursor_tracks_moved_and_down_not_scroll() {
        let mut c = MouseCursor::default();
        c.track(ev(MouseEventKind::Moved, 5, 7));
        assert_eq!((c.x, c.y), (5, 7));
        assert!(c.visible);
        c.track(ev(MouseEventKind::ScrollDown, 9, 9));
        assert_eq!((c.x, c.y), (5, 7), "scroll does not move cursor");
    }

    #[test]
    fn clickmap_hit_miss_and_edge_exclusive() {
        let mut m: ClickMap<&str> = ClickMap::new();
        m.register(Rect::new(0, 0, 10, 3), "a");
        m.register(Rect::new(20, 0, 5, 3), "b");
        assert_eq!(m.hit(5, 1), Some(&"a"));
        assert_eq!(m.hit(22, 2), Some(&"b"));
        assert_eq!(m.hit(15, 1), None, "gap between regions is a miss");
        assert_eq!(m.hit(10, 0), None, "right edge x==width is exclusive");
        assert_eq!(m.hit(0, 3), None, "bottom edge y==height is exclusive");
    }

    #[test]
    fn clickmap_first_registered_wins_on_overlap() {
        let mut m: ClickMap<u32> = ClickMap::new();
        m.register(Rect::new(0, 0, 10, 5), 1); // 先注册(底层)
        m.register(Rect::new(0, 0, 10, 5), 2); // 后注册(重叠)
        assert_eq!(m.hit(1, 1), Some(&1), "first registered wins on overlap");
    }
}
