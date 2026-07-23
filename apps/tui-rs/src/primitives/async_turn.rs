//! async-turn 原语(W-C 真实 impl,ADR-O4):对选中 session 发起异步 turn(fire-and-forget)。
//!
//! ADR-O4 首批四实例之一。invoke 走 trigger_turn(async_run=true):server create_task 后
//! 立返 {started,tick_id},不等 LLM 跑完(observe tick 事件流报进度),不阻塞 TUI。
//! 消息取 Control 输入区 turn_msg(空则不发)。harness_type 从 fork_tree 节点查,fallback
//! agent-os-v2(fork 树专为 agent-os-v2 构建,state.rs build_fork_tree 注释)。

use super::OrchestratePrimitive;
use crate::state::{trigger_turn, App, Selection};

pub struct AsyncTurnPrimitive;

pub fn make() -> Box<dyn OrchestratePrimitive> {
    Box::new(AsyncTurnPrimitive)
}

impl OrchestratePrimitive for AsyncTurnPrimitive {
    fn id(&self) -> &'static str { "async-turn" }
    fn key(&self) -> char { 't' }
    fn label(&self) -> &'static str { "async" }
    fn enabled(&self, sel: &Selection) -> bool { !sel.session_id.is_empty() }

    fn invoke(&self, sel: &Selection, app: &mut App) {
        let msg = app.turn_msg.trim().to_string();
        if msg.is_empty() {
            app.turn_status = Some("(空消息,输入后按 t 异步探)".to_string());
            return;
        }
        // harness_type 从 fork_tree 节点查;fork 树专为 agent-os-v2,缺失兜底 agent-os-v2。
        let ht = app
            .fork_tree
            .nodes
            .get(&sel.session_id)
            .map(|n| n.harness_type.clone())
            .filter(|h| !h.is_empty())
            .unwrap_or_else(|| "agent-os-v2".to_string());
        let sid = sel.session_id.clone();
        // 清输入区(与 do_turn 一致,防残留累加)。
        app.turn_msg.clear();
        app.textarea.clear();
        app.turn_status = Some("⠋ 异步 turn 已发起,进度见 observe".to_string());
        // async_run=true server 立返,但 ureq 同步阻塞本地 orche(~ms 级);spawn 免卡渲染帧。
        // tick_id 仅日志意义,异步进度走 observe WS;不引 channel 回传单串。
        std::thread::spawn(move || {
            let _ = trigger_turn(&ht, &sid, &msg, true);
        });
    }
}
