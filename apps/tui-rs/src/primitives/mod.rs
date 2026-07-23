//! ADR-O4:人控原语抽象层(OrchestratePrimitive trait + registry)。
//!
//! 原语 = Orchestrate tab 上一个人控动作(fork / async-turn / open-events / cancel)。
//! trait 最薄 5 字段:id/key/label/enabled/invoke。footer hint 渲染 + key dispatch + 灰显
//! 全从 `App.primitives` registry 自动派生;新原语 = impl + 注册,分发/hint/灰显零改。
//!
//! 横切拓扑/流控/人机三类(ADR-O4 原文)。首批四实例 fork/async-turn/open-events/cancel。
//! W-B 预建四空壳(占位 PlaceholderPrimitive)+ mod.rs all() 框架,W-C 各填自己文件,
//! mod.rs 不再改动 → W-C 并行零冲突。

use crate::state::{App, Selection};

mod async_turn;
mod cancel;
mod fork;
mod open_events;

/// 人控原语 trait(ADR-O4)。5 字段最小集,不预建 plugin/DSL/inventory。
///
/// - `id`    : 原语唯一标识(日志/调试用,不参与 dispatch)。
/// - `key`   : 单字符快捷键(footer hint 显示 + handle_base_key dispatch 用)。
/// - `label` : footer hint 显示文本(如 "fork")。
/// - `enabled`: 当前选中下是否可用(false → footer 灰显 + dispatch 跳过)。
/// - `invoke`: 执行原语(读 Selection + 改 App 状态)。
pub trait OrchestratePrimitive {
    fn id(&self) -> &'static str;
    fn key(&self) -> char;
    fn label(&self) -> &'static str;
    fn enabled(&self, sel: &Selection) -> bool;
    fn invoke(&self, sel: &Selection, app: &mut App);
}

/// 注册表构造:返回首批四原语。W-C 各填自己文件后,此函数不再改动。
pub fn all() -> Vec<Box<dyn OrchestratePrimitive>> {
    vec![
        fork::make(),
        async_turn::make(),
        open_events::make(),
        cancel::make(),
    ]
}

/// 占位原语:W-B 预建四空壳用。enabled 恒 false(footer 灰显 + dispatch 跳过),
/// invoke no-op。W-C 各文件替换为真实 impl 后,本结构体仍可被 W-C 复用为 fallback,
/// 或直接删(ponytail: 留到 W-C 完成后确认无引用再删)。
pub(crate) struct PlaceholderPrimitive {
    id: &'static str,
    key: char,
    label: &'static str,
}

impl PlaceholderPrimitive {
    pub(crate) const fn new(id: &'static str, key: char, label: &'static str) -> Self {
        Self { id, key, label }
    }
}

impl OrchestratePrimitive for PlaceholderPrimitive {
    fn id(&self) -> &'static str { self.id }
    fn key(&self) -> char { self.key }
    fn label(&self) -> &'static str { self.label }
    fn enabled(&self, _sel: &Selection) -> bool { false }
    fn invoke(&self, _sel: &Selection, _app: &mut App) {}
}
