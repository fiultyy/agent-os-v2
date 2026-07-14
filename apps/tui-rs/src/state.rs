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

use crate::components::mouse::MouseCursor;
use crate::components::tabs::TabBar;
use crate::kitty::TermCap;
use crossterm::event::{KeyCode, KeyEvent, MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use ratatui::text::Text;
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

// ═══ flow engine(ADR-1 P2:turn 链/分支/DAG on trigger_turn)══════════
// 镜像 services/orchestrator/src/harness/flow.py 的 FlowDef JSON DSL。
// 调度在 orche 侧;TUI 只 create/run/poll 状态 + 渲染 DAG。

#[derive(Serialize, Deserialize, Clone, Default)]
pub struct FlowCondition {
    pub field: String, // response | status
    pub op: String,    // contains | eq
    pub value: String,
}
#[derive(Serialize, Deserialize, Clone)]
pub struct FlowNode {
    pub id: String,
    pub harness: String, // claw | claude-code
    #[serde(default)]
    pub session_id: Option<String>,
    pub message: String,
}
#[derive(Serialize, Deserialize, Clone)]
pub struct FlowEdge {
    pub from: String,
    pub to: String,
    #[serde(default)]
    pub condition: Option<FlowCondition>,
}
/// FlowDef 既是 create POST body,也用于本地渲染 DAG 拓扑。
#[derive(Serialize, Deserialize, Clone, Default)]
pub struct FlowDef {
    pub nodes: Vec<FlowNode>,
    #[serde(default)]
    pub edges: Vec<FlowEdge>,
}

/// GET /h/flows/{id} 返回的单节点运行态。
#[derive(Deserialize, Clone, Default)]
pub struct FlowNodeState {
    pub id: String,
    pub status: String, // pending | running | completed | failed | skipped
    #[serde(default)]
    pub response: String,
    #[serde(default, rename = "status_code")]
    pub status_code: String,
}
/// GET /h/flows/{id} → flow 级 + 每 node 状态。
#[derive(Deserialize, Clone, Default)]
pub struct FlowStatus {
    pub flow_id: String,
    pub status: String, // pending | running | completed | failed
    #[serde(default)]
    pub nodes: HashMap<String, FlowNodeState>,
}

/// POST /h/flows 回执。
#[derive(Deserialize)]
struct CreateFlowResp {
    flow_id: String,
}

/// 创建 flow(POST /h/flows)。返回 flow_id。
pub fn create_flow(def: &FlowDef) -> Option<String> {
    let body = serde_json::to_value(def).ok()?;
    let v: serde_json::Value = ureq::post(&format!("{}/h/flows", ORCH))
        .send_json(body)
        .ok()?
        .into_json()
        .ok()?;
    v.get("flow_id").and_then(|x| x.as_str()).map(|s| s.to_string())
}
/// 异步跑 flow(POST /h/flows/{id}/run)。服务端立即返回,observe 收 flow_* 事件。
pub fn run_flow(flow_id: &str) -> Option<String> {
    let resp = ureq::post(&format!("{}/h/flows/{}/run", ORCH, flow_id))
        .send_string("")
        .ok()?;
    resp.into_string().ok()
}
/// 拉 flow 状态(GET /h/flows/{id})。orche 不可达返回 None。
pub fn fetch_flow(flow_id: &str) -> Option<FlowStatus> {
    ureq::get(&format!("{}/h/flows/{}", ORCH, flow_id))
        .call()
        .ok()?
        .into_json::<FlowStatus>()
        .ok()
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

// ═══ flow presets(ADR-1 P2:turn 链/分支/DAG 演示拓扑)══════════════
// 给 control mode 一键创建 + run。真实 harness 消息由 orche 触发 turn。

pub enum FlowPreset {
    Chain,   // A → B 单链(两 claw turn)
    Branch,  // A → B if cond else C
    Dag,     // A,C 并行 start → B(入度 2,合并)
}

/// 按预设构造一个 FlowDef。session_id=None 让 orche 自动建 session。
pub fn preset_flow(p: FlowPreset, msg: &str) -> FlowDef {
    fn n(id: &str, h: &str, m: &str) -> FlowNode {
        FlowNode { id: id.to_string(), harness: h.to_string(), session_id: None, message: m.to_string() }
    }
    fn e(from: &str, to: &str) -> FlowEdge {
        FlowEdge { from: from.to_string(), to: to.to_string(), condition: None }
    }
    match p {
        FlowPreset::Chain => FlowDef {
            nodes: vec![n("A", "claw", msg), n("B", "claw", "summarize the last reply in one line")],
            edges: vec![e("A", "B")],
        },
        FlowPreset::Branch => FlowDef {
            nodes: vec![n("A", "claw", msg), n("B", "claw", "reply: yes branch"), n("C", "claw", "reply: no branch")],
            edges: vec![
                FlowEdge { from: "A".into(), to: "B".into(),
                    condition: Some(FlowCondition { field: "response".into(), op: "contains".into(), value: "1".into() }) },
                FlowEdge { from: "A".into(), to: "C".into(), condition: None },
            ],
        },
        FlowPreset::Dag => FlowDef {
            nodes: vec![n("A", "claw", msg), n("C", "claw", "what is 2+2?"),
                        n("B", "claw", "merge: combine both prior replies")],
            // B 入度 2 → A,C 都完成才触发(DAG 合并节点)
            edges: vec![e("A", "B"), e("C", "B")],
        },
    }
}

/// TUI 跟踪的 flow:create 时入表,Tick 周期 poll GET /h/flows/{id} 更新状态。
#[derive(Clone)]
pub struct TrackedFlow {
    pub flow_id: String,
    pub def: FlowDef,
    pub status: Option<FlowStatus>,
}

// ═══ base panel(P1 三视图)══════════════════════════════════════════

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Panel {
    Home,
    Flows,
    Observe,
    Control,
}
impl Panel {
    pub fn label(self) -> &'static str {
        match self {
            Panel::Home => "HOME ◉ 概览",
            Panel::Flows => "FLOWS ◐ 编排 DAG",
            Panel::Observe => "OBSERVE ☰ 纵向堆叠",
            Panel::Control => "CONTROL ⌘ orchestrator",
        }
    }
    pub fn next(self) -> Self {
        match self {
            Panel::Home => Panel::Flows,
            Panel::Flows => Panel::Observe,
            Panel::Observe => Panel::Control,
            Panel::Control => Panel::Home,
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
    /// markdown 渲染正文(ADR-3 help 弹窗用 md_to_text)。Some 时 render_popup 优先用此字段。
    pub md_text: Option<Text<'static>>,
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
            md_text: None,
            state: PopupState::default(),
            modal: true,
            width: w,
            height: h,
            position: None,
            placed: false,
        }
    }
    /// 带 markdown 渲染正文的弹窗(ADR-3)。md_text 优先于 body。
    pub fn with_md(mut self, md: Text<'static>) -> Self {
        self.md_text = Some(md);
        self
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
    /// P2 flow:已 create 的 flow(id + def + 上次 poll 状态)。j/k 在 flow panel 内选。
    pub flows: Vec<TrackedFlow>,
    /// flow panel cursor(选哪个 tracked flow 看 DAG)。turn_msg 在 control mode 复用作 flow 首节点 message。
    pub flow_cursor: usize,
    /// 顶栏 TabBar(ADR-1:Home/Flows/Observe/Control 4 tab)。
    pub tabbar: TabBar,
    /// 鼠标光标(ADR-2:帧末黑底黄字高亮)。
    pub mouse: MouseCursor,
    /// 顶栏 tab 区域缓存(draw 算 → handle mouse hit 用)。
    pub tab_area: Rect,
}

impl App {
    pub fn new(term: TermCap) -> Self {
        Self {
            panel: Panel::Home,
            sessions: Default::default(),
            flat: vec![],
            cursor: 0,
            events: HashMap::new(),
            turn_status: None,
            turn_msg: String::new(),
            popups: vec![],
            term,
            size: (0, 0),
            instances: HashMap::new(),
            flows: vec![],
            flow_cursor: 0,
            tabbar: TabBar::new(vec![
                "Home".to_string(),
                "Flows".to_string(),
                "Observe".to_string(),
                "Control".to_string(),
            ]),
            mouse: MouseCursor::default(),
            tab_area: Rect::default(),
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

    // ── flow 操作(P2 编排:turn 链/分支/DAG)──────────────────────

    /// 创建一个预设 flow 并入表(不 run)。返回 flow_id 或错误文案。
    pub fn create_preset_flow(&mut self, p: FlowPreset) -> String {
        let def = preset_flow(p, &self.turn_msg);
        match create_flow(&def) {
            Some(id) => {
                self.flows.push(TrackedFlow { flow_id: id.clone(), def, status: fetch_flow(&id) });
                self.flow_cursor = self.flows.len().saturating_sub(1);
                self.turn_status = Some(format!("flow created: {}", id));
                id
            }
            None => {
                self.turn_status = Some("create flow 失败(orche :8001 不可达?)".to_string());
                String::new()
            }
        }
    }
    /// run 当前 cursor 的 tracked flow(POST /h/flows/{id}/run)。
    pub fn run_current_flow(&mut self) {
        let Some(tf) = self.flows.get(self.flow_cursor).cloned() else {
            self.turn_status = Some("(无 flow,先 f 创建)".to_string());
            return;
        };
        let st = run_flow(&tf.flow_id);
        self.turn_status = Some(st.unwrap_or_else(|| "run flow 失败".to_string()));
        if let Some(s) = fetch_flow(&tf.flow_id) {
            if let Some(t) = self.flows.get_mut(self.flow_cursor) {
                t.status = Some(s);
            }
        }
    }
    /// Tick:刷新所有非终态 flow 的状态(running/pending → poll)。
    /// ponytail: 终态(completed/failed)不再 poll 省请求。
    pub fn refresh_flows(&mut self) {
        for tf in self.flows.iter_mut() {
            let terminal = tf.status.as_ref().map(|s| s.status == "completed" || s.status == "failed").unwrap_or(false);
            if terminal {
                continue;
            }
            if let Some(s) = fetch_flow(&tf.flow_id) {
                tf.status = Some(s);
            }
        }
    }
    pub fn flow_cursor_down(&mut self) {
        if self.flow_cursor + 1 < self.flows.len() {
            self.flow_cursor += 1;
        }
    }
    pub fn flow_cursor_up(&mut self) {
        if self.flow_cursor > 0 {
            self.flow_cursor -= 1;
        }
    }
    /// 当前 tracked flow 的状态节点(给 render 查 node 状态)。
    pub fn current_flow(&self) -> Option<&TrackedFlow> {
        self.flows.get(self.flow_cursor)
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
    /// ADR-3: body 用 md_to_text 渲染(标题/列表/代码高亮)。
    pub fn open_help(&mut self) {
        let md = format!(
            "# v2 harness-bridge · P2 分层架构\n\n\
             **ratatui immediate-mode** 分层:\n\n\
             - 事件层 `events.rs` → 状态层 `state.rs` → 渲染层 `render.rs`\n\
             - 组件层 `components/` → `tui-popup` 拖拽 / `interact` 右键 / `image` icat\n\n\
             ## 终端检测\n\n\
             - Kitty `protocol` = `{}`\n\
             - `image_ok` = `{}`\n\
             - `poll_interval` = `{}ms`\n\n\
             ## 键位\n\n\
             - `tab` 切 base panel · `1-4` 选 tab\n\
             - `t` 触发 turn · `s` spawn 多实例 · `e` raw exec\n\
             - `f` 链 / `G` 分支 / `D` DAG 创建 flow · `R` 运行 · `j/k` 切 flow\n\
             - `p` / `?` / `h` 弹窗 · 右键 base panel 弹 context menu\n\
             - `esc` / `enter` 关闭弹窗 · `q` quit\n",
            self.term.protocol.label(),
            self.term.image_ok,
            self.term.poll_interval.as_millis(),
        );
        let mut full_md = md;
        if !self.term.hint.is_empty() {
            full_md.push_str(&format!("\n> ⚠ {}\n", self.term.hint));
        }
        let text = crate::components::markdown::md_to_text(&full_md);
        self.open_popup(
            Popup::centered("help", " v2 harness-bridge · help", vec![], 72, 22)
                .with_md(text),
        );
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
                if self.panel == Panel::Flows || self.panel == Panel::Control {
                    self.fetch_claw_events();
                }
                // P2 flow:周期 poll 非终态 flow 的 GET /h/flows/{id}(实时 node 状态)。
                if !self.flows.is_empty() {
                    self.refresh_flows();
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

    /// base panel 鼠标:右键弹 context menu / 左键 tab 切 panel / 滚轮列表。
    /// 复用 components/tabs.rs tabbar.hit(tab_area, col, row) 精确命中(ADR-2)。
    fn handle_base_mouse(&mut self, m: &MouseEvent) {
        // 光标总是跟踪(Moved/Down/Drag Left)。
        self.mouse.track(*m);
        match m.kind {
            MouseEventKind::Down(MouseButton::Right) => {
                // 右键 → context menu(用 help 弹窗承载,演示 interact 交互入口)。
                self.open_help();
            }
            MouseEventKind::ScrollDown => self.cursor_down(),
            MouseEventKind::ScrollUp => self.cursor_up(),
            MouseEventKind::Down(MouseButton::Left) => {
                // TabBar 命中切 tab(ADR-2:复用 tabs.rs hit)。
                if let Some(i) = self.tabbar.hit(self.tab_area, m.column, m.row) {
                    self.tabbar.select(i);
                    self.sync_panel_from_tab();
                }
            }
            _ => {}
        }
    }

    /// 把 tabbar.active 同步到 self.panel(0=Home,1=Flows,2=Observe,3=Control)。
    pub fn sync_panel_from_tab(&mut self) {
        self.panel = match self.tabbar.active {
            0 => Panel::Home,
            1 => Panel::Flows,
            2 => Panel::Observe,
            _ => Panel::Control,
        };
    }
    /// 把 self.panel 同步到 tabbar.active(render 前确保一致)。
    pub fn sync_tab_from_panel(&mut self) {
        let idx = match self.panel {
            Panel::Home => 0,
            Panel::Flows => 1,
            Panel::Observe => 2,
            Panel::Control => 3,
        };
        self.tabbar.select(idx);
    }

    /// base panel 键位(P1 保留 + P2 扩展 e=raw exec / p=弹窗)。返回 true = 退出 app。
    fn handle_base_key(&mut self, k: &KeyEvent) -> bool {
        match k.code {
            KeyCode::Char('q') => true,
            KeyCode::Tab => {
                self.tabbar.next();
                self.sync_panel_from_tab();
                false
            }
            KeyCode::Char('1') => {
                self.panel = Panel::Home;
                self.sync_tab_from_panel();
                false
            }
            KeyCode::Char('2') => {
                self.panel = Panel::Flows;
                self.sync_tab_from_panel();
                false
            }
            KeyCode::Char('3') => {
                self.panel = Panel::Observe;
                self.sync_tab_from_panel();
                false
            }
            KeyCode::Char('4') => {
                self.panel = Panel::Control;
                self.sync_tab_from_panel();
                false
            }
            KeyCode::Char('c') => {
                self.panel = Panel::Control;
                self.sync_tab_from_panel();
                false
            }
            KeyCode::Char('j') | KeyCode::Down => {
                // P2 flow panel:j/k 切 flow cursor;其余 panel 走 session cursor。
                if self.panel == Panel::Flows {
                    self.flow_cursor_down();
                } else {
                    self.cursor_down();
                }
                false
            }
            KeyCode::Char('k') | KeyCode::Up => {
                if self.panel == Panel::Flows {
                    self.flow_cursor_up();
                } else {
                    self.cursor_up();
                }
                false
            }
            KeyCode::Char('f') => {
                // P2 flow:create 预设链 flow(A→B)。
                self.create_preset_flow(FlowPreset::Chain);
                false
            }
            KeyCode::Char('G') => {
                // P2 flow:create 预设分支 flow(A→B if cond else C)。
                self.create_preset_flow(FlowPreset::Branch);
                false
            }
            KeyCode::Char('D') => {
                // P2 flow:create 预设 DAG(A,C 并行 → B 合并)。
                self.create_preset_flow(FlowPreset::Dag);
                false
            }
            KeyCode::Char('R') => {
                // P2 flow:run 当前 cursor flow(POST /h/flows/{id}/run)。
                self.run_current_flow();
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
            KeyCode::Char(c) => {
                // control mode:Char 追加 turn_msg(输入 message);单键 t/c/f/G/D/R/s/r/q 等已先 match
                if self.panel == Panel::Control {
                    self.turn_msg.push(c);
                }
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

// ═══ self-check:FlowStatus 反序列化(orche 契约)+ preset 拓扑 ═════════
// 唯一非平凡逻辑:serde 字段映射 drift 即 break。preset 的入度/边数验证拓扑正确。
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn flow_status_parses_orchestrator_payload() {
        // 真实 GET /h/flows/{id} 返回的形状(含 error 字段,serde 忽略)。
        let raw = r#"{"flow_id":"flow_abc","status":"running","nodes":{"A":{"id":"A","status":"completed","response":"16","status_code":"success","error":""},"B":{"id":"B","status":"pending","response":"","status_code":"","error":""}},"started_at":"t","finished_at":null}"#;
        let s: FlowStatus = serde_json::from_str(raw).unwrap();
        assert_eq!(s.flow_id, "flow_abc");
        assert_eq!(s.status, "running");
        assert_eq!(s.nodes.len(), 2);
        assert_eq!(s.nodes["A"].status, "completed");
        assert_eq!(s.nodes["A"].response, "16");
        assert_eq!(s.nodes["B"].status, "pending");
    }

    #[test]
    fn preset_dag_has_merge_node_with_indegree_2() {
        let def = preset_flow(FlowPreset::Dag, "m");
        // DAG:A,C start → B 合并(B 入度 2)。
        assert_eq!(def.nodes.len(), 3);
        let mut indeg: HashMap<&str, usize> = def.nodes.iter().map(|n| (n.id.as_str(), 0)).collect();
        for e in &def.edges {
            *indeg.get_mut(e.to.as_str()).unwrap() += 1;
        }
        assert_eq!(indeg["A"], 0);
        assert_eq!(indeg["C"], 0);
        assert_eq!(indeg["B"], 2, "B is the merge node (indegree 2)");
    }

    #[test]
    fn preset_branch_has_one_conditional_edge() {
        let def = preset_flow(FlowPreset::Branch, "m");
        let conds: Vec<_> = def.edges.iter().filter(|e| e.condition.is_some()).collect();
        assert_eq!(conds.len(), 1, "exactly one conditional edge (A→B if response contains 1)");
        let uncond: Vec<_> = def.edges.iter().filter(|e| e.condition.is_none()).collect();
        assert_eq!(uncond.len(), 1, "one unconditional edge (A→C else)");
    }
}
