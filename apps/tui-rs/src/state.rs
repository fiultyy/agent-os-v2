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
use ratatui::style::Color;
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
    /// cwd(claude-code 才有,orche /h/{type}/sessions 合并)。None=未取到/cc 外类型。
    #[serde(default)]
    pub cwd: Option<String>,
    /// claw 长连 running(orche 合并)。cc 无状态恒 false(按 turn spawn)。
    #[serde(default)]
    pub running: bool,
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
    /// IT7:observe 服务端 event_id(去重)。REST/WS 时序重叠时同一事件会重复,
    /// drain_ws 据 event_id 去重。serde default="" 兼容旧数据(空不过滤)。
    #[serde(default)]
    pub event_id: String,
}
#[derive(Deserialize)]
struct EventsResp {
    events: Vec<ObserveEvent>,
}

pub fn fetch_sessions() -> Option<SessionsGrouped> {
    let mut sg = ureq::get(&format!("{}/sessions/grouped", OBSERVE))
        .call()
        .ok()?
        .into_json::<SessionsGrouped>()
        .ok()?;
    // 合并 orche cwd/running(orche type 用 norm_ht 后的 claw/claude-code/agent-os-v2;离线静默跳过)。
    merge_orche_session_meta(&mut sg, "claw");
    merge_orche_session_meta(&mut sg, "claude-code");
    // ADR-3:C.T2 — agent-os-v2 漏合并致 TUI 显示 (no cwd)。obs_ht 直名(observe ht=agent-os-v2,
    // orche type 同名,无 openclaw 类映射,见 merge_orche_session_meta 内 obs_ht 逻辑)。
    merge_orche_session_meta(&mut sg, "agent-os-v2");
    Some(sg)
}

/// 拉 orche GET /h/{type}/sessions,按 session_id 匹配,把 cwd/running 合并进 sg。
/// observe harness_type 映射:openclaw↔claw(同一后端);claude-code 一致。
/// 离线/失败静默跳过(保留默认 None/false),与现有 orche 离线容忍一致。
fn merge_orche_session_meta(sg: &mut SessionsGrouped, orch_type: &str) {
    #[derive(Deserialize)]
    struct Item {
        session_id: String,
        #[serde(default)]
        cwd: Option<String>,
        #[serde(default)]
        running: bool,
    }
    #[derive(Deserialize)]
    struct Resp { sessions: Vec<Item> }
    let resp = match ureq::get(&format!("{}/h/{}/sessions", ORCH, orch_type))
        .call().ok()
        .and_then(|r| r.into_json::<Resp>().ok())
    {
        Some(r) => r,
        None => return, // orche 离线/解析失败:静默跳过
    };
    // orch_type → observe harness_type(claw 在 observe 叫 openclaw)。
    let obs_ht = if orch_type == "claw" { "openclaw" } else { orch_type };
    for it in &resp.sessions {
        if let Some(list) = sg.sessions_by_harness.get_mut(obs_ht) {
            for s in list.iter_mut() {
                if s.session_id == it.session_id {
                    s.cwd = it.cwd.clone();
                    s.running = it.running;
                }
            }
        }
        // claw 对应项也可能以 "claw" 出现(norm_ht 归一前),一并匹配。
        if orch_type == "claw" {
            if let Some(list) = sg.sessions_by_harness.get_mut("claw") {
                for s in list.iter_mut() {
                    if s.session_id == it.session_id {
                        s.cwd = it.cwd.clone();
                        s.running = it.running;
                    }
                }
            }
        }
    }
}
// ═══ ADR-O1/O2:Orchestrate tab · fork 谱系树 ═════════════════════════
// lineage 走 observe GET /sessions?harness_type=agent-os-v2(返 parent_session_id + agent_id,
// orche list_sessions 缺字段 defer)。客户端按 parent_session_id 建树。

/// fork 树节点状态符号(●active/✓done/⠋running/○idle,User Stories §4)。
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum NodeState {
    Active,
    Done,
    Running,
    Idle,
}
impl NodeState {
    pub fn glyph(self) -> &'static str {
        match self {
            NodeState::Active => "●",
            NodeState::Done => "✓",
            NodeState::Running => "⠋",
            NodeState::Idle => "○",
        }
    }
    pub fn from_event(event_type: &str, status: &str) -> Self {
        match (event_type, status) {
            ("tick_started", _) => NodeState::Running,
            ("tick_completed", "success") => NodeState::Done,
            // error/cancelled/其他终态均不显成 ✓(语义错);显式 Idle。
            ("tick_completed", _) => NodeState::Idle,
            ("branch_created", _) => NodeState::Active,
            _ => NodeState::Idle,
        }
    }
}

#[derive(Clone, Debug)]
pub struct ForkNode {
    pub session_id: String,
    pub agent_id: String,
    pub harness_type: String,
    pub parent: Option<String>,
    pub state: NodeState,
    /// 子节点 session_id(fork fan-out)。
    pub children: Vec<String>,
}

/// 光标选中投影(render footer / 原语入参)。User Stories §4 产出。
#[derive(Clone, Debug)]
pub struct Selection {
    pub session_id: String,
    pub agent_id: String,
    pub state: NodeState,
    pub parent: Option<String>,
}
impl Selection {
    pub fn from_node(n: &ForkNode) -> Self {
        Self {
            session_id: n.session_id.clone(),
            agent_id: n.agent_id.clone(),
            state: n.state,
            parent: n.parent.clone(),
        }
    }
}

/// fork 谱系树:扁平 node map + root session_id 列表(无 parent 的节点)。
/// ponytail: 全量拉取客户端建树(ADR-O2);session 过千加 observe parent 索引或 /children。
#[derive(Clone, Debug, Default)]
pub struct ForkTree {
    pub nodes: std::collections::HashMap<String, ForkNode>,
    pub roots: Vec<String>,
}

impl ForkTree {
    /// DFS 后序扁平序(j/k 跨层级光标用)。根优先 → 子树递归。
    pub fn flat_order(&self) -> Vec<String> {
        let mut out = Vec::new();
        for r in &self.roots {
            self.dfs(r, &mut out);
        }
        out
    }
    fn dfs(&self, sid: &str, out: &mut Vec<String>) {
        out.push(sid.to_string());
        if let Some(n) = self.nodes.get(sid) {
            for c in &n.children {
                self.dfs(c, out);
            }
        }
    }
    /// 每节点在 DFS 序里的深度(根=0);render 缩进 + anchor x 坐标用。
    pub fn depth(&self, sid: &str) -> usize {
        let mut d = 0;
        let mut cur = self.nodes.get(sid).and_then(|n| n.parent.clone());
        while let Some(p) = cur {
            d += 1;
            cur = self.nodes.get(&p).and_then(|n| n.parent.clone());
        }
        d
    }
}

/// observe GET /sessions?harness_type=agent-os-v2 → ForkTree(客户端按 parent 建树)。
/// 离线/失败返空树(与 fetch_sessions 容忍一致)。R5:只读 GET,不引 memory/ingest。
#[derive(Deserialize)]
struct OrchSession {
    session_id: String,
    #[serde(default)]
    harness_type: String,
    #[serde(default)]
    agent_id: String,
    #[serde(default)]
    parent_session_id: String,
}
#[derive(Deserialize)]
struct OrchSessionsResp { sessions: Vec<OrchSession> }

fn fetch_orch_sessions() -> Vec<OrchSession> {
    // timeout 防 observe 慢时 drain_ws 内 ureq 阻塞 30s(默认)致 TUI 帧冻结。
    ureq::get(&format!("{}/sessions", OBSERVE))
        .timeout(std::time::Duration::from_secs(2))
        .query("harness_type", "agent-os-v2")
        .call().ok()
        .and_then(|r| r.into_json::<OrchSessionsResp>().ok())
        .map(|r| r.sessions)
        .unwrap_or_default()
}

/// 建 fork 树:遍历 observe sessions,parent 非空 → 挂父 children;空 → root。
fn build_fork_tree(sessions: Vec<OrchSession>) -> ForkTree {
    let mut tree = ForkTree::default();
    // 先建所有节点(暂无 children)。
    for s in &sessions {
        let parent = if s.parent_session_id.is_empty() { None } else { Some(s.parent_session_id.clone()) };
        tree.nodes.insert(s.session_id.clone(), ForkNode {
            session_id: s.session_id.clone(),
            agent_id: s.agent_id.clone(),
            harness_type: s.harness_type.clone(),
            parent,
            state: NodeState::Idle,
            children: vec![],
        });
    }
    // 填 children + roots。parent 指向不存在的节点(孤儿)按 root 处理。
    for s in &sessions {
        match &tree.nodes.get(&s.session_id).and_then(|n| n.parent.clone()) {
            Some(p) if tree.nodes.contains_key(p) => {
                if let Some(pn) = tree.nodes.get_mut(p) {
                    if !pn.children.contains(&s.session_id) {
                        pn.children.push(s.session_id.clone());
                    }
                }
            }
            _ => {
                if !tree.roots.contains(&s.session_id) {
                    tree.roots.push(s.session_id.clone());
                }
            }
        }
    }
    tree
}

// ── IT2 节点 C session 管理辅助(接节点 B 后端端点)──────────────────
// 不可达 graceful:返 None / 默认 / false,不 panic。

