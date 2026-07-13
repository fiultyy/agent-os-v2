//! Kitty / 图形协议检测层。
//!
//! 优先用 ratatui-image 的 `Picker::guess_protocol`(发 escape sequence 探测 Kitty/Sixel/Iterm2),
//! 失败/非图形终端降级到 Halfblocks。同时读 terminfo / TERM_PROGRAM 环境变量作辅证。
//! 非 Kitty/Alacritty(无图形协议):关图片渲染 + 降刷新率 + 提示切换终端。

use std::time::Duration;

/// 终端图形能力检测结果。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TermCap {
    /// ratatui-image 探测到的协议(或降级 Halfblocks)。
    pub protocol: Protocol,
    /// 是否值得渲染图片(Kitty/Sixel/Iterm2/Halfblocks 均可;None = 完全不支持)。
    pub image_ok: bool,
    /// 推荐的事件轮询间隔:有图形协议的快终端用短间隔,降级终端拉长省 CPU。
    pub poll_interval: Duration,
    /// 给用户的提示文案(非空时渲染到底栏)。
    pub hint: &'static str,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Protocol {
    Kitty,
    Sixel,
    Iterm2,
    Halfblocks,
    /// 完全无法渲染图片(TTY/重定向)。
    None,
}

impl Protocol {
    pub fn label(&self) -> &'static str {
        match self {
            Protocol::Kitty => "Kitty icat",
            Protocol::Sixel => "Sixel",
            Protocol::Iterm2 => "iTerm2",
            Protocol::Halfblocks => "halfblocks",
            Protocol::None => "none",
        }
    }
}

/// 环境变量辅证:TERM_PROGRAM / TERM 标识的终端。
fn env_terminal() -> Option<String> {
    std::env::var("TERM_PROGRAM")
        .or_else(|_| std::env::var("TERMINAL_EMULATOR"))
        .ok()
}

/// 检测当前终端的图形能力。
///
/// 先看环境变量(TERM_PROGRAM=kitty.kitty 直接判定 Kitty,零开销),
/// 再试 ratatui-image `Picker::guess_protocol`(发 escape 探测,TTY 下失败)。
/// ponytail: 探测失败即降级,不 panic;poll_interval 按 image_ok 二档。
pub fn detect() -> TermCap {
    let env = env_terminal();

    // 快路径:环境变量明确标识 Kitty。
    if env.as_deref() == Some("kitty.kitty") {
        return TermCap {
            protocol: Protocol::Kitty,
            image_ok: true,
            poll_interval: Duration::from_millis(250),
            hint: "",
        };
    }

    // 探测路径:ratatui-image Picker(发 escape sequence)。仅 TTY 可用。
    if let Ok(mut picker) = ratatui_image::picker::Picker::from_termios() {
        let pt = picker.guess_protocol();
        let proto = match pt {
            ratatui_image::picker::ProtocolType::Kitty => Protocol::Kitty,
            ratatui_image::picker::ProtocolType::Sixel => Protocol::Sixel,
            ratatui_image::picker::ProtocolType::Iterm2 => Protocol::Iterm2,
            ratatui_image::picker::ProtocolType::Halfblocks => Protocol::Halfblocks,
        };
        // Halfblocks 是 ratatui-image 的兜底,代表没探测到图形协议但有 TTY。
        let image_ok = proto != Protocol::Halfblocks || supports_halfblocks(&env);
        return TermCap {
            protocol: proto.clone(),
            image_ok,
            poll_interval: poll_for(&proto),
            hint: hint_for(&proto, &env),
        };
    }

    // 降级:非 TTY(重定向/管道/CI)。关图片,降刷新,提示。
    TermCap {
        protocol: Protocol::None,
        image_ok: false,
        poll_interval: Duration::from_millis(1000),
        hint: "non-tty: 图片渲染关闭;切到 Kitty 终端获得 icat 图形",
    }
}

/// Halfblocks 仅需 256 色终端,基本都支持。
fn supports_halfblocks(env: &Option<String>) -> bool {
    // 非 dumb 终端即认为 halfblocks 可用。
    let term = std::env::var("TERM").unwrap_or_default();
    !term.is_empty() && term != "dumb" && env.is_some() || !term.is_empty()
}

fn poll_for(proto: &Protocol) -> Duration {
    match proto {
        Protocol::Kitty | Protocol::Sixel | Protocol::Iterm2 => Duration::from_millis(250),
        Protocol::Halfblocks => Duration::from_millis(500),
        Protocol::None => Duration::from_millis(1000),
    }
}

fn hint_for(proto: &Protocol, env: &Option<String>) -> &'static str {
    match proto {
        Protocol::Kitty | Protocol::Sixel | Protocol::Iterm2 => "",
        Protocol::Halfblocks => {
            if env.as_deref() == Some("Alacritty") || env.is_none() {
                "halfblocks 图片(降级);切到 Kitty 获得 icat 原生图形"
            } else {
                ""
            }
        }
        Protocol::None => "图片渲染关闭;切到 Kitty 终端获得 icat 图形",
    }
}
