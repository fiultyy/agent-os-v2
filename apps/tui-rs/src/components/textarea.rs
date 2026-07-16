//! codex 式多行 textarea(移植自 codex-rs/tui/src/bottom_pane/textarea.rs,精简)。
//!
//! 根治三个 bug:
//! - 单行截断:wrap 折行(textwrap)+ area 高度按 wrap 后行数(render.rs desired_height)
//! - 粘贴多行:由 state 层 PasteBurst 拦截 \n(本组件不管)
//! - 精确光标:cursor_pos_with_state 算屏幕 cell → control 层 REVERSED
//!
//! 去掉 codex 的:elements(mention 原子,v2 用 popup mention)/ word-delete / kill-line /
//! input(KeyEvent) 大路由(键路由在 state.rs)/ WidgetRef trait(0.28 无,改普通 fn render)。
//! 保留 `handle_key` 薄包装 + TextareaOp + line_count/clear/insert_text 兼容 state.rs 调用。

#![allow(dead_code)]

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::{buffer::Buffer, layout::Rect, style::Style};
use std::cell::RefCell;
use std::ops::Range;
use textwrap::Options;
use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

/// handle_key 返回的操作(state 层据此做副作用,如 @ mention 检测)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TextareaOp {
    Insert(char),
    Backspace,
    Left,
    Right,
    Home,
    End,
    None,
}

/// codex 式多行缓冲:text + cursor(byte)+ wrap 缓存 + preferred_col(垂直移动保列)。
#[derive(Debug, Clone)]
pub struct Textarea {
    text: String,
    cursor_pos: usize,
    wrap_cache: RefCell<Option<WrapCache>>,
    preferred_col: Option<usize>,
}

#[derive(Debug, Clone)]
struct WrapCache {
    width: u16,
    lines: Vec<Range<usize>>,
}

/// stateful widget state:跨帧存 scroll 行偏移。
#[derive(Debug, Default, Clone, Copy)]
pub struct TextareaState {
    pub scroll: u16,
}

impl Textarea {
    pub fn new() -> Self {
        Self { text: String::new(), cursor_pos: 0, wrap_cache: RefCell::new(None), preferred_col: None }
    }

    pub fn text(&self) -> &str {
        &self.text
    }

    pub fn cursor(&self) -> usize {
        self.cursor_pos
    }

    pub fn set_text(&mut self, text: &str) {
        self.text = text.to_string();
        self.cursor_pos = self.text.len();
        self.wrap_cache.replace(None);
        self.preferred_col = None;
    }

    pub fn set_cursor(&mut self, pos: usize) {
        self.cursor_pos = pos.clamp(0, self.text.len());
        self.preferred_col = None;
    }

    pub fn clear(&mut self) {
        self.text.clear();
        self.cursor_pos = 0;
        self.wrap_cache.replace(None);
        self.preferred_col = None;
    }

    pub fn is_empty(&self) -> bool {
        self.text.is_empty()
    }

    /// 逻辑行数(\n 数 + 1)。state 层 Up/Down 判"是否多行"用。
    pub fn line_count(&self) -> usize {
        self.text.matches('\n').count() + 1
    }

    pub fn insert_text(&mut self, s: &str) {
        self.insert_str_at(self.cursor_pos, s);
    }

    pub fn insert_str(&mut self, s: &str) {
        self.insert_str_at(self.cursor_pos, s);
    }

    pub fn insert_str_at(&mut self, pos: usize, s: &str) {
        if s.is_empty() {
            return;
        }
        let pos = pos.clamp(0, self.text.len());
        self.text.insert_str(pos, s);
        self.wrap_cache.replace(None);
        if pos <= self.cursor_pos {
            self.cursor_pos += s.len();
        }
        self.preferred_col = None;
    }

