//! 启发式粘贴检测(codex 式,乐观插入版)。
//!
//! burst 窗口内的 \n/Enter → 插入换行(非提交);超时 → 一次性 insert 整段。
//! 移植自 codex-rs/tui/src/bottom_pane/paste_burst.rs,但去 pending_first_char:
//! codex 用 pending 避免"打字前缀渲染后 paste 来了 retro-grab 闪烁",依赖快速 tick(9ms)flush。
//! v2 poll 慢(500-1000ms 省 CPU),pending 会导致首字符延迟显示 → 改乐观插入:
//! 非 burst 字符立即进 textarea;仅 burst(≥3 快速字符)才 retro-grab + buffer。
//! 代价:粘贴前缀短暂先显示再 retro-grab(闪烁),可接受。

#![allow(dead_code)]

use std::time::{Duration, Instant};

// 实测阈值(codex 调优):8ms 内 ≥3 字符判为 paste burst;Enter 抑制窗口 120ms。
const PASTE_BURST_MIN_CHARS: u16 = 3;
const PASTE_BURST_CHAR_INTERVAL: Duration = Duration::from_millis(8);
const PASTE_ENTER_SUPPRESS_WINDOW: Duration = Duration::from_millis(120);

#[derive(Default)]
pub struct PasteBurst {
    last_plain_char_time: Option<Instant>,
    consecutive_plain_char_burst: u16,
    burst_window_until: Option<Instant>,
    buffer: String,
    active: bool,
}

#[derive(Debug)]
pub enum CharDecision {
    /// 非 burst:立即插入 textarea(state 层 insert)。
    Typed(char),
    /// 开始缓冲,retro-grab 已插入的若干字符进 buffer。
    BeginBuffer { retro_chars: u16 },
    /// 缓冲中:追加当前字符到 buffer(不立即插入)。
    BufferAppend,
}

pub struct RetroGrab {
    pub start_byte: usize,
    pub grabbed: String,
}

#[derive(Debug)]
pub enum FlushResult {
    Paste(String),
    None,
}

impl PasteBurst {
    /// 普通字符决策:非 burst → Typed(立即插);≥3 快速 → BeginBuffer(retro);active → BufferAppend。
    pub fn on_plain_char(&mut self, ch: char, now: Instant) -> CharDecision {
        match self.last_plain_char_time {
            Some(prev) if now.duration_since(prev) <= PASTE_BURST_CHAR_INTERVAL => {
                self.consecutive_plain_char_burst =
                    self.consecutive_plain_char_burst.saturating_add(1)
            }
            _ => self.consecutive_plain_char_burst = 1,
        }
        self.last_plain_char_time = Some(now);

        if self.active {
            self.burst_window_until = Some(now + PASTE_ENTER_SUPPRESS_WINDOW);
            return CharDecision::BufferAppend;
        }

        if self.consecutive_plain_char_burst >= PASTE_BURST_MIN_CHARS {
            return CharDecision::BeginBuffer {
                retro_chars: self.consecutive_plain_char_burst.saturating_sub(1),
            };
        }

        CharDecision::Typed(ch)
    }

    /// 超时则 flush:active buffer → Paste(整段)。
    pub fn flush_if_due(&mut self, now: Instant) -> FlushResult {
        let timed_out = self
            .last_plain_char_time
            .is_some_and(|t| now.duration_since(t) > PASTE_BURST_CHAR_INTERVAL);
        if timed_out && self.is_active_internal() {
            self.active = false;
            let out = std::mem::take(&mut self.buffer);
            FlushResult::Paste(out)
        } else {
            FlushResult::None
        }
    }

    /// burst 中:\n 累积进 buffer 而非提交。
    pub fn append_newline_if_active(&mut self, now: Instant) -> bool {
        if self.is_active() {
            self.buffer.push('\n');
            self.burst_window_until = Some(now + PASTE_ENTER_SUPPRESS_WINDOW);
            true
        } else {
            false
        }
    }

    /// 判 Enter 应插入换行(burst 上下文)还是提交。
    pub fn newline_should_insert_instead_of_submit(&self, now: Instant) -> bool {
        let in_burst_window = self.burst_window_until.is_some_and(|until| now <= until);
        self.is_active() || in_burst_window
    }

    pub fn extend_window(&mut self, now: Instant) {
        self.burst_window_until = Some(now + PASTE_ENTER_SUPPRESS_WINDOW);
    }

