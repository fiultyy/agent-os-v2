//! 状态层(App State event loop)。
//!
//! 拥有数据 model + Panel 管理(open/focus/z-index 栈)+ 弹窗栈(modal 栈顶消费事件)。
//! 保留 P1 的 flow/stack/control 三视图 + trigger_turn(接 orche)+ observe events。
//!
//! 分层职责:
//! - events.rs 把 crossterm Event 归约成 AppEvent
//! - 本文件 App::handle(AppEvent) 消费事件,改状态
//! - render.rs 把状态画出来
//!
//! 弹窗栈:Vec 末尾是栈顶(z-index 最高)。模态弹窗激活时,handle() 先把 key/mouse 喂给
//! 栈顶弹窗的 PopupState/DialogState;rat-event Dialog qualifier 语义——消费即不下发 base panel。
#![allow(dead_code)]
//!
//! 拥有数据 model + Panel 管理(open/focus/z-index 栈)+ 弹窗栈(modal 栈顶消费事件)。
//! 保留 P1 的 flow/stack/control 三视图 + trigger_turn(接 orche)+ observe events。
//!
//! 分层职责:
//! - events.rs 把 crossterm Event 归约成 AppEvent
//! - 本文件 App::handle(AppEvent) 消费事件,改状态
//! - render.rs 把状态画出来
//!
//! 弹窗栈:Vec 末尾是栈顶(z-index 最高)。模态弹窗激活时,handle() 先把 key/mouse 喂给
//! 栈顶弹窗的 PopupState/DialogState;rat-event Dialog qualifier 语义——消费即不下发 base panel。

use crate::kitty::TermCap;
use crossterm::event::{KeyCode, KeyEvent, MouseButton, MouseEvent, MouseEventKind};
use serde::Deserialize;
use std::collections::HashMap;
use tui_popup::PopupState;

const OBSERVE: &str = "http://localhost:8002";
const ORCH: &str = "http://localhost:8001";
pub const CLAW_SESSION: &str = "agent:main:main";

// ═══ observe / orch 数据 model(P1 保留)══════════════════════════════

#[derive(Deserialize, Clone)]
pub struct Session {
    pub harness_type: String,
    pub session_id: String,
    #[allow(dead_code)]
    pub harness_id: String,
}
#[derive(Deserialize, Default, Clone)]
pub struct SessionsGrouped {
    pub sessions_by_harness: HashMap<String, Vec<Session>>,
}
#[derive(Deserialize, Clone)]
pub struct ObserveEvent {
    pub event_type: String,
    pub tick_id: String,
    /// 驱动该事件的实例标识(claude-code 多 PTY --resume / claw 多 gateway)。
    /// ADR-5:同 (harness_type, session_id) 可被多个 harness_id 驱动 = 多实例。
    pub harness_id: String,
    pub data: HashMap<String, serde_json::Value>,
}
#[derive(Deserialize)]
struct EventsResp {
    events: Vec<ObserveEvent>,
}

pub fn fetch_sessions() -> Option<SessionsGrouped> {
    ureq::get(&format!("{}/sessions/grouped", OBSERVE))
        .call()
        .ok()?
        .into_json::<SessionsGrouped>()
        .ok()
}
pub fn fetch_events(h: &str, sid: &str) -> Option<Vec<ObserveEvent>> {
    ureq::get(&format!("{}/sessions/{}/{}/events?limit=50", OBSERVE, h, sid))
        .call()
        .ok()?
        .into_json::<EventsResp>()
        .ok()
        .map(|e| e.events)
}

/// 触发一个 turn(POST /h/{type}/sessions/{sid}/turn)。type∈{claw,claude-code}。
/// 返回 server 回的 status 文本。
pub fn trigger_turn(ht: &str, sid: &str, message: &str) -> Option<String> {
    let resp = ureq::post(&format!("{}/h/{}/sessions/{}/turn", ORCH, ht, sid))
        .send_json(serde_json::json!({ "message": message }))
        .ok()?;
    resp.into_string().ok()
}

