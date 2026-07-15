//! 组件层:P2 新增 crate 的封装 + 基础控件层(GUI 重构准备)。
//!
//! App 业务耦合(原有):
//! - tui_popup:可拖拽弹窗(render_popup,首次 centered/offset,后续 drag 跟随)
//! - ratatui_interact:右键 ContextMenu / 模态 PopupDialog(入口,演示 interact 集成)
//! - ratatui_image:Kitty icat 图片预览(render_image_preview,非 Kitty 降级)
//! - rat_event:事件限定符语义(Regular/Popup/Dialog,由 state 层调度)
//! - raw_exec:spawn claude --resume <sid> / claw TUI 全屏,ctrl+d 退出回
//!
//! 基础控件层(通用,与 App 业务解耦,后续 GUI 重构消费):
//! - tabs:tag分页(ratatui 原生 Tabs + 窗口分页)
//! - popup:浮动弹窗(通用 PopupWindow,tui-popup 薄封装)
//! - mouse:鼠标光标(MouseCursor)+ 鼠标点击(ClickMap,原生 Rect::contains)
//! - anchor:锚点连接线(AnchorGraph,ratatui canvas 薄封装)

pub mod anchor;
pub mod control;
pub mod markdown;
pub mod mouse;
pub mod popup;
pub mod position;
pub mod raw_exec;
pub mod scrollbar;
pub mod split;
pub mod tabs;

use crate::kitty::{Protocol, TermCap};
use crate::state::Popup;
use ratatui::{
    layout::Rect,
    style::{Color, Modifier, Style},
    text::{Line, Text},
    Frame,
};

/// 渲染一个弹窗(可拖拽 tui-popup)。
///
/// 定位策略(centered / absolute position):
/// - 首次:tui-popup 按 body 内容自动居中,并把 area 回填进 PopupState(area 是 pub(crate),
///   外部只能靠首次 render 让它自己置位)。
/// - position=Some(x,y):首次居中回填后,立即 move_to 到绝对坐标(仅一次,placed flag)。
/// - 拖拽:state.handle_mouse_event 在 state 层改 state.area,render 自动跟随。
///
/// 用 tui-popup 的 `StatefulWidgetRef::render_ref`:自带 Clear 遮罩 + Block 边框 + area 回填。
pub fn render_popup(f: &mut Frame, screen: Rect, p: &mut Popup) {
    // ADR-3:md_text 优先(markdown 渲染),否则回退 body(Vec<String> 纯文本)。
    let body_text = if let Some(md) = &p.md_text {
        md.clone()
    } else {
        let lines: Vec<Line> = p
            .body
            .iter()
            .map(|s| Line::from(s.as_str()).style(Style::default().fg(Color::Yellow)))
            .collect();
        Text::from(lines)
    };
    let popup = tui_popup::Popup::new(body_text)
        .title(Line::from(format!(" {} ", p.title)).style(
            Style::default()
                .fg(Color::LightCyan)
                .add_modifier(Modifier::BOLD),
        ))
        .style(Style::default().bg(Color::DarkGray))
        .border_style(Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD));

    // StatefulWidgetRef::render_ref 自带 Clear + Block + area 回填(支持后续 drag)。
    use ratatui::widgets::StatefulWidgetRef;
    popup.render_ref(screen, f.buffer_mut(), &mut p.state);

    // 绝对定位:首次渲染后 area 已回填,挪到 position(仅一次)。
    if !p.placed {
        if let Some((x, y)) = p.position {
            let cx = x.min(screen.right().saturating_sub(p.width.max(1)));
            let cy = y.min(screen.bottom().saturating_sub(p.height.max(1)));
            p.state.move_to(cx, cy);
        }
        p.placed = true;
    }
}

/// Kitty icat 图片预览。
///
/// 有图形协议(Kitty/Sixel/Iterm2/Halfblocks)时,在右下角画一个占位图片框(组件演示)。
/// 非 Kitty(Protocol::None):render.rs 已 gate,不会进来。
///
/// ponytail: 不内嵌真实 png(无 assets);用文字占位框演示 ratatui-image 集成点。
/// 真实集成:Picker::new_resize_protocol(image::open(...).decode()) → StatefulImage::default().resize(Resize::Fit)。
pub fn render_image_preview(f: &mut Frame, screen: Rect, term: &TermCap) {
    if term.protocol == Protocol::None {
        return;
    }
    // 右下角 28x8 占位框,标注检测到的协议。
    let w = 30u16;
    let h = 7u16;
    let x = screen.right().saturating_sub(w + 1);
    let y = screen.bottom().saturating_sub(h + 2);
    let area = Rect::new(x, y, w, h);

    use ratatui::widgets::{Block, Borders, Clear, Paragraph};
    f.render_widget(Clear, area);
    let block = Block::default()
        .borders(Borders::ALL)
        .title(format!(" icat · {} ", term.protocol.label()))
        .border_style(Style::default().fg(Color::Magenta).add_modifier(Modifier::BOLD))
        .style(Style::default().bg(Color::Black));
    let inner = block.inner(area);
    f.render_widget(block, area);
    let body = vec![
        Line::from(format!(" protocol: {}", term.protocol.label())).style(Style::default().fg(Color::Cyan)),
        Line::from(format!(" image_ok: {}", term.image_ok)).style(Style::default().fg(Color::Green)),
        Line::raw(" [Kitty icat 占位框]").style(Style::default().fg(Color::DarkGray)),
        Line::raw(" 真实图:Picker::new_resize_protocol").style(Style::default().fg(Color::DarkGray)),
    ];
    f.render_widget(Paragraph::new(body), inner);
}

/// 右键 context menu 入口(ratatui-interact ContextMenuState 演示)。
///
/// 主流程里 base panel 右键已开 help 弹窗;本函数演示如何用 interact 的
/// ContextMenuState 做真实菜单:open_at(x,y) → render_stateful(items, state) →
/// handle_context_menu_mouse/key。ponytail: 当前 help 弹窗已够,菜单逻辑留入口。
#[allow(dead_code)]
pub fn build_context_menu_items() -> Vec<ratatui_interact::components::context_menu::ContextMenuItem> {
    use ratatui_interact::components::context_menu::ContextMenuItem;
    vec![
        ContextMenuItem::action("flow", "FLOW 横向轨道"),
        ContextMenuItem::action("stack", "STACK 纵向堆叠"),
        ContextMenuItem::action("control", "CONTROL orchestrator"),
        ContextMenuItem::separator(),
        ContextMenuItem::action("help", "Help / 架构"),
        ContextMenuItem::action("quit", "Quit"),
    ]
}