#[derive(Deserialize)]
struct AgentsResp { #[allow(dead_code)] default: Option<String>, agents: Vec<String> }
#[derive(Deserialize)]
struct CwdsResp { #[allow(dead_code)] default: Option<String>, cwds: Vec<String> }

// ADR-3: agent-os-v2 picker 源。contract {agents:[{id,name,default}]}(与 claw 的
// AgentsResp 不同——claw 是 [str],ao2 是 [{id,name,default}])。name/default 仅展示用,
// 提交时只用 id(POST /h/agent-os-v2/sessions {agent_id})。
#[derive(Deserialize, Clone)]
pub struct Ao2Agent {
    pub id: String,
    pub name: String,
    pub default: bool,
}
#[derive(Deserialize)]
struct Ao2AgentsResp { agents: Vec<Ao2Agent> }

/// GET /h/claw/agents → agent 列表。失败返 vec!["main"]。
pub fn fetch_claw_agents() -> Vec<String> {
    ureq::get(&format!("{}/h/claw/agents", ORCH))
        .call().ok()
        .and_then(|r| r.into_json::<AgentsResp>().ok())
        .map(|a| if a.agents.is_empty() { vec!["main".to_string()] } else { a.agents })
        .unwrap_or_else(|| vec!["main".to_string()])
}

/// 生成 claw session 唯一 conv key(回归 bug2「创建不新建」)。
/// orche routes.py 把 claw session_key 固定 `agent:<a>:main`,同 agent 已存在 → exists
/// 短路不新建;TUI create_session 不查 status → focus 旧 session(看似"创建不新建")。
/// 故生成 `agent:<a>:t<时间戳hex>` 让 orche 见新 conv → 必新建。agent_base 剥 "agent:"
/// 前缀(防 agent 已带前缀,split ':' 取第 2 段,裸名则原样)。
fn claw_session_key(agent: &str) -> String {
    let agent_base = agent.split(':').nth(1).unwrap_or(agent);
    let conv = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| format!("t{:x}", d.as_secs() % 0x1000000))
        .unwrap_or_else(|_| "new".to_string());
    format!("agent:{}:{}", agent_base, conv)
}
/// GET /h/claude-code/cwds → cwd 列表。失败返空。
pub fn fetch_cc_cwds() -> Vec<String> {
    ureq::get(&format!("{}/h/claude-code/cwds", ORCH))
        .call().ok()
        .and_then(|r| r.into_json::<CwdsResp>().ok())
        .map(|a| a.cwds)
        .unwrap_or_default()
}
/// ADR-3: GET /h/agent-os-v2/agents → registry agent 列表(picker 源)。
/// 失败返空 Vec(new popup 显空列表提示,不崩)。default 项用于预选。
pub fn fetch_ao2_agents() -> Vec<Ao2Agent> {
    ureq::get(&format!("{}/h/agent-os-v2/agents", ORCH))
        .call().ok()
        .and_then(|r| r.into_json::<Ao2AgentsResp>().ok())
        .map(|a| a.agents)
        .unwrap_or_default()
}
/// POST /h/claude-code/sessions {cwd} → session_id。失败返 None。
pub fn create_cc_session_cwd(cwd: &str) -> Option<String> {
    let resp = ureq::post(&format!("{}/h/claude-code/sessions", ORCH))
        .send_json(serde_json::json!({ "cwd": cwd })).ok()?;
    let v: serde_json::Value = resp.into_json().ok()?;
    v.get("session_id").and_then(|x| x.as_str()).map(|s| s.to_string())
}
/// POST /h/{type}/sessions/fork {source_session_id, first_message, new_session_id?}。
/// → (new_session_id, forked)。claw 501 时 None(ADR-4:claw fork 未实现)。
pub fn fork_session(ht: &str, source: &str, first_msg: &str) -> Option<(String, bool)> {
    let resp = ureq::post(&format!("{}/h/{}/sessions/fork", ORCH, ht))
        .send_json(serde_json::json!({
            "source_session_id": source,
            "first_message": first_msg,
        })).ok()?;
    let v: serde_json::Value = resp.into_json().ok()?;
    let new_sid = v.get("new_session_id").and_then(|x| x.as_str())?.to_string();
    let forked = v.get("forked").and_then(|x| x.as_bool()).unwrap_or(true);
    Some((new_sid, forked))
}
/// ADR-O6:POST /h/{type}/sessions/{id}/turn/cancel {tick_id} → 协作式中断异步 turn。
/// Ok(()) = 后端 200(cancelled);Err(msg) = 404(tick 不在/已结束)或 orche 不可达/超时。
/// timeout 防 orche 慢时 TUI 帧冻结(与 fetch_orch_sessions/merge_orche 一致)。
pub fn cancel_turn(ht: &str, sid: &str, tick_id: &str) -> Result<(), String> {
    let url = format!("{}/h/{}/sessions/{}/turn/cancel", ORCH, ht, sid);
    match ureq::post(&url)
        .timeout(std::time::Duration::from_secs(3))
        .send_json(serde_json::json!({ "tick_id": tick_id }))
    {
        Ok(r) if r.status() == 200 => Ok(()),
        Ok(r) => Err(format!("orche HTTP {}", r.status())),
        // 4xx/5xx 细分:404=tick 已结束/不存在,其他 4xx/5xx 透传 status,非 HTTP=连接/超时类。
        Err(ureq::Error::Status(code, _)) => Err(match code {
            404 => "tick 不在(已结束?)".into(),
            c => format!("orche HTTP {}", c),
        }),
        Err(_) => Err("orche 不可达/超时".into()),
    }
}
/// POST /h/{type}/sessions/{id}/archive {prompt?} → summary turn 文本。失败 None。
pub fn archive_session(ht: &str, sid: &str, prompt: Option<&str>) -> Option<String> {
    let body = match prompt {
        Some(p) => serde_json::json!({ "prompt": p }),
        None => serde_json::json!({}),
    };
    let resp = ureq::post(&format!("{}/h/{}/sessions/{}/archive", ORCH, ht, sid))
        .send_json(body).ok()?;
    resp.into_string().ok()
}
/// DELETE /h/{type}/sessions/{id} → raw_deleted bool。失败 false。
pub fn delete_session_raw(ht: &str, sid: &str) -> bool {
    // 或che delete(flow type 无端点→400;observe-only session store 无→404)。observe 兜底清
    // 镜像(TUI 不显)。observe ht 映射:claw→openclaw,其余原样(claude-code/flow)。
    let orche_ok = ureq::delete(&format!("{}/h/{}/sessions/{}", ORCH, ht, sid))
        .call().ok()
        .and_then(|r| r.into_json::<serde_json::Value>().ok())
        .and_then(|v| v.get("raw_deleted").and_then(|x| x.as_bool()))
        .unwrap_or(false);
    let ob_ht = match ht { "claw" => "openclaw", other => other };
    let observe_ok = ureq::delete(&format!("{}/sessions/{}/{}", OBSERVE, ob_ht, sid))
        .call().is_ok();
    orche_ok || observe_ok
}

pub fn fetch_events(h: &str, sid: &str) -> Option<Vec<ObserveEvent>> {
    // token_delta 流式 token 不存 app.events(撑 cap 挤历史 turn 结构)。filter token_delta。
    // limit 1000 = observe 端点 cap max(>1000 超限返空)。render 用 tick_completed.response,流式 P2 defer。
    ureq::get(&format!("{}/sessions/{}/{}/events?limit=1000", OBSERVE, h, sid))
        .call()
        .ok()?
        .into_json::<EventsResp>()
        .ok()
        .map(|e| e.events.into_iter().filter(|ev| ev.event_type != "token_delta").collect())
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

/// claw 手动重连(POST /h/claw/sessions/{sid}/reconnect)。返回 connected。
/// orche 不可达 / 4xx·5xx / 解析失败 → None(调用方已保证仅 claw 调本 fn)。
pub fn reconnect_claw(sid: &str) -> Option<bool> {
    let v: serde_json::Value = ureq::post(&format!("{}/h/claw/sessions/{}/reconnect", ORCH, sid))
        .send_string("")
        .ok()?
        .into_json()
        .ok()?;
    v.get("connected").and_then(|x| x.as_bool())
}

/// 复制到系统剪贴板(xclip)。无 xclip/失败静默。
fn copy_to_clipboard(s: &str) {
    use std::io::Write;
    use std::process::{Command, Stdio};
    let mut child = match Command::new("xclip")
        .arg("-selection").arg("clipboard")
        .stdin(Stdio::piped()).spawn()
    {
        Ok(c) => c,
        Err(_) => return,
    };
    if let Some(stdin) = child.stdin.as_mut() {
        let _ = stdin.write_all(s.as_bytes());
    }
    let _ = child.wait();
}

/// 触发一个 turn(POST /h/{type}/sessions/{sid}/turn)。type∈{claw,claude-code}。
/// `async_run=true` 仅 agent-os-v2 有意义:server create_task 后立返 {started,tick_id},
/// 不等 LLM 跑完(observe tick 事件流报进度)。返回 server 回的 status 文本。
pub fn trigger_turn(ht: &str, sid: &str, message: &str, async_run: bool) -> Option<String> {
    let resp = ureq::post(&format!("{}/h/{}/sessions/{}/turn", ORCH, ht, sid))
        .send_json(serde_json::json!({ "message": message, "async_run": async_run }))
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

/// 按 unicode-width 截断到 n 列宽(留 ellipsis 位)。CJK/emoji 按显示宽计,避免
/// chars().count() 把宽字符算 1 致列对齐错位(状态栏/footer/列表行)。逐 char 累加
/// width,不切 char 中间(无乱码/panic)。~17 调用点自动受益(ASCII 调用点 char=width 不变)。
pub fn trunc(s: &str, n: usize) -> String {
    use unicode_width::UnicodeWidthStr;
    let w = UnicodeWidthStr::width(s);
    if w <= n {
        return s.to_string();
    }
    let budget = n.saturating_sub(1); // 留 1 列给 …
    let mut out = String::new();
    let mut acc = 0usize;
    for ch in s.chars() {
        let cw = unicode_width::UnicodeWidthChar::width(ch).unwrap_or(0);
        if acc + cw > budget {
            break;
        }
        acc += cw;
        out.push(ch);
    }
    format!("{}…", out)
}

/// harness_type → (2 字母色块标签, 色)。左大纲色块列用。
pub fn harness_tag(h: &str) -> (&'static str, Color) {
    match h {
        "openclaw" => ("oc", Color::Cyan),
        "claude-code" => ("cc", Color::Blue),
        "flow" => ("fl", Color::Magenta),
        "agent-os-v2" => ("ao", Color::Green),
        _ => ("·", Color::Yellow),
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
    Flows,
    Observe,
    Control,
    /// ADR-O1:第4 tab,与 Flows 物理隔离(fork 谱系树 ≠ workflow flow DAG)。
    Orchestrate,
}
impl Panel {
    pub fn label(self) -> &'static str {
        match self {
            Panel::Flows => "FLOWS ◐ 编排 DAG",
            Panel::Observe => "OBSERVE ☰ 纵向堆叠",
            Panel::Control => "CONTROL ⌘ orchestrator",
            Panel::Orchestrate => "ORCHESTRATE ⑂ fork 谱系树",
        }
    }
    pub fn next(self) -> Self {
        match self {
            Panel::Flows => Panel::Observe,
            Panel::Observe => Panel::Control,
            Panel::Control => Panel::Orchestrate,
            Panel::Orchestrate => Panel::Flows,
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
    /// styled 正文行(per-span style,按钮色块用)。非空时 render_popup 优先于 md_text/body。
    pub body_lines: Vec<ratatui::text::Line<'static>>,
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
            body_lines: vec![],
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
    /// ADR-O1 Orchestrate tab:fork 树扁平序(DFS 后序)节点索引。
    OrchestrateNode(usize),
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
                Panel::Orchestrate => FocusTarget::OrchestrateNode(0),
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
    /// ADR-O1/O2 Orchestrate tab:fork 谱系树(observe GET /sessions 客户端建树)。
    pub fork_tree: ForkTree,
    /// Orchestrate tab 光标(fork_tree.nodes DFS 序索引)。j/k 跨层级移动。
    pub orch_cursor: usize,
    /// Orchestrate tab 当前选中(光标节点投影;render footer / 原语用)。
    pub orch_selection: Option<Selection>,
    /// ADR-O4:人控原语 registry。footer hint 渲染 + key dispatch + 灰显从此派生。
    /// W-B 注入 primitives::all()(四空壳占位);W-C 各填真实 impl。
    pub primitives: Vec<Box<dyn crate::primitives::OrchestratePrimitive>>,
    /// ADR-O6:session_id → 运行中异步 turn 的 tick_id。tick_started 插入,tick_completed 移除。
    /// cancel 原语从此查 tick_id(Selection 不带 tick_id,同 ht 走 fork_tree node 的做法)。
    /// ponytail: 仅内存态;tree 全量刷新(fetch_orch_tree)不清此 map(刷新不发 tick 事件,
    /// 节点状态另由 tick_started/completed 事件驱动刷新),stale 由 tick_completed 清。
    pub running_ticks: HashMap<String, String>,
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
    // ── Control 布局(窄大纲 + 色块组标签 + 右侧 tab 化,非业务字段)─────
    /// Control HSplit(左窄大纲 | 右主区)。pct=左占比,resizable。
    pub control_split: HSplit,
    /// Control 对话区 ScrollView(turn stream 滚动)。
    pub control_chat_scroll: ScrollView,
    /// IT4:chat tail 跟随。true=有新事件自动滚底;do_turn 发送置 true,PgUp/上滚置 false,
    /// PgDn/scroll_to_bottom 置 true。默认 true(进 Control 即跟最新)。
    pub chat_follow_tail: bool,
    /// Control 对话区 turn 总数(render_turn_stream 分组数,footer N/M 用)。
    pub control_turn_count: usize,
    /// render_turn_stream 缓存(key=cursor session key + 事件数;事件不变→复用,消除每帧重建)。
    pub cached_turn_lines: Option<(String, Option<(String, String)>, Vec<ratatui::text::Line<'static>>, usize)>,
    /// Control 区域缓存(draw 算 → handle mouse drag hit 用)。
    pub control_area: Rect,
    /// 输入栏 textarea 实际区(render_input_bar 算 + 存,鼠标划选用)。
    pub input_area: Rect,
    /// 鼠标正在拖 control 主分隔条(左|右 HSplit bar)。
    pub control_h_dragging: bool,
    /// 右主区 tab(对话 | flow | 属性)。复用 TabBar。
    pub control_right_tabs: TabBar,
    /// 右主区 tab 栏区域缓存(点击 hit 用)。
    pub right_tab_area: Rect,
    /// 左大纲 harness 组名(排序,draw_control 写入;色块点击 id-200 索引)。
    pub control_groups: Vec<String>,
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
    /// raw-exec 待执行 spawn(`e` 键 / 按钮3 设置)。run() loop 消费它:
    /// 挂起 TUI raw mode + alt screen → 子进程(claude --resume / openclaw)接管终端 → 退出后恢复 + 全重绘。
    pub pending_spawn: Option<(crate::components::raw_exec::Harness, Option<String>)>,
    /// optimistic 回显:do_turn 立即存用户消息(本地显 spinner 跑马灯),
    /// drain_ws 收 orche 该 cursor session 事件 → 确认 → None(去特效)。根治输入回显延迟。
    pub pending_turn: Option<(String, String)>, // (session_key, msg) per-session:切 session 不串显 spinner
    /// #8 pending 进入时刻;Tick 检 age>60s 兜底清(trigger 失败/WS 丢 tick_started → stale spinner 永驻)。
    pub pending_since: Option<std::time::Instant>,
    /// spinner 动画帧(Tick 递增,render 取 SPINNER[frame % len]);pending 时 poll 缩 80ms 流畅。
    pub spinner_frame: usize,
    /// 流式 token_delta 累积(drain_ws 收 token_delta → buffer;tick_completed/failed 清)。
    /// 不进 events(防撑 cap=200 挤掉历史 turn);render 在 ev_lines 末尾 append streaming 行
    /// (pending_turn 同款 cache 外每帧变)。openclaw turn 边收边显;native 不发 token_delta 故空。
    pub streaming_text: std::collections::HashMap<String, String>,
    /// WS 重连指示:key→最近 WsMsg::Error 时间(drain_ws Error 记,Event 收到清);
    /// status_spans 查 cursor session elapsed<60s 显"⚠WS重连"。
    pub ws_errors: std::collections::HashMap<String, std::time::Instant>,
    /// 后台 fetch 全量回传:do_turn spawn trigger_turn+fetch_events → tx 发 (key, Option<events>),
    /// Tick drain rx → events 全量替换 + 清 pending(去 spinner,user msg 由全量无缝接管)。非阻塞 UI。
    pub fetch_tx: std::sync::mpsc::Sender<(String, Option<Vec<ObserveEvent>>)>,
    fetch_rx: std::sync::mpsc::Receiver<(String, Option<Vec<ObserveEvent>>)>,
    /// Control 输入模式:true=所有字母进 turn_msg(输入栏可自由打字,不受 t/s/p/h 等快捷键抢占);
    /// Esc 退出到快捷键模式(此时 t/s/f/G/D/R/p/h 等生效),`i` 再进入。默认 true:进 Control 即可打字。
    pub insert_mode: bool,
    // ── ADR-1/ADR-7 输入 UX(节点 A 新增)──────────────────────────────
    /// ADR-1 多行 textarea 输入(codex 式编辑器)。turn_msg 保留作 do_turn 兼容缓冲。
    pub textarea: crate::components::textarea::Textarea,
    /// codex 式 textarea state(跨帧 scroll,精确光标 render 用)。
    pub textarea_state: crate::components::textarea::TextareaState,
    /// codex PasteBurst:tmux 无 bracketed paste 时靠时序启发式识别粘贴(burst 内 \n 插入非发送)。
    pub paste_burst: crate::components::paste_burst::PasteBurst,
    /// ADR-7 输入历史(↑/↓ 翻历史)。原生 Vec + 索引兜底(InputHistory 组件已注册但 state 仍用 Vec)。
    pub input_history: Vec<String>,
    /// 历史浏览游标(None=不在浏览历史,写新输入;Some(i)=指向 input_history[i])。
    pub history_cursor: Option<usize>,
    /// ADR-7 @mention 候选弹窗激活态。true=用户打了 @,mention popup 开;
    /// 组件由节点 B 在 components::mentions 注册,本 bool 兼作 gate。
    pub mentions_open: bool,
    /// ADR-3:×(顶栏右)→ quit_requested=true,run loop 退出(替代裸 q 的语义化退出)。
    pub quit_requested: bool,
    /// ADR-4 左侧折叠树:已折叠组名集合(HashSet)。toggle_group 增删;
    /// 默认空集=全展开。draw_control 读此判 header 展开态。
    pub control_collapsed: std::collections::HashSet<String>,
    /// IT3 ④:Observe 折叠树已折叠组名集合。toggle_observe_group 增删;默认空=全展开。
    pub observe_collapsed: std::collections::HashSet<String>,
    /// IT3 ④:Observe 当前"查看"的 session flat 索引(None=未选,显提示)。
    pub observe_view_cursor: Option<usize>,
    /// ADR-3 props 弹窗激活(open_props 置 true,esc/enter 关)。替代原常驻「属性」tab。
    pub props_open: bool,
    // ── IT2 节点 C:new/delete 弹窗态(session 管理)──────────────────────
    /// new 弹窗激活态。None=关;Some(Claw/Cc)=开并已选 harness 类型。
    pub new_popup: Option<NewKind>,
    /// 右键上下文菜单态(栈顶 Popup id="ctx" 时激活)。
    pub context_menu: Option<ContextMenu>,
    /// new 弹窗 picker 候选(agents 或 cwds,按 new_popup 渲染)。
    pub new_candidates: Vec<String>,
    /// new 弹窗 picker 选中索引。
    pub new_idx: usize,
    /// ADR-3:AoV2 picker 候选(GET /h/agent-os-v2/agents)。与 new_candidates
    /// 并列——claw/cc 用 Vec<String>,ao2 需 name+default 展示,故独立字段。
    /// new_idx 复用做选中位(与 claw/cc 共享)。
    pub new_ao2_agents: Vec<Ao2Agent>,
    /// new 弹窗 cc 自由输入 cwd(insert 模式键入)。
    pub new_cc_input: String,
    /// delete 确认弹窗激活态:Some(sid)=开,等待 y/N。
    pub delete_popup: Option<String>,
    /// IT7 ②:new 弹窗可点击区 clickmap(独立于 base clickmap,模态激活时 hit-test)。
    /// id: 700=claw btn 701=cc btn 710+i=claw agent 行 720+i=cc cwd 行 790=Create 791=Cancel。
    /// render 时按 popup area 注册,handle_popup_mouse hit。
    pub popup_clickmap: ClickMap<usize>,
}

/// IT2 节点 C:new 弹窗 harness 类型选择。
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum NewKind { Claw, Cc, AoV2 }

/// 右键分区上下文菜单的可执行操作。
#[derive(Clone, Debug)]
pub enum CtxAction {
    Turn, Spawn, Reconnect, Fork, Archive, Delete, RawExec, NewSession, Refresh,
    CopySid, CopyLastResponse, ScrollBottom, ClearInput, Newline, CopySelection,
    CreateChain, CreateBranch, CreateDag, RunFlow, JumpToControl(usize), Close,
}

/// 右键命中的分区目标(决定弹哪些菜单项)。
#[derive(Clone, Debug)]
pub enum RightClickTarget {
    OutlineSession(usize),
    OutlineBlank,
    Chat,
    Flow,
    Input,
    ObserveSession(usize),
    Other,
}

/// 激活的上下文菜单态(类比 new_popup;栈顶 Popup id="ctx" 时激活)。
#[derive(Clone, Debug)]
pub struct ContextMenu {
    pub anchor: (u16, u16),
    pub items: Vec<(String, CtxAction)>,
    pub selected: usize,
}

impl App {
    pub fn new(term: TermCap) -> Self {
        let (fetch_tx, fetch_rx) = std::sync::mpsc::channel();
        Self {
            panel: Panel::Control,
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
            fork_tree: ForkTree::default(),
            orch_cursor: 0,
            orch_selection: None,
            primitives: crate::primitives::all(),
            running_ticks: HashMap::new(),
            tabbar: {
                let mut t = TabBar::new(vec![
                    "Flows".to_string(),
                    "Observe".to_string(),
                    "Control".to_string(),
                    "Orchestrate".to_string(),
                ])
                .colors(vec![
                    crate::theme::DARK.accent2,
                    crate::theme::DARK.accent,
                    crate::theme::DARK.done,
                    crate::theme::DARK.accent2,
                ]);
                t.active = 2; // 默认主 tab Control
                t
            },
            mouse: MouseCursor::default(),
            tab_area: Rect::default(),
            observe_split: HSplit::new(35),
            observe_scroll: ScrollView::new(vec![]),
            observe_area: Rect::default(),
            observe_dragging: false,
            control_split: HSplit::new(22),
            control_chat_scroll: ScrollView::new(vec![])
                .border_mode(crate::components::scrollbar::BorderMode::Top)
                .title(" 对话 ")
                .show_scrollbar(false),
            chat_follow_tail: true,
            control_turn_count: 0,
            cached_turn_lines: None,
            control_area: Rect::default(),
            input_area: Rect::default(),
            control_h_dragging: false,
            control_right_tabs: TabBar::new(vec![
                // ADR-3:属性改 props 弹窗(i 键),右 tab 缩 2(对话/flow)。
                "对话".to_string(),
                "flow".to_string(),
            ]),
            right_tab_area: Rect::default(),
            control_groups: vec![],
            clickmap: ClickMap::new(),
            ws: None,
            focus: FocusTarget::TabBar,
            orche_online: true, // 默认假设在线,首次预检刷新
            last_action: None,
            pending_spawn: None,
            pending_turn: None,
            pending_since: None,
            spinner_frame: 0,
            streaming_text: std::collections::HashMap::new(),
            ws_errors: std::collections::HashMap::new(),
            fetch_tx, fetch_rx,
            insert_mode: true,
            textarea: crate::components::textarea::Textarea::new(),
            textarea_state: crate::components::textarea::TextareaState::default(),
            paste_burst: crate::components::paste_burst::PasteBurst::default(),
            input_history: vec![],
            history_cursor: None,
            mentions_open: false,
            quit_requested: false,
            control_collapsed: std::collections::HashSet::new(),
            observe_collapsed: std::collections::HashSet::new(),
            observe_view_cursor: None,
            props_open: false,
            new_popup: None,
            context_menu: None,
            new_candidates: vec![],
            new_idx: 0,
            new_ao2_agents: vec![],
            new_cc_input: String::new(),
            delete_popup: None,
            popup_clickmap: ClickMap::new(),
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
    /// IT2 节点 C:new/delete/fork/archive 成功后刷新 session 列表。
    pub fn refresh_sessions(&mut self) {
        if let Some(sg) = fetch_sessions() {
            self.set_sessions(sg);
        }
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
    /// 统一所有 cursor 变更入口(idx 越界或 == 当前 cursor → 不动,返回 false)。
    /// cursor 真正变化时重置对话视图状态,治三个跨 session 串线 bug:
    /// - #2 chat_follow_tail 串(A 不追尾状态带进 B,新 turn 不滚到底)
    /// - #3 textarea/turn_msg 草稿串(A 半截输入发到 B)
    /// - #6 control_chat_scroll.offset clamp 改写(A 滚位置切 B 再切回归零)
    /// ponytail: 一个函数守卫所有 caller(cursor_down/up/focus_new/click/jump_to/ctx),而非每处 patch。
    fn set_cursor_session(&mut self, idx: usize) -> bool {
        if idx >= self.flat.len() || idx == self.cursor {
            return false;
        }
        self.cursor = idx;
        self.fetch_current();
        self.chat_follow_tail = true;
        self.control_chat_scroll.scroll_to_bottom();
        self.cached_turn_lines = None;
        self.textarea.clear();
        self.turn_msg.clear();
        true
    }
    pub fn cursor_down(&mut self) {
        if self.cursor + 1 < self.flat.len() {
            self.set_cursor_session(self.cursor + 1);
        }
    }
    pub fn cursor_up(&mut self) {
        if self.cursor > 0 {
            self.set_cursor_session(self.cursor - 1);
        }
    }

    /// 清 pending spinner + 时刻(#8 配套:所有 pending_turn=None 点走此,防漏清 pending_since)。
    fn clear_pending(&mut self) {
        self.pending_turn = None;
        self.pending_since = None;
    }
    pub fn fetch_claw_events(&mut self) {
        if let Some(evs) = fetch_events("openclaw", CLAW_SESSION) {
            self.events
                .insert("openclaw/agent:main:main".to_string(), evs);
        }
    }
    pub fn do_turn(&mut self, async_run: bool) {
        // optimistic 立即回显(spinner + 用户输入)+ 异步发送(不阻塞 UI)。
        // WS 推 tick_started(含 user msg request)→ drain_ws push + 清 pending(切换正常显示);
        // subscribe connect 时序可能丢 WS tick_started → 后台 fetch 全量兜补(has_tick 才 replace)。
        let (raw_ht, sid) = self.flat.get(self.cursor)
            .map(|s| (s.harness_type.clone(), s.session_id.clone()))
            .unwrap_or_else(|| ("openclaw".to_string(), CLAW_SESSION.to_string()));
        let norm_ht_v = norm_ht(&raw_ht);
        let msg = self.turn_msg.clone();
        self.turn_msg.clear();
        self.textarea.clear(); // 清输入区(Enter 发送后),否则残留致下次 turn msg 累加
        if !msg.trim().is_empty() {
            let key = format!("{}/{}", raw_ht, sid);
            self.pending_turn = Some((key.clone(), msg.clone()));
            self.pending_since = Some(std::time::Instant::now()); // #8 记时刻,Tick 超 60s 兜底清
            let tx = self.fetch_tx.clone();
            std::thread::spawn(move || {
                let _ = trigger_turn(&norm_ht_v, &sid, &msg, async_run);
                // 等 observe ingest tick_started(含 user msg;LLM 首 token 前 gateway 推 stream start,
                // emitter 合成 tick_started → observe,~几百 ms)。subscribe 时序丢 WS tick_started 时,
                // fetch 全量补(observe 有)。sleep 确保拉到 tick_started。
                std::thread::sleep(std::time::Duration::from_millis(600));
                let evs = fetch_events(&raw_ht, &sid);
                let _ = tx.send((key, evs));
            });
        }
        self.chat_follow_tail = true;
        self.control_chat_scroll.scroll_to_bottom();
    }

    /// IT4:PgUp/PgDn 翻页步长——近似 chat body 可视高度(term 高 - status/tabs/input 外层 ≈ 一半)。
    /// ponytail: 粗估够用,精确需 body_area 缓存(无独立字段);step 偏大/小只影响翻页手感。
    fn page_viewport(&self) -> usize {
        (self.size.1 as usize / 2).max(1)
    }
    /// IT4:chat offset 是否在接近底部(tail 跟随判定:PgDn 滚到近底→重新跟尾)。
    fn near_bottom(&self) -> bool {
        // 折行后行数(wrap_cache 已建):未 wrap lines.len() 在长行折行后低估,致 PgDn 滚到
        // 近底判定不准(不重新 follow_tail → 新消息不滚底)。
        let total = self.control_chat_scroll.display_total();
        total <= 1 || self.control_chat_scroll.offset + self.page_viewport() >= total.saturating_sub(1)
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

    /// `r` 键:claw → 重连(POST reconnect),cc → 无状态提示,无 cursor → 普通刷新兜底。
    /// 重连后刷新 sessions(更新 running 显示)。
    pub fn do_reconnect_or_refresh(&mut self) {
        let Some(s) = self.flat.get(self.cursor).cloned() else {
            self.refresh_sessions();
            self.fetch_claw_events();
            return;
        };
        let ht = norm_ht(&s.harness_type);
        if ht == "claw" {
            match reconnect_claw(&s.session_id) {
                Some(true) => self.turn_status = Some(format!("claw 已重连:{}", trunc(&s.session_id, 12))),
                Some(false) => self.turn_status = Some("重连失败,gateway 离线?".to_string()),
                None => self.turn_status = Some("重连失败,gateway 离线?".to_string()),
            }
            self.refresh_sessions();
        } else {
            // cc(claude-code)无状态(子进程按 turn spawn),重连无意义。
            self.turn_status = Some("cc 无状态无需重连".to_string());
        }
    }

    /// 请求 raw-exec:按 cursor session 的 harness_type 决定拉起哪个 harness
    /// (claude-code → `claude --resume <sid>`;其余 → `openclaw`)。
    /// 不直接 spawn(无 terminal 句柄做挂起/恢复);设置 pending_spawn,run() loop 消费。
    pub fn request_raw_exec(&mut self) {
        let (harness, sid) = match self.flat.get(self.cursor) {
            Some(s) => (
                crate::components::raw_exec::harness_for(&s.harness_type),
                Some(s.session_id.clone()),
            ),
            None => (crate::components::raw_exec::Harness::ClaudeCode, None),
        };
        self.pending_spawn = Some((harness, sid));
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

    // ── ADR-O1/O2 Orchestrate tab · fork 谱系树 ──────────────────

    /// 拉 observe fork sessions + 建树 + 刷新选中投影。进 tab(4 键)/refresh 调。
    pub fn fetch_orch_tree(&mut self) {
        self.fork_tree = build_fork_tree(fetch_orch_sessions());
        self.orch_cursor = self.orch_cursor.min(self.orch_node_count().saturating_sub(1));
        self.sync_orch_selection();
    }

    /// DFS 序节点数(光标 cap)。
    pub fn orch_node_count(&self) -> usize {
        self.fork_tree.flat_order().len()
    }

    /// 光标投影到 orch_selection(render footer / 原语入参)。
    pub fn sync_orch_selection(&mut self) {
        let order = self.fork_tree.flat_order();
        self.orch_selection = order.get(self.orch_cursor)
            .and_then(|sid| self.fork_tree.nodes.get(sid))
            .map(Selection::from_node);
    }

    // ── ADR-O4:原语 registry 派生(footer hint + key dispatch)──────────

    /// Orchestrate tab footer hint:从 registry 派生。enabled 原语高亮,disabled 灰显。
    /// render 底栏 panel==Orchestrate 时调此(替代全局硬编码 hint)。
    pub fn orch_primitive_hint(&self) -> String {
        let sel = self.orch_selection.as_ref();
        let mut parts: Vec<String> = Vec::new();
        for p in &self.primitives {
            let on = sel.map(|s| p.enabled(s)).unwrap_or(false);
            // 灰显用 (·) 包裹 label,enabled 显 key+label。
            let seg = if on {
                format!("{}={}", p.key(), p.label())
            } else {
                format!("({})", p.label())
            };
            parts.push(seg);
        }
        format!(" {} ", parts.join(" · "))
    }

    /// Orchestrate tab key dispatch:按 key 查 registry,enabled 则 invoke。
    /// 返回 true = 已消费(handle_base_key 不再走后续分支)。
    /// disabled / 无匹配 → false(交回 handle_base_key 处理导航键 j/k/1-4/q 等)。
    /// 关键:即使 disabled 也消费(灰显按键无副作用,不回退到别 panel 的全局动作如 f=create flow)。
    pub fn dispatch_orchestrate_primitive(&mut self, key: char) -> bool {
        let sel = match self.orch_selection.clone() {
            Some(s) => s,
            None => return false, // 无选中:不消费,交回导航
        };
        let idx = self.primitives.iter().position(|p| p.key() == key);
        let Some(idx) = idx else { return false }; // 无原语绑此 key:不消费
        if !self.primitives[idx].enabled(&sel) {
            return true; // 灰显:消费但 no-op(不回退到全局 f=create flow 等)
        }
        // 借用冲突:primitives[idx].invoke 需 &mut self,但 primitives 在 self 内。
        // 取出 Box → invoke → 放回(原语无状态,取出/放回语义等价)。安全且惯用。
        let prim = std::mem::replace(&mut self.primitives[idx], Box::new(crate::primitives::PlaceholderPrimitive::new("", '\0', "")));
        prim.invoke(&sel, self);
        self.primitives[idx] = prim; // 放回(恢复原 instance)
        true
    }

    /// j/Down:Orchestrate 光标下移(DFS 序跨层级)。
    pub fn orch_cursor_down(&mut self) {
        let n = self.orch_node_count();
        if n == 0 { return; }
        if self.orch_cursor + 1 < n {
            self.orch_cursor += 1;
        }
        self.focus = FocusTarget::OrchestrateNode(self.orch_cursor);
        self.sync_orch_selection();
    }

    /// k/Up:Orchestrate 光标上移(DFS 序跨层级)。
    pub fn orch_cursor_up(&mut self) {
        if self.orch_cursor > 0 {
            self.orch_cursor -= 1;
        }
        self.focus = FocusTarget::OrchestrateNode(self.orch_cursor);
        self.sync_orch_selection();
    }

    /// 鼠标/ClickMap 选中:DFS 序 idx → orch_cursor + selection。
    pub fn orch_select_idx(&mut self, idx: usize) {
        if idx < self.orch_node_count() {
            self.orch_cursor = idx;
            self.focus = FocusTarget::OrchestrateNode(idx);
            self.sync_orch_selection();
        }
    }

    /// branch_created / tick_started / tick_completed → 更新 fork 树节点状态。
    /// drain_ws agent-os-v2 事件分支调。session_id 匹配树节点;不在树则刷新整树(新 fork)。
    pub fn apply_orch_tree_event(&mut self, session_id: &str, ev: &ObserveEvent) {
        let status = ev.data.get("status").and_then(|v| v.as_str()).unwrap_or("");
        let new_state = NodeState::from_event(&ev.event_type, status);
        // ADR-O6:维护 session→tick_id,供 cancel 原语查。tick_started 插,tick_completed 清。
        match ev.event_type.as_str() {
            "tick_started" => { self.running_ticks.insert(session_id.to_string(), ev.tick_id.clone()); }
            "tick_completed" => { self.running_ticks.remove(session_id); }
            _ => {}
        }
        let in_tree = self.fork_tree.nodes.contains_key(session_id);
        if in_tree {
            // branch_created 可能引入新子节点:刷新整树(observe 已 update parent)。
            if ev.event_type == "branch_created" {
                self.fetch_orch_tree();
                return;
            }
            if let Some(n) = self.fork_tree.nodes.get_mut(session_id) {
                n.state = new_state;
            }
            self.sync_orch_selection();
        } else {
            // 未知 session(fork 子节点首次出现)→ 刷新整树补节点。
            self.fetch_orch_tree();
        }
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
    /// 关 ctx 菜单(弹窗 + 状态)。
    fn close_context_menu(&mut self) {
        self.close_popup("ctx");
        self.context_menu = None;
    }
    /// ctx 菜单键盘:j/k 选、Enter 执行、Esc 关。
    fn handle_context_menu_key(&mut self, k: &KeyEvent) -> bool {
        match k.code {
            KeyCode::Esc => { self.close_context_menu(); true }
            KeyCode::Char('j') | KeyCode::Down => {
                if let Some(cm) = self.context_menu.as_mut() {
                    if cm.selected + 1 < cm.items.len() { cm.selected += 1; }
                }
                true
            }
            KeyCode::Char('k') | KeyCode::Up => {
                if let Some(cm) = self.context_menu.as_mut() {
                    if cm.selected > 0 { cm.selected -= 1; }
                }
                true
            }
            KeyCode::Enter => {
                let action = self.context_menu.as_ref()
                    .and_then(|c| c.items.get(c.selected).map(|(_, a)| a.clone()));
                if let Some(a) = action { self.run_ctx_action(a); }
                true
            }
            _ => false,
        }
    }
    /// 执行菜单项动作(调现有方法)。除切弹窗类(Delete/NewSession/JumpToControl 内部自关),
    /// 末尾统一 close_context_menu(单击/Enter 后菜单消失)。
    /// delete 弹窗 styled body:[y]确认 Green │ [N]取消 Red,公用描边分隔。
    fn delete_popup_body(sid: &str) -> Vec<ratatui::text::Line<'static>> {
        use ratatui::style::{Color, Style};
        use ratatui::text::{Line, Span};
        vec![
            Line::from(format!("删除 {} ?", trunc(sid, 24))),
            Line::from(vec![
                Span::styled(" [y] 确认 ", Style::default().fg(Color::Black).bg(Color::Green)),
                Span::styled("│", Style::default().fg(Color::Cyan)),
                Span::styled(" [N] 取消 ", Style::default().fg(Color::Black).bg(Color::Red)),
            ]),
        ]
    }
    /// 开 delete 确认弹窗(styled body_lines + 设 delete_popup)。
    fn open_delete_popup(&mut self, sid: &str) {
        self.delete_popup = Some(sid.to_string());
        let mut p = Popup::centered("delete", " delete session ", vec![], 48, 7);
        p.body_lines = Self::delete_popup_body(sid);
        self.open_popup(p);
    }
    /// 确认删 cursor session(键盘 y / 鼠标 [y] 共用)。
    fn confirm_delete(&mut self) {
        let sid = self.delete_popup.take().unwrap_or_default();
        if sid.is_empty() {
            self.close_popup("delete");
            return;
        }
        let ht = self.flat.iter()
            .find(|s| s.session_id == sid)
            .map(|s| norm_ht(&s.harness_type))
            .unwrap_or_else(|| "claw".to_string());
        let ok = delete_session_raw(&ht, &sid);
        self.turn_status = Some(if ok {
            format!("deleted: {}", trunc(&sid, 12))
        } else {
            format!("delete 失败(orche 不可达?): {}", trunc(&sid, 12))
        });
        self.close_popup("delete");
        self.refresh_sessions();
    }
    /// 取消删(键盘 n/esc / 鼠标 [N] 共用)。
    fn cancel_delete(&mut self) {
        self.close_popup("delete");
        self.delete_popup = None;
    }

    fn run_ctx_action(&mut self, a: CtxAction) {
        use CtxAction::*;
        match a {
            Turn => self.do_turn(false),
            Spawn => self.do_spawn(),
            Reconnect => self.do_reconnect_or_refresh(),
            Fork => self.do_fork(),
            Archive => {
                if let Some(s) = self.flat.get(self.cursor).cloned() {
                    let ht = norm_ht(&s.harness_type);
                    match archive_session(&ht, &s.session_id, None) {
                        Some(msg) => self.turn_status = Some(format!("archived: {}", trunc(msg.trim(), 40))),
                        None => self.turn_status = Some("archive 失败".into()),
                    }
                }
            }
            Delete => {
                let sid = self.current_sid();
                if !sid.starts_with("(无") {
                    self.close_context_menu();
                    self.open_delete_popup(&sid);
                    return; // 切 delete 弹窗,不统一 close
                }
            }
            RawExec => self.request_raw_exec(),
            NewSession => { self.close_context_menu(); self.open_new_popup(); return; }
            Refresh => { self.refresh_sessions(); self.refresh_flows(); }
            CopySid => {
                if let Some(s) = self.flat.get(self.cursor) {
                    copy_to_clipboard(&s.session_id);
                }
            }
            CopyLastResponse => {
                let key = self.flat.get(self.cursor)
                    .map(|s| format!("{}/{}", s.harness_type, s.session_id));
                if let Some(k) = key {
                    if let Some(evs) = self.events.get(&k) {
                        if let Some(last) = evs.last() {
                            let txt = ["message", "text", "content", "response", "output"].iter()
                                .find_map(|f| last.data.get(*f).and_then(|v| v.as_str()))
                                .unwrap_or("").to_string();
                            if !txt.is_empty() { copy_to_clipboard(&txt); }
                        }
                    }
                }
            }
            ScrollBottom => { self.control_chat_scroll.scroll_to_bottom(); self.chat_follow_tail = true; }
            ClearInput => { self.textarea.clear(); self.turn_msg.clear(); }
            Newline => self.textarea.insert_newline(),
            CreateChain => { self.create_preset_flow(FlowPreset::Chain); }
            CreateBranch => { self.create_preset_flow(FlowPreset::Branch); }
            CreateDag => { self.create_preset_flow(FlowPreset::Dag); }
            RunFlow => self.run_current_flow(),
            CopySelection => {
                if let Some((a, b)) = self.textarea.selection_range() {
                    if a < b {
                        copy_to_clipboard(&self.textarea.text()[a..b]);
                    }
                }
            }
            JumpToControl(idx) => { self.close_context_menu(); self.jump_to_control(idx); return; }
            Close => {}
        }
        self.close_context_menu();
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
        // cursor session 属性(props 并入 help;open_props/props_open 闲置保留)
        if let Some(s) = self.flat.get(self.cursor) {
            let inst = self.instance_count(&s.harness_type, &s.session_id);
            let ev_key = format!("{}/{}", s.harness_type, &s.session_id);
            let ev_n = self.events.get(&ev_key).map(|e| e.len()).unwrap_or(0);
            full_md.push_str(&format!(
                "\n## 当前 session\n\n- sid `{}`\n- harness `{}`\n- 实例 {}\n- 事件 {}\n",
                trunc(&s.session_id, 30), s.harness_type, inst, ev_n,
            ));
        }
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
                // drain 后台 fetch 全量回传(do_turn spawn):全量替换 events(含 user msg + 历史)
                // + 清 pending(去 spinner,user msg 由全量无缝接管)。fetch 失败(evs=None)仍清 pending。
                while let Ok((key, evs)) = self.fetch_rx.try_recv() {
                    // has_tick = fetch 含 tick_started request == pending msg(本次 user msg 确认)。
                    // 历史非空 tick_started request ≠ 本次 msg,不算 — 避免 fetch 全量含历史时误清
                    // pending 致 spinner 提前停(本次 tick_started 还没 ingest,LLM 首 token 慢)。
                    // fetch key 必须匹配 pending session_key(本次 do_turn session)+ request == pending msg。
                    let pending = self.pending_turn.clone();
                    let has_tick = evs.as_ref().map_or(false, |e| e.iter().any(|ev| {
                        if ev.event_type != "tick_started" { return false; }
                        let req = ev.data.get("request").and_then(|v| v.as_str()).unwrap_or("");
                        pending.as_ref().map_or(false, |(pk, pm)| pk == &key && !req.is_empty() && pm.starts_with(req))
                    }));
                    match evs {
                        Some(evs) if has_tick => {
                            // 只在 has_tick 时 replace:observe 是 WS 源(broadcast=ingest 后),全量 ⊇ WS 已推,
                            // 故含 tick_started 的 fetch 全量可信、不丢 WS 增量。治 replace 覆盖 bug。
                            self.events.insert(key, evs);
                            self.clear_pending();
                        }
                        Some(_) => { /* has_tick=false:observe 未 ingest tick_started,不动,等 WS drain_ws 清 pending */ }
                        None => { self.clear_pending(); /* fetch 失败(evs=None):对齐注释,#7 */ }
                    }
                }
                // #8 超时兜底:trigger 失败/WS 丢 tick_started → pending 永驻 spinner 永转。
                // pending age >= 60s 视为 stale(正常 native turn <10s;60s 保守不误清慢 turn)→ 清。
                if let Some(since) = self.pending_since {
                    if since.elapsed().as_secs() >= 60 {
                        self.clear_pending();
                    }
                }
                // spinner 跑马灯帧推进(pending 时 main.rs poll 缩 80ms → ~12fps 流畅)。
                self.spinner_frame = self.spinner_frame.wrapping_add(1);
                // PasteBurst flush:超时 burst 一次性插入(tmux 无 bracketed 时粘贴靠此时序 flush)
                if self.panel == Panel::Control && self.insert_mode {
                    use crate::components::paste_burst::FlushResult;
                    let now = std::time::Instant::now();
                    if let FlushResult::Paste(s) = self.paste_burst.flush_if_due(now) {
                        self.textarea.insert_text(&s);
                        self.turn_msg = self.textarea.text().to_string();
                    }
                }
            }
            AppEvent::Key(k) => {
                // IT5 ③:启动时 observe 不可达 → flat 空。首次按键懒重试(非 Tick REST 轮询,
                // 不违 ADR-1 T4;用户驱动,observe 起来后一次自愈,无需重启 TUI)。
                // ponytail: ureq 无超时;flat 有数据后此分支每键恒 false,O(1) 跳过。
                if self.flat.is_empty() {
                    if let Some(sg) = fetch_sessions() {
                        if !sg.sessions_by_harness.is_empty() {
                            self.set_sessions(sg);
                        }
                    }
                }
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
            AppEvent::Paste(s) => {
                // IT7 ④:bracketed paste 路由。
                // new 弹窗 cc 模式开 → 粘进 new_cc_input;否则 Control insert 模式 → textarea。
                if self.popups.last().map(|p| p.id == "new").unwrap_or(false)
                    && matches!(self.new_popup, Some(NewKind::Cc))
                {
                    self.new_cc_input.push_str(&s);
                } else if self.panel == Panel::Control && self.insert_mode {
                    self.textarea.insert_text(&s);
                    self.turn_msg = self.textarea.text().to_string();
                    // bracketed paste 已整段给 textarea,清 burst 状态(避免与 burst 冲突)
                    self.paste_burst.clear_after_explicit_paste();
                }
            }
        }
        // ADR-3:×(顶栏右,id999)→ quit_requested,run loop 退出。
        self.quit_requested
    }

    /// 弹窗栈顶消费 key。返回 true = 已消费(关闭/聚焦切换)。
    fn handle_popup_key(&mut self, k: &KeyEvent) -> bool {
        // IT2 节点 C:new/delete 操作弹窗先于通用 Esc/Enter/Tab 处理。
        if self.handle_action_popup_key(k) {
            return true;
        }
        // ctx 菜单(栈顶 id="ctx"):j/k 导航、Enter 执行、Esc 关。
        if self.popups.last().map(|p| p.id == "ctx").unwrap_or(false) {
            return self.handle_context_menu_key(k);
        }
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

    /// IT2 节点 C:new/delete 弹窗自定义键路由。
    /// new 弹窗:c/d 选类型、j/k 选候选、enter 确认、esc 关。
    /// delete 弹窗:y 确认删、n/esc 取消。
    fn handle_action_popup_key(&mut self, k: &KeyEvent) -> bool {
        // new 弹窗(栈顶 id="new")
        if self.popups.last().map(|p| p.id == "new").unwrap_or(false) {
            return self.handle_new_popup_key(k);
        }
        // delete 确认弹窗(栈顶 id="delete")
        if self.popups.last().map(|p| p.id == "delete").unwrap_or(false) {
            if self.delete_popup.is_some() {
                match k.code {
                    KeyCode::Esc | KeyCode::Char('n') | KeyCode::Char('N') => self.cancel_delete(),
                    KeyCode::Char('y') | KeyCode::Char('Y') => self.confirm_delete(),
                    _ => {}
                }
                return true;
            }
        }
        false
    }

    /// new 弹窗键路由:c=claw / d=cc / j/k 选 / enter 确认 / esc 关。
    fn handle_new_popup_key(&mut self, k: &KeyEvent) -> bool {
        match k.code {
            KeyCode::Esc => {
                self.close_popup("new");
                self.new_popup = None;
                true
            }
            KeyCode::Char('c') => {
                // 选 claw:fetch agents 填候选。
                self.new_popup = Some(NewKind::Claw);
                self.new_candidates = fetch_claw_agents();
                self.new_idx = 0;
                true
            }
            KeyCode::Char('d') => {
                // 选 cc:fetch cwds 填候选 + 清自由输入。
                self.new_popup = Some(NewKind::Cc);
                self.new_candidates = fetch_cc_cwds();
                self.new_idx = 0;
                self.new_cc_input.clear();
                true
            }
            KeyCode::Char('o') => {
                // ADR-3:选 agent-os-v2 → 拉 registry agents,预选 default 项。
                // 不可达时 new_ao2_agents 空,do_new_session 显提示(不崩)。
                self.new_popup = Some(NewKind::AoV2);
                self.new_ao2_agents = fetch_ao2_agents();
                self.new_idx = self.new_ao2_agents.iter().position(|a| a.default)
                    .unwrap_or(0);
                true
            }
            KeyCode::Char('j') | KeyCode::Down => {
                // ADR-3:AoV2 候选在 new_ao2_agents(非 new_candidates),故按 picker
                // 类型取对应 len,否则 AoV2 永远 len=0 → j/Down 失效(只鼠标能选)。
                let len = match self.new_popup {
                    Some(NewKind::AoV2) => self.new_ao2_agents.len(),
                    _ => self.new_candidates.len(),
                };
                if self.new_popup.is_some() && self.new_idx + 1 < len {
                    self.new_idx += 1;
                }
                true
            }
            KeyCode::Char('k') | KeyCode::Up => {
                if self.new_idx > 0 {
                    self.new_idx -= 1;
                }
                true
            }
            KeyCode::Enter => {
                self.do_new_session();
                true
            }
            _ => {
                // cc 自由输入态:可键入 cwd(字母/数字/路径符)。
                if matches!(self.new_popup, Some(NewKind::Cc)) {
                    match k.code {
                        KeyCode::Backspace => { self.new_cc_input.pop(); return true; }
                        KeyCode::Char(ch) => { self.new_cc_input.push(ch); return true; }
                        _ => {}
                    }
                }
                false
            }
        }
    }

    /// IT2 节点 C:new 弹窗 enter → 创建 session(按 new_popup 类型)。
    fn do_new_session(&mut self) {
        match self.new_popup {
            Some(NewKind::Claw) => {
                let agent = self.new_candidates.get(self.new_idx).cloned()
                    .unwrap_or_else(|| "main".to_string());
                // claw "new session" = 为 agent 开新对话(唯一 conv)。裸 agent 名 →
                // orche 固定 agent:<a>:main(routes.py),同 agent 已存在 → exists 短路不
                // 新建(create_session 不查 status → focus 到旧 session)。故生成唯一 conv
                // key(见 claw_session_key),orche 见 ":" 用之 → 必新建。
                let key = claw_session_key(&agent);
                if let Some(sid) = create_session("claw", Some(&key)) {
                    self.turn_status = Some(format!("created claw session: {}", trunc(&sid, 16)));
                    self.close_popup("new");
                    self.new_popup = None;
                    self.refresh_sessions();
                    self.focus_new_session(&sid);
                } else {
                    self.turn_status = Some("create claw session 失败(orche :8001 不可达?)".to_string());
                }
            }
            Some(NewKind::Cc) => {
                // 自由输入非空优先,否则用选中的 cwd 候选。
                let cwd = if !self.new_cc_input.is_empty() {
                    self.new_cc_input.clone()
                } else {
                    self.new_candidates.get(self.new_idx).cloned().unwrap_or_default()
                };
                if cwd.is_empty() {
                    self.turn_status = Some("(cwd 为空,键入路径或从候选选)".to_string());
                    return;
                }
                if let Some(sid) = create_cc_session_cwd(&cwd) {
                    self.turn_status = Some(format!("created cc session: {}", trunc(&sid, 16)));
                    self.close_popup("new");
                    self.new_popup = None;
                    self.refresh_sessions();
                    self.focus_new_session(&sid);
                } else {
                    self.turn_status = Some("create cc session 失败(orche :8001 不可达?)".to_string());
                }
            }
            Some(NewKind::AoV2) => {
                // ADR-3:传选中的 agent_id(替换原硬编 None)。服务端 _build_native_session
                // 用 registry.get(agent_id) 取 spec(per-agent profile/skills/cwd)。
                // 候选空(或che 不可达)→ 显提示,不盲目 POST(避免服务端 default 兜底创建
                // 出用户没选的 agent)。
                let agent_id = self.new_ao2_agents.get(self.new_idx).map(|a| a.id.clone());
                if agent_id.is_none() {
                    self.turn_status = Some(
                        "(无 agent 候选——orche :8001 不可达?按 o 重试)".to_string());
                    return;
                }
                if let Some(sid) = create_session("agent-os-v2", agent_id.as_deref()) {
                    self.turn_status = Some(format!("created ao session: {}", trunc(&sid, 16)));
                    self.close_popup("new");
                    self.new_popup = None;
                    self.refresh_sessions();
                    self.focus_new_session(&sid);
                } else {
                    self.turn_status = Some("create ao session 失败(orche :8001 不可达?)".to_string());
                }
            }
            None => {
                self.turn_status = Some("(先选类型:c=claw / d=claude-code / o=agent-os-v2)".to_string());
            }
        }
    }

    /// new/fork 成功后把 cursor 切到新 session(刷新后按 sid 找 flat 索引)。
    pub fn focus_new_session(&mut self, sid: &str) {
        if let Some(idx) = self.flat.iter().position(|s| s.session_id == sid) {
            self.set_cursor_session(idx);
            self.focus = FocusTarget::ControlSession(idx);
            // 主动订阅新 session WS(不等 main loop 检测 sub_key 变;idempotent,确保即时收事件)。
            if let Some(mgr) = self.ws.as_ref() {
                if let Some(s) = self.flat.get(idx) {
                    mgr.subscribe(&s.harness_type, &s.session_id);
                }
            }
        }
    }

    /// 弹窗栈顶消费 mouse:tui-popup PopupState.handle_mouse_event(拖拽)。
    /// IT7 ②:new 弹窗左键点击优先 popup_clickmap hit-test(700/701/710+i/720+i/790/791)。
    fn handle_popup_mouse(&mut self, m: &MouseEvent) {
        // modal 期间也跟光标(否则菜单激活时 mouse.track 不调 → MouseCursor 渲染冻结)。
        self.mouse.track(*m);
        // 菜单激活时再右键:关旧菜单 + 新坐标重开(常见 GUI 行为;否则 modal 拦截 Right Down
        // 走 popup 路径不处理 → 右键别处无反应,菜单钉死原位)。
        if m.kind == MouseEventKind::Down(MouseButton::Right) {
            let target = self.classify_right_click(m.column, m.row);
            self.close_context_menu();
            self.open_context_menu((m.column, m.row), target);
            return;
        }
        if m.kind == MouseEventKind::Down(MouseButton::Left)
            && self.popups.last().map(|p| p.id == "new").unwrap_or(false)
        {
            if let Some(id) = self.popup_clickmap.hit(m.column, m.row).copied() {
                self.handle_new_popup_click(id);
                return;
            }
        }
        if m.kind == MouseEventKind::Down(MouseButton::Left)
            && self.popups.last().map(|p| p.id == "ctx").unwrap_or(false)
        {
            if let Some(id) = self.popup_clickmap.hit(m.column, m.row).copied() {
                if id == 800 {
                    self.close_context_menu();
                } else if id >= 900 {
                    let i = id - 900;
                    let action = self.context_menu.as_ref()
                        .and_then(|c| c.items.get(i).map(|(_, a)| a.clone()));
                    if let Some(a) = action {
                        self.run_ctx_action(a);
                    }
                }
                return;
            }
        }
        // delete 弹窗:600=[y]确认 → confirm_delete / 601=[N]取消 → cancel_delete
        if m.kind == MouseEventKind::Down(MouseButton::Left)
            && self.popups.last().map(|p| p.id == "delete").unwrap_or(false)
        {
            if let Some(id) = self.popup_clickmap.hit(m.column, m.row).copied() {
                match id {
                    600 => self.confirm_delete(),
                    601 => self.cancel_delete(),
                    _ => {}
                }
                return;
            }
        }
        if let Some(p) = self.popups.last_mut() {
            p.state.handle_mouse_event(*m);
        }
    }

    /// IT7 ②:new 弹窗可点击区命中 → 对应操作。
    /// 700=选 claw 701=选 cc 710+i=选 claw agent 720+i=选 cc cwd 790=Create 791=Cancel。
    fn handle_new_popup_click(&mut self, id: usize) {
        match id {
            700 => {
                self.new_popup = Some(NewKind::Claw);
                self.new_candidates = fetch_claw_agents();
                self.new_idx = 0;
            }
            701 => {
                self.new_popup = Some(NewKind::Cc);
                self.new_candidates = fetch_cc_cwds();
                self.new_idx = 0;
                self.new_cc_input.clear();
            }
            702 => {
                // ADR-3:点 ao tab → 拉 registry agents,预选 default 项。
                self.new_popup = Some(NewKind::AoV2);
                self.new_ao2_agents = fetch_ao2_agents();
                self.new_idx = self.new_ao2_agents.iter().position(|a| a.default)
                    .unwrap_or(0);
            }
            790 => self.do_new_session(),
            791 => {
                self.close_popup("new");
                self.new_popup = None;
            }
            n if (710..720).contains(&n) => {
                let i = n - 710;
                if i < self.new_candidates.len() {
                    self.new_idx = i;
                }
            }
            n if (720..730).contains(&n) => {
                let i = n - 720;
                if i < self.new_candidates.len() {
                    self.new_idx = i;
                }
            }
            n if (730..740).contains(&n) => {
                // ADR-3:AoV2 picker 候选行点击(与 claw/cc 同模式:new_idx 选中)。
                let i = n - 730;
                if i < self.new_ao2_agents.len() {
                    self.new_idx = i;
                }
            }
            _ => {}
        }
    }

    /// base panel 鼠标:右键弹 context menu / 左键 tab 切 panel / 滚轮列表。
    /// ADR-2:Observe tab 加分隔条拖拽(HSplit.drag)+ turn stream 滚轮(ScrollView)。
    /// 触发 Control 按钮 id(0-7)动作(鼠标点击 + 键盘 Enter 共用,F1 修复)。
    fn trigger_control_button(&mut self, id: usize) {
        match id {
            0 => { self.do_turn(false); self.mark_action("trigger"); }
            1 => { self.do_spawn(); self.mark_action("spawn"); }
            2 => {
                // 对齐 r 键:claw→重连+刷新,cc→无状态提示,无 cursor→刷新兜底
                self.do_reconnect_or_refresh();
                self.orche_online = fetch_orche_health();
                self.mark_action("reconnect");
            }
            3 => self.request_raw_exec(),
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
                let target = self.classify_right_click(m.column, m.row);
                self.open_context_menu((m.column, m.row), target);
            }
            MouseEventKind::ScrollDown => {
                if self.panel == Panel::Observe || self.panel == Panel::Control {
                    if self.panel == Panel::Observe {
                        self.observe_scroll.scroll_down(1);
                    } else {
                        self.control_chat_scroll.scroll_down(1);
                        self.chat_follow_tail = true;
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
                        self.chat_follow_tail = false;
                    }
                } else {
                    self.cursor_up();
                }
            }
            MouseEventKind::Down(MouseButton::Left) => {
                // 输入栏 textarea 划选起点(底部输入栏,优先于 HSplit/clickmap)
                if self.panel == Panel::Control
                    && self.input_area.contains(ratatui::layout::Position { x: m.column, y: m.row })
                {
                    if let Some(p) = self.textarea.pos_at_point(
                        (m.column, m.row), self.input_area, &self.textarea_state,
                    ) {
                        self.textarea.set_cursor(p);
                        self.textarea.start_selection(p);
                        self.turn_msg = self.textarea.text().to_string();
                    }
                    return;
                }
                // Control 左|右 HSplit 分隔条命中(仅水平 bar;垂直堆叠已 tab 化,无 VStack 分隔条)。
                if self.panel == Panel::Control && self.control_area.contains(ratatui::layout::Position { x: m.column, y: m.row }) {
                    let [_left, hbar, _right] = self.control_split.rects(self.control_area);
                    if hbar.contains(ratatui::layout::Position { x: m.column, y: m.row }) {
                        self.control_h_dragging = true;
                        return;
                    }
                }
                // ADR-3(IT3 ①):全局 ×(id999)/i(id998)按钮前置——任何 panel 任何位置点 × 都 quit。
                // 原先 tabbar.hit 先跑,× 落在 tab_area 右端时被 tabbar 命中短路,quit 永不触发。
                // 现提到 tabbar.hit 之前:命中 998/999 直接 return,不走后续 tab/clickmap。
                if let Some(id) = self.clickmap.hit(m.column, m.row) {
                    if *id == 999 {
                        self.quit_requested = true;
                        return;
                    }
                    if *id == 998 {
                        self.open_help();
                        return;
                    }
                }
                // 顶栏 TabBar 命中切 base panel。
                if let Some(i) = self.tabbar.hit(self.tab_area, m.column, m.row) {
                    self.tabbar.select(i);
                    self.sync_panel_from_tab();
                    return;
                }
                // 右主区 tab(对话/flow/属性)点击。
                if self.panel == Panel::Control {
                    if let Some(i) = self.control_right_tabs.hit(self.right_tab_area, m.column, m.row) {
                        self.control_right_tabs.select(i);
                        return;
                    }
                }
                // ClickMap 命中:id 0-7 按钮、100+ session 项、200+ 组色块、300=new 按钮(仅 Control)。
                if self.panel == Panel::Control {
                    if let Some(id) = self.clickmap.hit(m.column, m.row) {
                        if *id == 300 {
                            // IT2 节点 C:大纲侧 [+] new 按钮。
                            self.open_new_popup();
                            self.mark_action("new_btn");
                            return;
                        }
                        if *id >= 200 {
                            // ADR-4:组色块 id-200 = group_idx → toggle 折叠/展开(替代裸跳转)。
                            if let Some(group) = self.control_groups.get(*id - 200).cloned() {
                                self.toggle_group(&group);
                            }
                        } else if *id >= 100 {
                            // session 项:id-100 = flat index → 切 cursor(set_cursor_session 重置视图状态)。
                            let idx = *id - 100;
                            if idx < self.flat.len() {
                                self.set_cursor_session(idx);
                                self.mark_action("session");
                            }
                        } else {
                            // 按钮 id 0-7。
                            self.trigger_control_button(*id);
                        }
                        return;
                    }
                }
                // IT3 ④:Observe 折叠树交互。
                //   id 400 = 跳转 Control 按钮(observe_view_cursor 指向的 session → jump_to_control)
                //   id 500+ = 组 header(gi = id-500)→ toggle_observe_group(展开/收起)
                //   id 600+ = session 行(flat idx = id-600)→ 设 observe_view_cursor(查看,不跳)
                if self.panel == Panel::Observe {
                    if let Some(id) = self.clickmap.hit(m.column, m.row) {
                        if *id == 400 {
                            // 跳转 Control:用 observe_view_cursor(若有),否则 fallback cursor。
                            let idx = self.observe_view_cursor.unwrap_or(self.cursor);
                            self.jump_to_control(idx);
                            return;
                        }
                        if *id >= 600 {
                            let idx = *id - 600;
                            if idx < self.flat.len() {
                                self.observe_view_cursor = Some(idx);
                            }
                            return;
                        }
                        if *id >= 500 {
                            // 组 header:用 flat 的 harness_type 列表(排序去重)定位组名。
                            let mut groups: Vec<String> =
                                self.flat.iter().map(|s| s.harness_type.clone()).collect();
                            groups.sort();
                            groups.dedup();
                            if let Some(g) = groups.get(*id - 500).cloned() {
                                self.toggle_observe_group(&g);
                            }
                            return;
                        }
                    }
                }
                // ADR-O1 Orchestrate:ClickMap 选中 fork 树节点(id 700+ = DFS idx)。
                if self.panel == Panel::Orchestrate {
                    if let Some(id) = self.clickmap.hit(m.column, m.row) {
                        if *id >= 700 {
                            self.orch_select_idx(*id - 700);
                            return;
                        }
                    }
                }
            }
            MouseEventKind::Drag(MouseButton::Left) => {
                // textarea 划选拖动(selection 已开始)
                if self.panel == Panel::Control && self.textarea.selection_range().is_some() {
                    if let Some(p) = self.textarea.pos_at_point(
                        (m.column, m.row), self.input_area, &self.textarea_state,
                    ) {
                        self.textarea.extend_selection(p);
                    }
                    return;
                }
                // Control 左|右 HSplit bar 拖拽(垂直堆叠已 tab 化,无 VStack 拖拽)。
                if self.control_h_dragging {
                    let [_left, hbar, _right] = self.control_split.rects(self.control_area);
                    let dx: i32 = if m.column > hbar.x { 1 } else if m.column < hbar.x { -1 } else { 0 };
                    if dx != 0 {
                        self.control_split.drag(dx, self.control_area);
                    }
                }
            }
            MouseEventKind::Up(MouseButton::Left) => {
                // 划选释放:保持选区(不自动删;Backspace 删 / Ctrl+C 复制)
                self.control_h_dragging = false;
            }
            _ => {}
        }
    }

    /// 把 tabbar.active 同步到 self.panel(0=Flows,1=Observe,2=Control,3=Orchestrate)。
    pub fn sync_panel_from_tab(&mut self) {
        self.panel = match self.tabbar.active {
            0 => Panel::Flows,
            1 => Panel::Observe,
            2 => Panel::Control,
            _ => Panel::Orchestrate,
        };
    }
    /// 把 self.panel 同步到 tabbar.active(render 前确保一致)。
    pub fn sync_tab_from_panel(&mut self) {
        let idx = match self.panel {
            Panel::Flows => 0,
            Panel::Observe => 1,
            Panel::Control => 2,
            Panel::Orchestrate => 3,
        };
        self.tabbar.select(idx);
    }

    /// ADR-2(第八轮):Observe→Control 跨 tab 跳转(cursor 同步 + WS 重订阅)。
    /// 点 Observe session 或键盘 Enter on ObserveSession focus → panel=Control + cursor=idx。
    /// 切 Control cursor session 后 WS manager 重订阅(observe 实时事件流入 cursor session)。
    /// 业务方法不改:仅组合现有 set panel/cursor/fetch/subscribe(UI 状态操作)。
    pub fn jump_to_control(&mut self, idx: usize) {
        self.set_cursor_session(idx);
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

    /// 色块组标签点击:跳到该 harness 组的首个 session(切 cursor + fetch)。
    pub fn jump_to_group(&mut self, group: &str) {
        if let Some(idx) = self.flat.iter().position(|s| s.harness_type == group) {
            self.set_cursor_session(idx);
        }
    }

    /// ADR-4:折叠/展开某组(toggle)。header 点击或键盘 toggle 调用。
    /// 在 control_collapsed HashSet 里增/删组名;draw_control 读此判展开态。
    pub fn toggle_group(&mut self, group: &str) {
        if !self.control_collapsed.insert(group.to_string()) {
            // insert 返 false = 已存在 → 移除(展开)。
            self.control_collapsed.remove(group);
        }
    }

    /// IT3 ④:Observe 折叠树组 toggle。observe_collapsed HashSet 增删。
    pub fn toggle_observe_group(&mut self, group: &str) {
        if !self.observe_collapsed.insert(group.to_string()) {
            self.observe_collapsed.remove(group);
        }
    }

    /// ADR-3:`i`(顶栏右)开 props 弹窗(替代常驻「属性」tab)。
    /// 置 props_open=true;render 据此画 props modal;esc/enter 关。
    /// IT3 ②:终端能力(protocol/image_ok/poll)从原右下 icat 框并入此弹窗。
    /// fork cursor session(cc-only;claw 501 ADR-4)。textarea 文本作 first_msg。
    /// 供 F 快捷键 + 右键菜单 Fork 复用。
    pub fn do_fork(&mut self) {
        let (ht, sid) = self.flat.get(self.cursor)
            .map(|s| (norm_ht(&s.harness_type), s.session_id.clone()))
            .unwrap_or_else(|| ("claw".to_string(), String::new()));
        if sid.is_empty() {
            return;
        }
        let first_msg = self.textarea.text().to_string();
        match fork_session(&ht, &sid, &first_msg) {
            Some((new_sid, forked)) if forked => {
                self.turn_status = Some(format!("forked → {}", trunc(&new_sid, 16)));
                self.refresh_sessions();
                self.focus_new_session(&new_sid);
            }
            Some((_new_sid, _forked)) => {
                self.turn_status = Some("fork 未生效(forked=false)".to_string());
            }
            None => {
                self.turn_status = Some(if ht == "claw" {
                    "claw fork 暂不支持(ADR-4)".to_string()
                } else {
                    "fork 失败(orche 不可达?)".to_string()
                });
            }
        }
    }

    /// 右键落点 → 分区目标(panel + rect + clickmap id 归一)。
    fn classify_right_click(&self, col: u16, row: u16) -> RightClickTarget {
        use RightClickTarget::*;
        let pos = ratatui::layout::Position { x: col, y: row };
        if self.panel == Panel::Control && self.input_area.contains(pos) {
            return Input;
        }
        if self.panel == Panel::Control && self.control_area.contains(pos) {
            let [left, _, _] = self.control_split.rects(self.control_area);
            if !left.contains(pos) {
                // 右主区:tab0=对话,tab1=flow
                return match self.control_right_tabs.active { 1 => Flow, _ => Chat };
            }
            if let Some(id) = self.clickmap.hit(col, row).copied() {
                if id >= 100 && id < 200 {
                    return OutlineSession(id - 100);
                }
            }
            return OutlineBlank;
        }
        if self.panel == Panel::Observe {
            if let Some(id) = self.clickmap.hit(col, row).copied() {
                if id >= 600 {
                    return ObserveSession(id - 600);
                }
            }
            return Other;
        }
        Other
    }

    /// 按分区目标构建菜单项 + 推 id="ctx" Popup(锚点跟随右键光标)。
    pub fn open_context_menu(&mut self, anchor: (u16, u16), target: RightClickTarget) {
        let items: Vec<(String, CtxAction)> = match target {
            RightClickTarget::OutlineSession(idx) => {
                self.set_cursor_session(idx);
                let ht = self.flat.get(self.cursor)
                    .map(|s| norm_ht(&s.harness_type)).unwrap_or_else(|| "claw".to_string());
                let mut v = vec![
                    ("Trigger turn".into(), CtxAction::Turn),
                    ("Spawn".into(), CtxAction::Spawn),
                ];
                if ht == "claw" { v.push(("Reconnect".into(), CtxAction::Reconnect)); }
                if ht == "claude-code" { v.push(("Fork".into(), CtxAction::Fork)); }
                v.push(("Archive".into(), CtxAction::Archive));
                v.push(("Delete…".into(), CtxAction::Delete));
                v.push(("Raw exec".into(), CtxAction::RawExec));
                v.push(("Copy sid".into(), CtxAction::CopySid));
                v
            }
            RightClickTarget::OutlineBlank => vec![
                ("New session".into(), CtxAction::NewSession),
                ("Refresh".into(), CtxAction::Refresh),
            ],
            RightClickTarget::Chat => vec![
                ("Send turn".into(), CtxAction::Turn),
                ("Copy last response".into(), CtxAction::CopyLastResponse),
                ("Scroll bottom".into(), CtxAction::ScrollBottom),
            ],
            RightClickTarget::Flow => vec![
                ("Create Chain".into(), CtxAction::CreateChain),
                ("Create Branch".into(), CtxAction::CreateBranch),
                ("Create DAG".into(), CtxAction::CreateDag),
                ("Run flow".into(), CtxAction::RunFlow),
                ("Refresh".into(), CtxAction::Refresh),
            ],
            RightClickTarget::Input => vec![
                ("Send".into(), CtxAction::Turn),
                ("Clear".into(), CtxAction::ClearInput),
                ("Newline".into(), CtxAction::Newline),
                ("Copy selection".into(), CtxAction::CopySelection),
            ],
            RightClickTarget::ObserveSession(idx) => {
                self.set_cursor_session(idx);
                vec![
                    ("Jump to Control".into(), CtxAction::JumpToControl(idx)),
                    ("Copy sid".into(), CtxAction::CopySid),
                ]
            }
            RightClickTarget::Other => return,
        };
        let h = (items.len() as u16 + 5).clamp(7, 22);
        self.context_menu = Some(ContextMenu { anchor, items, selected: 0 });
        self.open_popup(Popup {
            id: "ctx", title: " context ".into(), body: vec![], md_text: None,
            state: tui_popup::PopupState::default(), modal: true,
            width: 28, height: h, position: Some(anchor), placed: false, body_lines: vec![],
        });
    }

    pub fn open_props(&mut self) {
        self.props_open = true;
        // props body:cursor session 属性 + 终端能力段。
        let mut body: Vec<String> = vec![];
        match self.flat.get(self.cursor) {
            Some(s) => {
                let inst = self.instance_count(&s.harness_type, &s.session_id);
                let ev_key = format!("{}/{}", s.harness_type, s.session_id);
                let ev_n = self.events.get(&ev_key).map(|e| e.len()).unwrap_or(0);
                let turn_disp = self.turn_status.clone().unwrap_or_else(|| "(未触发)".to_string());
                body.push(format!(" sid     {}", trunc(&s.session_id, 30)));
                body.push(format!(" harness {}", s.harness_type));
                body.push(format!(" 实例    {}{}", inst, if inst >= 2 { "  (×N multi)" } else { "" }));
                body.push(format!(" 事件    {}", ev_n));
                body.push(format!(" last    {}", trunc(&turn_disp, 40)));
            }
            None => body.push("(无 session · r 刷新)".to_string()),
        }
        body.push(String::new());
        body.push(format!(" protocol = {}", self.term.protocol.label()));
        body.push(format!(" image_ok = {}", self.term.image_ok));
        body.push(format!(" poll     = {}ms", self.term.poll_interval.as_millis()));
        if !self.term.hint.is_empty() {
            body.push(format!(" ⚠ {}", self.term.hint));
        }
        let h = (body.len() as u16 + 4).clamp(10, 24);
        self.open_popup(
            Popup::centered("props", " props · 终端能力 ", body, 60, h),
        );
    }

    /// IT2 节点 C:开 new 弹窗(选 claw/cc → picker → 创建)。
    pub fn open_new_popup(&mut self) {
        // 默认选 claw + 预 fetch:tui-popup 首次 render 按初始 body 固定 area,若初始
        // body 小(None 态 ~3 行)area 固定小,后续按 c 选 claw body 增到全部候选 area
        // 不扩 → 只显顶部第一个(如 claw-02)。故打开即默认 claw + fetch,首次 render
        // body(update_action_popup_bodies 在 render_popup 前填)就含全部候选 → area 足够。
        // 用户仍可按 d 切 cc(body 行数 ≤ claw 时不截断)。
        self.new_popup = Some(NewKind::Claw);
        self.new_candidates = fetch_claw_agents();
        self.new_idx = 0;
        self.new_cc_input.clear();
        self.open_popup(Popup::centered("new", " new session ", vec![], 56, 22));
    }

    /// ADR-7:输入历史 push(发送 turn 后调)。原生 Vec 兜底(InputHistory 组件待注册)。
    pub fn push_history(&mut self, msg: &str) {
        if !msg.is_empty() {
            self.input_history.push(msg.to_string());
            self.history_cursor = None; // 回到新输入态
        }
    }

    /// ADR-7:历史 ↑(上一条)。None 时从末条开始;到首条停。
    pub fn history_prev(&mut self) -> Option<&str> {
        if self.input_history.is_empty() {
            return None;
        }
        let idx = match self.history_cursor {
            None => self.input_history.len() - 1, // 首次从末条开始
            Some(0) => return None,               // 已到首条,停(不越界)
            Some(i) => i - 1,                      // i >= 1,saturate safety
        };
        self.history_cursor = Some(idx);
        self.input_history.get(idx).map(|s| s.as_str())
    }

    /// ADR-7:历史 ↓(下一条)。到末条后回 None(清空输入栏写新输入)。
    pub fn history_next(&mut self) -> Option<&str> {
        let idx = self.history_cursor?;
        let next = idx + 1;
        if next >= self.input_history.len() {
            self.history_cursor = None;
            None
        } else {
            self.history_cursor = Some(next);
            self.input_history.get(next).map(|s| s.as_str())
        }
    }

    /// base panel 键位(P1 保留 + P2 扩展 e=raw exec / p=弹窗)。返回 true = 退出 app。
    fn handle_base_key(&mut self, k: &KeyEvent) -> bool {
        // ADR-O4:Orchestrate tab 原语 key dispatch(最薄优先级)。
        // 命中原语 key(enabled invoke / disabled 静默消费)→ return;导航键(j/k/1-4/q)无原语
        // 绑定 → dispatch 返 false → 走后续 match。R1:仅 Orchestrate tab,Flows/flow 0 改动。
        if self.panel == Panel::Orchestrate {
            if let KeyCode::Char(ch) = k.code {
                if self.dispatch_orchestrate_primitive(ch) {
                    return false;
                }
            }
        }
        // ADR-1/ADR-7:Control insert 模式 = textarea 编辑态。
        // 文字键/Backspace/Left/Right/Enter/Esc 进 textarea;↑↓ 翻历史;@ 触 mention popup。
        // textarea.handle_key 直接 mutate text+cursor;Enter=Send 发送 turn(发送后 clear)。
        // Tab/BackTab fall through 到导航(打字时仍可切焦点)。
        if self.panel == Panel::Control && self.insert_mode {
            use crate::components::paste_burst::{CharDecision, FlushResult};
            use crossterm::event::KeyModifiers;
            let now = std::time::Instant::now();
            // 先 flush 到期的 burst(超时 → 一次性插入整段 paste)
            match self.paste_burst.flush_if_due(now) {
                FlushResult::Paste(s) => {
                    self.textarea.insert_text(&s);
                    self.turn_msg = self.textarea.text().to_string();
                }
                FlushResult::None => {}
            }
            match k.code {
                KeyCode::Esc => {
                    self.insert_mode = false;
                    self.mentions_open = false;
                    self.paste_burst.clear_window_after_non_char();
                    return false;
                }
                KeyCode::Up => {
                    // 多行(含 \n)→ textarea 逻辑行移;单行 → 翻历史
                    if self.textarea.line_count() > 1 {
                        self.textarea.move_cursor_up();
                        self.turn_msg = self.textarea.text().to_string();
                        return false;
                    }
                    if let Some(h) = self.history_prev() {
                        let s = h.to_string();
                        self.turn_msg = s.clone();
                        self.textarea.set_text(&s);
                    }
                    return false;
                }
                KeyCode::Down => {
                    if self.textarea.line_count() > 1 {
                        self.textarea.move_cursor_down();
                        self.turn_msg = self.textarea.text().to_string();
                        return false;
                    }
                    match self.history_next() {
                        Some(h) => {
                            let s = h.to_string();
                            self.turn_msg = s.clone();
                            self.textarea.set_text(&s);
                        }
                        None => {
                            self.turn_msg.clear();
                            self.textarea.clear();
                        }
                    }
                    return false;
                }
                // Enter/\\r/\\n(终端兼容):burst 内 → 插入换行非发送;否则 Send
                KeyCode::Enter | KeyCode::Char('\r') | KeyCode::Char('\n') => {
                    if self.mentions_open {
                        self.mentions_open = false;
                        return false;
                    }
                    if k.modifiers.intersects(KeyModifiers::SHIFT | KeyModifiers::ALT) {
                        self.textarea.insert_newline();
                        self.turn_msg = self.textarea.text().to_string();
                        return false;
                    }
                    // PasteBurst:burst 内 \\n → 累积进 buffer(不发送)
                    if self.paste_burst.append_newline_if_active(now) {
                        return false;
                    }
                    // burst 窗口边缘(刚结束 120ms 内)→ 插 \\n(避免粘贴尾 Enter 误发)
                    if self.paste_burst.newline_should_insert_instead_of_submit(now) {
                        self.textarea.insert_newline();
                        self.paste_burst.extend_window(now);
                        self.turn_msg = self.textarea.text().to_string();
                        return false;
                    }
                    // 真 Send
                    let msg = self.textarea.text().to_string();
                    self.push_history(&msg);
                    self.turn_msg = msg;
                    self.do_turn(false);
                    self.textarea.clear();
                    self.paste_burst.clear_after_explicit_paste();
                    self.mark_action("trigger");
                    return false;
                }
                KeyCode::Tab | KeyCode::BackTab => {
                    // fall through 到焦点导航
                }
                KeyCode::PageDown => {
                    self.control_chat_scroll.page_down(self.page_viewport());
                    if self.near_bottom() { self.chat_follow_tail = true; }
                    return false;
                }
                KeyCode::PageUp => {
                    self.control_chat_scroll.page_up(self.page_viewport());
                    self.chat_follow_tail = false;
                    return false;
                }
                KeyCode::Char('j') if k.modifiers.contains(KeyModifiers::CONTROL) => {
                    // Ctrl+J 换行(tmux 可靠透传)
                    if let Some(p) = self.paste_burst.flush_before_modified_input() {
                        self.textarea.insert_text(&p);
                    }
                    self.textarea.insert_newline();
                    self.paste_burst.clear_window_after_non_char();
                    self.turn_msg = self.textarea.text().to_string();
                    return false;
                }
                KeyCode::Char('r') if k.modifiers.contains(KeyModifiers::CONTROL) => {
                    // Ctrl+R 重连(insert 内键盘触发,不切模式;normal 模式用 r)
                    if let Some(p) = self.paste_burst.flush_before_modified_input() {
                        self.textarea.insert_text(&p);
                        self.turn_msg = self.textarea.text().to_string();
                    }
                    self.paste_burst.clear_window_after_non_char();
                    self.do_reconnect_or_refresh();
                    return false;
                }
                KeyCode::Char('c') if k.modifiers.contains(KeyModifiers::CONTROL) => {
                    // Ctrl+C:有选区→复制到剪贴板(选区保持);无选区→清空输入区
                    if let Some((a, b)) = self.textarea.selection_range() {
                        if a < b {
                            copy_to_clipboard(&self.textarea.text()[a..b]);
                        }
                    } else {
                        if let Some(p) = self.paste_burst.flush_before_modified_input() {
                            self.textarea.insert_text(&p);
                        }
                        self.paste_burst.clear_window_after_non_char();
                        self.textarea.clear();
                        self.turn_msg.clear();
                    }
                    return false;
                }
                KeyCode::Char('h') if k.modifiers.contains(KeyModifiers::CONTROL) => {
                    // Ctrl+H = 多数终端 Ctrl+Backspace 的编码(^H),等同删词。
                    if let Some(p) = self.paste_burst.flush_before_modified_input() {
                        self.textarea.insert_text(&p);
                    }
                    self.paste_burst.clear_window_after_non_char();
                    self.textarea.delete_word_backward();
                    self.turn_msg = self.textarea.text().to_string();
                    return false;
                }
                KeyCode::Char(c) if c != '\r' && c != '\n' => {
                    let has_ctrl = k.modifiers.contains(KeyModifiers::CONTROL);
                    let has_alt = k.modifiers.contains(KeyModifiers::ALT);
                    if has_ctrl || has_alt {
                        // Ctrl/Alt 组合键:flush burst,组合键暂忽略(Ctrl+J 已上面处理)
                        if let Some(p) = self.paste_burst.flush_before_modified_input() {
                            self.textarea.insert_text(&p);
                            self.turn_msg = self.textarea.text().to_string();
                        }
                        self.paste_burst.clear_window_after_non_char();
                        return false;
                    }
                    if !c.is_ascii() {
                        // 非 ASCII(IME)直接插入,不进 burst
                        if let Some(p) = self.paste_burst.flush_before_modified_input() {
                            self.textarea.insert_text(&p);
                        }
                        self.textarea.insert_text(&c.to_string());
                        self.turn_msg = self.textarea.text().to_string();
                        return false;
                    }
                    // ASCII 普通字符 → PasteBurst 决策
                    match self.paste_burst.on_plain_char(c, now) {
                        CharDecision::Typed(_) => {
                            // 非 burst:立即插入 textarea(乐观显示)
                            self.textarea.insert_text(&c.to_string());
                            self.turn_msg = self.textarea.text().to_string();
                            if c == '@' {
                                self.mentions_open = true;
                            }
                            return false;
                        }
                        CharDecision::BufferAppend => {
                            self.paste_burst.append_char_to_buffer(c, now);
                            return false;
                        }
                        CharDecision::BeginBuffer { retro_chars } => {
                            // retro-grab:抠 textarea cursor 前 retro_chars 字符进 buffer。
                            // 任何快速 ≥3 字符(8ms 内)即判 burst(人打字 >50ms 不触发);
                            // ponytail: 去 codex decide_begin_buffer 的 looks_pastey 门槛(≥16/含空白),
                            //   因 tmux 无 bracketed,短粘贴也要 burst 才能拦 \n;代价:快速打字偶发闪烁。
                            let cur = self.textarea.cursor();
                            let txt = self.textarea.text().to_string();
                            let safe_cur = cur.min(txt.len());
                            let before = &txt[..safe_cur];
                            let start_byte = crate::components::paste_burst::retro_start_index(
                                before,
                                retro_chars as usize,
                            );
                            let grabbed = before[start_byte..].to_string();
                            if start_byte < safe_cur {
                                self.textarea.replace_range_raw(start_byte..safe_cur, "");
                            }
                            self.paste_burst.begin_with_retro_grabbed(grabbed, now);
                            self.paste_burst.append_char_to_buffer(c, now);
                            return false;
                        }
                    }
                }
                _ => {
                    // 编辑键(Backspace/Delete/Left/Right/Home/End):flush burst + textarea
                    if let Some(p) = self.paste_burst.flush_before_modified_input() {
                        self.textarea.insert_text(&p);
                    }
                    use crate::components::textarea::TextareaOp;
                    let op = self.textarea.handle_key(k);
                    self.paste_burst.clear_window_after_non_char();
                    self.turn_msg = self.textarea.text().to_string();
                    match op {
                        TextareaOp::Insert(cc) if cc == '@' => self.mentions_open = true,
                        TextareaOp::Backspace => {
                            if !self.turn_msg.ends_with('@') {
                                self.mentions_open = false;
                            }
                        }
                        _ => {}
                    }
                    return false;
                }
            }
        }
        match k.code {
            // IT4:PgDn/PgUp 滚动。Control=chat 卷轴;Observe=turn stream。
            KeyCode::PageDown => {
                if self.panel == Panel::Observe {
                    self.observe_scroll.page_down(self.page_viewport());
                } else {
                    self.control_chat_scroll.page_down(self.page_viewport());
                    if self.near_bottom() { self.chat_follow_tail = true; }
                }
                false
            }
            KeyCode::PageUp => {
                if self.panel == Panel::Observe {
                    self.observe_scroll.page_up(self.page_viewport());
                } else {
                    self.control_chat_scroll.page_up(self.page_viewport());
                    self.chat_follow_tail = false;
                }
                false
            }
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
                // Control:Enter 触发聚焦按钮;焦点不在按钮(输入态/大纲/TabBar)→ 发送 turn(trigger 按钮 0)。
                // 修"enter 无效":输入态下 focus≠ControlButton,原逻辑什么都不做。
                if self.panel == Panel::Control {
                    let btn = match self.focus {
                        FocusTarget::ControlButton(i) => i,
                        _ => 0,
                    };
                    self.trigger_control_button(btn);
                }
                // ADR-2(第八轮):Observe session 聚焦时 Enter → 跳 Control(cursor 同步 + WS 重订阅)。
                if self.panel == Panel::Observe {
                    if matches!(self.focus, FocusTarget::ObserveSession) {
                        self.jump_to_control(self.cursor);
                    }
                }
                // ADR-O4:Orchestrate tab Enter → 查 key='\n' 原语(open-events)。
                // Enter 非 KeyCode::Char,不走上面的 Char dispatch;此处显式路由保持 registry 派发。
                if self.panel == Panel::Orchestrate && self.dispatch_orchestrate_primitive('\n') {
                    return false;
                }
                false
            }
            KeyCode::Char('1') => {
                self.panel = Panel::Flows;
                self.sync_tab_from_panel();
                false
            }
            KeyCode::Char('2') => {
                self.panel = Panel::Observe;
                self.sync_tab_from_panel();
                false
            }
            KeyCode::Char('3') => {
                self.panel = Panel::Control;
                self.sync_tab_from_panel();
                // ADR-3:进入 Control 时预检 orche health(非阻塞,失败默认 false)。
                self.orche_online = fetch_orche_health();
                false
            }
            KeyCode::Char('4') => {
                // ADR-O1:第4 tab Orchestrate。进入即拉 observe fork 树(ADR-O2)。
                self.panel = Panel::Orchestrate;
                self.sync_tab_from_panel();
                self.fetch_orch_tree();
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
                } else if self.panel == Panel::Orchestrate {
                    // ADR-O1:Orchestrate j/k 跨层级 DFS 序移动光标。
                    self.orch_cursor_down();
                } else if self.panel == Panel::Control {
                    // ADR-4:Control tab 非 insert_mode 时 j/Down 直接切 session cursor(主交互),
                    // 不再依赖 BackTab cycle 到 ControlSession 焦点(BackTab 在 tmux/无鼠标不可靠)。
                    // ControlButton 焦点导航 tradeoff 退到鼠标/快捷键(t/s/r/e/f/G/D/R)。
                    // insert_mode 分支(line ~2590)提前 return,textarea 行移/历史不破坏。
                    self.cursor_down();
                    self.focus = FocusTarget::ControlSession(self.cursor);
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
                } else if self.panel == Panel::Orchestrate {
                    self.orch_cursor_up();
                } else if self.panel == Panel::Control {
                    // ADR-4:Control tab 非 insert_mode 时 k/Up 直接切 session cursor(见 j/Down 分支 ADR-4)。
                    self.cursor_up();
                    self.focus = FocusTarget::ControlSession(self.cursor);
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
                self.do_turn(false);
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
                self.do_reconnect_or_refresh();
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
                // raw exec:挂起 TUI 全屏拉起 cursor session 的 harness(claude --resume / openclaw)。
                // 实际 spawn 由 run() loop 消费 pending_spawn(原占位弹窗已废)。
                self.request_raw_exec();
                false
            }
            KeyCode::Char('i') if self.panel == Panel::Control => {
                // 进入输入模式(可自由打字);Esc 退出。
                self.insert_mode = true;
                false
            }
            KeyCode::Char('[') if self.panel == Panel::Control => {
                self.control_right_tabs.prev();
                false
            }
            KeyCode::Char(']') if self.panel == Panel::Control => {
                self.control_right_tabs.next();
                false
            }
            // ── IT2 节点 C:session 管理键(normal 模式,光标 session)──────────
            KeyCode::Char('n') if self.panel == Panel::Control => {
                self.open_new_popup();
                false
            }
            KeyCode::Char('d') if self.panel == Panel::Control => {
                // 开 delete 确认弹窗(光标 session)。
                let sid = self.current_sid();
                if !sid.starts_with("(无") {
                    self.open_delete_popup(&sid);
                }
                false
            }
            KeyCode::Char('a') if self.panel == Panel::Control => {
                let (ht, sid) = self.flat.get(self.cursor)
                    .map(|s| (norm_ht(&s.harness_type), s.session_id.clone()))
                    .unwrap_or_else(|| ("claw".to_string(), String::new()));
                if !sid.is_empty() {
                    match archive_session(&ht, &sid, None) {
                        Some(msg) => self.turn_status = Some(format!("archived: {}", trunc(&msg.trim(), 40))),
                        None => self.turn_status = Some(format!("archive 失败(orche 不可达?): {}", trunc(&sid, 12))),
                    }
                }
                false
            }
            KeyCode::Char('F') if self.panel == Panel::Control => {
                self.do_fork();
                false
            }
            // normal 模式下普通字母/退格无动作:打字统一由 insert 模式处理(见函数顶 capture)。
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
        // tick_started = turn 确认(user msg 由 tick_started.request 接管)。清 pending 按
        // pending session_key 匹配事件 key(不依赖 cursor,见下),无需 cursor_key。
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
                    // 收到事件 = WS 连通(重连成功)→ 清 ws_errors[key]。
                    self.ws_errors.remove(&key);
                    // tick_started(cursor session)且 request == pending msg = 本次 user msg 确认 → 清 pending。
                    // 必须匹配 pending msg:observe 双源(空+非空)+ 历史 tick_started request 非空但不本次,
                    // 只匹配本次 msg 才清(避免历史/空 request 误清致 spinner 提前停)。
                    if ev.event_type == "tick_started" {
                        let req = ev.data.get("request").and_then(|v| v.as_str()).unwrap_or("");
                        // 按 pending session_key 匹配事件 key(不依赖 cursor:切 session 后原 session
                        // tick_started 仍能清其 pending)+ msg 匹配(避免历史/空 request 误清)。
                        if let Some((pk, pm)) = &self.pending_turn {
                            if pk == &key && !req.is_empty() && pm.starts_with(req) {
                                self.clear_pending();
                            }
                        }
                    } else if ev.event_type == "tick_completed" || ev.event_type == "tick_failed" {
                        // 兜底清 pending:tick_started 的 starts_with 匹配可能因 server 截断/重写
                        // request 失配(tick_started 漏/丢)致 spinner 卡到 60s 兜底;终态事件到 =
                        // turn 已完成,同 key 直接清(治 turn 完成但 spinner 跑满 60s 的失真)。
                        if let Some((pk, _)) = &self.pending_turn {
                            if pk == &key {
                                self.clear_pending();
                            }
                        }
                        // turn 终态:清流式 buffer(response 进 events 替代 streaming 行)。
                        self.streaming_text.remove(&key);
                    }
                    // 累积 turn 事件(同 fetch_events 效果:events[key].push + 实例去重计数)。
                    // ADR-O1:Orchestrate tab 实时刷新——agent-os-v2 fork/tick 事件转发给 fork 树。
                    if self.panel == Panel::Orchestrate
                        && key.starts_with("agent-os-v2/")
                        && matches!(ev.event_type.as_str(), "branch_created" | "tick_started" | "tick_completed")
                    {
                        let sid = key.strip_prefix("agent-os-v2/").unwrap_or("");
                        self.apply_orch_tree_event(sid, &ev);
                    }
                    // token_delta 流式 token 不存 app.events(撑爆 cap=200 挤掉历史 turn 结构;
                    // render 用 tick_completed.response,流式 token P2 defer)
                    if ev.event_type == "token_delta" {
                        // 流式累积到 buffer(不进 events 防 cap=200);tick_completed/failed 清。
                        // render 在 ev_lines 末尾 append streaming 行(pending_turn 同款)。
                        if let Some(delta) = ev.data.get("delta_text").and_then(|v| v.as_str()) {
                            if !delta.is_empty() {
                                self.streaming_text.entry(key.clone()).or_default().push_str(delta);
                            }
                        }
                        continue;
                    }
                    let evs = self.events.entry(key.clone()).or_default();
                    // IT7:去重——REST fetch_events(替换)+ WS drain_ws(追加)时序重叠会重复。
                    // 非空 event_id:按 event_id 精确去重。空 event_id(旧数据/无 id):按
                    // (event_type, tick_id, harness_id) 复合键去重(治 REST+WS 双源空 event_id
                    // 重复渲染同 turn;含 harness_id 区分多实例同 turn,不误并)。
                    let dup = if !ev.event_id.is_empty() {
                        evs.iter().any(|e| e.event_id == ev.event_id)
                    } else {
                        evs.iter().any(|e| e.event_id.is_empty()
                            && e.event_type == ev.event_type
                            && e.tick_id == ev.tick_id
                            && e.harness_id == ev.harness_id)
                    };
                    if dup {
                        continue;
                    }
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
                crate::ws::WsMsg::MemoryEvent { ev } => {
                    self.apply_memory_event(&ev);
                }
                crate::ws::WsMsg::OrchEvent { session_id, ev } => {
                    self.apply_orch_event(&session_id, &ev);
                }
                crate::ws::WsMsg::Error { key, .. } => {
                    // WS 断/重连失败(ws_loop 自管 backoff 重连,见 ws.rs);记最近 error 时间,
                    // status_spans 显"⚠WS重连"(重连成功收到 Event 自动清 ws_errors[key])。
                    self.ws_errors.insert(key.clone(), std::time::Instant::now());
                }
                crate::ws::WsMsg::Reconnected { key } => {
                    // WS connect OK(重连成功)→ 清 ws_errors(不依赖新 Event;
                    // observe 重启后无新广播时 ws_errors 靠此清)。
                    self.ws_errors.remove(&key);
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

    /// memory WS 事件 → app.events["memory/memory"](observe tab 事件流渲染)。
    /// ponytail: 最小——只 push 事件流,不建 panel/状态结构;DAG 聚合等 orchestrate 成主力再加。
    fn apply_memory_event(&mut self, ev: &ObserveEvent) {
        self.push_event("memory/memory".to_string(), ev.clone());
    }

    /// orchestrate WS 事件 → app.events["orchestrate/{session_id}"](observe tab 事件流渲染)。
    /// ponytail: 同 apply_memory_event——只 push 事件流,聚合 defer。
    fn apply_orch_event(&mut self, session_id: &str, ev: &ObserveEvent) {
        let key = format!("orchestrate/{}", session_id);
        self.push_event(key, ev.clone());
    }

    /// 共用:push ev 到 app.events[key](event_id 去重 + cap 200,同 drain_ws Event 分支)。
    fn push_event(&mut self, key: String, ev: ObserveEvent) {
        let evs = self.events.entry(key.clone()).or_default();
        if !ev.event_id.is_empty() && evs.iter().any(|e| e.event_id == ev.event_id) {
            return;
        }
        if evs.len() >= 200 {
            evs.remove(0);
        }
        evs.push(ev);
    }
}

// ═══ self-check:FlowStatus 反序列化(orche 契约)+ preset 拓扑 ═════════
// 唯一非平凡逻辑:serde 字段映射 drift 即 break。preset 的入度/边数验证拓扑正确。
#[cfg(test)]
mod tests {
    use super::*;

    // ═══ ADR-O1/O2 fork 谱系树:build_fork_tree + DFS 序 + depth + NodeState ═══
    // 非平凡逻辑:observe sessions(含 parent_session_id)→ 客户端建树;drift 即 break。

    #[test]
    fn build_fork_tree_links_children_and_roots() {
        let sessions = vec![
            OrchSession { session_id: "root".into(), harness_type: "agent-os-v2".into(), agent_id: "native".into(), parent_session_id: String::new() },
            OrchSession { session_id: "c1".into(), harness_type: "agent-os-v2".into(), agent_id: "a".into(), parent_session_id: "root".into() },
            OrchSession { session_id: "c2".into(), harness_type: "agent-os-v2".into(), agent_id: "b".into(), parent_session_id: "root".into() },
            // 孤儿(parent 指向不存在的 session)→ 当 root 处理。
            OrchSession { session_id: "orphan".into(), harness_type: "agent-os-v2".into(), agent_id: "o".into(), parent_session_id: "ghost".into() },
        ];
        let tree = build_fork_tree(sessions);
        assert_eq!(tree.nodes.get("root").unwrap().children, vec!["c1", "c2"]);
        assert!(tree.nodes.get("c1").unwrap().parent.as_deref() == Some("root"));
        // 根 + 孤儿(root 顺序:root 先入)。
        assert!(tree.roots.contains(&"root".to_string()));
        assert!(tree.roots.contains(&"orphan".to_string()));
    }

    #[test]
    fn fork_tree_dfs_order_and_depth() {
        // root → c1 → gc;root → c2。
        let sessions = vec![
            OrchSession { session_id: "root".into(), harness_type: "agent-os-v2".into(), agent_id: "n".into(), parent_session_id: String::new() },
            OrchSession { session_id: "c1".into(), harness_type: "agent-os-v2".into(), agent_id: "a".into(), parent_session_id: "root".into() },
            OrchSession { session_id: "gc".into(), harness_type: "agent-os-v2".into(), agent_id: "g".into(), parent_session_id: "c1".into() },
            OrchSession { session_id: "c2".into(), harness_type: "agent-os-v2".into(), agent_id: "b".into(), parent_session_id: "root".into() },
        ];
        let tree = build_fork_tree(sessions);
        // DFS(root → c1 → gc → c2):父先于子。
        let order = tree.flat_order();
        assert_eq!(order, vec!["root", "c1", "gc", "c2"]);
        assert_eq!(tree.depth("root"), 0);
        assert_eq!(tree.depth("c1"), 1);
        assert_eq!(tree.depth("gc"), 2);
        assert_eq!(tree.depth("c2"), 1);
    }

    #[test]
    fn node_state_maps_from_event() {
        assert_eq!(NodeState::from_event("tick_started", ""), NodeState::Running);
        assert_eq!(NodeState::from_event("tick_completed", "success"), NodeState::Done);
        assert_eq!(NodeState::from_event("tick_completed", "cancelled"), NodeState::Idle);
        // error 不显成 ✓(语义错)→ Idle。
        assert_eq!(NodeState::from_event("tick_completed", "error"), NodeState::Idle);
        assert_eq!(NodeState::from_event("branch_created", ""), NodeState::Active);
        assert_eq!(NodeState::from_event("unknown", ""), NodeState::Idle);
        // glyph 覆盖 4 态(防退化成单一符号)。
        let glyphs: Vec<&str> = [NodeState::Active, NodeState::Done, NodeState::Running, NodeState::Idle]
            .iter().map(|s| s.glyph()).collect();
        assert_eq!(glyphs, vec!["●", "✓", "⠋", "○"]);
    }

    #[test]
    fn apply_orch_tree_event_updates_node_state() {
        // 建空 app + 注入一棵树 → 模拟 tick_started/tick_completed WS 事件刷状态。
        let mut app = App::new(crate::kitty::detect());
        let sessions = vec![
            OrchSession { session_id: "root".into(), harness_type: "agent-os-v2".into(), agent_id: "native".into(), parent_session_id: String::new() },
            OrchSession { session_id: "c1".into(), harness_type: "agent-os-v2".into(), agent_id: "a".into(), parent_session_id: "root".into() },
        ];
        app.fork_tree = build_fork_tree(sessions);
        app.panel = Panel::Orchestrate;
        app.orch_cursor = 1; // c1
        app.sync_orch_selection();

        // tick_started(c1)→ Running。
        let ev_started = ObserveEvent { event_type: "tick_started".into(), tick_id: "t1".into(), harness_id: "h".into(), data: HashMap::new(), event_id: "e1".into() };
        app.apply_orch_tree_event("c1", &ev_started);
        assert_eq!(app.fork_tree.nodes.get("c1").unwrap().state, NodeState::Running);

        // tick_completed(c1)→ Done。
        let mut data = HashMap::new();
        data.insert("status".into(), serde_json::json!("success"));
        let ev_done = ObserveEvent { event_type: "tick_completed".into(), tick_id: "t2".into(), harness_id: "h".into(), data, event_id: "e2".into() };
        app.apply_orch_tree_event("c1", &ev_done);
        assert_eq!(app.fork_tree.nodes.get("c1").unwrap().state, NodeState::Done);
        // 选中投影同步(光标在 c1)。
        assert_eq!(app.orch_selection.as_ref().unwrap().state, NodeState::Done);
    }

    #[test]
    fn orch_cursor_navigation_clamps() {
        let mut app = App::new(crate::kitty::detect());
        let sessions = vec![
            OrchSession { session_id: "root".into(), harness_type: "agent-os-v2".into(), agent_id: "n".into(), parent_session_id: String::new() },
            OrchSession { session_id: "c1".into(), harness_type: "agent-os-v2".into(), agent_id: "a".into(), parent_session_id: "root".into() },
        ];
        app.fork_tree = build_fork_tree(sessions);
        app.panel = Panel::Orchestrate;
        app.orch_cursor = 0;
        app.orch_cursor_down(); // → 1
        assert_eq!(app.orch_cursor, 1);
        app.orch_cursor_down(); // clamp(只有 2 节点)
        assert_eq!(app.orch_cursor, 1);
        app.orch_cursor_up(); // → 0
        assert_eq!(app.orch_cursor, 0);
        app.orch_cursor_up(); // clamp
        assert_eq!(app.orch_cursor, 0);
    }

    #[test]
    fn orch_primitive_registry_has_five_primitives() {
        // ADR-O4:五原语注册(fork/async-turn/open-events/cancel + compare bonus C)。
        let app = App::new(crate::kitty::detect());
        let ids: Vec<&str> = app.primitives.iter().map(|p| p.id()).collect();
        assert_eq!(ids, vec!["fork", "async-turn", "open-events", "cancel", "compare"]);
        let keys: Vec<char> = app.primitives.iter().map(|p| p.key()).collect();
        assert_eq!(keys, vec!['f', 't', '\n', 'x', 'c']);
    }

    #[test]
    fn orch_enter_routes_to_observe_open_events() {
        // T3(skeptic 补):Orchestrate tab Enter → open-events 原语 → panel=Observe +
        // focus=ObserveSession + cursor 同步到选中 session_id。
        // flat 必须含选中 sid(focus_new_session 按 sid 找 flat 索引 + set_cursor_session)。
        let mut app = App::new(crate::kitty::detect());
        app.fork_tree = build_fork_tree(vec![
            OrchSession { session_id: "root".into(), harness_type: "agent-os-v2".into(), agent_id: "n".into(), parent_session_id: String::new() },
        ]);
        app.flat = vec![Session {
            harness_type: "agent-os-v2".into(), session_id: "root".into(),
            harness_id: "h1".into(), cwd: None, running: false,
        }];
        app.panel = Panel::Orchestrate;
        app.orch_cursor = 0;
        app.sync_orch_selection();
        // 预置:选中 root。
        assert_eq!(app.orch_selection.as_ref().unwrap().session_id, "root");
        // Enter 经 handle_base_key 的 Orchestrate 分支 → dispatch '\n' → open-events 原语。
        app.handle_base_key(&KeyEvent::new(KeyCode::Enter, crossterm::event::KeyModifiers::empty()));
        // 真断言三连:panel / focus / cursor session_id 对齐。
        assert_eq!(app.panel, Panel::Observe, "Enter should route to Observe panel");
        assert_eq!(app.focus, FocusTarget::ObserveSession, "focus should be ObserveSession");
        assert_eq!(app.flat.get(app.cursor).map(|s| s.session_id.as_str()), Some("root"),
            "cursor should align to selected session_id");
    }

    #[test]
    fn orch_primitive_enabled_and_dispatch_consumes() {
        // 有选中(idle root):fork/async-turn/open-events enabled,cancel disabled(非 Running)。
        // dispatch 命中 bound key → 消费(true);命中未绑 key('q')→ 不消费(false,交回导航)。
        let mut app = App::new(crate::kitty::detect());
        let sessions = vec![
            OrchSession { session_id: "root".into(), harness_type: "agent-os-v2".into(), agent_id: "n".into(), parent_session_id: String::new() },
        ];
        app.fork_tree = build_fork_tree(sessions);
        app.panel = Panel::Orchestrate;
        app.sync_orch_selection();
        let sel = app.orch_selection.clone().unwrap();
        // enabled 断言(不 dispatch 真实原语免触发网络/fork 副作用)。
        let enabled: Vec<&str> = app.primitives.iter().filter(|p| p.enabled(&sel)).map(|p| p.id()).collect();
        assert_eq!(enabled, vec!["fork", "async-turn", "open-events"], "real primitives enabled on selection");
        // cancel disabled(非 Running)→ dispatch 'x' 消费 true(no-op 灰显)。
        assert!(app.dispatch_orchestrate_primitive('x'));
        // 未绑 key('q')→ 不消费 false。
        assert!(!app.dispatch_orchestrate_primitive('q'));
        // 无选中 → 不消费。
        app.orch_selection = None;
        assert!(!app.dispatch_orchestrate_primitive('f'));
    }

    #[test]
    fn orch_primitive_hint_derives_from_registry() {
        // footer hint 从 registry 派生:enabled 显 key=label,disabled 显 (label)。
        // 有选中(idle root):fork/async/events enabled,cancel 非 Running disabled。
        let mut app = App::new(crate::kitty::detect());
        app.fork_tree = build_fork_tree(vec![
            OrchSession { session_id: "r".into(), harness_type: "agent-os-v2".into(), agent_id: "n".into(), parent_session_id: String::new() },
        ]);
        app.sync_orch_selection();
        let hint = app.orch_primitive_hint();
        // enabled 原语显 key=label。
        assert!(hint.contains("f=fork"), "enabled fork should show key=label: hint={}", hint);
        assert!(hint.contains("t=async"), "enabled async-turn should show t=async: hint={}", hint);
        // cancel 非 Running disabled → 显 (cancel),不含 key=。
        assert!(hint.contains("(cancel)"), "disabled cancel grayed: hint={}", hint);
        assert!(!hint.contains("x=cancel"), "disabled should not show key: hint={}", hint);
    }

    #[test]
    fn orch_dispatch_does_not_swallow_navigation_keys() {
        // handle_base_key 在 Orchestrate tab:j/k 仍导航(无原语绑 j/k)。
        let mut app = App::new(crate::kitty::detect());
        app.fork_tree = build_fork_tree(vec![
            OrchSession { session_id: "r".into(), harness_type: "agent-os-v2".into(), agent_id: "n".into(), parent_session_id: String::new() },
            OrchSession { session_id: "c".into(), harness_type: "agent-os-v2".into(), agent_id: "a".into(), parent_session_id: "r".into() },
        ]);
        app.panel = Panel::Orchestrate;
        app.orch_cursor = 0;
        app.sync_orch_selection();
        // j 无原语绑定 → 走导航 → cursor 下移。
        app.handle_base_key(&KeyEvent::new(KeyCode::Char('j'), crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.orch_cursor, 1, "j should still navigate in Orchestrate");
    }

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

    /// ADR-1:Control 按钮 ClickMap 命中 id=3(raw-exec)→ 请求 pending_spawn(原占位弹窗已废)。
    /// 手动注册 clickmap region(id=3),模拟 draw_control 注册后 handle_base_mouse 命中。
    #[test]
    fn control_clickmap_rawexec_requests_spawn() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.flat = vec![Session {
            harness_type: "claude-code".into(), session_id: "abc123".into(), harness_id: "h1".into(), cwd: None, running: false,
        }];
        app.cursor = 0;
        app.clickmap.clear();
        app.clickmap.register(Rect::new(10, 5, 20, 1), 3);
        let m = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 15,
            row: 5,
            modifiers: crossterm::event::KeyModifiers::empty(),
        };
        app.handle_base_mouse(&m);
        assert!(app.pending_spawn.is_some(), "raw-exec button should set pending_spawn");
        let (h, sid) = app.pending_spawn.unwrap();
        assert_eq!(h, crate::components::raw_exec::Harness::ClaudeCode);
        assert_eq!(sid.as_deref(), Some("abc123"));
    }

    /// ADR-2(第八轮):Observe session 点 → 跳 Control(cursor 同步 + panel 切 Control)。
    /// 手动注册 clickmap region(id=1),模拟 draw_observe_scroll 注册后 handle_base_mouse 命中。
    #[test]
    /// IT3 ④:Observe session 点击 → 设 observe_view_cursor(查看,不跳 Control);
    /// 跳 Control 用独立按钮(id 400)。
    #[test]
    fn observe_clickmap_session_sets_view_cursor() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Observe;
        app.flat = vec![
            Session { harness_type: "claw".into(), session_id: "sess-a".into(), harness_id: "h1".into(), cwd: None, running: false },
            Session { harness_type: "claw".into(), session_id: "sess-b".into(), harness_id: "h2".into(), cwd: None, running: false },
        ];
        app.cursor = 0;
        // session 行 clickmap id = 600 + flat_idx(模拟 draw_stack 注册 session 行 1)。
        app.clickmap.clear();
        app.clickmap.register(Rect::new(0, 3, 35, 1), 600 + 1);
        let m = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 5,
            row: 3,
            modifiers: crossterm::event::KeyModifiers::empty(),
        };
        app.handle_base_mouse(&m);
        // 点 session → observe_view_cursor=Some(1),但不跳 Control(panel 仍 Observe)。
        assert_eq!(app.observe_view_cursor, Some(1), "clicking session sets observe_view_cursor");
        assert_eq!(app.panel, Panel::Observe, "clicking session views in-place, no jump");
    }

    /// IT3 ④:Observe 跳转按钮(id 400)→ jump_to_control(view_cursor 同步 cursor + panel=Control)。
    #[test]
    fn observe_jump_button_jumps_to_control() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Observe;
        app.flat = vec![
            Session { harness_type: "claw".into(), session_id: "sess-a".into(), harness_id: "h1".into(), cwd: None, running: false },
            Session { harness_type: "claw".into(), session_id: "sess-b".into(), harness_id: "h2".into(), cwd: None, running: false },
        ];
        app.observe_view_cursor = Some(1);
        // 跳转按钮 clickmap id=400。
        app.clickmap.clear();
        app.clickmap.register(Rect::new(0, 20, 40, 1), 400);
        let m = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 5,
            row: 20,
            modifiers: crossterm::event::KeyModifiers::empty(),
        };
        app.handle_base_mouse(&m);
        assert_eq!(app.panel, Panel::Control, "jump button → panel=Control");
        assert_eq!(app.cursor, 1, "jump syncs view_cursor(1) into cursor");
    }

    /// IT3 ④:Observe 组 header 点击(id 500+gi)→ toggle_observe_group。
    #[test]
    fn observe_group_header_toggles() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Observe;
        app.flat = vec![
            Session { harness_type: "claude-code".into(), session_id: "cc-1".into(), harness_id: "h1".into(), cwd: None, running: false },
            Session { harness_type: "openclaw".into(), session_id: "oc-1".into(), harness_id: "h2".into(), cwd: None, running: false },
        ];
        // 组 id=500+0 = claude-code(排序首)。
        app.clickmap.clear();
        app.clickmap.register(Rect::new(0, 2, 40, 1), 500);
        let m = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 1,
            row: 2,
            modifiers: crossterm::event::KeyModifiers::empty(),
        };
        assert!(!app.observe_collapsed.contains("claude-code"));
        app.handle_base_mouse(&m);
        assert!(app.observe_collapsed.contains("claude-code"), "header click → 折叠");
        app.handle_base_mouse(&m);
        assert!(!app.observe_collapsed.contains("claude-code"), "再点 → 展开");
    }

    /// IT3 ④:toggle_observe_group 增删 observe_collapsed。
    #[test]
    fn toggle_observe_group_flips() {
        let mut app = App::new(crate::kitty::detect());
        assert!(!app.observe_collapsed.contains("g"));
        app.toggle_observe_group("g");
        assert!(app.observe_collapsed.contains("g"));
        app.toggle_observe_group("g");
        assert!(!app.observe_collapsed.contains("g"));
    }

    /// ADR-2(第八轮):Observe 键盘 Enter on ObserveSession focus → 跳 Control(cursor 同步)。
    #[test]
    fn observe_enter_jumps_to_control() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Observe;
        app.focus = FocusTarget::ObserveSession;
        app.flat = vec![
            Session { harness_type: "claw".into(), session_id: "sess-a".into(), harness_id: "h1".into(), cwd: None, running: false },
            Session { harness_type: "claw".into(), session_id: "sess-b".into(), harness_id: "h2".into(), cwd: None, running: false },
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
            event_id: String::new(),
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
            event_id: String::new(),
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

    // ── IT7:WS 事件 event_id 去重(REST fetch + WS drain 时序重叠)──────

    fn ev_with(id: &str, harness_id: &str) -> ObserveEvent {
        ObserveEvent {
            event_type: "tick_completed".to_string(),
            tick_id: "t1".to_string(),
            harness_id: harness_id.to_string(),
            data: HashMap::new(),
            event_id: id.to_string(),
        }
    }

    /// IT7:drain_ws 对非空 event_id 已存在事件去重(skip)。
    #[test]
    fn drain_ws_dedups_by_event_id() {
        use crate::ws::{WsManager, WsMsg};
        use std::sync::mpsc;
        let (tx, rx) = mpsc::channel::<WsMsg>();
        let mut app = App::new(crate::kitty::detect());
        app.ws = Some(WsManager::mock(rx));
        let key = "openclaw/agent:main:main".to_string();
        // 预置 REST 已拉到的事件 e1(模拟 fetch_events 替换进 events[key])。
        app.events.entry(key.clone()).or_default().push(ev_with("e1", "h1"));
        // WS 推送:e1 重复 + e2 新增。
        tx.send(WsMsg::Event { key: key.clone(), ev: ev_with("e1", "h1") }).unwrap();
        tx.send(WsMsg::Event { key: key.clone(), ev: ev_with("e2", "h1") }).unwrap();
        app.drain_ws();
        let evs = &app.events[&key];
        assert_eq!(evs.len(), 2, "e1 应去重,e2 新增,共 2 条");
        assert!(evs.iter().all(|e| e.event_id != "e1" || evs.iter().filter(|x| x.event_id == "e1").count() == 1));
    }

    /// IT7:空 event_id(旧数据)按 (event_type, tick_id, harness_id) 复合键去重;
    /// 不同 harness_id 的同 turn(多实例)不误并(h1/h2 保留 2 条)。
    #[test]
    fn drain_ws_keeps_empty_event_id() {
        use crate::ws::{WsManager, WsMsg};
        use std::sync::mpsc;
        let (tx, rx) = mpsc::channel::<WsMsg>();
        let mut app = App::new(crate::kitty::detect());
        app.ws = Some(WsManager::mock(rx));
        let key = "claude-code/s1".to_string();
        tx.send(WsMsg::Event { key: key.clone(), ev: ev_with("", "h1") }).unwrap();
        tx.send(WsMsg::Event { key: key.clone(), ev: ev_with("", "h2") }).unwrap();
        app.drain_ws();
        assert_eq!(app.events[&key].len(), 2, "不同 harness_id 不误并,两条都保留");
        // 多实例计数仍按 harness_id 去重(h1+h2=2)。
        assert_eq!(app.instances.get(&key).copied().unwrap_or(0), 2);
    }

    /// IT7:空 event_id 同 (event_type, tick_id, harness_id) 复合键 → 去重(REST+WS 双源
    /// 重复渲染同 turn 的治本点;event_id 缺失时按复合键兜底)。
    /// 流式 token_delta 累积到 streaming_text(非 events);events 不含 token_delta(cap 安全)。
    #[test]
    fn drain_ws_token_delta_accumulates_to_streaming_buffer() {
        use crate::ws::{WsManager, WsMsg};
        use std::sync::mpsc;
        let (tx, rx) = mpsc::channel::<WsMsg>();
        let mut app = App::new(crate::kitty::detect());
        app.ws = Some(WsManager::mock(rx));
        let key = "openclaw/agent:main".to_string();
        // 推 3 个 token_delta(delta_text 累积)。
        for d in ["Hello", ", ", "AO2"] {
            let mut data = HashMap::new();
            data.insert("delta_text".to_string(), serde_json::Value::String(d.to_string()));
            tx.send(WsMsg::Event {
                key: key.clone(),
                ev: ObserveEvent {
                    event_type: "token_delta".to_string(),
                    tick_id: "t1".to_string(),
                    harness_id: "h1".to_string(),
                    data,
                    event_id: "".to_string(),
                },
            }).unwrap();
        }
        app.drain_ws();
        // 累积到 streaming_text(非 events)。
        assert_eq!(app.streaming_text.get(&key).map(|s| s.as_str()), Some("Hello, AO2"));
        // events 不含 token_delta(cap=200 安全,防撑爆挤掉历史 turn 结构)。
        let no_delta = app.events.get(&key)
            .map_or(true, |evs| evs.iter().all(|e| e.event_type != "token_delta"));
        assert!(no_delta, "token_delta 不应进 events");
    }

    /// tick_completed/tick_failed 清 streaming buffer(response 进 events 替代 streaming 行)。
    #[test]
    fn drain_ws_tick_terminal_clears_streaming_buffer() {
        use crate::ws::{WsManager, WsMsg};
        use std::sync::mpsc;
        let (tx, rx) = mpsc::channel::<WsMsg>();
        let mut app = App::new(crate::kitty::detect());
        app.ws = Some(WsManager::mock(rx));
        let key = "openclaw/agent:main".to_string();
        // 预置流式 buffer(模拟 token_delta 已累积)。
        app.streaming_text.insert(key.clone(), "partial response".to_string());
        // tick_completed → 清 streaming。
        tx.send(WsMsg::Event {
            key: key.clone(),
            ev: ObserveEvent {
                event_type: "tick_completed".to_string(),
                tick_id: "t1".to_string(),
                harness_id: "h1".to_string(),
                data: HashMap::new(),
                event_id: "e1".to_string(),
            },
        }).unwrap();
        app.drain_ws();
        assert!(!app.streaming_text.contains_key(&key), "tick_completed 清 streaming buffer");
        assert_eq!(app.events[&key].len(), 1, "tick_completed 进 events");
    }

    #[test]
    fn drain_ws_dedups_empty_event_id_by_composite_key() {
        use crate::ws::{WsManager, WsMsg};
        use std::sync::mpsc;
        let (tx, rx) = mpsc::channel::<WsMsg>();
        let mut app = App::new(crate::kitty::detect());
        app.ws = Some(WsManager::mock(rx));
        let key = "claude-code/s1".to_string();
        // 预置 REST 已拉到的空 event_id 事件(模拟 fetch_events)。
        app.events.entry(key.clone()).or_default().push(ev_with("", "h1"));
        // WS 推同 (event_type, tick_id, harness_id) 的重复 → 去重。
        tx.send(WsMsg::Event { key: key.clone(), ev: ev_with("", "h1") }).unwrap();
        app.drain_ws();
        assert_eq!(app.events[&key].len(), 1, "同复合键空 event_id 去重为 1 条");
    }

    /// trunc 按 unicode-width 截断(非 chars().count):CJK 按显示宽计,列对齐准确;
    /// 不切 char 中间(无乱码);留 ellipsis 位。
    #[test]
    fn trunc_uses_unicode_width_not_char_count() {
        // ASCII:width = char count,行为不变
        assert_eq!(trunc("hello world", 5), "hell…");
        assert_eq!(trunc("hi", 5), "hi");           // 不超宽,原样
        // CJK 每字 2 列宽:trunc(s,5) 留 4 列(2 CJK)+ …,非 5 char(会超宽 10 列)
        assert_eq!(trunc("中文字符测试", 5), "中文…");
        // 混合 ASCII+CJK 按显示宽累加
        assert_eq!(trunc("ab中文", 5), "ab中…");     // ab(2)+中(2)=4,+…=5 列
        // 不切 char 中间:budget=2(留 … 位),c 后停(中 2 列超预算)
        assert_eq!(trunc("abc中", 3), "ab…");
        // 空串
        assert_eq!(trunc("", 5), "");
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

    /// ADR-4:Control 方向键(normal 模式)直接切 session cursor + 焦点归 ControlSession,
    /// 不再依赖 BackTab cycle 到 ControlSession(BackTab 在 tmux/无鼠标不可靠)。
    /// insert 模式下 ↑↓ 翻历史/textarea 行移(ADR-7),故此测 focus 移动须 normal 模式。
    /// ControlButton 焦点导航 tradeoff 退到鼠标/快捷键(t/s/r/e/f/G/D/R)。
    #[test]
    fn control_arrow_keys_move_session_cursor() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = false; // normal 模式:↑↓ 切 session cursor(非翻历史)
        app.flat = vec![
            Session { harness_type: "claw".into(), session_id: "sess-a".into(), harness_id: "h1".into(), cwd: None, running: false },
            Session { harness_type: "claw".into(), session_id: "sess-b".into(), harness_id: "h2".into(), cwd: None, running: false },
        ];
        app.cursor = 0;
        app.focus = FocusTarget::ControlButton(0); // 起始焦点在按钮(验证不依赖 focus 入口)
        // Down:j 方向键 → cursor 1 + 焦点 ControlSession(1)。
        app.handle_base_key(&KeyEvent::new(KeyCode::Down, crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.cursor, 1);
        assert_eq!(app.focus, FocusTarget::ControlSession(1));
        // Up:k 方向键 → cursor 0 + 焦点 ControlSession(0)。
        app.handle_base_key(&KeyEvent::new(KeyCode::Up, crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.cursor, 0);
        assert_eq!(app.focus, FocusTarget::ControlSession(0));
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

    /// 右主区 tab(对话/flow)切换:ADR-3 属性改 props 弹窗,右 tab 缩 2。next/prev 循环、select 定位。
    #[test]
    fn control_right_tab_cycles() {
        let mut app = App::new(crate::kitty::detect());
        assert_eq!(app.control_right_tabs.titles.len(), 2, "ADR-3:右 tab 2 个(对话/flow,属性走 props)");
        assert_eq!(app.control_right_tabs.active, 0, "默认对话 tab");
        app.control_right_tabs.next();
        assert_eq!(app.control_right_tabs.active, 1, "next → flow");
        app.control_right_tabs.next();
        assert_eq!(app.control_right_tabs.active, 0, "next 循环回 对话");
        app.control_right_tabs.prev();
        assert_eq!(app.control_right_tabs.active, 1, "prev → flow");
        app.control_right_tabs.select(1);
        assert_eq!(app.control_right_tabs.active, 1);
    }

    /// [` ` / `]` 键(normal 模式)切右 tab。
    #[test]
    fn control_right_tab_keys() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = false; // normal 模式快捷键生效
        app.handle_base_key(&KeyEvent::new(KeyCode::Char(']'), crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.control_right_tabs.active, 1, "] → flow");
        app.handle_base_key(&KeyEvent::new(KeyCode::Char('['), crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.control_right_tabs.active, 0, "[ → 对话");
    }

    /// ADR-4:色块组标签点击(clickmap id 200+group_idx)→ toggle 折叠/展开(非跳转)。
    #[test]
    fn control_group_tag_click_toggles() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.flat = vec![
            Session { harness_type: "claude-code".into(), session_id: "cc-1".into(), harness_id: "h1".into(), cwd: None, running: false },
            Session { harness_type: "claude-code".into(), session_id: "cc-2".into(), harness_id: "h2".into(), cwd: None, running: false },
            Session { harness_type: "openclaw".into(), session_id: "oc-1".into(), harness_id: "h3".into(), cwd: None, running: false },
        ];
        app.control_groups = vec!["claude-code".into(), "openclaw".into()];
        app.cursor = 0;
        assert!(app.control_collapsed.is_empty(), "默认全展开");
        // 点击 openclaw 色块(id = 200 + 1)→ 折叠 openclaw 组。
        app.clickmap.clear();
        app.clickmap.register(Rect::new(0, 0, 2, 1), 201);
        let m = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 1, row: 0,
            modifiers: crossterm::event::KeyModifiers::empty(),
        };
        app.handle_base_mouse(&m);
        assert!(app.control_collapsed.contains("openclaw"), "点色块 → openclaw 折叠");
        assert_eq!(app.cursor, 0, "ADR-4:toggle 不再移动 cursor(仅折叠态)");
        // 再点一次 → 展开(移除)。
        app.handle_base_mouse(&m);
        assert!(!app.control_collapsed.contains("openclaw"), "再点 → 展开");
    }

    // ── 输入栏修复自检(ADR:backspace/enter/scroll)──────────────────────

    /// Backspace 在 Control 输入栏删末字符(修"backspace 无效")。
    #[test]
    fn control_backspace_pops_turn_msg() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        // ADR-1:insert 模式输入走 textarea;turn_msg 是同步镜像。
        app.textarea.set_text("abc");
        app.turn_msg = "abc".to_string();
        app.handle_base_key(&KeyEvent::new(KeyCode::Backspace, crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.turn_msg, "ab");
        assert_eq!(app.textarea.text(), "ab");
        // 非 Control panel:Backspace 不影响 turn_msg。
        app.panel = Panel::Flows;
        app.handle_base_key(&KeyEvent::new(KeyCode::Backspace, crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.turn_msg, "ab", "Backspace outside Control must not edit turn_msg");
    }

    /// Enter 在 Control 输入态(focus≠按钮)触发 turn → 发送后清空输入栏(修"enter 无效")。
    /// 测试环境 orche 离线,trigger_turn 返 None,但 do_turn 仍清 turn_msg。
    #[test]
    fn control_enter_typing_sends_and_clears() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.focus = FocusTarget::TabBar; // 输入态:焦点不在按钮
        app.textarea.set_text("hi");
        app.turn_msg = "hi".to_string();
        app.handle_base_key(&KeyEvent::new(KeyCode::Enter, crossterm::event::KeyModifiers::empty()));
        assert!(app.turn_msg.is_empty(), "Enter in typing mode should send + clear turn_msg");
        assert!(app.textarea.text().is_empty());
    }

    /// insert 模式:被绑定的字母(p/h/t...)进 turn_msg,不触快捷键;Esc 退到 normal 后才触发。
    /// 修"输入栏 p 键弹 help":p 应打字而非弹窗。
    #[test]
    fn control_insert_mode_types_bound_letters() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = true;
        app.turn_msg.clear();
        // insert 模式:p 打字(不弹 help)
        app.handle_base_key(&KeyEvent::new(KeyCode::Char('p'), crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.turn_msg, "p");
        assert!(app.popups.is_empty(), "insert 模式 p 不应弹 help");
        // h 同理打字
        app.handle_base_key(&KeyEvent::new(KeyCode::Char('h'), crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.turn_msg, "ph");
        // Esc 退出 insert → p 弹 help
        app.handle_base_key(&KeyEvent::new(KeyCode::Esc, crossterm::event::KeyModifiers::empty()));
        assert!(!app.insert_mode);
        app.handle_base_key(&KeyEvent::new(KeyCode::Char('p'), crossterm::event::KeyModifiers::empty()));
        assert!(app.popups.iter().any(|x| x.id == "help"), "normal 模式 p 应弹 help");
        // i 重新进入 insert
        app.handle_base_key(&KeyEvent::new(KeyCode::Char('i'), crossterm::event::KeyModifiers::empty()));
        assert!(app.insert_mode, "i 重新进入 insert 模式");
    }

    // ── ADR-1/ADR-3/ADR-7 输入 UX 自检(节点 A 新增)─────────────────────

    /// ADR-7:Enter 发送 turn 后 push 进历史;↑ 翻上一条回填;↓ 翻过末条清空。
    #[test]
    fn input_history_nav_up_down() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = true;
        // ADR-1:insert 模式输入走 textarea;Enter 读 textarea.text() 发送 + clear。
        app.textarea.set_text("first");
        app.turn_msg = "first".into();
        app.handle_base_key(&KeyEvent::new(KeyCode::Enter, crossterm::event::KeyModifiers::empty()));
        app.textarea.set_text("second");
        app.turn_msg = "second".into();
        app.handle_base_key(&KeyEvent::new(KeyCode::Enter, crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.input_history, vec!["first".to_string(), "second".to_string()]);
        app.handle_base_key(&KeyEvent::new(KeyCode::Up, crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.turn_msg, "second");
        app.handle_base_key(&KeyEvent::new(KeyCode::Up, crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.turn_msg, "first");
        app.handle_base_key(&KeyEvent::new(KeyCode::Up, crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.turn_msg, "first");
        app.handle_base_key(&KeyEvent::new(KeyCode::Down, crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.turn_msg, "second");
        app.handle_base_key(&KeyEvent::new(KeyCode::Down, crossterm::event::KeyModifiers::empty()));
        assert!(app.turn_msg.is_empty(), "↓ 过末条 → 清空(写新输入)");
    }

    /// ADR-7:@ 触 mention popup;Esc 关 popup。
    #[test]
    fn at_char_triggers_mention_popup() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = true;
        app.turn_msg.clear();
        assert!(!app.mentions_open);
        app.handle_base_key(&KeyEvent::new(KeyCode::Char('@'), crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.turn_msg, "@");
        assert!(app.mentions_open, "@ → mention popup 开");
        app.handle_base_key(&KeyEvent::new(KeyCode::Esc, crossterm::event::KeyModifiers::empty()));
        assert!(!app.mentions_open);
    }

    /// IT5 ②:Enter 以 Char('\r') / Char('\n') 到达时也触发 Send(不被 textarea 当普通字符插入)。
    /// 发送成功的判据:textarea + turn_msg 被清(do_turn 尾部 clear);若当普通字符插入,
    /// textarea 会含 '\r'/文本未清。turn_status 取决于 orche 在线与否,不强断言。
    #[test]
    fn enter_as_char_cr_triggers_send() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = true;
        app.textarea.set_text("hello");
        assert_eq!(app.textarea.text(), "hello");
        // 终端把 Enter 发成 Char('\r')。
        app.handle_base_key(&KeyEvent::new(KeyCode::Char('\r'), crossterm::event::KeyModifiers::empty()));
        assert!(app.textarea.text().is_empty(), "Char('\\r') → 发送后 textarea 清空(非插入 '\\r')");
        assert!(app.turn_msg.is_empty(), "Char('\\r') → turn_msg 清空(do_turn 执行)");
    }

    /// IT5 ②:Char('\n') 同理触发 Send。
    #[test]
    fn enter_as_char_lf_triggers_send() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = true;
        app.textarea.set_text("world");
        app.handle_base_key(&KeyEvent::new(KeyCode::Char('\n'), crossterm::event::KeyModifiers::empty()));
        assert!(app.textarea.text().is_empty(), "Char('\\n') → 发送后 textarea 清空(非插入 '\\n')");
        assert!(app.turn_msg.is_empty(), "Char('\\n') → turn_msg 清空(do_turn 执行)");
    }

    /// ADR-3:×(clickmap id999)→ quit_requested=true;handle() 返 true 退出。
    #[test]
    fn quit_button_sets_quit_requested() {
        let mut app = App::new(crate::kitty::detect());
        app.clickmap.clear();
        app.clickmap.register(Rect::new(0, 0, 2, 1), 999);
        let m = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 1, row: 0,
            modifiers: crossterm::event::KeyModifiers::empty(),
        };
        let quit = app.handle(&crate::events::AppEvent::Mouse(m));
        assert!(app.quit_requested, "× → quit_requested=true");
        assert!(quit, "handle 返 true(run loop 退出)");
    }

    /// ADR-3:i(clickmap id998)→ open_help(全局 help;props 内容并入 help 段)。
    #[test]
    fn info_button_opens_help_popup() {
        let mut app = App::new(crate::kitty::detect());
        app.clickmap.clear();
        app.clickmap.register(Rect::new(0, 0, 2, 1), 998);
        let m = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 1, row: 0,
            modifiers: crossterm::event::KeyModifiers::empty(),
        };
        app.handle_base_mouse(&m);
        assert!(app.popups.iter().any(|x| x.id == "help"), "info(998) → help 弹窗入栈");
    }

    #[test]
    fn ctx_outline_cc_shows_fork_hides_reconnect() {
        let mut app = App::new(crate::kitty::detect());
        app.flat = vec![Session {
            harness_type: "claude-code".into(), session_id: "abc".into(), harness_id: "h".into(),
            cwd: None, running: false,
        }];
        app.cursor = 0;
        app.open_context_menu((1, 1), RightClickTarget::OutlineSession(0));
        let labels: Vec<&str> = app.context_menu.as_ref().unwrap().items.iter().map(|(l, _)| l.as_str()).collect();
        assert!(labels.contains(&"Fork"), "cc 显 Fork: {:?}", labels);
        assert!(!labels.contains(&"Reconnect"), "cc 隐 Reconnect");
    }

    #[test]
    fn ctx_outline_claw_shows_reconnect_hides_fork() {
        let mut app = App::new(crate::kitty::detect());
        app.flat = vec![Session {
            harness_type: "openclaw".into(), session_id: "agent:main:main".into(), harness_id: "h".into(),
            cwd: None, running: false,
        }];
        app.cursor = 0;
        app.open_context_menu((1, 1), RightClickTarget::OutlineSession(0));
        let labels: Vec<&str> = app.context_menu.as_ref().unwrap().items.iter().map(|(l, _)| l.as_str()).collect();
        assert!(labels.contains(&"Reconnect"), "claw 显 Reconnect: {:?}", labels);
        assert!(!labels.contains(&"Fork"), "claw 隐 Fork");
    }

    #[test]
    fn ctx_esc_closes_menu() {
        let mut app = App::new(crate::kitty::detect());
        app.open_context_menu((1, 1), RightClickTarget::OutlineBlank);
        assert!(app.context_menu.is_some());
        app.handle_context_menu_key(&KeyEvent::new(KeyCode::Esc, crossterm::event::KeyModifiers::empty()));
        assert!(app.context_menu.is_none(), "Esc 关 ctx");
        assert!(!app.popups.iter().any(|p| p.id == "ctx"));
    }

    #[test]
    fn ctx_jk_moves_selected() {
        let mut app = App::new(crate::kitty::detect());
        app.open_context_menu((1, 1), RightClickTarget::OutlineBlank); // 2 items
        let n = app.context_menu.as_ref().unwrap().items.len();
        app.handle_context_menu_key(&KeyEvent::new(KeyCode::Char('j'), crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.context_menu.as_ref().unwrap().selected, 1, "j 下移");
        app.handle_context_menu_key(&KeyEvent::new(KeyCode::Char('j'), crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.context_menu.as_ref().unwrap().selected, n - 1, "j clamp 末尾");
        app.handle_context_menu_key(&KeyEvent::new(KeyCode::Char('k'), crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.context_menu.as_ref().unwrap().selected, 0, "k 上移");
    }

    /// ADR-4:toggle_group 增删 control_collapsed(折叠/展开)。
    #[test]
    fn toggle_group_flips_collapsed() {
        let mut app = App::new(crate::kitty::detect());
        assert!(!app.control_collapsed.contains("g1"));
        app.toggle_group("g1");
        assert!(app.control_collapsed.contains("g1"), "首次 toggle → 折叠");
        app.toggle_group("g1");
        assert!(!app.control_collapsed.contains("g1"), "再 toggle → 展开");
    }

    // ── IT2 节点 C 自测:new/delete/fork 键路由 + 弹窗态 ──────────────────

    /// n(normal 模式,Control)→ 开 new 弹窗(id="new" 入栈,new_popup=None 待选类型)。
    #[test]
    fn normal_n_opens_new_popup() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = false;
        app.handle_base_key(&KeyEvent::new(KeyCode::Char('n'), crossterm::event::KeyModifiers::empty()));
        assert!(app.popups.iter().any(|p| p.id == "new"), "n → new 弹窗入栈");
        // 默认 claw + 预 fetch:首次 render body 即含全部候选(修 tui-popup area 首次
        // 固定——初始小 body 致 area 小,后续选 claw body 增 area 不扩只显顶部 claw-02)
        assert_eq!(app.new_popup, Some(NewKind::Claw), "默认 claw");
        assert!(!app.new_candidates.is_empty(), "预 fetch(失败 fallback main)");
    }

    /// n(insert 模式)→ 打字进 textarea,不开弹窗(n 被顶 capture 消费)。
    #[test]
    fn insert_n_types_not_opens_popup() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = true;
        app.handle_base_key(&KeyEvent::new(KeyCode::Char('n'), crossterm::event::KeyModifiers::empty()));
        assert!(app.popups.is_empty(), "insert 模式 n 不应弹窗");
        assert_eq!(app.turn_msg, "n");
    }

    /// d(normal,Control)有光标 session → 开 delete 确认弹窗(delete_popup=Some(sid))。
    #[test]
    fn normal_d_opens_delete_confirm() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = false;
        app.flat = vec![Session {
            harness_type: "claude-code".into(), session_id: "abc".into(), harness_id: "h".into(), cwd: None, running: false,
        }];
        app.cursor = 0;
        app.handle_base_key(&KeyEvent::new(KeyCode::Char('d'), crossterm::event::KeyModifiers::empty()));
        assert!(app.popups.iter().any(|p| p.id == "delete"), "d → delete 弹窗入栈");
        assert_eq!(app.delete_popup.as_deref(), Some("abc"));
    }

    /// r(normal)cc cursor → "cc 无状态无需重连"(reconnect 仅 claw 有意义)。
    #[test]
    fn r_key_cc_shows_no_reconnect() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = false;
        app.flat = vec![Session {
            harness_type: "claude-code".into(), session_id: "abc".into(), harness_id: "h".into(),
            cwd: None, running: false,
        }];
        app.cursor = 0;
        app.handle_base_key(&KeyEvent::new(KeyCode::Char('r'), crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.turn_status.as_deref(), Some("cc 无状态无需重连"));
    }

    /// r(normal)claw cursor orche 离线 → 重连失败文案(测试环境 orche 不可达)。
    #[test]
    fn r_key_claw_offline_reconnect_fail() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = false;
        app.flat = vec![Session {
            harness_type: "openclaw".into(), session_id: "agent:main:main".into(), harness_id: "h".into(),
            cwd: None, running: false,
        }];
        app.cursor = 0;
        app.handle_base_key(&KeyEvent::new(KeyCode::Char('r'), crossterm::event::KeyModifiers::empty()));
        let st = app.turn_status.clone().unwrap_or_default();
        assert!(st.contains("重连"), "claw r → 触发重连(turn_status 含'重连'),实际: {}", st);
    }

    /// Ctrl+R(insert_mode 输入栏)claw cursor orche 离线 → 重连失败。
    /// 验证 insert 内键盘触发重连(不需 esc 切 normal)。
    #[test]
    fn ctrl_r_insert_mode_triggers_reconnect() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = true;
        app.flat = vec![Session {
            harness_type: "openclaw".into(), session_id: "agent:main:main".into(), harness_id: "h".into(),
            cwd: None, running: false,
        }];
        app.cursor = 0;
        app.handle_base_key(&KeyEvent::new(KeyCode::Char('r'), crossterm::event::KeyModifiers::CONTROL));
        let st = app.turn_status.clone().unwrap_or_default();
        assert!(st.contains("重连"), "insert Ctrl+R claw → 触发重连,实际: {}", st);
    }

    /// Ctrl+C(insert 无选区)清空输入区。
    #[test]
    fn ctrl_c_clears_input() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = true;
        app.textarea.set_text("hello world");
        app.turn_msg = "hello world".to_string();
        app.handle_base_key(&KeyEvent::new(KeyCode::Char('c'), crossterm::event::KeyModifiers::CONTROL));
        assert!(app.textarea.is_empty(), "Ctrl+C 清空: {}", app.textarea.text());
        assert!(app.turn_msg.is_empty());
    }

    /// Ctrl+Backspace(insert)删一个词。
    #[test]
    fn ctrl_bs_deletes_word() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = true;
        app.textarea.set_text("hello world");
        app.textarea.set_cursor(11);
        app.handle_base_key(&KeyEvent::new(KeyCode::Backspace, crossterm::event::KeyModifiers::CONTROL));
        assert_eq!(app.textarea.text(), "hello ", "Ctrl+BS 删词: {}", app.textarea.text());
    }

    /// r(normal)无 cursor session → 兜底普通刷新(不 panic)。
    #[test]
    fn r_key_no_cursor_falls_back_to_refresh() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = false;
        app.flat = vec![];
        // 不 panic 即通过(handle_base_key 返 false)。
        let quit = app.handle_base_key(&KeyEvent::new(KeyCode::Char('r'), crossterm::event::KeyModifiers::empty()));
        assert!(!quit);
    }

    /// new 弹窗 picker:c=claw 选(不触网,orche 离线 → 候选默认 ["main"])。
    #[test]
    fn new_popup_claw_pick_sets_candidates() {
        let mut app = App::new(crate::kitty::detect());
        app.open_new_popup();
        // 测试环境 orche 离线,fetch_claw_agents 兜底返 ["main"]。
        let handled = app.handle_popup_key(&KeyEvent::new(
            KeyCode::Char('c'), crossterm::event::KeyModifiers::empty()));
        assert!(handled, "new 弹窗 c 被消费");
        assert_eq!(app.new_popup, Some(NewKind::Claw));
        assert!(!app.new_candidates.is_empty(), "fetch 兜底至少 1 候选");
    }

    /// new 弹窗 j/k 移动选中索引(clamp 边界)。
    #[test]
    fn new_popup_jk_moves_picker() {
        let mut app = App::new(crate::kitty::detect());
        app.open_new_popup();
        app.new_popup = Some(NewKind::Claw);
        app.new_candidates = vec!["a".into(), "b".into(), "c".into()];
        app.new_idx = 0;
        app.handle_popup_key(&KeyEvent::new(KeyCode::Char('j'), crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.new_idx, 1);
        // k 回 0。
        app.handle_popup_key(&KeyEvent::new(KeyCode::Char('k'), crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.new_idx, 0);
        // k 不下溢(仍 0)。
        app.handle_popup_key(&KeyEvent::new(KeyCode::Char('k'), crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.new_idx, 0);
    }

    /// new 弹窗 esc 关弹窗 + 清 new_popup。
    #[test]
    fn new_popup_esc_closes() {
        let mut app = App::new(crate::kitty::detect());
        app.open_new_popup();
        assert!(app.popups.iter().any(|p| p.id == "new"));
        app.handle_popup_key(&KeyEvent::new(KeyCode::Esc, crossterm::event::KeyModifiers::empty()));
        assert!(!app.popups.iter().any(|p| p.id == "new"), "esc 关 new 弹窗");
        assert!(app.new_popup.is_none());
    }

    /// 回归 bug1「new-session 弹窗只显 claw-02」:tui-popup render_ref 首次按 body 固定 area,
    /// 之后用旧 area 不重算(tui-popup-0.5.1 popup.rs:143 `state.area.take()` 用 next.w/h)。
    /// 修复:open_new_popup 打开即 new_popup=Claw+fetch,首帧 body(update_action_popup_bodies
    /// 填)即含全部候选 → area 一次算大 → 全显。若 revert 回 None 态,首帧 body 仅 ~2 行 →
    /// area 小 → 后续切 claw body 增 area 不扩 → 只显顶部候选。此测试锚定 render 层 body
    /// 真全显(上方 normal_n/new_popup_claw_pick 只验 state 字段 new_popup==Claw,验不到 area)。
    #[test]
    fn new_popup_renders_all_candidates_not_truncated() {
        use ratatui::{backend::TestBackend, Terminal};
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        // 注入 >1 候选(测试环境 fetch_claw_agents 兜底 ["main"],无法暴露多候选截断)。
        let cands = vec!["claw-01".to_string(), "claw-02".to_string(), "claw-03".to_string()];
        app.new_candidates = cands.clone();
        app.open_new_popup(); // 修复态:new_popup=Claw(revert 为 None 则此测试失败)
        app.new_candidates = cands; // 覆盖 fetch 兜底,模拟 orche 在线返多 agent

        let mut term = Terminal::new(TestBackend::new(80, 24)).unwrap();
        term.draw(|f| crate::render::draw(f, &mut app)).unwrap();

        // 拼 buffer 为字符串(同 main.rs print_buffer):遍历每 cell.symbol()。
        let buf = term.backend().buffer();
        let mut s = String::new();
        for y in 0..buf.area.height {
            for x in 0..buf.area.width {
                s.push_str(&buf[(x, y)].symbol());
            }
            s.push('\n');
        }
        for c in &["claw-01", "claw-02", "claw-03"] {
            assert!(s.contains(c), "弹窗须显候选 {c}(回归:area 首次固定致只显顶部)");
        }
    }

    /// 回归 bug2「创建不新建」:claw session_key 须含唯一 conv(时间戳 hex),非固定
    /// `agent:<a>:main`。根因:orche routes.py 固定 main conv,同 agent 已存在 → exists
    /// 短路不新建;TUI create_session 不查 status → focus 旧 session(体感"创建不新建")。
    /// 修复:claw_session_key 生成 `agent:<a>:t<ts>`。真机复验(orche :8001):修复态 create
    /// 返 agent:main:t59c570(list 含,r 刷新 oc 12→14);旧态固定 main 则 exists 短路不新建。
    #[test]
    fn claw_session_key_unique_conv_not_main() {
        let k = claw_session_key("claw-02");
        assert!(k.starts_with("agent:claw-02:"), "格式 agent:<a>:<conv>: {}", k);
        // 核心:conv 不能固定 :main(否则 orche exists 短路,bug2 复发)。
        assert!(!k.ends_with(":main"), "conv 须唯一(非 main): {}", k);
        assert!(k.contains(":t"), "conv 含时间戳标识 t: {}", k);
        // agent_base 剥前缀:带 "agent:" 的取第 2 段作 base。
        let k2 = claw_session_key("agent:claw-03:main");
        assert!(k2.starts_with("agent:claw-03:t"), "剥前缀取 base: {}", k2);
    }

    /// delete 弹窗 N/esc 取消(不删,弹窗关)。
    #[test]
    fn delete_popup_n_cancels() {
        let mut app = App::new(crate::kitty::detect());
        app.delete_popup = Some("sid-x".to_string());
        app.open_popup(Popup::centered("delete", "del", vec![], 40, 5));
        app.handle_popup_key(&KeyEvent::new(KeyCode::Char('N'), crossterm::event::KeyModifiers::empty()));
        assert!(!app.popups.iter().any(|p| p.id == "delete"), "N 关 delete 弹窗");
        assert!(app.delete_popup.is_none(), "取消 → delete_popup 清空");
    }

    /// F(normal,Control)有光标 session → fork 调用(orche 离线 None → claw 提示 ADR-4)。
    #[test]
    fn normal_f_fork_offline_graceful() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = false;
        app.flat = vec![Session {
            harness_type: "openclaw".into(), session_id: "s1".into(), harness_id: "h".into(), cwd: None, running: false,
        }];
        app.cursor = 0;
        app.handle_base_key(&KeyEvent::new(KeyCode::Char('F'), crossterm::event::KeyModifiers::empty()));
        // openclaw→claw,orche 离线 → None → claw fork 暂不支持(ADR-4)提示。
        assert!(app.turn_status.as_deref().unwrap_or("").contains("fork"), "F 应设 turn_status 含 fork");
        assert!(app.popups.is_empty(), "F 不开弹窗");
    }

    /// clickmap id=300([+] new 按钮)鼠标点击 → 开 new 弹窗。
    #[test]
    fn new_button_click_opens_popup() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.clickmap.clear();
        app.clickmap.register(Rect::new(0, 0, 8, 1), 300);
        let m = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 2, row: 0,
            modifiers: crossterm::event::KeyModifiers::empty(),
        };
        app.handle_base_mouse(&m);
        assert!(app.popups.iter().any(|p| p.id == "new"), "id=300 → new 弹窗");
    }

    /// IT4 ②③:do_turn 发送后 chat_follow_tail=true 且 offset 滚底。
    /// do_turn 触发网络(fetch_current 等),ws=None/offline graceful,不影响 tail/scroll 副作用。
    #[test]
    fn do_turn_sets_tail_and_scrolls_bottom() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        // 预灌 chat 内容,offset 在顶。
        app.control_chat_scroll.set_content(vec![ratatui::text::Line::from("a"); 40]);
        app.control_chat_scroll.offset = 0;
        app.chat_follow_tail = false;
        app.turn_msg.clear();
        app.do_turn(false);
        assert!(app.chat_follow_tail, "do_turn sets tail=true");
        assert_eq!(app.control_chat_scroll.offset, 39, "scroll_to_bottom locks to total-1");
    }

    /// #2/#3/#6:cursor 切换(set_cursor_session 统一入口)重置对话视图状态。
    /// 切 session 时 follow_tail=true + scroll_to_bottom + textarea/turn_msg clear + cached_turn_lines None。
    /// 治:A 不追尾串到 B(#2)/ A 草稿发到 B(#3)/ A 滚位置切 B 再切回归零(#6)/ 旧缓存串显(#1)。
    #[test]
    fn cursor_switch_resets_view_state() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.flat = vec![
            Session { harness_type: "claude-code".into(), session_id: "s1".into(), harness_id: "h".into(), cwd: None, running: true },
            Session { harness_type: "claude-code".into(), session_id: "s2".into(), harness_id: "h".into(), cwd: None, running: true },
        ];
        app.cursor = 0;
        app.control_chat_scroll.set_content(vec![ratatui::text::Line::from("a"); 40]);
        app.control_chat_scroll.offset = 20;      // 旧 session 滚位置(#6)
        app.chat_follow_tail = false;             // 用户 PgUp 过(#2)
        app.textarea.insert_text("draft s1");     // 草稿(#3)
        app.turn_msg = "draft s1".to_string();
        app.cached_turn_lines = Some(("claude-code/s1".into(), None, vec![], 0));  // 旧缓存(#1)
        app.cursor_down();  // → set_cursor_session(1)
        assert_eq!(app.cursor, 1, "cursor moved to s2");
        assert!(app.chat_follow_tail, "#2 follow_tail reset on switch");
        assert_ne!(app.control_chat_scroll.offset, 20, "#6 offset not stale from prev session");
        assert_eq!(app.control_chat_scroll.offset, 39, "scroll_to_bottom locks current content tail");
        assert!(app.textarea.text().is_empty(), "#3 textarea cleared on switch");
        assert!(app.turn_msg.is_empty(), "#3 turn_msg cleared on switch");
        assert!(app.cached_turn_lines.is_none(), "#1 cache invalidated on switch");
    }

    /// #5:服务端 tick_started request[:500] 截断 → TUI 用 starts_with 匹配清 pending。
    /// pm(完整 600 字)starts_with req(前 500 字)= true → 清 pending。
    /// 旧 pm==req 精确匹配会失败 → spinner 永转(治 >500 字 prompt 场景)。
    #[test]
    fn drain_ws_long_msg_clears_pending_via_starts_with() {
        use crate::ws::{WsManager, WsMsg};
        use std::sync::mpsc;
        let (tx, rx) = mpsc::channel::<WsMsg>();
        let mut app = App::new(crate::kitty::detect());
        app.ws = Some(WsManager::mock(rx));
        let key = "claude-code/s1".to_string();
        let long_msg = "x".repeat(600);
        app.pending_turn = Some((key.clone(), long_msg.clone()));
        // observe 截断:request = msg[:500](services/orchestrator events.py:47 / observe events.py:87)。
        let mut data = HashMap::new();
        data.insert("request".to_string(), serde_json::Value::String(long_msg[..500].to_string()));
        let ev = ObserveEvent {
            event_type: "tick_started".to_string(),
            tick_id: "t1".to_string(),
            harness_id: "h".to_string(),
            data,
            event_id: "e1".to_string(),
        };
        tx.send(WsMsg::Event { key: key.clone(), ev }).unwrap();
        app.drain_ws();
        assert!(app.pending_turn.is_none(), "#5 starts_with 匹配截断 req 清 pending(治 >500 字 spinner 永转)");
    }

    /// #8:pending age >= 60s → Tick 兜底清(trigger 失败/WS 丢 tick_started 致 stale spinner 不永驻)。
    #[test]
    fn pending_timeout_clears_stale_spinner() {
        let mut app = App::new(crate::kitty::detect());
        app.pending_turn = Some(("agent-os-v2/s1".into(), "ping".into()));
        // 模拟 61s 前进入 pending(trigger 失败/WS 丢 → 永不自然清)。
        app.pending_since = Some(std::time::Instant::now().checked_sub(std::time::Duration::from_secs(61)).unwrap());
        app.handle(&crate::events::AppEvent::Tick);
        assert!(app.pending_turn.is_none(), "#8 age>=60s 兜底清 stale pending");
        assert!(app.pending_since.is_none(), "since 同步清");
        // 未超时(刚设)不清 —— 正常 turn 不被误清。
        app.pending_turn = Some(("agent-os-v2/s1".into(), "ping".into()));
        app.pending_since = Some(std::time::Instant::now());
        app.handle(&crate::events::AppEvent::Tick);
        assert!(app.pending_turn.is_some(), "未超时(刚设)不清,保护正常 turn");
    }

    /// IT4 ③:PgUp 脱离跟尾(tail=false),PgDn 近底重新跟尾(tail=true)。normal 模式。
    #[test]
    fn pageup_breaks_tail_pagedown_near_bottom_refollows() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = false;
        app.control_chat_scroll.set_content(vec![ratatui::text::Line::from("a"); 100]);
        app.control_chat_scroll.scroll_to_bottom(); // 起点:在底,tail=true
        assert!(app.chat_follow_tail);
        // PgUp:脱离跟尾。
        app.handle_base_key(&KeyEvent::new(KeyCode::PageUp, crosysterm_keymods()));
        assert!(!app.chat_follow_tail, "PgUp breaks tail");
        assert!(app.control_chat_scroll.offset < 99, "PgUp moved offset up");
        // 手动滚回近底后 PgDn:重新跟尾。
        app.control_chat_scroll.scroll_to_bottom();
        app.handle_base_key(&KeyEvent::new(KeyCode::PageDown, crosysterm_keymods()));
        assert!(app.chat_follow_tail, "PgDn near bottom refollows tail");
    }

    /// IT4 ③:insert 模式 PgUp/PgDn 也能滚 chat(打字时不被 _ 分支吞)。
    #[test]
    fn insert_mode_pageup_pagedown_scroll_chat() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = true;
        app.control_chat_scroll.set_content(vec![ratatui::text::Line::from("a"); 100]);
        app.control_chat_scroll.scroll_to_bottom();
        let before = app.control_chat_scroll.offset;
        app.handle_base_key(&KeyEvent::new(KeyCode::PageUp, crosysterm_keymods()));
        assert!(app.control_chat_scroll.offset < before, "insert PgUp scrolls chat up");
        assert!(!app.chat_follow_tail);
    }

    fn crosysterm_keymods() -> crossterm::event::KeyModifiers {
        crossterm::event::KeyModifiers::empty()
    }

    // ── IT7 ④ 粘贴 ──────────────────────────────────────────────────
    /// AppEvent::Paste 在 Control insert 模式 → textarea 插入 + turn_msg 同步。
    #[test]
    fn paste_into_textarea_control_insert() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = true;
        app.handle(&crate::events::AppEvent::Paste("pasted text".into()));
        assert_eq!(app.textarea.text(), "pasted text");
        assert_eq!(app.turn_msg, "pasted text");
    }