/// 创建 session(POST /h/{type}/sessions)。claw 用 claw 格式 agent:<a>:<c>;
/// claude-code 服务端生成 hex sid。返回 (session_id, type)。
pub fn create_session(ht: &str, agent_id: Option<&str>) -> Option<String> {
    let body = serde_json::json!({ "agent_id": agent_id.unwrap_or("") });
    let resp = ureq::post(&format!("{}/h/{}/sessions", ORCH, ht))
        .send_json(body)
        .ok()?;
    let v: serde_json::Value = resp.into_json().ok()?;
    v.get("session_id").and_then(|x| x.as_str()).map(|s| s.to_string())
}

/// 多实例 spawn(POST /h/{type}/sessions/{sid}/spawn)。
/// claude-code:同 sid 多 PTY --resume(ADR-5 无锁);claw 服务端拒绝(改用 create)。
pub fn spawn_instance(ht: &str, sid: &str) -> Option<String> {
    let resp = ureq::post(&format!("{}/h/{}/sessions/{}/spawn", ORCH, ht, sid))
        .send_string("")
        .ok()?;
    resp.into_string().ok()
}

/// 拉取一个 session 的事件并返回去重后的实例(harness_id)数。
/// ADR-5 多实例信号:同 (harness_type, session_id) 多 harness_id。
/// ponytail: limit=200 够数实例;observe 不可达返回 0。
pub fn count_instances(h: &str, sid: &str) -> usize {
    fetch_events(h, sid)
        .map(|evs| {
            let mut set: std::collections::HashSet<&str> = std::collections::HashSet::new();
            for e in &evs {
                if !e.harness_id.is_empty() {
                    set.insert(e.harness_id.as_str());
                }
            }
            set.len()
        })
        .unwrap_or(0)
}

pub fn fmt_val(d: &HashMap<String, serde_json::Value>, k: &str) -> String {
    match d.get(k) {
        Some(serde_json::Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
        None => String::new(),
    }
}

/// observe harness_type → orche 原语 type。
/// observe 继承 multi-harness-observe 用 "openclaw",orche P0 原语用 "claw"(同一后端)。
/// claude-code 两边一致。其余原样透传。
pub fn norm_ht(h: &str) -> String {
    match h {
        "openclaw" => "claw".to_string(),
        other => other.to_string(),
    }
}

pub fn trunc(s: &str, n: usize) -> String {
    let cnt = s.chars().count();
    if cnt <= n {
        s.to_string()
    } else {
        format!("{}…", s.chars().take(n).collect::<String>())
    }
}

// ═══ base panel(P1 三视图)══════════════════════════════════════════

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Panel {
    Flow,
    Stack,
    Control,
}
impl Panel {
    pub fn label(self) -> &'static str {
        match self {
            Panel::Flow => "FLOW ◐ 横向轨道",
            Panel::Stack => "STACK ☰ 纵向堆叠",
            Panel::Control => "CONTROL ⌘ orchestrator",
        }
    }
    pub fn next(self) -> Self {
        match self {
            Panel::Flow => Panel::Stack,
            Panel::Stack => Panel::Control,
            Panel::Control => Panel::Flow,
        }
    }
}

// ═══ 弹窗栈 modal═══════════════════════════════════════════════════

/// 单个弹窗实例:一个可拖拽 tui-popup + 标题/正文 + 是否模态。
/// 模态弹窗激活时(rat-event Dialog 语义)独占消费 key/mouse 事件。
#[derive(Debug)]
pub struct Popup {
    pub id: &'static str,
    pub title: String,
    pub body: Vec<String>,
    pub state: PopupState, // tui-popup: area(渲染回填) + drag_state
    pub modal: bool,
    /// 期望尺寸(列x行);PopupState.area 由 render 回填,body 决定实际尺寸。
    pub width: u16,
    pub height: u16,
    /// 绝对定位坐标;None = centered。首次渲染后(tui-popup 已回填 area)挪到此坐标。
    pub position: Option<(u16, u16)>,
    /// offset 是否已应用(避免每帧重复 move_to)。
    pub placed: bool,
}

impl Popup {
    pub fn centered(id: &'static str, title: &str, body: Vec<String>, w: u16, h: u16) -> Self {
        Self {
            id,
            title: title.to_string(),
            body,
            state: PopupState::default(),
            modal: true,
            width: w,
            height: h,
            position: None,
            placed: false,
        }
    }
}

// ═══ App state ═════════════════════════════════════════════════════

