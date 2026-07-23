//! compare 原语(ADR-O4 bonus C):并排对照选中 session 与其 parent 的最近 tick response。
//!
//! 纯前端拉取 + popup 展示(不实现 diff 算法,人眼对比两段上下/左右文本即可)。
//! invoke → fetch_events(选中 + parent)→ 各取最近 tick_completed.data.response → popup。
//! enabled 仅当 sel.parent.is_some()(root 无父,compare 无意义 → 灰显)。
//!
//! scope limit:仅对比最近一次 tick_completed 的 response;无 tick/observe 不可达显提示。
//! 不缓存(每次按键重新拉,session 事件在变);拉取在调用线程(同步 ureq,~ms 级)。

use super::OrchestratePrimitive;
use crate::state::{fetch_events, trunc, App, Popup, Selection};

pub struct ComparePrimitive;

pub fn make() -> Box<dyn OrchestratePrimitive> {
    Box::new(ComparePrimitive)
}

/// 从 events 找最近 tick_completed 的 data.response(无 → None)。
fn last_tick_response(events: &[crate::state::ObserveEvent]) -> Option<String> {
    events.iter().rev()
        .find(|e| e.event_type == "tick_completed")
        .and_then(|e| e.data.get("response"))
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
}

impl OrchestratePrimitive for ComparePrimitive {
    fn id(&self) -> &'static str { "compare" }
    fn key(&self) -> char { 'c' }
    fn label(&self) -> &'static str { "compare" }
    fn enabled(&self, sel: &Selection) -> bool {
        // 仅选中节点有父时启用(root 灰显)。
        sel.parent.is_some()
    }
    fn invoke(&self, sel: &Selection, app: &mut App) {
        let Some(parent_sid) = sel.parent.clone() else {
            app.turn_status = Some("compare: 选中无 parent".to_string());
            return;
        };
        // harness_type 从 fork_tree 节点查(parent 与选中同 fork 谱系,ht 一致)。
        let ht = app
            .fork_tree
            .nodes
            .get(&sel.session_id)
            .map(|n| n.harness_type.clone())
            .filter(|h| !h.is_empty())
            .unwrap_or_else(|| "agent-os-v2".to_string());

        let child_resp = fetch_events(&ht, &sel.session_id)
            .and_then(|evs| last_tick_response(&evs))
            .unwrap_or_else(|| "(无 tick_completed response)".to_string());
        let parent_resp = fetch_events(&ht, &parent_sid)
            .and_then(|evs| last_tick_response(&evs))
            .unwrap_or_else(|| "(无 tick_completed response)".to_string());

        // 两段对照 body:选中在上,parent 在下,人眼对比。
        let mut body: Vec<String> = Vec::new();
        body.push(format!("▸ 选中 {} ({})", trunc(&sel.session_id, 16), ht));
        body.extend(wrap_lines(&child_resp, 70));
        body.push(String::new());
        body.push(format!("▸ parent {}", trunc(&parent_sid, 16)));
        body.extend(wrap_lines(&parent_resp, 70));

        let popup = Popup::centered("compare", " compare: 选中 vs parent ", body, 76, 24);
        app.open_popup(popup);
    }
}

/// 朴素按字符宽度折行(无 CJK 宽度感知;response 多为 ASCII/拉丁够用)。
/// ponytail: 粗折行,精确 CJK 宽度需 unicode-width crate;TUI 对比够用不加依赖。
fn wrap_lines(s: &str, width: usize) -> Vec<String> {
    let mut out = Vec::new();
    for line in s.lines() {
        let chars: Vec<char> = line.chars().collect();
        for chunk in chars.chunks(width.max(1)) {
            out.push(chunk.iter().collect());
        }
    }
    if out.is_empty() { out.push(String::new()); }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::NodeState;
    use std::collections::HashMap;

    fn sel_with_parent(parent: Option<&str>) -> Selection {
        Selection {
            session_id: "child".into(),
            agent_id: "a".into(),
            state: NodeState::Idle,
            parent: parent.map(|s| s.to_string()),
        }
    }

    #[test]
    fn enabled_only_when_has_parent() {
        let p = ComparePrimitive;
        // root(无 parent)→ 灰显。
        assert!(!p.enabled(&sel_with_parent(None)));
        // 有 parent → 启用。
        assert!(p.enabled(&sel_with_parent(Some("root"))));
    }

    #[test]
    fn invoke_missing_parent_shows_status_no_popup() {
        // parent=None 防御(sel 退化成 root 形态):turn_status 显提示,不弹 popup。
        let mut app = App::new(crate::kitty::detect());
        ComparePrimitive.invoke(&sel_with_parent(None), &mut app);
        assert!(app.turn_status.as_ref().unwrap().contains("无 parent"));
        assert!(app.popups.is_empty(), "no popup when missing parent");
    }

    #[test]
    fn invoke_with_offline_observe_shows_popup_with_placeholders() {
        // observe 不可达(测试环境)→ 两侧 response 均占位 "(无 tick_completed response)",
        // 但 popup 仍弹出(数据流真跑 fetch_events,非 stub)。
        let mut app = App::new(crate::kitty::detect());
        app.fork_tree = crate::state::ForkTree::default();
        app.fork_tree.nodes.insert("child".into(), crate::state::ForkNode {
            session_id: "child".into(), agent_id: "a".into(),
            harness_type: "agent-os-v2".into(), parent: Some("root".into()),
            state: NodeState::Idle, children: vec![],
        });
        app.fork_tree.nodes.insert("root".into(), crate::state::ForkNode {
            session_id: "root".into(), agent_id: "n".into(),
            harness_type: "agent-os-v2".into(), parent: None,
            state: NodeState::Idle, children: vec!["child".into()],
        });
        ComparePrimitive.invoke(&sel_with_parent(Some("root")), &mut app);
        assert_eq!(app.popups.len(), 1, "compare popup should open");
        assert_eq!(app.popups[0].id, "compare");
        let body = &app.popups[0].body;
        assert!(body.iter().any(|l| l.contains("无 tick_completed response")),
            "offline observe → placeholder responses: body={:?}", body);
    }

    #[test]
    fn make_returns_compare_instance() {
        let p = make();
        assert_eq!(p.id(), "compare");
        assert_eq!(p.key(), 'c');
        assert_eq!(p.label(), "compare");
        // enabled:有 parent true,无 parent false(真实 ComparePrimitive 非占位恒 false)。
        assert!(p.enabled(&sel_with_parent(Some("r"))));
        assert!(!p.enabled(&sel_with_parent(None)));
    }

    #[test]
    fn last_tick_response_picks_most_recent() {
        // 多个 tick_completed → 取最近一个(rev + find);非 tick 事件忽略。
        let mk = |ev_type: &str, resp: &str| crate::state::ObserveEvent {
            event_type: ev_type.into(), tick_id: "t".into(), harness_id: "h".into(),
            data: {
                let mut m = HashMap::new();
                if !resp.is_empty() { m.insert("response".into(), serde_json::json!(resp)); }
                m
            },
            event_id: String::new(),
        };
        let evs = vec![
            mk("tick_completed", "old"),
            mk("tick_started", ""),
            mk("tick_completed", "new"),
        ];
        assert_eq!(last_tick_response(&evs).as_deref(), Some("new"));
        // 全无 tick_completed → None。
        let no_tick = vec![mk("tick_started", "")];
        assert!(last_tick_response(&no_tick).is_none());
    }
}
