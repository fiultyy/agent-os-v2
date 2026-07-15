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

use crate::components::mouse::{ClickMap, MouseCursor};
use crate::components::scrollbar::ScrollView;
use crate::components::split::HSplit;
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

/// orche /health 预检(ADR-3)。GET :8001/health → bool。
/// 非 业务方法:新 REST fetch,不触 state.rs 业务方法/数据字段。
pub fn fetch_orche_health() -> bool {
    ureq::get(&format!("{}/health", ORCH)).call().is_ok()
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

/// 键盘焦点目标(ADR-2:统一 focus indicator)。Tab 在目标间循环,方向键 panel 内切。
/// UI 状态字段,不影响业务方法/数据结构。
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum FocusTarget {
    TabBar,
    /// Control tab 按钮索引(trigger/spawn/refresh/rawexec/flow-create-chain/...)。
    ControlButton(usize),
    /// Control tab 左大纲 session 列表索引(F3:键盘焦点 cursor 导航)。
    ControlSession(usize),
    /// Observe tab session 列表。
    ObserveSession,
    /// Flows tab flow 列表索引。
    FlowsFlow(usize),
}

/// Control tab 按钮总数(trigger/spawn/refresh/rawexec + flow create Chain/Branch/DAG + run)。
/// 键盘焦点循环时用此 cap ControlButton(idx)。T2 扩按钮后此常量同步。
pub const CONTROL_BUTTON_COUNT: usize = 8;

impl FocusTarget {
    /// Tab 键:按 panel 切到下一个 focus 目标(panel 内 Tab → TabBar;TabBar → panel 首元素)。
    /// ponytail: 简化 cycle——Tab 在 TabBar 和当前 panel 首元素间切;方向键 panel 内移。
    pub fn cycle(self, panel: Panel) -> Self {
        match self {
            FocusTarget::TabBar => match panel {
                Panel::Control => FocusTarget::ControlSession(0), // F3:大纲首(左大纲主)
                Panel::Observe => FocusTarget::ObserveSession,
                Panel::Flows => FocusTarget::FlowsFlow(0),
                Panel::Home => FocusTarget::TabBar, // Home 无可聚焦元素,停在 TabBar
            },
            FocusTarget::ControlSession(_) if panel == Panel::Control => FocusTarget::ControlButton(0), // F3:大纲 → 输入栏按钮
            _ => FocusTarget::TabBar,
        }
    }
}

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
    /// Observe tab HSplit resizable 状态(ADR-2:session 树 | turn stream)。
    pub observe_split: HSplit,
    /// Observe tab turn stream ScrollView(ADR-2:长内容滚动)。
    pub observe_scroll: ScrollView,
    /// Observe tab 区域缓存(draw 算 → handle mouse drag hit 用)。
    pub observe_area: Rect,
    /// 鼠标是否正在拖 observe 分隔条(Drag 延续)。
    pub observe_dragging: bool,
    // ── Control Cursor 式布局(第六轮 T1/T2,非业务字段)───────────────
    /// Control tab HSplit(左大纲 | 右堆叠)resizable 状态。
    pub control_split: HSplit,
    /// Control tab 右堆叠 VerticalStack(对话 | 输入)resizable 状态。N-pane 可扩展(ADR-1)。
    pub control_stack: crate::components::split::VerticalStack,
    /// Control tab 对话区 ScrollView(turn stream 滚动)。
    pub control_chat_scroll: ScrollView,
    /// Control tab 区域缓存(draw 算 → handle mouse drag hit 用)。
    pub control_area: Rect,
    /// 鼠标是否正在拖 control 主分隔条(HSplit bar)。
    pub control_h_dragging: bool,
    /// 鼠标正在拖 control 堆叠分隔条的 pane_idx(VerticalStack separators[pane_idx],ADR-1)。
    pub control_v_dragging: Option<usize>,
    /// ClickMap 页面内交互元素命中(Control 按钮 + Observe session 项,ADR-1/ADR-2)。
    pub clickmap: ClickMap<usize>,
    /// ADR-1 T4:WS 直连 observe(弃 REST polling)。WS manager + 事件 channel。
    /// None = 未启用(--dump / 测试);交互模式 main.rs 注入。
    /// ponytail: Option 包裹避免测试/new 强依赖网络;业务方法不触此字段。
    pub ws: Option<crate::ws::WsManager>,
    // ── T1/T2 UI 状态(ADR-2/ADR-3,非业务字段)──────────────────────
    /// 键盘焦点目标(ADR-2:统一 focus indicator)。
    pub focus: FocusTarget,
    /// orche 在线状态(ADR-3:fetch_orche_health 按需预检——进 Control/refresh 触发,非周期 Tick)。离线时 Control 显提示。
    pub orche_online: bool,
    /// 上次按钮点击时间 + action 名(ADR-3:点击 loading 反馈,render 检 <500ms 高亮)。
    pub last_action: Option<(std::time::Instant, &'static str)>,
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
            observe_split: HSplit::new(35),
            observe_scroll: ScrollView::new(vec![]),
            observe_area: Rect::default(),
            observe_dragging: false,
            control_split: HSplit::new(30),
            // ADR-1(第八轮):push pane 扩 4 区(对话/输入/flow/属性)。VerticalStack 真扩展验证。
            control_stack: crate::components::split::VerticalStack::new(vec![40, 20, 20, 20]),
            control_chat_scroll: ScrollView::new(vec![]),
            control_area: Rect::default(),
            control_h_dragging: false,
            control_v_dragging: None,
            clickmap: ClickMap::new(),
            ws: None,
            focus: FocusTarget::TabBar,
            orche_online: true, // 默认假设在线,首次预检刷新
            last_action: None,
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

    /// ADR-3:记录按钮点击时间 + action 名(render 检 <500ms 显 loading 高亮)。UI 状态,不改业务。
    pub fn mark_action(&mut self, name: &'static str) {
        self.last_action = Some((std::time::Instant::now(), name));
    }

    /// ADR-3:上次动作是否在 loading 窗口内(<500ms)。render 用此判按钮高亮态。
    pub fn action_loading(&self, name: &str) -> bool {
        match self.last_action {
            Some((t, n)) => n == name && t.elapsed().as_millis() < 500,
            None => false,
        }
    }

    /// 消费一个 AppEvent。返回 true 表示要退出 app。    ///
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
                // ADR-1 T4:弃 REST polling。Tick 只 drain WS channel + UI 刷新。
                // turn 事件经 WS 推送(drain_ws → app.events[key]);
                // flow 事件经 WS 推送(drain_ws → app.flows[i].status)。
                // fetch_claw_events/refresh_flows 不再在 Tick 调(保留方法定义,业务不变)。
                self.drain_ws();
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
    /// ADR-2:Observe tab 加分隔条拖拽(HSplit.drag)+ turn stream 滚轮(ScrollView)。
    /// 触发 Control 按钮 id(0-7)动作(鼠标点击 + 键盘 Enter 共用,F1 修复)。
    fn trigger_control_button(&mut self, id: usize) {
        match id {
            0 => { self.do_turn(); self.mark_action("trigger"); }
            1 => { self.do_spawn(); self.mark_action("spawn"); }
            2 => {
                if let Some(sg) = fetch_sessions() {
                    self.set_sessions(sg);
                }
                self.fetch_claw_events();
                self.orche_online = fetch_orche_health();
            }
            3 => self.open_popup(Popup::centered(
                "raw-exec",
                " raw exec · spawn harness",
                vec![
                    "选 session → spawn claude --resume <sid> / claw TUI 全屏".to_string(),
                    format!(" 当前 cursor session: {}", self.current_sid()),
                    " ctrl+d 退出 harness 回 TUI(占位:交互模式生效)".to_string(),
                ],
                64,
                8,
            )),
            4 => { self.create_preset_flow(FlowPreset::Chain); self.mark_action("create_chain"); }
            5 => { self.create_preset_flow(FlowPreset::Branch); self.mark_action("create_branch"); }
            6 => { self.create_preset_flow(FlowPreset::Dag); self.mark_action("create_dag"); }
            7 => { self.run_current_flow(); self.mark_action("run_flow"); }
            _ => {}
        }
        // 焦点归该按钮(键盘聚焦框跟随)。
        self.focus = FocusTarget::ControlButton(id);
    }

    fn handle_base_mouse(&mut self, m: &MouseEvent) {
        // 光标总是跟踪(Moved/Down/Drag Left)。
        self.mouse.track(*m);
        match m.kind {
            MouseEventKind::Down(MouseButton::Right) => {
                self.open_help();
            }
            MouseEventKind::ScrollDown => {
                if self.panel == Panel::Observe || self.panel == Panel::Control {
                    if self.panel == Panel::Observe {
                        self.observe_scroll.scroll_down(1);
                    } else {
                        self.control_chat_scroll.scroll_down(1);
                    }
                } else {
                    self.cursor_down();
                }
            }
            MouseEventKind::ScrollUp => {
                if self.panel == Panel::Observe || self.panel == Panel::Control {
                    if self.panel == Panel::Observe {
                        self.observe_scroll.scroll_up(1);
                    } else {
                        self.control_chat_scroll.scroll_up(1);
                    }
                } else {
                    self.cursor_up();
                }
            }
            MouseEventKind::Down(MouseButton::Left) => {
                // 第六轮 T1:Control tab 分隔条命中(HSplit 水平 + VSplit 垂直堆叠)。
                // (Observe 第八轮改全屏卷轴,无 HSplit 分隔条,不再检测 observe_split bar。)
                if self.panel == Panel::Control && self.control_area.contains(ratatui::layout::Position { x: m.column, y: m.row }) {
                    let [_left, hbar, right] = self.control_split.rects(self.control_area);
                    if hbar.contains(ratatui::layout::Position { x: m.column, y: m.row }) {
                        self.control_h_dragging = true;
                        return;
                    }
                    let seps = self.control_stack.separators(right);
                    for (i, sep) in seps.iter().enumerate() {
                        if sep.contains(ratatui::layout::Position { x: m.column, y: m.row }) {
                            self.control_v_dragging = Some(i);
                            return;
                        }
                    }
                }
                // TabBar 命中切 tab。
                if let Some(i) = self.tabbar.hit(self.tab_area, m.column, m.row) {
                    self.tabbar.select(i);
                    self.sync_panel_from_tab();
                    return;
                }
                // ADR-1:Control 按钮 ClickMap 命中 → trigger_control_button(鼠标 + 键盘 Enter 共用,F1 修复)。
                // 第六轮 T1:Control 左大纲 session 项 ClickMap 命中(id≥100 → 切 cursor)。
                if self.panel == Panel::Control {
                    if let Some(id) = self.clickmap.hit(m.column, m.row) {
                        if *id >= 100 {
                            // session 项:id-100 = flat index → 切 cursor + fetch_current。
                            let idx = *id - 100;
                            if idx < self.flat.len() {
                                self.cursor = idx;
                                self.fetch_current();
                            }
                        } else {
                            // 按钮 id 0-7。
                            self.trigger_control_button(*id);
                        }
                        return;
                    }
                }
                // ADR-2(第八轮):Observe session 点 → 跳 Control(跨 tab cursor 同步 + WS 重订阅)。
                if self.panel == Panel::Observe {
                    if let Some(idx) = self.clickmap.hit(m.column, m.row) {
                        self.jump_to_control(*idx);
                        return;
                    }
                }
            }
            MouseEventKind::Drag(MouseButton::Left) => {
                // ADR-2:拖拽分隔条改 pct。
                if self.observe_dragging {
                    // dx 近似为鼠标列变化(单步 drag delta)→ HSplit.drag 算 pct delta。
                    // crossterm Drag 每事件给当前位置,无 prev;用 1/-1 步进近似方向。
                    // ponytail: 精确需存 last_x 算 dx;单步足够流畅(每像素一个事件)。
                    let [_left, _bar, _right] = self.observe_split.rects(self.observe_area);
                    // 方向:鼠标在 bar 右侧→右拖加左 pane;左侧→左拖减。
                    let bar_x = _bar.x;
                    let dx: i32 = if m.column > bar_x { 1 } else if m.column < bar_x { -1 } else { 0 };
                    if dx != 0 {
                        self.observe_split.drag(dx, self.observe_area);
                    }
                }
                // 第六轮 T1:Control tab 分隔条拖拽(HSplit 水平 + VSplit 垂直堆叠)。
                if self.control_h_dragging {
                    let [_left, hbar, _right] = self.control_split.rects(self.control_area);
                    let dx: i32 = if m.column > hbar.x { 1 } else if m.column < hbar.x { -1 } else { 0 };
                    if dx != 0 {
                        self.control_split.drag(dx, self.control_area);
                    }
                }
                if let Some(pane_idx) = self.control_v_dragging {
                    let [_left, _hbar, right] = self.control_split.rects(self.control_area);
                    let seps = self.control_stack.separators(right);
                    if pane_idx < seps.len() {
                        let vbar = seps[pane_idx];
                        let dy: i32 = if m.row > vbar.y { 1 } else if m.row < vbar.y { -1 } else { 0 };
                        if dy != 0 {
                            self.control_stack.drag(pane_idx, dy, right);
                        }
                    }
                }
            }
            MouseEventKind::Up(MouseButton::Left) => {
                self.observe_dragging = false;
                self.control_h_dragging = false;
                self.control_v_dragging = None;
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

    /// ADR-2(第八轮):Observe→Control 跨 tab 跳转(cursor 同步 + WS 重订阅)。
    /// 点 Observe session 或键盘 Enter on ObserveSession focus → panel=Control + cursor=idx。
    /// 切 Control cursor session 后 WS manager 重订阅(observe 实时事件流入 cursor session)。
    /// 业务方法不改:仅组合现有 set panel/cursor/fetch/subscribe(UI 状态操作)。
    pub fn jump_to_control(&mut self, idx: usize) {
        if idx < self.flat.len() {
            self.cursor = idx;
            self.fetch_current();
        }
        self.panel = Panel::Control;
        self.sync_tab_from_panel();
        self.orche_online = fetch_orche_health();
        // WS 重订阅 Control cursor session(若 WS manager 已注入)。
        if let Some(s) = self.flat.get(self.cursor) {
            if let Some(mgr) = self.ws.as_ref() {
                mgr.subscribe(&s.harness_type, &s.session_id);
            }
        }
        self.focus = FocusTarget::ControlSession(self.cursor);
    }

    /// base panel 键位(P1 保留 + P2 扩展 e=raw exec / p=弹窗)。返回 true = 退出 app。
    fn handle_base_key(&mut self, k: &KeyEvent) -> bool {
        match k.code {
            KeyCode::Char('q') => true,
            KeyCode::Tab => {
                self.tabbar.next();
                self.sync_panel_from_tab();
                // ADR-2:切 panel 后焦点归 TabBar(下次方向键进入 panel 元素)。
                self.focus = FocusTarget::TabBar;
                false
            }
            KeyCode::BackTab => {
                // ADR-2:Shift+Tab 在 TabBar 与当前 panel 元素间切焦点。
                self.focus = self.focus.cycle(self.panel);
                false
            }
            KeyCode::Enter => {
                // F1:键盘 Enter 触发聚焦的 Control 按钮(focus==ControlButton(i),鼠标点击共用 trigger_control_button)。
                if self.panel == Panel::Control {
                    if let FocusTarget::ControlButton(i) = self.focus {
                        self.trigger_control_button(i);
                    }
                }
                // ADR-2(第八轮):Observe session 聚焦时 Enter → 跳 Control(cursor 同步 + WS 重订阅)。
                if self.panel == Panel::Observe {
                    if matches!(self.focus, FocusTarget::ObserveSession) {
                        self.jump_to_control(self.cursor);
                    }
                }
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
                // ADR-3:进入 Control 时预检 orche health(非阻塞,失败默认 false)。
                self.orche_online = fetch_orche_health();
                false
            }
            KeyCode::Char('c') => {
                self.panel = Panel::Control;
                self.sync_tab_from_panel();
                self.orche_online = fetch_orche_health();
                false
            }
            KeyCode::Char('j') | KeyCode::Down => {
                // P2 flow panel:j/k 切 flow cursor;Observe panel:j/k 滚 turn stream(ADR-2)。
                if self.panel == Panel::Flows {
                    self.flow_cursor_down();
                    // ADR-2:方向键更新键盘焦点跟随 flow cursor。
                    self.focus = FocusTarget::FlowsFlow(self.flow_cursor);
                } else if self.panel == Panel::Observe {
                    self.observe_scroll.scroll_down(1);
                    self.focus = FocusTarget::ObserveSession;
                } else if self.panel == Panel::Control {
                    // F3:Control 方向键——大纲区(ControlSession)切 cursor;输入栏(ControlButton)切按钮。
                    match self.focus {
                        FocusTarget::ControlSession(_) => { self.cursor_down(); self.focus = FocusTarget::ControlSession(self.cursor); }
                        _ => { let next = match self.focus { FocusTarget::ControlButton(i) => (i + 1).min(CONTROL_BUTTON_COUNT - 1), _ => 0 }; self.focus = FocusTarget::ControlButton(next); }
                    }
                } else {
                    self.cursor_down();
                    self.focus = FocusTarget::ObserveSession;
                }
                false
            }
            KeyCode::Char('k') | KeyCode::Up => {
                if self.panel == Panel::Flows {
                    self.flow_cursor_up();
                    self.focus = FocusTarget::FlowsFlow(self.flow_cursor);
                } else if self.panel == Panel::Observe {
                    self.observe_scroll.scroll_up(1);
                    self.focus = FocusTarget::ObserveSession;
                } else if self.panel == Panel::Control {
                    // F3:Control 方向键——大纲区(ControlSession)切 cursor;输入栏(ControlButton)切按钮。
                    match self.focus {
                        FocusTarget::ControlSession(_) => { self.cursor_up(); self.focus = FocusTarget::ControlSession(self.cursor); }
                        _ => { let prev = match self.focus { FocusTarget::ControlButton(i) => i.saturating_sub(1), _ => 0 }; self.focus = FocusTarget::ControlButton(prev); }
                    }
                } else {
                    self.cursor_up();
                    self.focus = FocusTarget::ObserveSession;
                }
                false
            }
            KeyCode::Char('f') => {
                // P2 flow:create 预设链 flow(A→B)。
                self.create_preset_flow(FlowPreset::Chain);
                self.mark_action("create_chain");
                false
            }
            KeyCode::Char('G') => {
                // P2 flow:create 预设分支 flow(A→B if cond else C)。
                self.create_preset_flow(FlowPreset::Branch);
                self.mark_action("create_branch");
                false
            }
            KeyCode::Char('D') => {
                // P2 flow:create 预设 DAG(A,C 并行 → B 合并)。
                self.create_preset_flow(FlowPreset::Dag);
                self.mark_action("create_dag");
                false
            }
            KeyCode::Char('R') => {
                // P2 flow:run 当前 cursor flow(POST /h/flows/{id}/run)。
                self.run_current_flow();
                self.mark_action("run_flow");
                false
            }
            KeyCode::Char('t') => {
                self.do_turn();
                self.mark_action("trigger");
                false
            }
            KeyCode::Char('s') => {
                // 多实例:claude-code spawn 同 sid 多 PTY;claw create 新 session。
                self.do_spawn();
                self.mark_action("spawn");
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

    // ── WS 事件处理(ADR-1 T4:弃 polling,WS 推送更新 app.events/flows)──
    // 业务方法不变;WS message → app.events[key]/app.flows[i] 映射(同 fetch_events/refresh_flows 效果)。

    /// 非阻塞收 WS 事件,累积进 app.events[key](turn 事件)。
    /// 主 loop 每帧调(Tick 或 poll 间隙)。WS manager 未注入时 no-op。
    pub fn drain_ws(&mut self) {
        // ponytail: 先抽干 channel 到本地 Vec(不可变借 self.ws),再应用(可变借 self)。
        // 避免 try_recv 借 self.ws 期间可变借 self.events/instances 的 borrow 冲突。
        let msgs: Vec<crate::ws::WsMsg> = {
            let Some(mgr) = self.ws.as_ref() else { return };
            let mut out = Vec::new();
            let mut n = 0u32;
            while let Ok(msg) = mgr.rx.try_recv() {
                out.push(msg);
                n += 1;
                if n > 256 { break; } // 防极端积压卡帧(observe 高频 token_delta)
            }
            out
        };
        for msg in msgs {
            match msg {
                crate::ws::WsMsg::Event { key, ev } => {
                    // 累积 turn 事件(同 fetch_events 效果:events[key].push + 实例去重计数)。
                    let evs = self.events.entry(key.clone()).or_default();
                    // 限制单 key 事件数(同 REST limit=50 语义,防无限增长)。
                    if evs.len() >= 200 {
                        evs.remove(0);
                    }
                    evs.push(ev);
                    // 多实例计数:重算去重 harness_id 数(ADR-5:同 sid 多 harness_id)。
                    if !self.events[&key].is_empty() {
                        let cnt = self.events[&key].iter()
                            .filter(|e| !e.harness_id.is_empty())
                            .map(|e| e.harness_id.as_str())
                            .collect::<std::collections::HashSet<_>>().len();
                        self.instances.insert(key, cnt);
                    }
                }
                crate::ws::WsMsg::FlowEvent { flow_id, ev } => {
                    self.apply_flow_event(&flow_id, &ev);
                }
                crate::ws::WsMsg::Error { key, .. } => {
                    // 连接断;不重连(WS manager idempotent,主 loop 下次 subscribe 重建)。
                    // ponytail: 自动重连 defer。保留 key 在 subs(已 detach)。
                    let _ = key;
                }
            }
        }
    }

    /// flow WS 事件 → app.flows[i].status(ADR-4:flow WS 订阅 ('flow',flow_id))。
    /// data.flow_event ∈ {flow_started,node_started,node_completed,flow_completed}
    /// (见 services/orchestrator/src/harness/flow.py:97-120)。
    /// ponytail: 不完整重建 FlowStatus——只标 flow/node 状态(轻量);精确状态由
    /// run_current_flow/create_preset_flow 的 fetch_flow REST 初始拉取兜底。
    fn apply_flow_event(&mut self, flow_id: &str, ev: &ObserveEvent) {
        let Some(fe) = ev.data.get("flow_event").and_then(|v| v.as_str()) else { return };
        // 找 tracked flow(按 flow_id)。
        let idx = self.flows.iter().position(|tf| tf.flow_id == flow_id);
        let Some(i) = idx else { return };
        // node 事件:更新 node 状态(若 flow_payload 含 node_id/node_status)。
        if let Some(payload) = ev.data.get("flow_payload") {
            if let (Some(nid), Some(nst)) = (
                payload.get("node_id").and_then(|v| v.as_str()),
                payload.get("node_status").and_then(|v| v.as_str()),
            ) {
                let tf = &mut self.flows[i];
                let status = tf.status.get_or_insert_with(|| FlowStatus {
                    flow_id: flow_id.to_string(),
                    status: "running".to_string(),
                    nodes: HashMap::new(),
                });
                let node = status.nodes.entry(nid.to_string()).or_insert_with(|| FlowNodeState {
                    id: nid.to_string(),
                    status: String::new(),
                    response: String::new(),
                    status_code: String::new(),
                });
                node.status = nst.to_string();
                if let Some(resp) = payload.get("response").and_then(|v| v.as_str()) {
                    node.response = resp.to_string();
                }
            }
        }
        // flow 级事件:更新 flow.status。
        match fe {
            "flow_started" => {
                let tf = &mut self.flows[i];
                let status = tf.status.get_or_insert_with(|| FlowStatus {
                    flow_id: flow_id.to_string(),
                    status: "running".to_string(),
                    nodes: HashMap::new(),
                });
                status.status = "running".to_string();
            }
            "flow_completed" => {
                if let Some(tf) = self.flows.get_mut(i) {
                    if let Some(s) = tf.status.as_mut() {
                        s.status = "completed".to_string();
                    }
                }
            }
            "node_started" | "node_completed" => {
                // node 级已上面处理;flow status 保持 running。
            }
            _ => {}
        }
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

    /// ADR-1:Control 按钮 ClickMap 命中 → raw-exec open_popup(id=3)。
    /// 手动注册 clickmap region(id=3),模拟 draw_control 注册后 handle_base_mouse 命中。
    #[test]
    fn control_clickmap_rawexec_opens_popup() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        // 模拟 draw_control 注册 raw-exec 按钮(id=3)在 (10,5)-(30,6)。
        app.clickmap.clear();
        app.clickmap.register(Rect::new(10, 5, 20, 1), 3);
        // 点击该区域 → 应打开 raw-exec 弹窗。
        let m = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 15,
            row: 5,
            modifiers: crossterm::event::KeyModifiers::empty(),
        };
        app.handle_base_mouse(&m);
        assert!(app.popups.iter().any(|p| p.id == "raw-exec"), "raw-exec popup should open on button click");
    }

    /// ADR-2(第八轮):Observe session 点 → 跳 Control(cursor 同步 + panel 切 Control)。
    /// 手动注册 clickmap region(id=1),模拟 draw_observe_scroll 注册后 handle_base_mouse 命中。
    #[test]
    fn observe_clickmap_session_jumps_to_control() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Observe;
        // 填充 flat sessions(2 个)。
        app.flat = vec![
            Session { harness_type: "claw".into(), session_id: "sess-a".into(), harness_id: "h1".into() },
            Session { harness_type: "claw".into(), session_id: "sess-b".into(), harness_id: "h2".into() },
        ];
        app.cursor = 0;
        // 模拟 draw_observe_scroll 注册 session 项(id=1,第二行)在 (0,3)-(35,4)。
        app.clickmap.clear();
        app.clickmap.register(Rect::new(0, 3, 35, 1), 1);
        // 点击该区域 → cursor=1 + panel=Control(跨 tab 跳转)。
        let m = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 5,
            row: 3,
            modifiers: crossterm::event::KeyModifiers::empty(),
        };
        app.handle_base_mouse(&m);
        assert_eq!(app.cursor, 1, "clicking session row 1 should set cursor=1");
        assert_eq!(app.panel, Panel::Control, "clicking Observe session should jump to Control");
    }

    /// ADR-2(第八轮):Observe 键盘 Enter on ObserveSession focus → 跳 Control(cursor 同步)。
    #[test]
    fn observe_enter_jumps_to_control() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Observe;
        app.focus = FocusTarget::ObserveSession;
        app.flat = vec![
            Session { harness_type: "claw".into(), session_id: "sess-a".into(), harness_id: "h1".into() },
            Session { harness_type: "claw".into(), session_id: "sess-b".into(), harness_id: "h2".into() },
        ];
        app.cursor = 1;
        // Enter on ObserveSession → panel=Control + cursor 仍 1。
        app.handle_base_key(&KeyEvent::new(KeyCode::Enter, crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.panel, Panel::Control, "Enter on ObserveSession should jump to Control");
        assert_eq!(app.cursor, 1, "cursor synced across tab jump");
        assert_eq!(app.focus, FocusTarget::ControlSession(1), "focus set to ControlSession after jump");
    }


    /// ADR-1/ADR-2:TabBar 命中优先于 ClickMap(点 tab 栏不应触发按钮)。
    #[test]
    fn tabbar_hit_takes_priority_over_clickmap() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        // tab_area 在顶部 (0,0,80,3),clickmap region 在 (10,5)。
        app.tab_area = Rect::new(0, 0, 80, 3);
        app.clickmap.clear();
        // region 含点击点 (3,1)(与 tab_area 重叠)→ 真测 tabbar 短路 clickmap(若优先级反转,clickmap 命中→raw-exec 开)。
        app.clickmap.register(Rect::new(0, 0, 80, 3), 3);
        // 点击 tab 栏区域 → 应切 tab,不开 raw-exec 弹窗。
        let m = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 3,
            row: 1,
            modifiers: crossterm::event::KeyModifiers::empty(),
        };
        app.handle_base_mouse(&m);
        assert!(!app.popups.iter().any(|p| p.id == "raw-exec"), "tabbar hit should not trigger clickmap");
    }

    /// ADR-1 T2:flow WS 事件 → app.flows[i].status 更新。
    /// 模拟 observe flow 广播(node_completed + flow_payload),apply_flow_event 应更新 node 状态。
    #[test]
    fn flow_ws_event_updates_flow_status() {
        let mut app = App::new(crate::kitty::detect());
        // 注入一个 tracked flow(无初始 status)。
        let def = preset_flow(FlowPreset::Chain, "m");
        app.flows.push(TrackedFlow {
            flow_id: "flow_test1".to_string(),
            def,
            status: None,
        });
        // 模拟 WS flow 事件:node_completed,node A → completed,response "16"。
        let ev = ObserveEvent {
            event_type: "tick_completed".to_string(),
            tick_id: "flow_test1".to_string(),
            harness_id: "flow_engine_abcd".to_string(),
            data: {
                let mut d = HashMap::new();
                d.insert("flow_event".to_string(), serde_json::json!("node_completed"));
                d.insert("flow_payload".to_string(), serde_json::json!({
                    "node_id": "A", "node_status": "completed", "response": "16"
                }));
                d
            },
        };
        app.apply_flow_event("flow_test1", &ev);
        let tf = &app.flows[0];
        assert_eq!(tf.status.as_ref().unwrap().status, "running");
        assert_eq!(tf.status.as_ref().unwrap().nodes["A"].status, "completed");
        assert_eq!(tf.status.as_ref().unwrap().nodes["A"].response, "16");
    }

    /// ADR-1 T2:flow_completed WS 事件 → app.flows[i].status = completed。
    #[test]
    fn flow_ws_completed_marks_flow_done() {
        let mut app = App::new(crate::kitty::detect());
        app.flows.push(TrackedFlow {
            flow_id: "flow_test2".to_string(),
            def: preset_flow(FlowPreset::Chain, "m"),
            status: Some(FlowStatus {
                flow_id: "flow_test2".to_string(),
                status: "running".to_string(),
                nodes: HashMap::new(),
            }),
        });
        let ev = ObserveEvent {
            event_type: "tick_completed".to_string(),
            tick_id: "flow_test2".to_string(),
            harness_id: "flow_engine_abcd".to_string(),
            data: {
                let mut d = HashMap::new();
                d.insert("flow_event".to_string(), serde_json::json!("flow_completed"));
                d.insert("flow_payload".to_string(), serde_json::json!({}));
                d
            },
        };
        app.apply_flow_event("flow_test2", &ev);
        assert_eq!(app.flows[0].status.as_ref().unwrap().status, "completed");
    }

    /// ADR-1 T1+T2:Tick 弃 REST polling —— Tick 分支不含 fetch_claw_events/refresh_flows。
    /// 通过 handle(Tick) 不改 flows(空 WS channel 时)验证 Tick 不触发 REST。
    #[test]
    fn tick_does_not_poll_rest() {
        let mut app = App::new(crate::kitty::detect());
        // ws=None(drain_ws no-op),Tick 不应 panic 也不调 REST。
        app.handle(&crate::events::AppEvent::Tick);
        // events 仍空(无 WS,无 fetch_claw_events)。
        assert!(app.events.is_empty(), "Tick with no WS should not fetch events via REST");
    }

    // ── T1/T2 自测(ADR-1/ADR-2/ADR-3:焦点循环 + loading + UI 状态)──────

    /// ADR-2:FocusTarget::cycle 在 TabBar 与 panel 元素间切。
    #[test]
    fn focus_target_cycles_tabbar_and_panel() {
        // F3:Control panel 三态——TabBar → ControlSession(0)(大纲首)。
        assert_eq!(FocusTarget::TabBar.cycle(Panel::Control), FocusTarget::ControlSession(0));
        // ControlSession → ControlButton(0)(大纲 → 输入栏按钮)。
        assert_eq!(FocusTarget::ControlSession(0).cycle(Panel::Control), FocusTarget::ControlButton(0));
        // 从 ControlButton 回 TabBar。
        assert_eq!(FocusTarget::ControlButton(0).cycle(Panel::Control), FocusTarget::TabBar);
        // Observe:TabBar → ObserveSession。
        assert_eq!(FocusTarget::TabBar.cycle(Panel::Observe), FocusTarget::ObserveSession);
        // Flows:TabBar → FlowsFlow(0)。
        assert_eq!(FocusTarget::TabBar.cycle(Panel::Flows), FocusTarget::FlowsFlow(0));
        // Home:无元素,停在 TabBar。
        assert_eq!(FocusTarget::TabBar.cycle(Panel::Home), FocusTarget::TabBar);
    }

    /// ADR-2:键盘 BackTab(Shift+Tab)切焦点(经 handle)。
    #[test]
    fn backtab_cycles_focus() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.focus = FocusTarget::TabBar;
        let ev = crate::events::AppEvent::Key(KeyEvent::new(KeyCode::BackTab, crossterm::event::KeyModifiers::SHIFT));
        // F3:Shift+Tab 三态——TabBar → ControlSession(0) → ControlButton(0) → TabBar。
        app.handle(&ev);
        assert_eq!(app.focus, FocusTarget::ControlSession(0), "BackTab TabBar → ControlSession(0)");
        app.handle(&ev);
        assert_eq!(app.focus, FocusTarget::ControlButton(0), "BackTab ControlSession → ControlButton(0)");
        app.handle(&ev);
        assert_eq!(app.focus, FocusTarget::TabBar, "BackTab ControlButton → TabBar");
    }

    /// ADR-3:mark_action + action_loading(<500ms 高亮窗口)。
    #[test]
    fn mark_action_sets_loading_window() {
        let mut app = App::new(crate::kitty::detect());
        // 无 action:不 loading。
        assert!(!app.action_loading("trigger"));
        app.mark_action("trigger");
        // 刚标记:loading(<500ms)。
        assert!(app.action_loading("trigger"), "action just marked should be loading");
        assert!(!app.action_loading("spawn"), "different action name should not be loading");
    }

    /// ADR-2:App::new 初始化 UI 状态字段(focus/orche_online/last_action)。
    #[test]
    fn new_initializes_ui_state_fields() {
        let app = App::new(crate::kitty::detect());
        assert_eq!(app.focus, FocusTarget::TabBar);
        assert!(app.last_action.is_none());
        // orche_online 默认 true(首次预检刷新)。
        assert!(app.orche_online);
    }

    /// ADR-3:Control 按钮点击设 focus + mark_action。
    /// 模拟 clickmap 注册 trigger 按钮(id=0),点击后 focus=ControlButton(0) + last_action=trigger。
    #[test]
    fn control_click_sets_focus_and_loading() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.clickmap.clear();
        app.clickmap.register(Rect::new(0, 5, 20, 1), 0); // trigger 按钮
        let m = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 5,
            row: 5,
            modifiers: crossterm::event::KeyModifiers::empty(),
        };
        app.handle_base_mouse(&m);
        assert_eq!(app.focus, FocusTarget::ControlButton(0), "click should set focus to clicked button");
        assert!(app.action_loading("trigger"), "click should mark trigger as loading");
    }

    /// ADR-3:Control 方向键切 focus(ControlButton idx 在 0..CONTROL_BUTTON_COUNT 间)。
    #[test]
    fn control_arrow_keys_move_focus() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.focus = FocusTarget::ControlButton(0);
        // Down:j 方向键 → ControlButton(1)。
        app.handle_base_key(&KeyEvent::new(KeyCode::Down, crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.focus, FocusTarget::ControlButton(1));
        // Up:k 方向键 → 回 ControlButton(0)。
        app.handle_base_key(&KeyEvent::new(KeyCode::Up, crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.focus, FocusTarget::ControlButton(0));
    }

    /// ADR-3:fetch_orche_health 函数存在且不 panic(orche 离线时返 false,不 crash)。
    #[test]
    fn fetch_orche_health_returns_bool_without_panic() {
        // 测试环境 orche 不可达,应返 false 而非 panic。
        let _ = fetch_orche_health();
    }

    /// ADR-3:CONTROL_BUTTON_COUNT 覆盖所有 flow 按钮(create chain/branch/dag + run)。
    #[test]
    fn control_button_count_covers_all_flow_buttons() {
        // 8 按钮:trigger(0)/spawn(1)/refresh(2)/rawexec(3)/chain(4)/branch(5)/dag(6)/run(7)。
        assert_eq!(CONTROL_BUTTON_COUNT, 8);
    }

    /// ADR-1(第八轮):control_stack pcts 扩 4 pane(对话/输入/flow/属性)。VerticalStack 真扩展验证。
    #[test]
    fn control_stack_has_4_panes() {
        let app = App::new(crate::kitty::detect());
        assert_eq!(app.control_stack.pcts.len(), 4, "control_stack must have 4 panes (对话/输入/flow/属性)");
        // push pane 加区不改架构:VerticalStack.rects 返 len==pcts.len()。
        let panes = app.control_stack.rects(ratatui::layout::Rect::new(0, 0, 80, 40));
        assert_eq!(panes.len(), 4, "4 panes → 4 rects");
        // panes 上下堆叠(y 单调递增)。
        for w in panes.windows(2) {
            assert!(w[1].y >= w[0].y + w[0].height, "panes stack vertically");
        }
    }
}