pub struct App {
    /// 当前 focused base panel(P1)。
    pub panel: Panel,
    pub sessions: SessionsGrouped,
    pub flat: Vec<Session>,
    pub cursor: usize,
    pub events: HashMap<String, Vec<ObserveEvent>>,
    /// 每 session 的实例数(去重 harness_id)。"harness_type/session_id" → N。
    /// N≥2 = 多实例(ADR-5:同 sid 多 harness_id 驱动)。
    pub instances: HashMap<String, usize>,
    /// 上次触发 turn 的 server 回执。
    pub turn_status: Option<String>,
    /// control 栏可编辑 message。
    pub turn_msg: String,
    /// 弹窗栈:末尾是栈顶(z-index 最高)。
    pub popups: Vec<Popup>,
    /// 终端能力(P2:决定图片渲染 / 刷新率)。
    pub term: TermCap,
    /// 上次 layout 的终端尺寸(Resize 时重算)。
    pub size: (u16, u16),
}

impl App {
    pub fn new(term: TermCap) -> Self {
        Self {
            panel: Panel::Flow,
            sessions: Default::default(),
            flat: vec![],
            cursor: 0,
            events: HashMap::new(),
            turn_status: None,
            turn_msg: "what is 8+8?".to_string(),
            popups: vec![],
            term,
            size: (0, 0),
            instances: HashMap::new(),
        }
    }

    pub fn set_sessions(&mut self, sg: SessionsGrouped) {
        let mut harnesses: Vec<String> = sg.sessions_by_harness.keys().cloned().collect();
        harnesses.sort();
        self.flat.clear();
        for h in &harnesses {
            if let Some(ss) = sg.sessions_by_harness.get(h) {
                self.flat.extend(ss.iter().cloned());
            }
        }
        self.sessions = sg;
        if self.cursor >= self.flat.len() {
            self.cursor = 0;
        }
        self.fetch_current();
        self.count_all_instances();
    }
    pub fn fetch_current(&mut self) {
        if let Some(s) = self.flat.get(self.cursor) {
            let key = format!("{}/{}", s.harness_type, s.session_id);
            if let Some(evs) = fetch_events(&s.harness_type, &s.session_id) {
                let n = evs.iter().filter(|e| !e.harness_id.is_empty())
                    .map(|e| e.harness_id.as_str()).collect::<std::collections::HashSet<_>>().len();
                self.instances.insert(key.clone(), n);
                self.events.insert(key, evs);
            }
        }
    }
    /// 扫描所有 session 数实例数(用于 session 树 ×N 标记)。
    /// ponytail: set_sessions 时一次性拉,后续 fetch_current 增量刷新当前 session。
    pub fn count_all_instances(&mut self) {
        for s in &self.flat {
            let key = format!("{}/{}", s.harness_type, s.session_id);
            let n = count_instances(&s.harness_type, &s.session_id);
            self.instances.insert(key, n);
        }
    }
    /// 当前 session 的实例数(0 = 无事件/observe 不可达)。
    pub fn instance_count(&self, h: &str, sid: &str) -> usize {
        self.instances.get(&format!("{}/{}", h, sid)).copied().unwrap_or(0)
    }
    pub fn cursor_down(&mut self) {
        if self.cursor + 1 < self.flat.len() {
            self.cursor += 1;
            self.fetch_current();
        }
    }
    pub fn cursor_up(&mut self) {
        if self.cursor > 0 {
            self.cursor -= 1;
            self.fetch_current();
        }
    }
    pub fn fetch_claw_events(&mut self) {
        if let Some(evs) = fetch_events("openclaw", CLAW_SESSION) {
            self.events
                .insert("openclaw/agent:main:main".to_string(), evs);
        }
    }
    pub fn do_turn(&mut self) {
        // 触发当前 cursor session 的 turn(不再硬编码 claw;claude-code session 也能 turn)。
        // observe harness_type=openclaw ↔ orche 原语 claw(同一后端,命名差)。
        let (ht, sid) = self.flat.get(self.cursor)
            .map(|s| (norm_ht(&s.harness_type), s.session_id.clone()))
            .unwrap_or_else(|| ("claw".to_string(), CLAW_SESSION.to_string()));
        let st = trigger_turn(&ht, &sid, &self.turn_msg);
        self.turn_status = st;
        self.fetch_current();
        if ht == "claw" {
            self.fetch_claw_events();
        }
    }

