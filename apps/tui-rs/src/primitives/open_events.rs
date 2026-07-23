//! open-events 原语(W-C 填充):打开选中 session 的事件流(跳 Observe tab 或弹窗)。
//!
//! W-B 预建空壳:make() 返回 PlaceholderPrimitive,key='o',label="events"。
//! W-C 替换本文件 body 为真实 impl,mod.rs 不改。

use super::{OrchestratePrimitive, PlaceholderPrimitive};

pub fn make() -> Box<dyn OrchestratePrimitive> {
    Box::new(PlaceholderPrimitive::new("open_events", 'o', "events"))
}
