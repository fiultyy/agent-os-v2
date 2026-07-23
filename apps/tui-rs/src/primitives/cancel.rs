//! cancel 原语(W-C 填充):取消选中 session 的运行中 turn(POST .../cancel)。
//!
//! W-B 预建空壳:make() 返回 PlaceholderPrimitive,key='x',label="cancel"。
//! key='x'(取消语义,避撞 Control tab 的 'c');W-C 替换 body 为真实 impl,mod.rs 不改。

use super::{OrchestratePrimitive, PlaceholderPrimitive};

pub fn make() -> Box<dyn OrchestratePrimitive> {
    Box::new(PlaceholderPrimitive::new("cancel", 'x', "cancel"))
}