    /// spawn 多实例(s 键)。claude-code:POST /spawn 同 sid 多 PTY;
    /// claw/orche 拒绝 spawn,改 create 一个新 session。
    pub fn do_spawn(&mut self) {
        let Some(s) = self.flat.get(self.cursor).cloned() else {
            self.turn_status = Some("(无 session,无法 spawn)".to_string());
            return;
        };
        let ht = norm_ht(&s.harness_type);
        let sid = &s.session_id;
        if ht == "claude-code" {
            let st = spawn_instance(&ht, sid);
            self.turn_status = Some(st.unwrap_or_else(|| "spawn claude-code 多实例失败".to_string()));
        } else {
            // claw/openclaw:多 session = create(同 agent 或新 agent)。
            let agent = if sid.contains(':') { Some(sid.as_str()) } else { None };
            let st = create_session("claw", agent)
                .map(|new| format!("created claw session: {}", new))
                .unwrap_or_else(|| "create claw session 失败".to_string());
            self.turn_status = Some(st);
        }
        self.fetch_current();
    }

    // ── 弹窗栈操作 ──────────────────────────────────────────────

    pub fn open_popup(&mut self, p: Popup) {
        if !self.popups.iter().any(|x| x.id == p.id) {
            self.popups.push(p);
        }
    }
    pub fn close_top_popup(&mut self) {
        self.popups.pop();
    }
    pub fn close_popup(&mut self, id: &str) {
        self.popups.retain(|p| p.id != id);
    }
    pub fn top_popup_mut(&mut self) -> Option<&mut Popup> {
        self.popups.last_mut()
    }
    pub fn modal_active(&self) -> bool {
        self.popups.last().map(|p| p.modal).unwrap_or(false)
    }

    /// 便捷:打开 help 弹窗(展示分层架构 + Kitty 检测结果)。
    pub fn open_help(&mut self) {
        let mut body = vec![
            "P2 分层架构(ratatui immediate-mode):".to_string(),
            "  事件层 events.rs  →  状态层 state.rs  →  渲染层 render.rs".to_string(),
            "  组件层 components/  →  tui-popup 拖拽 / interact 右键 / image icat".to_string(),
            "".to_string(),
            format!(" Kitty 检测:protocol={} image_ok={}", self.term.protocol.label(), self.term.image_ok),
            format!(" poll_interval={}ms", self.term.poll_interval.as_millis()),
            "".to_string(),
            " 键位:tab 切 base panel · e raw exec · s spawn 多实例 · p 弹窗 · ?/h help · q quit".to_string(),
            " 弹窗:esc/enter 关闭 · 鼠标拖拽标题栏 · 右键 base panel 弹 context menu".to_string(),
        ];
        if !self.term.hint.is_empty() {
            body.push(String::new());
            body.push(format!(" ⚠ {}", self.term.hint));
        }
        self.open_popup(Popup::centered("help", " v2 harness-bridge · P2 分层架构", body, 70, 16));
    }

    // ── 事件消费 ──────────────────────────────────────────────────

    /// 消费一个 AppEvent。返回 true 表示要退出 app。
    ///
    /// 分层分发:弹窗栈顶模态激活时,Key/Mouse 先喂弹窗(rat-event Dialog 语义,消费即不下发);
    /// 否则走 base panel(P1 keybindings)。
    pub fn handle(&mut self, ev: &crate::events::AppEvent) -> bool {
        use crate::events::AppEvent;
        match ev {
            AppEvent::Quit => return true,
            AppEvent::Resize(w, h) => {
                self.size = (*w, *h);
            }
            AppEvent::Tick => {
                // ponytail: 固定计数轮询 claw events,observe 挂了静默跳过(复用 P1 逻辑)。
                if self.panel == Panel::Flow || self.panel == Panel::Control {
                    self.fetch_claw_events();
                }
            }
            AppEvent::Key(k) => {
                if self.modal_active() {
                    if self.handle_popup_key(k) {
                        return false;
                    }
                    // 弹窗未消费:模态下仍拦截(不下发 base panel),但放行 quit。
                    if k.code == KeyCode::Char('q') {
                        return true;
                    }
                    return false;
                }
                if self.handle_base_key(k) {
                    return true;
                }
            }
            AppEvent::Mouse(m) => {
                if self.modal_active() {
                    self.handle_popup_mouse(m);
                    return false;
                }
                self.handle_base_mouse(m);
            }
        }
        false
    }