    /// 通用替换(删除/替换基础)。cursor 三态保持(before/inside/after range)。
    pub fn replace_range_raw(&mut self, range: Range<usize>, text: &str) {
        let start = range.start.clamp(0, self.text.len());
        let end = range.end.clamp(0, self.text.len());
        if start > end {
            return;
        }
        let removed_len = end - start;
        let inserted_len = text.len();
        if removed_len == 0 && inserted_len == 0 {
            return;
        }
        let diff = inserted_len as isize - removed_len as isize;
        self.text.replace_range(start..end, text);
        self.wrap_cache.replace(None);
        self.preferred_col = None;
        self.cursor_pos = if self.cursor_pos < start {
            self.cursor_pos
        } else if self.cursor_pos <= end {
            start + inserted_len
        } else {
            ((self.cursor_pos as isize) + diff) as usize
        }
        .min(self.text.len());
    }

    // ── grapheme 边界(去 codex element 逻辑,只留 GraphemeCursor)──
    fn prev_atomic_boundary(&self, pos: usize) -> usize {
        if pos == 0 {
            return 0;
        }
        let mut gc = unicode_segmentation::GraphemeCursor::new(pos, self.text.len(), false);
        match gc.prev_boundary(&self.text, 0) {
            Ok(Some(b)) => b,
            Ok(None) => 0,
            Err(_) => pos.saturating_sub(1),
        }
    }

    fn next_atomic_boundary(&self, pos: usize) -> usize {
        if pos >= self.text.len() {
            return self.text.len();
        }
        let mut gc = unicode_segmentation::GraphemeCursor::new(pos, self.text.len(), false);
        match gc.next_boundary(&self.text, 0) {
            Ok(Some(b)) => b,
            Ok(None) => self.text.len(),
            Err(_) => pos.saturating_add(1).min(self.text.len()),
        }
    }

    // ── 编辑 ──
    pub fn delete_backward(&mut self, n: usize) {
        if n == 0 || self.cursor_pos == 0 {
            return;
        }
        let mut target = self.cursor_pos;
        for _ in 0..n {
            target = self.prev_atomic_boundary(target);
            if target == 0 {
                break;
            }
        }
        self.replace_range_raw(target..self.cursor_pos, "");
    }

    pub fn delete_forward(&mut self, n: usize) {
        if n == 0 || self.cursor_pos >= self.text.len() {
            return;
        }
        let mut target = self.cursor_pos;
        for _ in 0..n {
            target = self.next_atomic_boundary(target);
            if target >= self.text.len() {
                break;
            }
        }
        self.replace_range_raw(self.cursor_pos..target, "");
    }

    // ── 逻辑行边界 ──
    fn beginning_of_line(&self, pos: usize) -> usize {
        self.text[..pos].rfind('\n').map(|i| i + 1).unwrap_or(0)
    }
    fn beginning_of_current_line(&self) -> usize {
        self.beginning_of_line(self.cursor_pos)
    }
    fn end_of_line(&self, pos: usize) -> usize {
        self.text[pos..].find('\n').map(|i| i + pos).unwrap_or(self.text.len())
    }
    fn end_of_current_line(&self) -> usize {
        self.end_of_line(self.cursor_pos)
    }

    fn current_display_col(&self) -> usize {
        let bol = self.beginning_of_current_line();
        self.text[bol..self.cursor_pos].width()
    }

    /// 在 [line_start, line_end) 内移到 target_col 列(grapheme 累加宽度)。
    fn move_to_display_col_on_line(&mut self, line_start: usize, line_end: usize, target_col: usize) {
        let mut width_so_far = 0usize;
        for (i, g) in self.text[line_start..line_end].grapheme_indices(true) {
            width_so_far += g.width();
            if width_so_far > target_col {
                self.cursor_pos = line_start + i;
                return;
            }
        }
        self.cursor_pos = line_end;
    }

    // ── 移动 ──
    pub fn move_cursor_left(&mut self) {
        self.cursor_pos = self.prev_atomic_boundary(self.cursor_pos);
        self.preferred_col = None;
    }
    pub fn move_cursor_right(&mut self) {
        self.cursor_pos = self.next_atomic_boundary(self.cursor_pos);
        self.preferred_col = None;
    }

