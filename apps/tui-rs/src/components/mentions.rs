//! @ mention 候选弹窗(照抄 openai/codex `bottom_pane/mentions_v2/` 的形态,适配 ADR-7 契约)。
//!
//! codex 原版挂 codex_file_search + codex_utils_fuzzy_match + Plugin/Skill/File/Dir 多类型 +
//! ScrollState;ADR-7 把候选收敛成纯 `Vec<String>`(调用方传 session 列表),故本控件砍掉文件
//! 搜索 / 多类型 / 外部 fuzzy crate,用子串过滤替代(ponytail: 候选量 = session 数,O(n) 子串
//! 足够;真要 fuzzy 时再换 crate,无需现在拖依赖)。
//!
//! API(ADR-7):
//! - `Mentions::new(candidates: Vec<String>)`
//! - `trigger()` / `open()`:打开弹窗(`@` 调用方触发,或直接 open)
//! - `render_popup(f, area)`:渲染候选列表(选中高亮 + 边框)
//! - `select() -> Option<String>`:返回当前选中候选(Enter 提交时调用方取值)
//! - `set_query(&str)`:边打字边过滤(`@foo` 的 `foo` 部分喂进来)
//! - `handle_key(KeyEvent)`:Up/Down/Esc/Enter 路由(Enter/Esc 不在此消费,由调用方按
//!   TextareaOp::Send 决定;这里只移动光标)
//!
//! 调用方约定(textarea 集成时):光标前最近 `@` 后的文本 = query;Enter → `select()` 取值
//! 插回 textarea 并 `close()`;Esc → `close()`。

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::{
    layout::Rect,
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Clear, List, ListItem, ListState},
    Frame,
};

/// 弹窗建议最大可见行数(调用方据此裁剪 area 高度)。codex `MAX_POPUP_ROWS` 的等价物。
/// ponytail: ratatui List 自适应 area 高度滚动,故本控件不强制裁剪;此常量仅作 area 高度建议。
#[allow(dead_code)]
const MAX_VISIBLE_ROWS: usize = 8;

/// @ mention 候选弹窗。
///
/// `candidates` 是全量候选(session 列表,由调用方传入);`query` 是 `@` 后的过滤串;
/// `selected` 是当前高亮在 **过滤后列表** 中的下标。open=false 时 render_popup 是 no-op。
#[derive(Debug)]
pub struct Mentions {
    candidates: Vec<String>,
    query: String,
    selected: usize,
    open: bool,
}

impl Mentions {
    pub fn new(candidates: Vec<String>) -> Self {
        Self {
            candidates,
            query: String::new(),
            selected: 0,
            open: false,
        }
    }

    /// 打开弹窗(`@` 触发)。重置 query 与选中。
    pub fn trigger(&mut self) {
        self.open();
    }

    /// 显式打开。
    pub fn open(&mut self) {
        self.open = true;
        self.query.clear();
        self.selected = 0;
    }

    /// 关闭(Enter 提交后 / Esc / 失焦)。不清 candidates。
    pub fn close(&mut self) {
        self.open = false;
        self.query.clear();
        self.selected = 0;
    }

    pub fn is_open(&self) -> bool {
        self.open
    }

    /// 更新过滤串(`@` 后的文本)。重置选中到首条。
    pub fn set_query(&mut self, q: &str) {
        self.query = q.to_string();
        self.selected = 0;
    }

    /// 替换全量候选(session 列表刷新时)。
    pub fn set_candidates(&mut self, candidates: Vec<String>) {
        self.candidates = candidates;
        self.clamp_selected();
    }

    /// 过滤后的候选(子串匹配,大小写不敏感)。query 为空时返回全量。
    pub fn filtered(&self) -> Vec<&String> {
        let q = self.query.trim().to_lowercase();
        if q.is_empty() {
            return self.candidates.iter().collect();
        }
        self.candidates
            .iter()
            .filter(|c| c.to_lowercase().contains(&q))
            .collect()
    }

    /// 当前选中的候选(Enter 提交时调用方取值)。返回 clone 的 String(调用方要插回 textarea)。
    /// 弹窗未 open 时返回 None(无选择)。
    pub fn select(&self) -> Option<String> {
        if !self.open {
            return None;
        }
        let rows = self.filtered();
        rows.get(self.selected).map(|s| (*s).clone())
    }

    /// 键路由:Up/Down 移动光标(循环);其他键忽略(Enter/Esc 由调用方决策)。
    /// 返回 true 表示键被本控件消费(调用方不应再喂给 textarea)。
    pub fn handle_key(&mut self, key: KeyEvent) -> bool {
        if !self.open {
            return false;
        }
        match key.code {
            KeyCode::Up => {
                self.move_up();
                true
            }
            KeyCode::Down => {
                self.move_down();
                true
            }
            _ => false,
        }
    }

    fn move_up(&mut self) {
        let len = self.filtered().len();
        if len == 0 {
            return;
        }
        // 循环:到顶回到底(codex move_up_wrap 同款)。
        self.selected = if self.selected == 0 {
            len - 1
        } else {
            self.selected - 1
        };
    }

    fn move_down(&mut self) {
        let len = self.filtered().len();
        if len == 0 {
            return;
        }
        self.selected = (self.selected + 1) % len;
    }

    fn clamp_selected(&mut self) {
        let len = self.filtered().len();
        if len == 0 {
            self.selected = 0;
        } else if self.selected >= len {
            self.selected = len - 1;
        }
    }

