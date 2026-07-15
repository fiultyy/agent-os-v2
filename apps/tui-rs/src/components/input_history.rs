//! 输入历史 ring(照抄 openai/codex `bottom_pane/chat_composer_history.rs` 的 shell-style 回溯)。
//!
//! ADR-7 契约:`InputHistory::new()` / `push(msg)` / `prev()/next() -> Option<&str>`。
//!
//! codex 原版把「持久化跨会话条目 + 本会话条目 + Ctrl+R 增量搜索 + 异步 AppEvent 回填」揉在
//! 一个状态机里(700+ 行,依赖 codex-protocol/codex-roles)。ADR-1 的输入栏只需要 shell 式
//! Up/Down 回溯,本组件是 codex 导航核心(去掉持久化/搜索/异步)的纯内存 ring:
//!
//! - `cursor: None` = 未在回溯。
//! - `prev()`(Up):`None`→最新;`idx 0`→`None`(已到最旧,不越界);否则 `idx-1`。
//! - `next()`(Down):`None`→`None`(没在回溯);越过最新→`None`(退出回溯,调用方清空草稿);
//!   否则 `idx+1`。
//! - `push`:空串忽略、相邻重复折叠、重置 cursor(新条目改变 offset 空间)。
//!
//! ponytail: codex 的 last_history_text/cursor-at-line-boundary 门控属于 textarea 编辑器职责
//! (T-textarea),不属历史 ring——历史只暴露 prev/next,门控留给调用方。

/// 输入历史(纯内存,新条目 push 到尾部,Up=prev 向旧,Down=next 向新)。
#[derive(Debug, Default, Clone)]
pub struct InputHistory {
    entries: Vec<String>,
    /// 当前回溯游标(`None` = 未回溯;`Some(i)` 指向 `entries[i]`)。
    cursor: Option<usize>,
}

impl InputHistory {
    pub fn new() -> Self {
        Self::default()
    }

    /// 记录一条提交。空串忽略、相邻重复折叠、重置回溯游标。
    ///
    /// cursor 必须重置:新条目改变 offset 空间,继续用旧 cursor 会指到错条目
    /// (照抄 codex `record_local_submission` 的 `history_cursor = None`)。
    pub fn push(&mut self, msg: impl Into<String>) {
        let msg = msg.into();
        if msg.is_empty() {
            return;
        }
        if self.entries.last().is_some_and(|prev| prev == &msg) {
            self.cursor = None;
            return;
        }
        self.entries.push(msg);
        self.cursor = None;
    }

    /// 回溯上一条(更旧)。首次从最新开始;到最旧返回 `None` 不越界。
    pub fn prev(&mut self) -> Option<&str> {
        if self.entries.is_empty() {
            return None;
        }
        let next_idx = match self.cursor {
            None => self.entries.len() - 1,
            Some(0) => return None, // already at oldest
            Some(idx) => idx - 1,
        };
        self.cursor = Some(next_idx);
        Some(self.entries[next_idx].as_str())
    }

    /// 前进下一条(更新)。未回溯或越过最新返回 `None`(调用方清空草稿退出回溯)。
    pub fn next(&mut self) -> Option<&str> {
        let len = self.entries.len();
        let next_idx = match self.cursor {
            None => return None, // not browsing
            Some(idx) if idx + 1 >= len => {
                // Past newest – exit browsing.
                self.cursor = None;
                return None;
            }
            Some(idx) => idx + 1,
        };
        self.cursor = Some(next_idx);
        Some(self.entries[next_idx].as_str())
    }

    /// 重置回溯游标(下次 Up 从最新恢复)。新增/提交草稿时调用。
    pub fn reset(&mut self) {
        self.cursor = None;
    }

    /// 历史条目数。
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn push_ignores_empty_and_collapses_adjacent_dup() {
        let mut h = InputHistory::new();
        h.push("");
        assert!(h.is_empty());
        h.push("a");
        h.push("a"); // adjacent dup collapsed
        h.push("b");
        assert_eq!(h.len(), 2);
    }

    #[test]
    fn prev_walks_newest_to_oldest() {
        let mut h = InputHistory::new();
        h.push("first");
        h.push("second");
        h.push("third");
        assert_eq!(h.prev(), Some("third")); // newest
        assert_eq!(h.prev(), Some("second"));
        assert_eq!(h.prev(), Some("first")); // oldest
        assert_eq!(h.prev(), None); // boundary, no wrap
    }

    #[test]
    fn next_advances_and_exits_past_newest() {
        let mut h = InputHistory::new();
        h.push("a");
        h.push("b");
        h.prev(); // cursor at b
        assert_eq!(h.prev(), Some("a"));
        assert_eq!(h.next(), Some("b"));
        assert_eq!(h.next(), None); // past newest -> exit browsing
    }

    #[test]
    fn next_when_not_browsing_is_none() {
        let mut h = InputHistory::new();
        h.push("a");
        assert_eq!(h.next(), None);
    }

    #[test]
    fn push_resets_cursor() {
        let mut h = InputHistory::new();
        h.push("a");
        h.push("b");
        h.prev(); // b
        h.push("c"); // reset
        assert_eq!(h.prev(), Some("c")); // resumes from newest
    }

    #[test]
    fn empty_history_returns_none() {
        let mut h = InputHistory::new();
        assert_eq!(h.prev(), None);
        assert_eq!(h.next(), None);
    }
}