    /// wrap 后上一视觉行;首行 → cursor 0。preferred_col 保列。
    pub fn move_cursor_up(&mut self) {
        if let Some((target_col, maybe_line)) = {
            let cache_ref = self.wrap_cache.borrow();
            if let Some(cache) = cache_ref.as_ref() {
                let lines = &cache.lines;
                if let Some(idx) = Self::wrapped_line_index_by_start(lines, self.cursor_pos) {
                    let cur_range = &lines[idx];
                    let target_col = self
                        .preferred_col
                        .unwrap_or_else(|| self.text[cur_range.start..self.cursor_pos].width());
                    if idx > 0 {
                        let prev = &lines[idx - 1];
                        Some((target_col, Some((prev.start, prev.end))))
                    } else {
                        Some((target_col, None))
                    }
                } else {
                    None
                }
            } else {
                None
            }
        } {
            match maybe_line {
                Some((line_start, line_end)) => {
                    if self.preferred_col.is_none() {
                        self.preferred_col = Some(target_col);
                    }
                    self.move_to_display_col_on_line(line_start, line_end, target_col);
                    return;
                }
                None => {
                    self.cursor_pos = 0;
                    self.preferred_col = None;
                    return;
                }
            }
        }
        // fallback:逻辑行
        if let Some(prev_nl) = self.text[..self.cursor_pos].rfind('\n') {
            let target_col = match self.preferred_col {
                Some(c) => c,
                None => {
                    let c = self.current_display_col();
                    self.preferred_col = Some(c);
                    c
                }
            };
            let prev_line_start = self.text[..prev_nl].rfind('\n').map(|i| i + 1).unwrap_or(0);
            self.move_to_display_col_on_line(prev_line_start, prev_nl, target_col);
        } else {
            self.cursor_pos = 0;
            self.preferred_col = None;
        }
    }

    pub fn move_cursor_down(&mut self) {
        if let Some((target_col, maybe_line)) = {
            let cache_ref = self.wrap_cache.borrow();
            if let Some(cache) = cache_ref.as_ref() {
                let lines = &cache.lines;
                if let Some(idx) = Self::wrapped_line_index_by_start(lines, self.cursor_pos) {
                    let cur_range = &lines[idx];
                    let target_col = self
                        .preferred_col
                        .unwrap_or_else(|| self.text[cur_range.start..self.cursor_pos].width());
                    if idx + 1 < lines.len() {
                        let next = &lines[idx + 1];
                        Some((target_col, Some((next.start, next.end))))
                    } else {
                        Some((target_col, None))
                    }
                } else {
                    None
                }
            } else {
                None
            }
        } {
            match maybe_line {
                Some((line_start, line_end)) => {
                    if self.preferred_col.is_none() {
                        self.preferred_col = Some(target_col);
                    }
                    self.move_to_display_col_on_line(line_start, line_end, target_col);
                    return;
                }
                None => {
                    self.cursor_pos = self.text.len();
                    self.preferred_col = None;
                    return;
                }
            }
        }
        let target_col = match self.preferred_col {
            Some(c) => c,
            None => {
                let c = self.current_display_col();
                self.preferred_col = Some(c);
                c
            }
        };
        if let Some(next_nl) = self.text[self.cursor_pos..].find('\n').map(|i| i + self.cursor_pos) {
            let next_line_start = next_nl + 1;
            let next_line_end = self.text[next_line_start..]
                .find('\n')
                .map(|i| i + next_line_start)
                .unwrap_or(self.text.len());
            self.move_to_display_col_on_line(next_line_start, next_line_end, target_col);
        } else {
            self.cursor_pos = self.text.len();
            self.preferred_col = None;
        }
    }

    pub fn move_cursor_to_beginning_of_line(&mut self, _move_up_at_bol: bool) {
        self.cursor_pos = self.beginning_of_current_line();
        self.preferred_col = None;
    }
    pub fn move_cursor_to_end_of_line(&mut self, _move_down_at_eol: bool) {
        self.cursor_pos = self.end_of_current_line();
        self.preferred_col = None;
    }