    /// 弹窗栈顶消费 key。返回 true = 已消费(关闭/聚焦切换)。
    fn handle_popup_key(&mut self, k: &KeyEvent) -> bool {
        match k.code {
            KeyCode::Esc | KeyCode::Enter => {
                self.close_top_popup();
                true
            }
            KeyCode::Tab => {
                // 多弹窗时 tab 把栈顶下沉,下一个上浮(z-order 轮换)。
                if self.popups.len() > 1 {
                    let top = self.popups.pop().unwrap();
                    self.popups.insert(0, top);
                }
                true
            }
            _ => false,
        }
    }

    /// 弹窗栈顶消费 mouse:tui-popup PopupState.handle_mouse_event(拖拽)。
    fn handle_popup_mouse(&mut self, m: &MouseEvent) {
        if let Some(p) = self.popups.last_mut() {
            p.state.handle_mouse_event(*m);
        }
    }

    /// base panel 鼠标:右键弹 context menu / 左键切 panel(简化:点顶部 title 区切)。
    /// ponytail: 不做完整 hit-test;右键任意位置开 help-menu,左键按 y 分区切 panel。
    fn handle_base_mouse(&mut self, m: &MouseEvent) {
        match m.kind {
            MouseEventKind::Down(MouseButton::Right) => {
                // 右键 → context menu(用 help 弹窗承载,演示 interact 交互入口)。
                self.open_help();
            }
            MouseEventKind::ScrollDown => self.cursor_down(),
            MouseEventKind::ScrollUp => self.cursor_up(),
            MouseEventKind::Down(MouseButton::Left) => {
                // 点顶栏(title 行)区域切 panel:简化为 y==0 时按 x 分三段。
                if m.row == 0 {
                    let third = self.size.0.max(1) / 3;
                    self.panel = if m.column < third {
                        Panel::Flow
                    } else if m.column < third * 2 {
                        Panel::Stack
                    } else {
                        Panel::Control
                    };
                }
            }
            _ => {}
        }
    }

    /// base panel 键位(P1 保留 + P2 扩展 e=raw exec / p=弹窗)。返回 true = 退出 app。
    fn handle_base_key(&mut self, k: &KeyEvent) -> bool {
        match k.code {
            KeyCode::Char('q') => true,
            KeyCode::Tab => {
                self.panel = self.panel.next();
                false
            }
            KeyCode::Char('1') => {
                self.panel = Panel::Flow;
                false
            }
            KeyCode::Char('2') => {
                self.panel = Panel::Stack;
                false
            }
            KeyCode::Char('3') => {
                self.panel = Panel::Control;
                false
            }
            KeyCode::Char('c') => {
                self.panel = Panel::Control;
                false
            }
            KeyCode::Char('j') | KeyCode::Down => {
                self.cursor_down();
                false
            }
            KeyCode::Char('k') | KeyCode::Up => {
                self.cursor_up();
                false
            }
            KeyCode::Char('t') => {
                self.do_turn();
                false
            }
            KeyCode::Char('s') => {
                // 多实例:claude-code spawn 同 sid 多 PTY;claw create 新 session。
                self.do_spawn();
                false
            }
            KeyCode::Char('r') => {
                if let Some(sg) = fetch_sessions() {
                    self.set_sessions(sg);
                }
                self.fetch_claw_events();
                false
            }
            KeyCode::Char('p') => {
                self.open_help();
                false
            }
            KeyCode::Char('?') | KeyCode::Char('h') => {
                self.open_help();
                false
            }
            KeyCode::Char('e') => {
                // raw exec:占位提示(真实 spawn 见 components/raw_exec.rs)。
                self.open_popup(Popup::centered(
                    "raw-exec",
                    " raw exec · spawn harness",
                    vec![
                        "选 session → spawn claude --resume <sid> / claw TUI 全屏".to_string(),
                        format!(" 当前 cursor session: {}", self.current_sid()),
                        " ctrl+d 退出 harness 回 TUI(占位:交互模式生效)".to_string(),
                    ],
                    64,
                    8,
                ));
                false
            }
            _ => false,
        }
    }

    pub fn current_sid(&self) -> String {
        self.flat
            .get(self.cursor)
            .map(|s| s.session_id.clone())
            .unwrap_or_else(|| "(无 session)".to_string())
    }
}
