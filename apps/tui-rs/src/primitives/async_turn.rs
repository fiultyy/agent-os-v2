//! async-turn 原语(W-C 填充):对选中 session 发起异步 turn(fire-and-forget)。
//!
//! W-B 预建空壳:make() 返回 PlaceholderPrimitive,key='a',label="async"。
//! W-C 替换本文件 body 为真实 impl,mod.rs 不改。

use super::{OrchestratePrimitive, PlaceholderPrimitive};

pub fn make() -> Box<dyn OrchestratePrimitive> {
    Box::new(PlaceholderPrimitive::new("async_turn", 'a', "async"))
}
