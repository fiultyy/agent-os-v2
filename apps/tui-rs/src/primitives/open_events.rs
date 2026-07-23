//! open-events 原语(ADR-O4):跳 Observe tab 并聚焦选中 session 的事件流。
//!
//! 纯前端无 HTTP:panel=Observe + focus_new_session(sel.session_id)。
//! key=Enter(与 j/k 导航键不撞;Enter 经 Orchestrate Enter 分支用 '\n' 查 registry)。

use super::OrchestratePrimitive;
use crate::state::{App, Panel, Selection};

pub struct OpenEventsPrimitive;

pub fn make() -> Box<dyn OrchestratePrimitive> {
    Box::new(OpenEventsPrimitive)
}

impl OrchestratePrimitive for OpenEventsPrimitive {
    fn id(&self) -> &'static str { "open-events" }
    fn key(&self) -> char { '\n' }
    fn label(&self) -> &'static str { "events" }
    fn enabled(&self, _sel: &Selection) -> bool { true }
    fn invoke(&self, sel: &Selection, app: &mut App) {
        // focus_new_session 按 sid 找 flat 索引并切 cursor(含 WS 重订阅)。
        app.focus_new_session(&sel.session_id);
        // focus_new_session 落 ControlSession 焦点(为 fork/new 设计);open-events 要 Observe 视图。
        app.panel = Panel::Observe;
        app.focus = crate::state::FocusTarget::ObserveSession;
        app.sync_tab_from_panel();
    }
}