    pub fn begin_with_retro_grabbed(&mut self, grabbed: String, now: Instant) {
        if !grabbed.is_empty() {
            self.buffer.push_str(&grabbed);
        }
        self.active = true;
        self.burst_window_until = Some(now + PASTE_ENTER_SUPPRESS_WINDOW);
    }

    pub fn append_char_to_buffer(&mut self, ch: char, now: Instant) {
        self.buffer.push(ch);
        self.burst_window_until = Some(now + PASTE_ENTER_SUPPRESS_WINDOW);
    }

    /// retro-grab:从 cursor 前抓 retro_chars 个字符作 paste 前缀(含空白或 ≥16 字符才判 paste)。
    pub fn decide_begin_buffer(
        &mut self,
        now: Instant,
        before: &str,
        retro_chars: usize,
    ) -> Option<RetroGrab> {
        let start_byte = retro_start_index(before, retro_chars);
        let grabbed = before[start_byte..].to_string();
        let looks_pastey =
            grabbed.chars().any(|c| c.is_whitespace()) || grabbed.chars().count() >= 16;
        if looks_pastey {
            self.begin_with_retro_grabbed(grabbed.clone(), now);
            Some(RetroGrab { start_byte, grabbed })
        } else {
            None
        }
    }

    /// 非 char 输入(Backspace/方向键等)前先 flush buffer。
    pub fn flush_before_modified_input(&mut self) -> Option<String> {
        if self.is_active() {
            self.active = false;
            Some(std::mem::take(&mut self.buffer))
        } else {
            None
        }
    }

    pub fn clear_window_after_non_char(&mut self) {
        self.consecutive_plain_char_burst = 0;
        self.last_plain_char_time = None;
        self.burst_window_until = None;
        self.active = false;
    }

    pub fn is_active(&self) -> bool {
        self.is_active_internal()
    }

    fn is_active_internal(&self) -> bool {
        self.active || !self.buffer.is_empty()
    }

    /// 显式 paste(bracketed)到达后清状态。
    pub fn clear_after_explicit_paste(&mut self) {
        self.last_plain_char_time = None;
        self.consecutive_plain_char_burst = 0;
        self.burst_window_until = None;
        self.active = false;
        self.buffer.clear();
    }
}

pub fn retro_start_index(before: &str, retro_chars: usize) -> usize {
    if retro_chars == 0 {
        return before.len();
    }
    before
        .char_indices()
        .rev()
        .nth(retro_chars.saturating_sub(1))
        .map(|(idx, _)| idx)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn t(ms: u64) -> Instant {
        Instant::now()
            .checked_sub(Duration::from_millis(100))
            .unwrap()
            + Duration::from_millis(ms)
    }

    #[test]
    fn typed_when_not_burst() {
        // 单字符(无前序快速)→ Typed(立即插,不缓冲)
        let mut pb = PasteBurst::default();
        let d = pb.on_plain_char('a', t(0));
        assert!(matches!(d, CharDecision::Typed('a')), "单字符应 Typed");
    }

    #[test]
    fn begin_buffer_on_third_fast_char() {
        // 3 个快速字符(8ms 内):前 2 个 Typed,第 3 个 BeginBuffer{retro=2}
        let mut pb = PasteBurst::default();
        let t0 = t(0);
        assert!(matches!(pb.on_plain_char('a', t0), CharDecision::Typed('a')));
        assert!(matches!(pb.on_plain_char('b', t0), CharDecision::Typed('b')));
        match pb.on_plain_char('c', t0) {
            CharDecision::BeginBuffer { retro_chars } => assert_eq!(retro_chars, 2),
            other => panic!("第3快速字符应 BeginBuffer, 得 {:?}", other),
        }
    }

    #[test]
    fn newline_in_burst_inserts_not_submit() {
        let mut pb = PasteBurst::default();
        let t0 = t(0);
        // 建 burst(模拟 BeginBuffer 后 active)
        pb.begin_with_retro_grabbed("ab".to_string(), t0);
        assert!(pb.append_newline_if_active(t0), "burst 中 \\n 应插入(返 true)");
    }

    #[test]
    fn newline_outside_burst_does_not_insert() {
        let mut pb = PasteBurst::default();
        assert!(!pb.append_newline_if_active(t(0)), "非 burst \\n 不插入(返 false)");
    }
}
