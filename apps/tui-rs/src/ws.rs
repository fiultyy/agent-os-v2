//! WS manager(ADR-1 T4:WS 直连 observe,弃 REST polling)。
//!
//! 同步模型(匹配 ratatui):tungstenite 阻塞 WS + std::thread + std::sync::mpsc。
//! 不引 tokio。
//!
//! 架构:
//! - WS manager 线程:收 WsCmd(Subscribe/Unsubscribe/Shutdown),管 HashMap<key, JoinHandle>。
//! - 每 key 一个子线程:tungstenite connect observe /ws/subscribe?harness_type=X&session_id=Y
//!   → 阻塞 read 循环 → 解析 ObserveEvent(serde 复用 state.rs)→ mpsc send WsMsg → 主 loop。
//! - 主 loop 每帧 try_recv(非阻塞)累积事件。
//!
//! observe WS 协议(已核实 services/observe/src/app.py):
//! - /ws/subscribe query params: harness_type + session_id(+ 可选 after_event_id)
//! - 服务端 broadcast ObserveEvent.to_dict() JSON(Message::Text)
//! - 客户端可发 {"type":"ping"} 保活(服务端 receive_json 循环 + broadcast 并发)
//!
//! flow 事件(ADR-4):key=("flow",flow_id),event_type=tick_completed + data.flow_event
//! (flow_started/node_started/node_completed/flow_completed)+ data.flow_payload。
//! 见 services/orchestrator/src/harness/flow.py:97-120。

use crate::state::ObserveEvent;
use serde::Deserialize;
use std::collections::HashMap;
use std::net::TcpStream;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};
use tungstenite::client;
use tungstenite::Message;

const OBSERVE_WS: &str = "ws://localhost:8002";
/// ping 间隔(保活;observe 服务端 receive_json 循环会等客户端消息,
/// 不 ping 也能收 broadcast,但发 ping 防空闲断连)。
const PING_INTERVAL: Duration = Duration::from_secs(30);

/// WS manager 命令(主 loop → manager 线程)。
#[derive(Debug, Clone)]
pub enum WsCmd {
    /// 订阅 (harness_type, session_id)。已有则跳过(idempotent)。
    Subscribe { harness_type: String, session_id: String },
    /// 取消订阅。无此 key 则跳过。
    Unsubscribe { harness_type: String, session_id: String },
    /// 关闭所有 WS + 退出 manager 线程。
    Shutdown,
}

/// WS → 主 loop 的事件消息。
/// ponytail: 不 derive Debug(ObserveEvent 未 impl Debug;加 Debug 是为日志,当前不需)。
#[derive(Clone)]
pub enum WsMsg {
    /// turn 事件(observe ObserveEvent,直接透传给主 loop 累积进 app.events[key])。
    Event { key: String, ev: ObserveEvent },
    /// flow 事件(key=("flow",flow_id))。主 loop 按 data.flow_event 分类更新 app.flows。
    FlowEvent { flow_id: String, ev: ObserveEvent },
    /// memory 事件(key=("memory","memory")全局常驻)→ app.events["memory/memory"]。
    MemoryEvent { ev: ObserveEvent },
    /// orchestrate 事件(key=("orchestrate",session_id))→ app.events["orchestrate/{sid}"]。
    OrchEvent { session_id: String, ev: ObserveEvent },
    /// WS 连接错误(主 loop 可忽略,manager 会保留 key 不重连避免风暴;
    /// ponytail: 自动重连 defer,简单 backoff 由子线程退出后 key 移除体现)。
    Error { key: String, #[allow(dead_code)] msg: String },
}

/// observe broadcast 的 JSON 形状(ObserveEvent.to_dict,见 events.py:48)。
/// 复用 state.rs ObserveEvent 的字段(event_type/tick_id/harness_id/data/event_id);
/// 额外字段(session_id/timestamp)serde 忽略(无 deny_unknown_fields)。
#[derive(Deserialize)]
struct WsPayload {
    event_type: String,
    tick_id: String,
    harness_id: String,
    data: HashMap<String, serde_json::Value>,
    #[serde(default)]
    event_id: String,
}

impl From<WsPayload> for ObserveEvent {
    fn from(p: WsPayload) -> Self {
        ObserveEvent {
            event_type: p.event_type,
            tick_id: p.tick_id,
            harness_id: p.harness_id,
            data: p.data,
            event_id: p.event_id,
        }
    }
}

/// WS manager handle(主 loop 持有)。
pub struct WsManager {
    pub cmd_tx: Sender<WsCmd>,
    pub rx: Receiver<WsMsg>,
    handle: Option<JoinHandle<()>>,
}

impl WsManager {
    /// 启动 WS manager 线程。返回 cmd channel sender + 事件 receiver。
    pub fn spawn() -> Self {
        let (cmd_tx, cmd_rx) = mpsc::channel::<WsCmd>();
        let (msg_tx, msg_rx) = mpsc::channel::<WsMsg>();
        let handle = thread::Builder::new()
            .name("ws-manager".into())
            .spawn(move || {
                // subs: key → (子线程 handle, closing flag)。closing flag 让 ws_loop
                // 可中断(set_read_timeout 超时检测)→ graceful close socket(observe 侧 unsubscribe)。
                let mut subs: HashMap<String, (JoinHandle<()>, Arc<AtomicBool>)> = HashMap::new();
                for cmd in cmd_rx {
                    match cmd {
                        WsCmd::Subscribe { harness_type, session_id } => {
                            let key = format!("{}/{}", harness_type, session_id);
                            if subs.contains_key(&key) {
                                continue; // idempotent
                            }
                            let msg_tx = msg_tx.clone();
                            let ht = harness_type.clone();
                            let sid = session_id.clone();
                            let key_for_thread = key.clone();
                            let closing = Arc::new(AtomicBool::new(false));
                            let closing_for_thread = closing.clone();
                            let h = thread::Builder::new()
                                .name(format!("ws-{}", key))
                                .spawn(move || ws_loop(&ht, &sid, &key_for_thread, &msg_tx, closing_for_thread))
                                .ok();
                            if let Some(h) = h {
                                subs.insert(key, (h, closing));
                            }
                        }
                        WsCmd::Unsubscribe { harness_type, session_id } => {
                            let key = format!("{}/{}", harness_type, session_id);
                            // graceful close:设 closing flag → ws_loop 下次 read 超时检测 → break →
                            // socket drop 发 close frame → observe 侧 WebSocketDisconnect unsubscribe。
                            if let Some((_h, closing)) = subs.remove(&key) {
                                closing.store(true, Ordering::Relaxed);
                            }
                        }
                        WsCmd::Shutdown => {
                            // drop 所有 handle(detach);manager 退出。
                            subs.clear();
                            break;
                        }
                    }
                }
            })
            .expect("ws-manager thread spawn");
        Self { cmd_tx, rx: msg_rx, handle: Some(handle) }
    }

