//! 事件层(crossterm event poll)。
//!
//! 把底层 crossterm `Event` 翻译成对 app 有意义的 `AppEvent`:
//! - Key → focused base panel 操作(tab/j/k/t/数字键等)
//! - Mouse → 点击 / 拖拽(tui-popup PopupState) / 滚轮(列表滚动) / 右键(ContextMenu)
//! - Resize → 重算 layout
//!
//! 弹窗栈顶(modal)优先消费事件:用 rat-event 的 `Dialog` qualifier 语义——
//! 模态弹窗激活时,所有 mouse/key 事件先喂给栈顶弹窗,ConsumedEvent 即不再下发 base panel。
#![allow(dead_code)]
//!
//! 把底层 crossterm `Event` 翻译成对 app 有意义的 `AppEvent`:
//! - Key → focused base panel 操作(tab/j/k/t/数字键等)
//! - Mouse → 点击 / 拖拽(tui-popup PopupState) / 滚轮(列表滚动) / 右键(ContextMenu)
//! - Resize → 重算 layout
//!
//! 弹窗栈顶(modal)优先消费事件:用 rat-event 的 `Dialog` qualifier 语义——
//! 模态弹窗激活时,所有 mouse/key 事件先喂给栈顶弹窗,ConsumedEvent 即不再下发 base panel。

use crossterm::event::{self, Event, KeyEvent, MouseEvent};
use std::time::Duration;

/// 对 app 有意义的事件(底层 crossterm Event 的归约)。
#[derive(Debug)]
pub enum AppEvent {
    /// crossterm 原始 key 事件(交由 state 层按 panel/弹窗分发)。
    Key(KeyEvent),
    /// crossterm 原始 mouse 事件(交由 state 层做弹窗拖拽 / 右键菜单 hit-test)。
    Mouse(MouseEvent),
    /// 终端 resize:触发 layout 重算。
    Resize(u16, u16),
    /// 轮询超时(无事件):用于周期性拉 observe 数据。
    Tick,
    /// 退出 app。
    Quit,
    /// IT7 ④:终端 bracketed paste(crossterm Event::Paste)。state 层 insert 到 textarea / 弹窗输入。
    Paste(String),
}

/// 轮询一次 crossterm 事件,超时返回 `Tick`。
///
/// `interval` 由 kitty.rs 检测决定(图形终端短间隔,降级长间隔省 CPU)。
pub fn poll_once(interval: Duration) -> AppEvent {
    if event::poll(interval).unwrap_or(false) {
        match event::read() {
            Ok(Event::Key(k)) => AppEvent::Key(k),
            Ok(Event::Mouse(m)) => AppEvent::Mouse(m),
            Ok(Event::Resize(w, h)) => AppEvent::Resize(w, h),
            // IT7 ④:bracketed paste(终端进 paste mode 后整段文本一次投递)。
            Ok(Event::Paste(s)) => AppEvent::Paste(s),
            Ok(_) => AppEvent::Tick,
            Err(_) => AppEvent::Tick,
        }
    } else {
        AppEvent::Tick
    }
}

/// rat-event 模态语义:弹窗激活时,该事件是否应被弹窗独占消费?
///
/// 用 rat-event 的 `Dialog` qualifier 语义——模态激活时所有 key/mouse 都先归弹窗。
/// 这里返回 `true` 表示 "栈顶模态存在且这是一个它要拦的事件"。
pub fn is_modal_event(ev: &AppEvent) -> bool {
    matches!(ev, AppEvent::Key(_) | AppEvent::Mouse(_))
}
