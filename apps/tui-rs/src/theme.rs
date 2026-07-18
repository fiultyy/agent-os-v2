//! 现代深色主题常量(单 DARK const,零配置)。语义色集中,各渲染区引用 crate::theme::DARK。
//! 不改交互,只统一视觉:深色底 + 高亮语义色(accent/highlight/success/done/error)。

#![allow(dead_code)]

use ratatui::style::Color;

#[derive(Clone, Copy)]
pub struct Theme {
    pub bg: Color,            // 主背景(Black)
    pub bg_surface: Color,    // 卡片/弹窗表面(DarkGray)
    pub fg: Color,            // 主前景(White)
    pub fg_muted: Color,      // 标签/提示/分隔(DarkGray)
    pub fg_subtle: Color,     // 极淡(Gray,DIM 二级淡化)
    pub accent: Color,        // primary:标题/链接/强调(Cyan)
    pub accent2: Color,       // secondary:选中/工具(Blue)
    pub highlight: Color,     // cursor/hover/focus(Yellow)
    pub success: Color,       // 在线/running/确认(Green)
    pub done: Color,          // completed/DONE(Magenta)
    pub error: Color,         // 错误/offline/failed/取消(Red)
    pub section: Color,       // 大 section 标题(LightMagenta)
    pub border: Color,        // 普通边框(DarkGray)
    pub border_accent: Color, // 强调边框/popup(Cyan)
}

pub const DARK: Theme = Theme {
    bg: Color::Black,
    bg_surface: Color::DarkGray,
    fg: Color::White,
    fg_muted: Color::DarkGray,
    fg_subtle: Color::Gray,
    accent: Color::Cyan,
    accent2: Color::Blue,
    highlight: Color::Yellow,
    success: Color::Green,
    done: Color::Magenta,
    error: Color::Red,
    section: Color::LightMagenta,
    border: Color::DarkGray,
    border_accent: Color::Cyan,
};