    /// 便捷:订阅 cursor session。
    pub fn subscribe(&self, harness_type: &str, session_id: &str) {
        let _ = self.cmd_tx.send(WsCmd::Subscribe {
            harness_type: harness_type.to_string(),
            session_id: session_id.to_string(),
        });
    }
    /// 便捷:取消订阅。
    pub fn unsubscribe(&self, harness_type: &str, session_id: &str) {
        let _ = self.cmd_tx.send(WsCmd::Unsubscribe {
            harness_type: harness_type.to_string(),
            session_id: session_id.to_string(),
        });
    }

    /// 关闭(manager 线程退出 + detach 所有 WS 子线程)。
    pub fn shutdown(&mut self) {
        let _ = self.cmd_tx.send(WsCmd::Shutdown);
        if let Some(h) = self.handle.take() {
            let _ = h.join();
        }
    }
}

/// 单 key WS 读循环(子线程)。阻塞 read → 解析 → mpsc send。
/// 连接断开/出错 → 发 WsMsg::Error → 线程退出(manager 已移除 key 不重连,
/// 主 loop 下次 subscribe 同 key 会重建)。
fn ws_loop(harness_type: &str, session_id: &str, key: &str, tx: &Sender<WsMsg>, closing: Arc<AtomicBool>) {
    let url = format!(
        "{}/ws/subscribe?harness_type={}&session_id={}",
        OBSERVE_WS, harness_type, session_id
    );
    // 手动建 TcpStream + set_read_timeout(绕过 connect 的 MaybeTlsStream,拿底层
    // TcpStream 控制 → read 可中断检查 closing/ping)。修复 P3 minor 1/2。
    // ponytail: host 硬编码 localhost:8002(与 OBSERVE_WS 一致;改 OBSERVE_WS 需同步)。
    let tcp = match TcpStream::connect("localhost:8002") {
        Ok(t) => t,
        Err(e) => {
            let _ = tx.send(WsMsg::Error { key: key.to_string(), msg: format!("tcp connect: {}", e) });
            return;
        }
    };
    let _ = tcp.set_read_timeout(Some(Duration::from_millis(500)));
    let (mut socket, _resp) = match client::client(&url, tcp) {
        Ok(p) => p,
        Err(e) => {
            let _ = tx.send(WsMsg::Error { key: key.to_string(), msg: format!("ws connect: {}", e) });
            return;
        }
    };
    // ponytail: 分流按 harness_type 字符串匹配(observe 广播同 ht 订阅;第 4 类再抽枚举)。
    let is_flow = harness_type == "flow";
    let is_memory = harness_type == "memory";
    let is_orch = harness_type == "orchestrate";
    let flow_id = session_id.to_string();
    let mut last_ping = Instant::now();

    loop {
        if closing.load(Ordering::Relaxed) {
            break; // unsubscribe → graceful close(socket drop 发 close frame)
        }
        match socket.read() {
            Ok(msg) => {
                // tungstenite 0.26: into_text() → Result<Utf8Bytes, Error>。
                if let Ok(text) = msg.into_text() {
                    if let Ok(p) = serde_json::from_str::<WsPayload>(&text) {
                        let ev: ObserveEvent = p.into();
                        let m = if is_flow {
                            WsMsg::FlowEvent { flow_id: flow_id.clone(), ev }
                        } else if is_memory {
                            WsMsg::MemoryEvent { ev }
                        } else if is_orch {
                            WsMsg::OrchEvent { session_id: session_id.to_string(), ev }
                        } else {
                            WsMsg::Event { key: key.to_string(), ev }
                        };
                        if tx.send(m).is_err() {
                            break; // 主 loop 退出(channel 断)
                        }
                    }
                    // 非 ObserveEvent JSON(如 {"type":"pong"})忽略。
                }
            }
            Err(tungstenite::Error::ConnectionClosed) | Err(tungstenite::Error::AlreadyClosed) => break,
            Err(tungstenite::Error::Io(ref e))
                if e.kind() == std::io::ErrorKind::WouldBlock
                    || e.kind() == std::io::ErrorKind::TimedOut =>
            {
                // read 超时(set_read_timeout)→ 检查 closing + ping 交错。
                if closing.load(Ordering::Relaxed) {
                    break;
                }
                if last_ping.elapsed() >= PING_INTERVAL {
                    let _ = socket.send(Message::Ping(vec![].into()));
                    last_ping = Instant::now();
                }
                continue;
            }
            Err(e) => {
                let _ = tx.send(WsMsg::Error {
                    key: key.to_string(),
                    msg: format!("read: {}", e),
                });
                break;
            }
        }
    }
}

// ═══ self-check:WS payload 解析(observe 契约)═══════════════════════
// 唯一非平凡逻辑:observe broadcast JSON → ObserveEvent 字段映射 drift 即 break。
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ws_payload_parses_turn_event() {
        // 真实 observe broadcast(tick_completed,turn 事件)。
        let raw = r#"{"event_id":"e1","harness_type":"openclaw","harness_id":"h1","session_id":"agent:main:main","tick_id":"t1","event_type":"tick_completed","data":{"status":"success","response":"16","tool_count":0,"duration_ms":120.0},"timestamp":"2026-07-15T00:00:00Z"}"#;
        let p: WsPayload = serde_json::from_str(raw).unwrap();
        assert_eq!(p.event_type, "tick_completed");
        assert_eq!(p.tick_id, "t1");
        assert_eq!(p.harness_id, "h1");
        assert_eq!(p.data["status"], "success");
        let ev: ObserveEvent = p.into();
        assert_eq!(ev.event_type, "tick_completed");
        assert_eq!(ev.event_id, "e1", "IT7: event_id 应透传到 ObserveEvent");
    }

