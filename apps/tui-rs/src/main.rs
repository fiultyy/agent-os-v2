//! v2 harness-bridge TUI(ratatui · P2 分层架构)。
//!
//! 分层(Kitty Layout 由终端原生处理,应用层不模拟多窗口):
//! - 事件层 events.rs:crossterm poll,Key→focused panel / Mouse→点击拖拽滚轮 / Resize→重算
//! - 状态层 state.rs:App State event loop,Panel open/focus/z-index 栈,弹窗栈 modal 栈顶消费事件,数据 model
//! - 渲染层 render.rs:ratatui immediate-mode,Clear 遮罩,z-order base→overlay→modal,弹窗 Rect centered/absolute
//! - 组件层 components/:tui-popup 拖拽 + ratatui-interact 右键/模态 + ratatui-image icat + rat-event Dialog
//! - kitty.rs:Kitty/图形协议检测(terminfo/TERM_PROGRAM/icat),非 Kitty 降级(关图/降刷新/提示)
//!
//! P1 保留:flow/stack/control 三视图(base panels)+ raw exec(spawn claude --resume / claw 全屏)。
//! tab 切 base panel;弹窗栈;Kitty 检测决定图片/刷新。
//! `--dump` 非交互渲染输出(验证分层 + base panel + 弹窗栈 + Kitty 检测)。

mod components;
mod events;
mod kitty;
mod render;
mod state;
mod widgets_demo;
mod ws;