    // ── wrap / 光标定位 ──
    pub fn desired_height(&self, width: u16) -> u16 {
        self.wrapped_lines(width).len() as u16
    }

    fn wrapped_line_index_by_start(lines: &[Range<usize>], pos: usize) -> Option<usize> {
        let idx = lines.partition_point(|r| r.start <= pos);
        if idx == 0 { None } else { Some(idx - 1) }
    }

    /// 算光标屏幕坐标(area 内绝对坐标,含 area.x/y offset)。
    pub fn cursor_pos_with_state(&self, area: Rect, state: &TextareaState) -> Option<(u16, u16)> {
        let lines = self.wrapped_lines(area.width);
        let effective_scroll = self.effective_scroll(area.height, &lines, state.scroll);
        let i = Self::wrapped_line_index_by_start(&lines, self.cursor_pos)?;
        let ls = &lines[i];
        let col = self.text[ls.start..self.cursor_pos].width() as u16;
        let screen_row = i.saturating_sub(effective_scroll as usize).try_into().unwrap_or(0);
        Some((area.x + col, area.y + screen_row))
    }

    fn wrapped_lines(&self, width: u16) -> Vec<Range<usize>> {
        let w = width.max(1); // 宽 0 不 wrap(避死循环),至少 1
        {
            let mut cache = self.wrap_cache.borrow_mut();
            let needs = match cache.as_ref() {
                Some(c) => c.width != w,
                None => true,
            };
            if needs {
                let lines = wrap_ranges(&self.text, w);
                *cache = Some(WrapCache { width: w, lines });
            }
        }
        self.wrap_cache.borrow().as_ref().unwrap().lines.clone()
    }

    /// scroll 保证光标可见;fits 则不滚。
    fn effective_scroll(&self, area_height: u16, lines: &[Range<usize>], current_scroll: u16) -> u16 {
        let total_lines = lines.len() as u16;
        if area_height >= total_lines {
            return 0;
        }
        let cursor_line_idx = Self::wrapped_line_index_by_start(lines, self.cursor_pos).unwrap_or(0) as u16;
        let max_scroll = total_lines.saturating_sub(area_height);
        let mut scroll = current_scroll.min(max_scroll);
        if cursor_line_idx < scroll {
            scroll = cursor_line_idx;
        } else if cursor_line_idx >= scroll + area_height {
            scroll = cursor_line_idx + 1 - area_height;
        }
        scroll
    }

    // ── render(0.28 普通 fn,非 WidgetRef trait)──
    /// 画 wrap 文本 + 更新 state.scroll。光标 reverse 由调用方(control.rs)做。
    pub fn render(&self, area: Rect, buf: &mut Buffer, state: &mut TextareaState) {
        let lines = self.wrapped_lines(area.width);
        let scroll = self.effective_scroll(area.height, &lines, state.scroll);
        state.scroll = scroll;
        let start = scroll as usize;
        let end = (start + area.height as usize).min(lines.len());
        for (row, idx) in (start..end).enumerate() {
            let r = &lines[idx];
            let y = area.y + row as u16;
            if y >= area.bottom() {
                break;
            }
            let s = &self.text[r.start..r.end.min(self.text.len())];
            buf.set_string(area.x, y, s, Style::default());
        }
    }

    /// 兼容 state.rs 的薄包装:转发编辑键。Enter/Newline/Up/Down 不在此(state 层管)。
    pub fn handle_key(&mut self, k: &KeyEvent) -> TextareaOp {
        match k.code {
            KeyCode::Backspace => { self.delete_backward(1); TextareaOp::Backspace }
            KeyCode::Delete => { self.delete_forward(1); TextareaOp::Backspace }
            KeyCode::Left => { self.move_cursor_left(); TextareaOp::Left }
            KeyCode::Right => { self.move_cursor_right(); TextareaOp::Right }
            KeyCode::Home => { self.move_cursor_to_beginning_of_line(false); TextareaOp::Home }
            KeyCode::End => { self.move_cursor_to_end_of_line(false); TextareaOp::End }
            KeyCode::Char(c) => { self.insert_str(&c.to_string()); TextareaOp::Insert(c) }
            _ => TextareaOp::None,
        }
    }

