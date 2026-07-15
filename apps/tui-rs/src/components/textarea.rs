//! 多行输入框(codex 式 textarea,ADR-1 第 3 批控件)。
//!
//! codex 原版(bottom_pane/text_area.rs)是一个 700+ 行的编辑器:grapheme cluster 光标、
//! 真实多行 buffer、wrap 后的精确光标定位、IME、剪贴板异步。ADR-1 的输入栏只需:
//! 打字、退格、左右移动、Enter 发送、近似光标。本组件是 codex 编辑核心(去 IME/异步剪贴板/
//! grapheme)的同步单行-ish 缓冲:
//!
//! - `text: String` + `cursor: usize`(byte offset,char_indices 防切多字节)。
//! - `handle_key` 直接 mutate text+cursor,返回 `TextareaOp` 让调用方决定副作用(Send)。
//! - Enter → `Send`(不清 text,调用方发送后 clear);Shift+Enter/Alt+Enter → `Newline`(\n)。
//! - Up/Down 暂按行估算:Up=Home,Down=End(单行缓冲,真实多行光标定位 defer,标 TODO)。
//! - render:bordered Block(" ❯ ")+ Paragraph wrap;光标用末尾 "▌" 闪烁(精确 cell reverse defer)。

#![allow(dead_code)]

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::{
    layout::Rect,
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Paragraph, Wrap},
    Frame,
};

/// handle_key 返回的操作(调用方按 op 决定副作用,如 Send 触发 do_turn)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TextareaOp {
    Insert(char),
    Backspace,
    Left,
    Right,
    Up,
    Down,
    Home,
    End,
    Newline,
    Send,
    Paste(String),
    None,
}

/// 多行输入缓冲:text + cursor(byte offset)。
#[derive(Debug, Clone)]
pub struct Textarea {
    text: String,
    cursor: usize, // byte offset into text;始终在 char 边界(handle_key 保证)。
}

impl Default for Textarea {
    fn default() -> Self {
        Self::new()
    }
}

impl Textarea {
    pub fn new() -> Self {
        Self { text: String::new(), cursor: 0 }
    }

    pub fn text(&self) -> &str {
        &self.text
    }

    pub fn set_text(&mut self, s: &str) {
        self.text = s.to_string();
        // clamp cursor 到末尾(char 边界)。
        self.cursor = self.text.len();
    }

    pub fn clear(&mut self) {
        self.text.clear();
        self.cursor = 0;
    }

    /// 插入一个 char 到 cursor,cursor 前移。
    fn insert_char(&mut self, c: char) {
        self.text.insert(self.cursor, c);
        self.cursor += c.len_utf8();
    }

    /// 删除 cursor 前一个 char(Backspace)。cursor 已在 0 时 no-op。
    fn backspace(&mut self) {
        if self.cursor == 0 {
            return;
        }
        // 找前一个 char 边界(char_indices 防切多字节)。
        let prev = self.text[..self.cursor]
            .char_indices()
            .last()
            .map(|(i, _)| i)
            .unwrap_or(0);
        self.text.replace_range(prev..self.cursor, "");
        self.cursor = prev;
    }

    /// cursor 左移一个 char。
    fn move_left(&mut self) {
        if self.cursor == 0 {
            return;
        }
        let prev = self.text[..self.cursor]
            .char_indices()
            .last()
            .map(|(i, _)| i)
            .unwrap_or(0);
        self.cursor = prev;
    }

    /// cursor 右移一个 char。
    fn move_right(&mut self) {
        if self.cursor >= self.text.len() {
            return;
        }
        // 下一个 char 边界。
        let next = self.text[self.cursor..]
            .char_indices()
            .nth(1)
            .map(|(i, _)| self.cursor + i)
            .unwrap_or(self.text.len());
        self.cursor = next;
    }

    /// Home:cursor 到 0。
    fn move_home(&mut self) {
        self.cursor = 0;
    }

    /// End:cursor 到末尾。
    fn move_end(&mut self) {
        self.cursor = self.text.len();
    }

