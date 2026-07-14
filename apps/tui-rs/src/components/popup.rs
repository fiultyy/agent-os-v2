//! 浮动弹窗:可复用 PopupWindow(tui-popup 0.5.1 之上薄封装)。
//!
//! tui-popup 提供 Popup + PopupState(render_ref 自带 Clear 遮罩 + Block + area 回填 +
//! handle_mouse_event 拖拽),但不提供 modal 标志 / z-order 栈 / 背景 dim——这些归调用方。
//! 本控件加 modal/at/area/handle_mouse,让调用方用 Vec<PopupWindow>(末尾栈顶)自管 z-order。
//!
//! 0.28 陷阱(已核实 tui-popup 0.5.1 + ratatui 0.28.1):0.4 拉 ratatui 0.27 冲突,只可用 0.5.x;
//! handle_mouse_event 需 tui-popup crossterm feature(Cargo.toml 已开);PopupState.area() 经
//! derive-getters 返回 &Option<Rect>,取值用 .as_ref().copied();move_to 在首次 render_ref 回填
//! area 前是 no-op(故 placed 标志:首次渲染后再挪 position)。
//! ponytail: 拖拽是全区域抓取(tui-popup mouse_down 检查整个 area.contains,非仅标题栏)。

#![allow(dead_code)]

use crossterm::event::MouseEvent;
use ratatui::{
    layout::Rect,
    style::{Color, Modifier, Style},
    text::{Line, Text},
    widgets::StatefulWidgetRef,
    Frame,
};
use tui_popup::{Popup, PopupState};

/// 通用浮动弹窗:标题 + 正文 + 可拖拽 + modal 标志 + 绝对/居中定位。
#[derive(Debug)]
pub struct PopupWindow {
    pub title: Line<'static>,
    pub body: Text<'static>,
    pub state: PopupState,
    pub modal: bool,
    /// 绝对定位坐标;None = 居中(首次渲染后挪到位)。
    pub position: Option<(u16, u16)>,
    pub placed: bool,
}

impl PopupWindow {
    pub fn new(title: impl Into<String>, body: Text<'static>) -> Self {
        Self {
            title: Line::from(title.into())
                .style(Style::default().fg(Color::LightCyan).add_modifier(Modifier::BOLD)),
            body,
            state: PopupState::default(),
            modal: false,
            position: None,
            placed: false,
        }
    }

    /// 居中(默认)。
    pub fn centered(title: impl Into<String>, body: Text<'static>) -> Self {
        Self::new(title, body)
    }

    pub fn modal(mut self, m: bool) -> Self {
        self.modal = m;
        self
    }

    /// 绝对定位(左上角坐标)。
    pub fn at(mut self, x: u16, y: u16) -> Self {
        self.position = Some((x, y));
        self
    }

    /// 渲染(render_ref 自带 Clear + Block + area 回填)。
    /// 首次渲染后按 position 挪到位(仅一次,placed 标志防重复 move_to)。
    ///
    /// position 不显式 clamp:tui-popup 0.5.1 render_ref 内部已 clamp
    /// (popup.rs:147 `x.clamp(buf.area.x, area.right()-width)`),故 .at(越界) 不会
    /// 把弹窗移出屏——靠 tui-popup 兜底。老 render_popup(mod.rs) 多做一次显式 clamp 是
    /// 冗余;未来重构 render.rs 改用 PopupWindow 后即统一(迁移债,非 bug)。
    pub fn render(&mut self, f: &mut Frame, screen: Rect) {
        let p = Popup::new(self.body.clone())
            .title(self.title.clone())
            .style(Style::default().bg(Color::DarkGray))
            .border_style(Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD));
        p.render_ref(screen, f.buffer_mut(), &mut self.state);
        if !self.placed {
            if let Some((x, y)) = self.position {
                self.state.move_to(x, y);
            }
            self.placed = true;
        }
    }

    /// 弹窗当前区域(给 z-order hit-test 用)。PopupState.area() 返回 &Option<Rect>。
    pub fn area(&self) -> Option<Rect> {
        self.state.area().as_ref().copied()
    }

    /// 喂鼠标事件(tui-popup handle_mouse_event:Down/Drag/Up 左键拖拽,全区域抓取)。
    pub fn handle_mouse(&mut self, m: MouseEvent) {
        self.state.handle_mouse_event(m);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builder_chains_modal_and_position() {
        let p = PopupWindow::centered("title", Text::raw("body")).modal(true).at(5, 3);
        assert!(p.modal);
        assert_eq!(p.position, Some((5, 3)));
        assert!(!p.placed, "placed only after first render");
    }

    #[test]
    fn default_is_non_modal_centered() {
        let p = PopupWindow::new("t", Text::raw("b"));
        assert!(!p.modal);
        assert_eq!(p.position, None);
    }
}