    /// 显式触发换行(state 层 Ctrl+J / Shift+Enter / paste \n 用)。
    pub fn insert_newline(&mut self) {
        self.insert_str("\n");
    }
}

impl Default for Textarea {
    fn default() -> Self {
        Self::new()
    }
}

/// wrap 文本为 Vec<Range<usize>>(byte range)。
/// ponytail: codex 用 unsafe offset_from;此处用 ptr-to-usize 减法(无 unsafe block,
/// 对 textwrap 返回的 Borrowed slice 正确——FirstFit 不 copy,ptr 在原 text 范围内)。
fn wrap_ranges(text: &str, width: u16) -> Vec<Range<usize>> {
    let opts = Options::new(width as usize).wrap_algorithm(textwrap::WrapAlgorithm::FirstFit);
    let text_ptr = text.as_ptr() as usize;
    let mut ranges = Vec::new();
    for cow in textwrap::wrap(text, &opts).iter() {
        let s = cow.as_ref();
        let start = s.as_ptr() as usize - text_ptr;
        let end = start + s.len();
        ranges.push(start..end);
    }
    if ranges.is_empty() {
        ranges.push(0..0);
    }
    ranges
}

#[cfg(test)]
mod tests {
    use super::*;
    use crossterm::event::KeyModifiers;

    #[test]
    fn new_is_empty() {
        let t = Textarea::new();
        assert_eq!(t.text(), "");
        assert_eq!(t.cursor(), 0);
        assert!(t.is_empty());
    }

    #[test]
    fn insert_advances_cursor() {
        let mut t = Textarea::new();
        t.insert_str("abc");
        assert_eq!(t.text(), "abc");
        assert_eq!(t.cursor(), 3);
    }

    #[test]
    fn insert_str_at_middle() {
        let mut t = Textarea::new();
        t.set_text("ac");
        t.set_cursor(1);
        t.insert_str("b");
        assert_eq!(t.text(), "abc");
        assert_eq!(t.cursor(), 2);
        t.insert_str("xy\nz");
        assert_eq!(t.text(), "abxy\nzc");
        assert_eq!(t.line_count(), 2);
    }

    #[test]
    fn replace_range_updates_cursor_three_states() {
        let mut t = Textarea::new();
        t.set_text("hello world");
        t.set_cursor(2);
        t.replace_range_raw(5..11, "WORLD");
        assert_eq!(t.text(), "helloWORLD");
        assert_eq!(t.cursor(), 2, "cursor before range 不动");
        t.set_text("hello world");
        t.set_cursor(7);
        t.replace_range_raw(5..11, "X");
        assert_eq!(t.cursor(), 6, "cursor inside → range start + inserted");
        t.set_text("hello world");
        t.set_cursor(11);
        t.replace_range_raw(0..5, "HI");
        assert_eq!(t.cursor(), 8, "cursor after → shift by diff");
    }

    #[test]
    fn delete_backward_forward_edges() {
        let mut t = Textarea::new();
        t.set_text("abc");
        t.delete_backward(1);
        assert_eq!(t.text(), "ab");
        t.set_cursor(0);
        t.delete_forward(1);
        assert_eq!(t.text(), "b");
    }

    #[test]
    fn cursor_left_right_handle_graphemes() {
        let mut t = Textarea::new();
        t.set_text("a👍b"); // 6 byte:a(1)+👍(4)+b(1);cursor=6
        t.move_cursor_left();
        assert_eq!(t.cursor(), 5, "left once → b 前(grapheme 边界 6→5)");
        t.move_cursor_left();
        assert_eq!(t.cursor(), 1, "left twice → 👍 前(跳过 4-byte grapheme)");
        t.move_cursor_left();
        assert_eq!(t.cursor(), 0);
        t.move_cursor_right();
        assert_eq!(t.cursor(), 1);
        t.move_cursor_right();
        assert_eq!(t.cursor(), 5, "right 跳过 👍 到 b 前");
    }