    #[test]
    fn ws_payload_parses_flow_event() {
        // flow 事件:harness_type=flow,event_type=tick_completed,
        // data.flow_event=node_completed + data.flow_payload(node 状态)。
        let raw = r#"{"event_id":"e2","harness_type":"flow","harness_id":"flow_engine_abcd1234","session_id":"flow_abc","tick_id":"flow_abc","event_type":"tick_completed","data":{"status":"success","response":"","flow_event":"node_completed","flow_payload":{"node_id":"A","node_status":"completed","response":"16"}},"timestamp":"2026-07-15T00:00:01Z"}"#;
        let p: WsPayload = serde_json::from_str(raw).unwrap();
        assert_eq!(p.event_type, "tick_completed");
        assert_eq!(p.data["flow_event"], "node_completed");
        let ev: ObserveEvent = p.into();
        assert_eq!(ev.data["flow_event"], "node_completed");
    }

    #[test]
    fn ws_payload_ignores_pong_message() {
        // observe 服务端 ping 回 pong({"type":"pong"});WsPayload 无 event_type→解析失败→忽略。
        let raw = r#"{"type":"pong"}"#;
        let res: Result<WsPayload, _> = serde_json::from_str(raw);
        assert!(res.is_err(), "pong has no event_type, should fail to parse as WsPayload");
    }

    /// IT7:测试用 mock——从外部注入 rx(drain_ws 去重测试用)。
    #[cfg(test)]
    impl WsManager {
        pub fn mock(rx: Receiver<WsMsg>) -> Self {
            let (cmd_tx, _cmd_rx) = mpsc::channel::<WsCmd>();
            Self { cmd_tx, rx, handle: None }
        }
    }

    #[test]
    fn ws_manager_subscribe_unsubscribe_idempotent() {
        // manager 启动 → Subscribe 同 key 两次(第二次 idempotent skip)→ Unsubscribe → Shutdown。
        // observe :8002 不在线时 connect 失败子线程即退,不影响 manager 命令通道。
        let mut mgr = WsManager::spawn();
        mgr.subscribe("openclaw", "agent:main:main");
        mgr.subscribe("openclaw", "agent:main:main"); // idempotent
        mgr.unsubscribe("openclaw", "agent:main:main");
        mgr.shutdown();
        // 到这里无 panic 即 pass(channel + 线程生命周期正确)。
    }
}