use crate::events::{poll_once, AppEvent};
use crate::state::{fetch_events, fetch_sessions, App};
use crossterm::{
    event::{DisableMouseCapture, EnableMouseCapture},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{backend::CrosstermBackend, Terminal};
use std::io;

// ═══ run / dump / main ══════════════════════════════════════════════

/// 交互 event loop:分层 events → state → render。
///
/// poll 间隔由 kitty 检测决定(图形终端短间隔,降级长间隔省 CPU)。
/// 弹窗栈 modal 激活时,state.handle 内部已把 key/mouse 先喂栈顶弹窗(rat-event Dialog 语义)。
/// ADR-1 T4:WS manager 注入 app,Tick drain_ws 收 WS 事件(弃 REST polling)。
fn run(terminal: &mut Terminal<CrosstermBackend<io::Stdout>>, mut app: App) -> io::Result<()> {
    let poll = app.term.poll_interval;
    // ADR-4:cursor 切换 → 重订阅 session WS(关旧开新)。跟踪当前订阅 key。
    let mut sub_key: Option<String> = app.flat.get(app.cursor)
        .map(|s| format!("{}/{}", s.harness_type, s.session_id));
    // ADR-4:flow WS 订阅跟踪。create_preset_flow/run_current_flow 后 flows 增长,
    // 主 loop 检测新 flow_id → subscribe('flow',flow_id)。业务方法不改(ADR-3),
    // 订阅在主 loop 侧驱动。终态 flow 保留 WS(收历史,保守;ponytail: 不主动 unsubscribe)。
    let mut sub_flows: std::collections::HashSet<String> = std::collections::HashSet::new();
    loop {
        terminal.draw(|f| render::draw(f, &mut app))?;
        let ev = poll_once(poll);
        // q / Quit 经 handle 返回 true 退出。
        if app.handle(&ev) {
            break;
        }
        if matches!(ev, AppEvent::Quit) {
            break;
        }
        // ADR-4:cursor 移动后检查是否需重订阅 WS(cursor_down/up/set_sessions 改 cursor)。
        if let Some(mgr) = app.ws.as_ref() {
            let cur_key = app.flat.get(app.cursor)
                .map(|s| format!("{}/{}", s.harness_type, s.session_id));
            if cur_key != sub_key {
                // 关旧开新。
                if let Some(old) = sub_key.as_ref() {
                    if let Some((ht, sid)) = old.split_once('/') {
                        mgr.unsubscribe(ht, sid);
                    }
                }
                if let Some(s) = app.flat.get(app.cursor) {
                    mgr.subscribe(&s.harness_type, &s.session_id);
                }
                sub_key = cur_key;
            }
            // ADR-4 T2:新 tracked flow → 订阅 flow WS(收 flow_started/node_*/flow_completed)。
            for tf in &app.flows {
                if !sub_flows.contains(&tf.flow_id) {
                    mgr.subscribe("flow", &tf.flow_id);
                    sub_flows.insert(tf.flow_id.clone());
                }
            }
        }
    }
    // 关闭 WS manager(manager 线程 + 所有 WS 子线程 detach)。
    if let Some(mut mgr) = app.ws.take() {
        mgr.shutdown();
    }
    Ok(())
}

fn main() -> io::Result<()> {
    if std::env::args().any(|a| a == "--widgets-dump") {
        widgets_demo::run_dump();
        return Ok(());
    }
    if std::env::args().any(|a| a == "--widgets") {
        return widgets_demo::run();
    }
    if std::env::args().any(|a| a == "--dump") {
        run_dump();
        return Ok(());
    }

    // P2:Kitty 检测(交互模式,TTY 可用)。
    let term = kitty::detect();

    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let mut app = App::new(term);
    if let Some(sg) = fetch_sessions() {
        app.set_sessions(sg);
    }

    // ADR-1 T4:启动 WS manager,初始订阅 cursor session。
    app.ws = Some(ws::WsManager::spawn());
    if let Some(s) = app.flat.get(app.cursor) {
        app.ws.as_ref().unwrap().subscribe(&s.harness_type, &s.session_id);
    }

    let res = run(&mut terminal, app);

    disable_raw_mode()?;
    execute!(io::stdout(), LeaveAlternateScreen, DisableMouseCapture)?;
    if let Err(e) = res {
        eprintln!("error: {}", e);
    }
    Ok(())
}

// ═══ --dump:非交互分层渲染验证 ═════════════════════════════════════

/// 构造一个演示 TrackedFlow(DAG:A,C 并行 start → B 合并)+ 模拟 node 状态。
/// 用于 --dump 渲染真实 DAG 视图,不依赖 orche 在线。
fn demo_tracked_flow() -> state::TrackedFlow {
    use std::collections::HashMap;
    let def = state::preset_flow(state::FlowPreset::Dag, "what is 8+8?");
    let mut nodes: HashMap<String, state::FlowNodeState> = HashMap::new();
    nodes.insert("A".into(), state::FlowNodeState { id: "A".into(), status: "completed".into(), response: "16".into(), status_code: "success".into() });
    nodes.insert("C".into(), state::FlowNodeState { id: "C".into(), status: "completed".into(), response: "4".into(), status_code: "success".into() });
    nodes.insert("B".into(), state::FlowNodeState { id: "B".into(), status: "running".into(), response: String::new(), status_code: String::new() });
    let status = state::FlowStatus { flow_id: "flow_demo00000".into(), status: "running".into(), nodes };
    state::TrackedFlow { flow_id: "flow_demo00000".to_string(), def, status: Some(status) }
}

fn run_dump() {
    use ratatui::backend::TestBackend;

    // --dump 是非 TTY(管道/CI):kitty::detect 会降级到 Protocol::None。
    let term = kitty::detect();
    println!("═══ kitty.rs · 终端能力检测 ═══");
    println!(" protocol      : {} ({:?})", term.protocol.label(), term.protocol);
    println!(" image_ok      : {}", term.image_ok);
    println!(" poll_interval : {}ms", term.poll_interval.as_millis());
    if !term.hint.is_empty() {
        println!(" hint          : {}", term.hint);
    }
    println!();

    let mut app = App::new(term);
    // 为在 dump 里展示 overlay 层,临时把 protocol 提到 Halfblocks(最小图形协议)。
    // dump 是非 TTY,detect 本应 None;这里覆盖以演示分层渲染包含 image overlay 框。
    app.term = kitty::TermCap {
        protocol: kitty::Protocol::Halfblocks,
        image_ok: true,
        poll_interval: std::time::Duration::from_millis(500),
        hint: "",
    };

    if let Some(sg) = fetch_sessions() {
        app.set_sessions(sg);
    }

    // P2 flow 演示:注入一个 tracked flow(DAG 拓扑 A,C→B 合并)+ 模拟 node 状态,
    // 让 --dump 能渲染真实 DAG 视图(不依赖 orche 在线)。orche 在线时按 f/g/D 创建真 flow。
    app.flows.push(demo_tracked_flow());

    if let Some(evs) = fetch_events("openclaw", state::CLAW_SESSION) {
        // 数实例 + 存事件(openclaw/agent:main:main 是多实例 demo session)。
        let n = evs.iter().filter(|e| !e.harness_id.is_empty())
            .map(|e| e.harness_id.as_str()).collect::<std::collections::HashSet<_>>().len();
        app.instances.insert("openclaw/agent:main:main".to_string(), n);
        app.events
            .insert("openclaw/agent:main:main".to_string(), evs);
    }

    // 多实例摘要(ADR-5 实证):哪些 session 是多实例(×N)。
    println!("═══ 多实例摘要(ADR-5:同 sid 多 harness_id)═══");
    let mut multi: Vec<(String, usize)> = app.instances.iter()
        .filter(|(_, n)| **n >= 2)
        .map(|(k, n)| (k.clone(), *n)).collect();
    multi.sort_by(|a, b| b.1.cmp(&a.1));
    if multi.is_empty() {
        println!(" (无多实例 session · ×N 标记不会出现)");
    } else {
        for (k, n) in multi.iter().take(10) {
            println!("  ×{}  {}", n, k);
        }
        if multi.len() > 10 {
            println!("  … +{} more", multi.len() - 10);
        }
    }
    println!(" 共 {} session,{} 多实例", app.flat.len(), multi.len());
    println!();

    let w = 132u16;
    let h = 38u16;
    let mut terminal = Terminal::new(TestBackend::new(w, h)).unwrap();

    // 1. base panels(4 tab:Home/Flows/Observe/Control,分层 layer 0)。
    for (idx, panel) in [state::Panel::Home, state::Panel::Flows, state::Panel::Observe, state::Panel::Control].iter().enumerate() {
        app.panel = *panel;
        terminal.draw(|f| render::draw(f, &mut app)).unwrap();
        println!("═══ ratatui · {} 视图(layer 0 base panel)═══", panel.label());
        print_buffer(&terminal);
        if idx < 3 {
            println!();
        }
    }

    // 2. 弹窗栈(layer 2 modal):开 help 弹窗,展示 modal 叠在 base 之上。
    app.panel = state::Panel::Control;
    app.open_help();
    terminal.draw(|f| render::draw(f, &mut app)).unwrap();
    println!("\n═══ 弹窗栈(layer 2 modal)· help 弹窗叠在 CONTROL 之上 ═══");
    print_buffer(&terminal);

    // 3. 多弹窗 z-order:再开 raw-exec 弹窗,栈顶在上。
    app.open_popup(state::Popup::centered(
        "raw-exec",
        " raw exec · spawn harness",
        vec![
            components::raw_exec::describe(
                components::raw_exec::Harness::ClaudeCode,
                Some("demo-sid-001"),
            ),
            " ctrl+d 退出 harness 回 TUI".to_string(),
        ],
        64,
        10,
    ));
    terminal.draw(|f| render::draw(f, &mut app)).unwrap();
    println!("\n═══ 弹窗栈 z-order · help(底) + raw-exec(栈顶,最上)═══");
    print_buffer(&terminal);

    // 4. raw exec describe(不 spawn,只打印命令)。
    println!("\n═══ components/raw_exec.rs · spawn 命令(describe,不执行)═══");
    println!("claude-code:");
    println!(
        "  {}",
        components::raw_exec::describe(components::raw_exec::Harness::ClaudeCode, Some("demo-sid-001"))
    );
    println!("\nclaw:");
    println!(
        "  {}",
        components::raw_exec::describe(components::raw_exec::Harness::Claw, None)
    );
}

fn print_buffer(term: &Terminal<ratatui::backend::TestBackend>) {
    let buf = term.backend().buffer();
    for y in 0..buf.area.height {
        let mut s = String::new();
        for x in 0..buf.area.width {
            s.push_str(&buf[(x, y as u16)].symbol());
        }
        println!("{}", s.trim_end());
    }
}
