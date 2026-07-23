//! fork 原语(ADR-O4):fork 当前选中 tree 节点(POST /h/{type}/sessions/fork)。
//!
//! 与 Control 面板 `do_fork`(读 flat[cursor])不同,原语在 Orchestrate Selection 上
//! 操作:source = sel.session_id,harness_type 从 fork_tree node 查(Selection 不携带 ht)。
//! first_message 取 textarea 文本(fan-out 基线)。成功 → turn_status + refresh + focus 新 session。

use super::OrchestratePrimitive;
use crate::state::{fork_session, norm_ht, trunc, App, Selection};

pub struct ForkPrimitive;

pub fn make() -> Box<dyn OrchestratePrimitive> {
    Box::new(ForkPrimitive)
}

impl OrchestratePrimitive for ForkPrimitive {
    fn id(&self) -> &'static str { "fork" }
    fn key(&self) -> char { 'f' }
    fn label(&self) -> &'static str { "fork" }
    fn enabled(&self, _sel: &Selection) -> bool {
        true // Selection 非空即可(dispatch 已保证 orch_selection = Some)
    }
    fn invoke(&self, sel: &Selection, app: &mut App) {
        // ht 从 tree node 查(Selection 不带 ht);node 缺失 → cc 兜底(claw 拒,见 ADR-4)。
        let ht = app
            .fork_tree
            .nodes
            .get(&sel.session_id)
            .map(|n| norm_ht(&n.harness_type))
            .unwrap_or_else(|| "claude-code".to_string());
        let first_msg = app.textarea.text().to_string();
        match fork_session(&ht, &sel.session_id, &first_msg) {
            Some((new_sid, forked)) if forked => {
                app.turn_status = Some(format!("forked → {}", trunc(&new_sid, 16)));
                app.refresh_sessions();
                app.focus_new_session(&new_sid);
            }
            Some((_new_sid, _forked)) => {
                app.turn_status = Some("fork 未生效(forked=false)".to_string());
            }
            None => {
                app.turn_status = Some(if ht == "claw" {
                    "claw fork 暂不支持(ADR-4)".to_string()
                } else {
                    "fork 失败(orche 不可达?)".to_string()
                });
            }
        }
    }
}