    #[test]
    fn multibyte_cursor_safe() {
        let mut t = Textarea::new();
        t.set_text("你好");
        t.move_cursor_left();
        assert_eq!(t.cursor(), 3, "中文 left 按 char 非 byte");
        t.delete_backward(1);
        assert_eq!(t.text(), "好");
    }

    #[test]
    fn line_count_counts_newlines() {
        let mut t = Textarea::new();
        assert_eq!(t.line_count(), 1);
        t.set_text("abc");
        assert_eq!(t.line_count(), 1);
        t.set_text("a\nb\nc");
        assert_eq!(t.line_count(), 3);
    }

    #[test]
    fn handle_key_insert_and_backspace() {
        let mut t = Textarea::new();
        let op = t.handle_key(&KeyEvent::new(KeyCode::Char('x'), KeyModifiers::NONE));
        assert_eq!(op, TextareaOp::Insert('x'));
        assert_eq!(t.text(), "x");
        let op = t.handle_key(&KeyEvent::new(KeyCode::Backspace, KeyModifiers::NONE));
        assert_eq!(op, TextareaOp::Backspace);
        assert_eq!(t.text(), "");
    }

    #[test]
    fn handle_key_left_right_home_end() {
        let mut t = Textarea::new();
        t.set_text("abc");
        t.handle_key(&KeyEvent::new(KeyCode::Home, KeyModifiers::NONE));
        assert_eq!(t.cursor(), 0);
        t.handle_key(&KeyEvent::new(KeyCode::End, KeyModifiers::NONE));
        assert_eq!(t.cursor(), 3);
        t.handle_key(&KeyEvent::new(KeyCode::Left, KeyModifiers::NONE));
        assert_eq!(t.cursor(), 2);
        t.handle_key(&KeyEvent::new(KeyCode::Right, KeyModifiers::NONE));
        assert_eq!(t.cursor(), 3);
    }

    #[test]
    fn clear_resets() {
        let mut t = Textarea::new();
        t.set_text("abc");
        t.clear();
        assert_eq!(t.text(), "");
        assert_eq!(t.cursor(), 0);
    }

    // bug1 回归:单行超长 wrap 后 desired_height 多行
    #[test]
    fn desired_height_counts_wrapped_lines() {
        let mut t = Textarea::new();
        t.set_text(&"a".repeat(80));
        let h = t.desired_height(20);
        assert!(h >= 4, "80 字符 @width 20 应 wrap ≥4 行,实际 {}", h);
        assert_eq!(t.desired_height(200), 1);
    }

    #[test]
    fn cursor_pos_with_state_wraps() {
        let mut t = Textarea::new();
        t.set_text("hello world here");
        let lines = t.wrapped_lines(6);
        assert!(lines.len() >= 2, "宽 6 应 wrap 多行");
        let st = TextareaState::default();
        let pos = t.cursor_pos_with_state(Rect::new(0, 0, 6, 10), &st);
        assert!(pos.is_some());
        let (_cx, cy) = pos.unwrap();
        assert!(cy >= 2, "cursor 在末应在 wrap 后第 3 行附近,y={}", cy);
    }

    #[test]
    fn wrapped_navigation_up_down() {
        let mut t = Textarea::new();
        t.set_text("abcdefghij");
        let _ = t.wrapped_lines(4);
        t.set_cursor(10);
        t.move_cursor_up();
        assert!(t.cursor() < 10, "up 应移到上一行");
        t.move_cursor_down();
        assert_eq!(t.cursor(), 10, "down 回末行");
    }

    #[test]
    fn empty_text_renders_without_panic() {
        let t = Textarea::new();
        let mut st = TextareaState::default();
        let mut buf = ratatui::buffer::Buffer::empty(Rect::new(0, 0, 10, 3));
        t.render(Rect::new(0, 0, 10, 3), &mut buf, &mut st);
    }
}