    /// 渲染候选弹窗。未 open 时 no-op。area 由调用方定位(通常 textarea 上方)。
    /// 调用方应先 `f.render_widget(Clear, area)`(本函数自带 Clear)。
    pub fn render_popup(&self, f: &mut Frame, area: Rect) {
        if !self.open {
            return;
        }
        f.render_widget(Clear, area);

        let rows = self.filtered();
        let title = if self.query.is_empty() {
            " @ mentions ".to_string()
        } else {
            format!(" @ {} ", self.query)
        };
        let block = Block::default()
            .borders(Borders::ALL)
            .title(title)
            .border_style(Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD))
            .style(Style::default().bg(Color::DarkGray));

        if rows.is_empty() {
            // 空态:codex 的 "no matches" 等价。
            let inner = block.inner(area);
            f.render_widget(block, area);
            let msg = Line::from(vec![
                Span::raw("  "),
                Span::styled(
                    "no matches",
                    Style::default().add_modifier(Modifier::ITALIC),
                ),
            ]);
            f.render_widget(ratatui::widgets::Paragraph::new(msg), inner);
            return;
        }

        // 高亮选中字符(对齐 codex 的 "> " gutter + accent)。
        let items: Vec<ListItem> = rows
            .iter()
            .map(|c| {
                ListItem::new(Line::from(vec![
                    Span::raw("  "),
                    Span::styled(
                        c.as_str().to_string(),
                        Style::default().fg(Color::White),
                    ),
                ]))
            })
            .collect();

        let mut state = ListState::default();
        // 选中态:ListState::selected 接收过滤后列表的下标,与 self.selected 对齐。
        state.select(Some(self.selected));

        let list = List::new(items)
            .block(block)
            .highlight_style(
                Style::default()
                    .fg(Color::Black)
                    .bg(Color::Cyan)
                    .add_modifier(Modifier::BOLD),
            )
            .highlight_symbol("> ");

        f.render_stateful_widget(list, area, &mut state);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn candidates() -> Vec<String> {
        vec![
            "session-alpha".to_string(),
            "session-beta".to_string(),
            "session-gamma".to_string(),
            "other".to_string(),
        ]
    }

    #[test]
    fn closed_by_default_and_noop_render_selects_none() {
        let m = Mentions::new(candidates());
        assert!(!m.is_open());
        assert_eq!(m.select(), None, "closed popup selects nothing");
    }

    #[test]
    fn trigger_opens_and_resets_query() {
        let mut m = Mentions::new(candidates());
        m.set_query("stale");
        m.trigger();
        assert!(m.is_open());
        assert!(m.query.is_empty(), "trigger resets query");
        assert_eq!(m.select().as_deref(), Some("session-alpha"));
    }

    #[test]
    fn filter_is_case_insensitive_substring() {
        let mut m = Mentions::new(candidates());
        m.open();
        m.set_query("BETA");
        assert_eq!(
            m.filtered()
                .iter()
                .map(|s| s.as_str())
                .collect::<Vec<_>>(),
            vec!["session-beta"]
        );
        assert_eq!(m.select().as_deref(), Some("session-beta"));
    }

    #[test]
    fn empty_query_returns_all() {
        let mut m = Mentions::new(candidates());
        m.open();
        assert_eq!(m.filtered().len(), 4);
    }

    #[test]
    fn down_wraps_and_up_wraps() {
        let mut m = Mentions::new(candidates());
        m.open();
        // alpha(0) -> beta(1) -> gamma(2) -> other(3) -> alpha(0)
        for expected in ["session-beta", "session-gamma", "other", "session-alpha"] {
            m.handle_key(KeyEvent::new(KeyCode::Down, crossterm::event::KeyModifiers::NONE));
            assert_eq!(m.select().as_deref(), Some(expected));
        }
        // up wraps back: alpha -> other
        m.handle_key(KeyEvent::new(KeyCode::Up, crossterm::event::KeyModifiers::NONE));
        assert_eq!(m.select().as_deref(), Some("other"));
    }

    #[test]
    fn handle_key_only_consumes_updown_when_open() {
        let mut m = Mentions::new(candidates());
        // closed: nothing consumed
        assert!(!m.handle_key(KeyEvent::new(
            KeyCode::Down,
            crossterm::event::KeyModifiers::NONE,
        )));
        m.open();
        assert!(m.handle_key(KeyEvent::new(
            KeyCode::Down,
            crossterm::event::KeyModifiers::NONE,
        )));
        // Enter/Esc 不消费(调用方决策)
        assert!(!m.handle_key(KeyEvent::new(
            KeyCode::Enter,
            crossterm::event::KeyModifiers::NONE,
        )));
        assert!(!m.handle_key(KeyEvent::new(
            KeyCode::Esc,
            crossterm::event::KeyModifiers::NONE,
        )));
    }

    #[test]
    fn clamp_selected_after_candidates_shrink() {
        let mut m = Mentions::new(candidates());
        m.open();
        m.handle_key(KeyEvent::new(KeyCode::Down, crossterm::event::KeyModifiers::NONE));
        m.handle_key(KeyEvent::new(KeyCode::Down, crossterm::event::KeyModifiers::NONE));
        m.handle_key(KeyEvent::new(KeyCode::Down, crossterm::event::KeyModifiers::NONE));
        assert_eq!(m.select().as_deref(), Some("other"));
        // 缩到 1 条:选中应 clamp 到 0。
        m.set_candidates(vec!["only".to_string()]);
        assert_eq!(m.select().as_deref(), Some("only"));
    }

    #[test]
    fn no_matches_selects_none() {
        let mut m = Mentions::new(candidates());
        m.open();
        m.set_query("zzz");
        assert!(m.filtered().is_empty());
        assert_eq!(m.select(), None);
    }
}