    /// AppEvent::Paste 非 Control panel → 不插(忽略)。
    #[test]
    fn paste_ignored_outside_control() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Flows;
        app.handle(&crate::events::AppEvent::Paste("x".into()));
        assert_eq!(app.textarea.text(), "", "Home panel ignores paste");
    }

    /// new 弹窗 cc 模式开 → Paste 粘进 new_cc_input。
    #[test]
    fn paste_into_new_cc_input() {
        let mut app = App::new(crate::kitty::detect());
        app.open_new_popup();
        app.new_popup = Some(NewKind::Cc);
        app.handle(&crate::events::AppEvent::Paste("/home/x".into()));
        assert_eq!(app.new_cc_input, "/home/x");
    }

    /// new 弹窗 o=agent-os-v2 选(ADR-3:拉 registry agents,orche 离线时 graceful 空)。
    #[test]
    fn new_popup_ao_pick_fetches_agents_or_empty() {
        let mut app = App::new(crate::kitty::detect());
        app.open_new_popup();
        let handled = app.handle_popup_key(&KeyEvent::new(
            KeyCode::Char('o'), crossterm::event::KeyModifiers::empty()));
        assert!(handled, "new 弹窗 o 被消费");
        assert_eq!(app.new_popup, Some(NewKind::AoV2));
        // 单测环境 orche :8001 未启动 → fetch_ao2_agents 返空(graceful,不 panic)。
        // 真 runtime orche 在线 → new_ao2_agents 含 registry agents(help default)。
        assert!(app.new_ao2_agents.is_empty(), "orche 离线时 ao 候选空(graceful)");
    }

    // ── IT7 ② 弹窗可点击 ────────────────────────────────────────────
    /// new 弹窗 popup_clickmap hit 700/701/790/791/710+i → 对应操作。
    #[test]
    fn new_popup_click_selects_harness() {
        let mut app = App::new(crate::kitty::detect());
        app.open_new_popup();
        // 700 = 选 claw。
        app.handle_new_popup_click(700);
        assert_eq!(app.new_popup, Some(NewKind::Claw));
        // 701 = 选 cc。
        app.handle_new_popup_click(701);
        assert_eq!(app.new_popup, Some(NewKind::Cc));
        // 702 = 选 agent-os-v2(ADR-3:拉 registry agents;orche 离线→空)。
        app.handle_new_popup_click(702);
        assert_eq!(app.new_popup, Some(NewKind::AoV2));
        assert!(app.new_ao2_agents.is_empty(), "orche 离线→ao 候选空");
    }

    /// ADR-3:AoV2 picker 候选行点击(730+i)更新 new_idx;候选空时显提示不 POST。
    #[test]
    fn new_popup_aov2_picker_click_and_empty_guard() {
        let mut app = App::new(crate::kitty::detect());
        app.open_new_popup();
        app.new_popup = Some(NewKind::AoV2);
        // 注入 2 个假候选(orche 离线时 fetch 返空,手动填测 picker 逻辑)。
        app.new_ao2_agents = vec![
            Ao2Agent { id: "main".into(), name: "Main".into(), default: true },
            Ao2Agent { id: "help".into(), name: "向导".into(), default: false },
        ];
        app.new_idx = 0; // default 预选 main
        // 点 731 = 第 2 行(help)→ new_idx 切到 1。
        app.handle_new_popup_click(731);
        assert_eq!(app.new_idx, 1, "730+i 点击切换 AoV2 picker 选中");
        // 点越界 732 → 不崩(idx 不变)。
        app.handle_new_popup_click(732);
        assert_eq!(app.new_idx, 1, "越界 730+i 不改 idx");
        // 候选空时 do_new_session 显提示,不 POST(不创建 session)。
        app.new_ao2_agents.clear();
        let before = app.flat.len();
        app.do_new_session();
        assert!(app.turn_status.as_deref().unwrap_or("").contains("无 agent 候选"),
            "候选空→显提示不 POST");
        assert_eq!(app.flat.len(), before, "候选空→不创建 session");
    }

    /// ADR-3 regression:AoV2 picker 键盘 j/Down 必须能动(原 bug:new_candidates
    /// 空致条件永假,j 失效只能鼠标选)。注入 2 候选,j 0→1,k 1→0。
    #[test]
    fn new_popup_aov2_jk_moves_picker() {
        let mut app = App::new(crate::kitty::detect());
        app.open_new_popup();
        app.new_popup = Some(NewKind::AoV2);
        app.new_ao2_agents = vec![
            Ao2Agent { id: "main".into(), name: "Main".into(), default: true },
            Ao2Agent { id: "help".into(), name: "向导".into(), default: false },
        ];
        app.new_idx = 0;
        // j → idx 1(能下移;旧 bug 卡在 0)。
        app.handle_popup_key(&KeyEvent::new(
            KeyCode::Char('j'), crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.new_idx, 1, "AoV2 picker j 必须下移(原 bug 失效)");
        // 再 j → 越界不动(只有 2 候选,idx 1 已末)。
        app.handle_popup_key(&KeyEvent::new(
            KeyCode::Char('j'), crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.new_idx, 1, "末候选 j 越界不动");
        // k → idx 0(上移)。
        app.handle_popup_key(&KeyEvent::new(
            KeyCode::Char('k'), crossterm::event::KeyModifiers::empty()));
        assert_eq!(app.new_idx, 0, "AoV2 picker k 上移");
    }

    #[test]
    fn new_popup_click_cancel_closes() {
        let mut app = App::new(crate::kitty::detect());
        app.open_new_popup();
        assert!(app.popups.iter().any(|p| p.id == "new"));
        app.handle_new_popup_click(791); // Cancel
        assert!(!app.popups.iter().any(|p| p.id == "new"), "791 关 new 弹窗");
        assert!(app.new_popup.is_none());
    }

    #[test]
    fn new_popup_click_candidate_sets_idx() {
        let mut app = App::new(crate::kitty::detect());
        app.open_new_popup();
        app.new_popup = Some(NewKind::Claw);
        app.new_candidates = vec!["a".into(), "b".into(), "c".into()];
        app.new_idx = 0;
        // 710 + 2 = 第三个候选 → idx 2。
        app.handle_new_popup_click(712);
        assert_eq!(app.new_idx, 2);
        // 越界 id(710+5)→ 不变。
        app.handle_new_popup_click(715);
        assert_eq!(app.new_idx, 2, "out-of-range idx ignored");
    }

    /// modal 激活时鼠标命中 popup_clickmap → handle_popup_mouse 路由到点击操作。
    #[test]
    fn new_popup_mouse_left_click_routes_to_clickmap() {
        let mut app = App::new(crate::kitty::detect());
        app.open_new_popup();
        // 模拟 render 注册:在 (0,0) 注册 701(cc 按钮)。
        app.popup_clickmap.register(Rect::new(0, 0, 6, 1), 701);
        let m = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 2, row: 0,
            modifiers: crossterm::event::KeyModifiers::empty(),
        };
        assert!(app.modal_active());
        app.handle_popup_mouse(&m);
        assert_eq!(app.new_popup, Some(NewKind::Cc), "点击 701 → 选 cc");
    }

    // ── IT7 ① textarea 多行 Up/Down 在 handle_base_key 委托 ──────────
    /// 多行 textarea:Up/Down 先走 textarea 行移(不翻历史)。
    #[test]
    fn multiline_up_down_delegates_to_textarea() {
        let mut app = App::new(crate::kitty::detect());
        app.panel = Panel::Control;
        app.insert_mode = true;
        app.textarea.set_text("ab\ncd");
        app.turn_msg = "ab\ncd".into();
        // cursor 在末尾(cd 尾)→ Down 返 None → 翻历史(空历史 → no-op,cursor 不变)。
        app.handle_base_key(&KeyEvent::new(KeyCode::Down, crosysterm_keymods()));
        assert_eq!(app.textarea.cursor(), app.textarea.text().len(), "Down on last line keeps cursor");
        // Up → codex 保列:cd 行 col 2 → ab 行 col 2 = ab 末(byte 2,非旧行首语义)。
        app.handle_base_key(&KeyEvent::new(KeyCode::Up, crosysterm_keymods()));
        assert_eq!(app.textarea.cursor(), 2, "Up 保列移到上一行 ab 末");
    }
}