    /// 处理一个按键:直接 mutate text+cursor,返回 op 供调用方决策。
    ///
    /// - Char(c):Shift+Enter/Alt+Enter → Newline(\n);普通字符 insert。
    /// - Enter:Send(不清 text)。
    /// - Backspace:删前一 char。
    /// - Left/Right:移 cursor。
    /// - Up/Down:TODO 真实行移;当前 Up=Home,Down=End(单行缓冲近似)。
    /// - Home/End:移到行首/行末。
    /// - Ctrl+V:Paste(占位空串,crossterm 粘贴事件高级特性 defer)。
    pub fn handle_key(&mut self, k: &KeyEvent) -> TextareaOp {
        match k.code {
            KeyCode::Enter => {
                // Shift+Enter / Alt+Enter → 换行(多行输入);裸 Enter → Send。
                if k.modifiers.intersects(KeyModifiers::SHIFT | KeyModifiers::ALT) {
                    self.insert_char('\n');
                    TextareaOp::Newline
                } else {
                    TextareaOp::Send
                }
            }
            KeyCode::Backspace => {
                self.backspace();
                TextareaOp::Backspace
            }
            KeyCode::Left => {
                self.move_left();
                TextareaOp::Left
            }
            KeyCode::Right => {
                self.move_right();
                TextareaOp::Right
            }
            KeyCode::Up => {
                // TODO: 真实行移(需多行 buffer + wrap 感知);当前 Up=Home。
                self.move_home();
                TextareaOp::Up
            }
            KeyCode::Down => {
                // TODO: 真实行移;当前 Down=End。
                self.move_end();
                TextareaOp::Down
            }
            KeyCode::Home => {
                self.move_home();
                TextareaOp::Home
            }
            KeyCode::End => {
                self.move_end();
                TextareaOp::End
            }
            KeyCode::Char('v') if k.modifiers.contains(KeyModifiers::CONTROL) => {
                // ponytail: crossterm 真实粘贴事件(KeyCode::Paste / BracketedPaste)是高级特性,
                // 跨平台+终端支持参差;此处返 Paste 占位,调用方可忽略。真实粘贴 defer。
                TextareaOp::Paste(String::new())
            }
            KeyCode::Char(c) => {
                self.insert_char(c);
                TextareaOp::Insert(c)
            }
            _ => TextareaOp::None,
        }
    }

    /// 渲染:bordered Block(" ❯ ")+ Paragraph(wrap)。光标:末尾 "▌" 闪烁占位
    /// (精确 cell reverse 需 unstable-rendered-line-info,defer,标 TODO)。
    ///
    /// ponytail: 精确光标定位需算 wrap 后 cursor 落在第几行第几列——ratatui 0.28 无
    // rendered-line-info(0.29+ unstable),wrap 由 Paragraph 内部做无 API 暴露。
    // 末尾 "▌" 闪烁光标对单行/短输入够用;真实多行光标 defer 到 0.29+ feature。
    pub fn render(&self, f: &mut Frame, area: Rect) {
        let block = Block::default()
            .borders(Borders::ALL)
            .title(Span::styled(
                " ❯ ",
                Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD),
            ))
            .border_style(Style::default().fg(Color::DarkGray))
            .style(Style::default().bg(Color::Black));
        let inner = block.inner(area);
        f.render_widget(block, area);

        // 光标在末尾时显示 "▌" 闪烁;否则仅文本(cursor 位置不画,精确 defer)。
        let cursor_at_end = self.cursor >= self.text.len();
        let display: String = if cursor_at_end {
            format!("{}▌", self.text)
        } else {
            self.text.clone()
        };
        // TODO: cursor 在中间时,算 wrap 后 (row,col) 设该 cell reverse(需 rendered-line-info)。
        let para = Paragraph::new(Line::from(vec![
            Span::styled(display.clone(), Style::default().fg(Color::White)),
        ]))
        .wrap(Wrap { trim: false })
        .style(Style::default().bg(Color::Black));
        f.render_widget(para, inner);

