//! fork 原语(W-C 填充):fork 当前选中 session(POST .../sessions/fork)。
//!
//! W-B 预建空壳:make() 返回 PlaceholderPrimitive,key='f',label="fork"。
//! W-C 替换本文件 body 为真实 impl(enabled/invoke 走 fork_session),mod.rs 不改。

use super::{OrchestratePrimitive, PlaceholderPrimitive};

pub fn make() -> Box<dyn OrchestratePrimitive> {
    Box::new(PlaceholderPrimitive::new("fork", 'f', "fork"))
}
