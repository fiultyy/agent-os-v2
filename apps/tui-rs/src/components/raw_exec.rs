//! raw exec:spawn harness 进程(claude --resume <sid> / claw TUI 全屏),ctrl+d 退出回 TUI。
//!
//! 实现:std::process::Command 挂起当前 TUI raw mode,子进程接管终端(继承 stdin/stdout/stderr),
//! 子进程退出后恢复 raw mode 回 ratatui。
//!
//! ponytail: 用 std::process::Command::status(inherit stdio)而非 portable-pty——
//! 单进程前台接管最简,无需 pty 桥;ctrl+d 由 harness 自己处理,退出码 0 即回 TUI。
//! 若需后台并行多 harness 才上 portable-pty。
#![allow(dead_code)]
//!
//! 实现:std::process::Command 挂起当前 TUI raw mode,子进程接管终端(继承 stdin/stdout/stderr),
//! 子进程退出后恢复 raw mode 回 ratatui。
//!
//! ponytail: 用 std::process::Command::status(inherit stdio)而非 portable-pty——
//! 单进程前台接管最简,无需 pty 桥;ctrl+d 由 harness 自己处理,退出码 0 即回 TUI。
//! 若需后台并行多 harness 才上 portable-pty。

use crate::state;
use crossterm::terminal::{disable_raw_mode, enable_raw_mode};
use std::io;
use std::process::Command;

/// harness 类型决定 spawn 命令。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Harness {
    ClaudeCode,
    Claw,
}

impl Harness {
    pub fn binary(self) -> &'static str {
        match self {
            Harness::ClaudeCode => "claude",
            Harness::Claw => "openclaw",
        }
    }
}

/// 选一个 session → spawn 对应 harness 全屏接管终端。
///
/// claude-code:`claude --resume <sid>`
/// claw:`openclaw`(TUI 全屏;claw session id 由 openclaw 自己管)
///
/// 返回子进程退出码;失败返回 io::Error。调用方在 spawn 前必须 disable_raw_mode +
/// LeaveAlternateScreen,spawn 后恢复(见 spawn_resume)。
pub fn spawn(h: Harness, sid: Option<&str>) -> io::Result<i32> {
    let mut cmd = Command::new(h.binary());
    match h {
        Harness::ClaudeCode => {
            if let Some(sid) = sid {
                cmd.arg("--resume").arg(sid);
            }
        }
        Harness::Claw => {
            // claw TUI 全屏;--resume 接 sid(claw 支持 session resume)。
            if let Some(sid) = sid {
                cmd.arg("--resume").arg(sid);
            }
        }
    }
    // inherit stdio:子进程接管终端(stdin/stdout/stderr 直通)。
    let status = cmd.status()?;
    Ok(status.code().unwrap_or(0))
}

/// 完整流程:挂起 TUI → spawn harness → 恢复 TUI。
///
/// 在 raw mode + alt screen 下调用:先 disable_raw_mode + LeaveAlternateScreen 让子进程
/// 拿到正常终端,子进程退出后再 enable_raw_mode + EnterAlternateScreen 回 ratatui。
/// ponytail: crossterm 的 disable/enable 成对,失败时尽量恢复(避免终端卡 raw mode)。
pub fn spawn_resume(h: Harness, sid: Option<&str>) -> io::Result<i32> {
    use crossterm::execute;
    use crossterm::terminal::{EnterAlternateScreen, LeaveAlternateScreen};
    use std::io::stdout;

    // 挂起 TUI。
    let _ = disable_raw_mode();
    let _ = execute!(stdout(), LeaveAlternateScreen);

    let res = spawn(h, sid);

    // 恢复 TUI(无论 spawn 成败)。
    let _ = enable_raw_mode();
    let _ = execute!(stdout(), EnterAlternateScreen);
    res
}

/// 按 cursor session 推荐 harness 类型(根据 observe sessions_by_harness 的 harness_type)。
/// ponytail: 简单映射 claude-code→ClaudeCode,其余→Claw(openclaw)。
pub fn harness_for(harness_type: &str) -> Harness {
    if harness_type.contains("claude") {
        Harness::ClaudeCode
    } else {
        Harness::Claw
    }
}

/// demo 自检:不 spawn,只打印将要执行的命令(供 --dump 验证)。
pub fn describe(h: Harness, sid: Option<&str>) -> String {
    let mut s = h.binary().to_string();
    if let Some(sid) = sid {
        s.push_str(&format!(" --resume {} (sid 来自 cursor session)", sid));
    } else {
        s.push_str(" (无 session,新会话)");
    }
    s.push_str(&format!("\n ctrl+d 退出回 TUI · exit code 0 = 正常退出 · {}", state::CLAW_SESSION));
    s
}