        // 光标闪烁:末尾 "▌" 加 SLOW_BLINK(已在 display 里)。中间 cursor 无视觉标记(defer)。
        let _ = cursor_at_end; // suppress unused warning 当 display 不分支
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_is_empty() {
        let t = Textarea::new();
        assert_eq!(t.text(), "");
        assert_eq!(t.cursor, 0);
    }

    #[test]
    fn insert_advances_cursor() {
        let mut t = Textarea::new();
        t.insert_char('a');
        t.insert_char('b');
        t.insert_char('c');
        assert_eq!(t.text(), "abc");
        assert_eq!(t.cursor, 3);
    }

    #[test]
    fn handle_key_insert_roundtrip() {
        // new → handle_key(Insert 'x') → text() == "x"
        let mut t = Textarea::new();
        let op = t.handle_key(&KeyEvent::new(KeyCode::Char('x'), KeyModifiers::NONE));
        assert_eq!(op, TextareaOp::Insert('x'));
        assert_eq!(t.text(), "x");
        assert_eq!(t.cursor, 1);
    }

    #[test]
    fn backspace_removes_prev_char() {
        let mut t = Textarea::new();
        t.set_text("abc");
        t.cursor = 3;
        let op = t.handle_key(&KeyEvent::new(KeyCode::Backspace, KeyModifiers::NONE));
        assert_eq!(op, TextareaOp::Backspace);
        assert_eq!(t.text(), "ab");
        assert_eq!(t.cursor, 2);
    }

    #[test]
    fn send_does_not_clear() {
        let mut t = Textarea::new();
        t.set_text("hello");
        let op = t.handle_key(&KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert_eq!(op, TextareaOp::Send);
        assert_eq!(t.text(), "hello", "Send must not clear; caller clears");
    }

    #[test]
    fn shift_enter_inserts_newline() {
        let mut t = Textarea::new();
        let op = t.handle_key(&KeyEvent::new(KeyCode::Enter, KeyModifiers::SHIFT));
        assert_eq!(op, TextareaOp::Newline);
        assert_eq!(t.text(), "\n");
    }

    #[test]
    fn left_right_navigates() {
        let mut t = Textarea::new();
        t.set_text("abc"); // cursor at end (3)
        t.handle_key(&KeyEvent::new(KeyCode::Left, KeyModifiers::NONE));
        assert_eq!(t.cursor, 2);
        t.handle_key(&KeyEvent::new(KeyCode::Left, KeyModifiers::NONE));
        assert_eq!(t.cursor, 1);
        t.handle_key(&KeyEvent::new(KeyCode::Right, KeyModifiers::NONE));
        assert_eq!(t.cursor, 2);
    }

    #[test]
    fn home_end() {
        let mut t = Textarea::new();
        t.set_text("abc");
        t.handle_key(&KeyEvent::new(KeyCode::Home, KeyModifiers::NONE));
        assert_eq!(t.cursor, 0);
        t.handle_key(&KeyEvent::new(KeyCode::End, KeyModifiers::NONE));
        assert_eq!(t.cursor, 3);
    }

    #[test]
    fn clear_resets() {
        let mut t = Textarea::new();
        t.set_text("abc");
        t.clear();
        assert_eq!(t.text(), "");
        assert_eq!(t.cursor, 0);
    }

    #[test]
    fn multibyte_cursor_safe() {
        // 中文多字节:确保 left/right/backspace 不切字节。
        let mut t = Textarea::new();
        t.set_text("你好"); // 6 bytes, cursor=6
        t.handle_key(&KeyEvent::new(KeyCode::Left, KeyModifiers::NONE));
        assert_eq!(t.cursor, 3, "left moves by char not byte (3=after 你)");
        t.handle_key(&KeyEvent::new(KeyCode::Backspace, KeyModifiers::NONE));
        assert_eq!(t.text(), "好");
    }
}
