//! cancel 原语(ADR-O4/O6):协作式中断选中 session 的运行中异步 turn。
//!
//! invoke → POST /h/{ht}/sessions/{sid}/turn/cancel body{tick_id}(W-A-T1 后端端点)。
//! enabled 仅当 Selection.state == Running(异步 turn 在飞);tick_id 从 App.running_ticks
//! 查(Selection 不带 tick_id,同 ht 走 fork_tree node 的做法)。404/无 tick_id → turn_status。
//!
//! scope limit(ADR-O6):协作式取消,能否干净打断 GLM httpx 请求不定;此处只保证发 cancel +
//! 据响应显态。tree 节点刷新由后端 emit 的 tick_completed(cancelled) 经 WS drain 驱动。

use super::OrchestratePrimitive;
use crate::state::{cancel_turn, norm_ht, trunc, App, NodeState, Selection};

pub struct CancelPrimitive;

pub fn make() -> Box<dyn OrchestratePrimitive> {
    Box::new(CancelPrimitive)
}

impl OrchestratePrimitive for CancelPrimitive {
    fn id(&self) -> &'static str { "cancel" }
    fn key(&self) -> char { 'x' }
    fn label(&self) -> &'static str { "cancel" }
    fn enabled(&self, sel: &Selection) -> bool {
        sel.state == NodeState::Running
    }
    fn invoke(&self, sel: &Selection, app: &mut App) {
        // tick_id 从 running_ticks 查;ht 从 tree node 查(Selection 两样都不带)。
        let tick_id = match app.running_ticks.get(&sel.session_id).cloned() {
            Some(t) => t,
            None => {
                app.turn_status = Some("cancel: 无运行中 tick(未收 tick_started?)".to_string());
                return;
            }
        };
        let ht = app
            .fork_tree
            .nodes
            .get(&sel.session_id)
            .map(|n| norm_ht(&n.harness_type))
            .unwrap_or_else(|| "agent-os-v2".to_string());

        // ADR-O6:协作式中断。HTTP 集中在 cancel_turn(同 fork_session/create_session 模式)。
        app.turn_status = Some(match cancel_turn(&ht, &sel.session_id, &tick_id) {
            Ok(()) => format!("cancelled tick {}", trunc(&tick_id, 12)),
            Err(e) => format!("cancel 失败: {}", e),
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sel(state: NodeState) -> Selection {
        Selection { session_id: "s".into(), agent_id: "a".into(), state, parent: None }
    }

    #[test]
    fn enabled_only_when_running() {
        let p = CancelPrimitive;
        assert!(!p.enabled(&sel(NodeState::Active)));
        assert!(!p.enabled(&sel(NodeState::Done)));
        assert!(!p.enabled(&sel(NodeState::Idle)));
        assert!(p.enabled(&sel(NodeState::Running)));
    }

    #[test]
    fn invoke_missing_tick_shows_status_no_http() {
        // 无 running_ticks[tick_id] → turn_status 显提示,不发 HTTP(hermetic,无 orche 依赖)。
        let mut app = App::new(crate::kitty::detect());
        assert!(app.running_ticks.is_empty());
        CancelPrimitive.invoke(&sel(NodeState::Running), &mut app);
        assert!(app.turn_status.as_ref().unwrap().contains("无运行中 tick"));
    }

    #[test]
    fn make_returns_cancel_instance() {
        // registry:make() 必返真实 CancelPrimitive(id/key/label),非占位(占位 enabled 恒 false)。
        let p = make();
        assert_eq!(p.id(), "cancel");
        assert_eq!(p.key(), 'x');
        assert_eq!(p.label(), "cancel");
        assert!(p.enabled(&sel(NodeState::Running)));
    }
}
