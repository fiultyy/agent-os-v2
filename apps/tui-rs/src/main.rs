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
mod primitives;
mod render;
mod state;
mod theme;
mod widgets_demo;
mod ws;

use crate::events::{poll_once, AppEvent};
use crate::state::{fetch_events, fetch_sessions, App};
use crossterm::{
    event::{DisableBracketedPaste, DisableMouseCapture, EnableBracketedPaste, EnableMouseCapture},
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
    let base_poll = app.term.poll_interval;
    // ADR-4:cursor 切换 → 重订阅 session WS(关旧开新)。跟踪当前订阅 key。
    let mut sub_key: Option<String> = app.flat.get(app.cursor)
        .map(|s| format!("{}/{}", s.harness_type, s.session_id));
    // ADR-4:flow WS 订阅跟踪。create_preset_flow/run_current_flow 后 flows 增长,
    // 主 loop 检测新 flow_id → subscribe('flow',flow_id)。业务方法不改(ADR-3),
    // 订阅在主 loop 侧驱动。终态 flow 保留 WS(收历史,保守;ponytail: 不主动 unsubscribe)。
    let mut sub_flows: std::collections::HashSet<String> = std::collections::HashSet::new();
    // ADR-O1:Orchestrate fork 树根 session WS 订阅(收 branch_created/tick_* 刷节点状态)。
    // ponytail: 只订根(fork 子节点的 branch_created 经根 emit;tick 事件按 session 订阅需更多 key,
    // defer 全树订阅)。session 过千改 observe parent 索引(ADR-O2)。
    let mut sub_orch_roots: std::collections::HashSet<String> = std::collections::HashSet::new();
    loop {
        terminal.draw(|f| render::draw(f, &mut app))?;
        // pending(spinner)期间缩 poll 80ms → spinner ~12fps 流畅;idle 用 base_poll 省 CPU。
        let poll = if app.pending_turn.is_some() {
            std::time::Duration::from_millis(80)
        } else {
            base_poll
        };
        let ev = poll_once(poll);
        // q / Quit 经 handle 返回 true 退出。
        if app.handle(&ev) {
            break;
        }
        if matches!(ev, AppEvent::Quit) {
            break;
        }
        // raw-exec:消费 pending_spawn(e 键 / 按钮3)→ 挂起 TUI 全屏拉起 harness → 恢复 + 全重绘。
        if let Some((h, sid)) = app.pending_spawn.take() {
            let _ = components::raw_exec::spawn_resume(h, sid.as_deref());
            // 子进程占用主屏、重进 alt screen(空白);swap_buffers 重置 inactive buffer,
            // 使下一帧 flush 对空 previous → 全重绘(否则 ratatui diff 判无变化,屏留白)。
            let _ = terminal.swap_buffers();
            let _ = terminal.draw(|f| render::draw(f, &mut app));
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
            // ADR-O1:Orchestrate fork 树订阅(agent-os-v2 WS,刷节点状态)。订阅全树
            // 节点(roots + 子 fork 节点),不只 roots —— 否则子节点 tick 事件不到
            // drain_ws,状态静态。Set 记忆防重复订阅。
            for sid in app.fork_tree.nodes.keys() {
                if !sub_orch_roots.contains(sid) {
                    mgr.subscribe("agent-os-v2", sid);
                    sub_orch_roots.insert(sid.clone());
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
    if std::env::args().any(|a| a == "--help" || a == "-h") {
        println!("v2-tui-rs — harness-bridge TUI (ratatui)");
        println!();
        println!("用法:");
        println!("  v2-tui-rs                      交互 TUI(需 TTY)");
        println!("  v2-tui-rs --dump [--replay P]  离线渲染分层/弹窗(非 TTY,确定性)");
        println!("  v2-tui-rs --widgets-dump       控件 demo 离线渲染");
        println!("  v2-tui-rs --widgets            控件 demo 交互");
        println!("  v2-tui-rs --help | -h          本用法并退出");
        return Ok(());
    }
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
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture, EnableBracketedPaste)?;
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
    // Part4:memory 全局常驻订阅(observe memory lifecycle 广播,session 固定 "memory")。
    app.ws.as_ref().unwrap().subscribe("memory", "memory");

    let res = run(&mut terminal, app);

    disable_raw_mode()?;
    execute!(io::stdout(), LeaveAlternateScreen, DisableMouseCapture, DisableBracketedPaste)?;
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

/// ADR-O1:为 --dump 植入演示 fork 谱系树(root native → 2 children,状态混合),
/// 让 Orchestrate tab 渲染真实 Braille 树(不依赖 observe 在线)。
fn seed_demo_fork_tree(app: &mut state::App) {
    use state::{ForkNode, ForkTree, NodeState};
    use std::collections::HashMap;
    let mut nodes = HashMap::new();
    let root = "sess-root-native".to_string();
    let c1 = "sess-fork-explore-a".to_string();
    let c2 = "sess-fork-explore-b".to_string();
    nodes.insert(root.clone(), ForkNode {
        session_id: root.clone(), agent_id: "native".into(), harness_type: "agent-os-v2".into(),
        parent: None, state: NodeState::Active, children: vec![c1.clone(), c2.clone()],
    });
    nodes.insert(c1.clone(), ForkNode {
        session_id: c1.clone(), agent_id: "explore-a".into(), harness_type: "agent-os-v2".into(),
        parent: Some(root.clone()), state: NodeState::Running, children: vec![],
    });
    nodes.insert(c2.clone(), ForkNode {
        session_id: c2.clone(), agent_id: "explore-b".into(), harness_type: "agent-os-v2".into(),
        parent: Some(root.clone()), state: NodeState::Done, children: vec![],
    });
    app.fork_tree = ForkTree { nodes, roots: vec![root] };
    app.orch_cursor = 0;
    app.sync_orch_selection();
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

    // --replay <path>:从 e2e dump JSON 加载真实事件(离线确定性,跳过网络拉取)。
    // 用法:v2-tui-rs --dump --replay /tmp/e2e_observe_events.json
    let replay_path: Option<String> = std::env::args().position(|a| a == "--replay")
        .and_then(|i| std::env::args().nth(i + 1));
    if let Some(p) = &replay_path {
        println!("═══ --replay · {} ═══", p);
        match std::fs::read_to_string(p) {
            Ok(txt) => match serde_json::from_str::<
                std::collections::HashMap<String, Vec<state::ObserveEvent>>,
            >(&txt) {
                Ok(map) => {
                    for (k, evs) in &map {
                        println!("  {} : {} 事件", k, evs.len());
                    }
                    for (k, evs) in map {
                        app.events.insert(k, evs);
                    }
                }
                Err(e) => println!("  解析失败: {}", e),
            },
            Err(e) => println!("  读取失败: {}", e),
        }
    } else if let Some(evs) = fetch_events("openclaw", state::CLAW_SESSION) {
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

    // 1. base panels(5 tab:Flows/Observe/Control/Orchestrate,分层 layer 0)。
    for (idx, panel) in [
        state::Panel::Flows, state::Panel::Observe, state::Panel::Control, state::Panel::Orchestrate,
    ].iter().enumerate() {
        app.panel = *panel;
        if *panel == state::Panel::Orchestrate {
            // ADR-O1:植入演示 fork 谱系树(root native → 2 children,状态混合)。
            seed_demo_fork_tree(&mut app);
        }
        terminal.draw(|f| render::draw(f, &mut app)).unwrap();
        println!("═══ ratatui · {} 视图(layer 0 base panel)═══", panel.label());
        print_buffer(&terminal);
        if idx < 3 {
            println!();
        }
    }

    // 1b. memory/orch 事件流(e2e --replay 验证 TUI 消费侧:apply_memory/orch_event +
    // render event_glyph 的 memory_event/orch_event 分支)。observe_lanes 直接打印,
    // 不依赖 cursor/draw_stack(Observe tab 走 cursor session,这里渲染指定 key 更确定)。
    for (k, evs) in &app.events {
        if k == "memory/memory" || k.starts_with("orchestrate/") {
            println!("\n═══ Observe · {} ═══", k);
            for line in render::observe_lanes(evs) {
                let s: String = line.spans.iter().fold(String::new(), |mut acc, sp| {
                    acc.push_str(sp.content.as_ref());
                    acc
                });
                println!("{}", s);
            }
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

    // 5. streaming 渲染(token_delta 累积行;cursor session 流式中,spinner + wrap 连续文本)。
    app.panel = state::Panel::Control;
    app.popups.clear();
    if let Some(s) = app.flat.get(app.cursor) {
        let key = format!("{}/{}", s.harness_type, s.session_id);
        app.streaming_text.insert(key,
            "配置📖学架构:我一步步带你走,先看 orchestrator 的作用,再看 observe 和 TUI 三大组件。".to_string());
        terminal.draw(|f| render::draw(f, &mut app)).unwrap();
        println!("\n═══ streaming · token_delta 累积行(cursor session 流式中)═══");
        print_buffer(&terminal);
    } else {
        println!("\n═══ streaming · (observe 离线 flat 空,skip)═══");
    }
}

fn print_buffer(term: &Terminal<ratatui::backend::TestBackend>) {
    use unicode_width::UnicodeWidthStr;
    let buf = term.backend().buffer();
    for y in 0..buf.area.height {
        let mut s = String::new();
        let mut x = 0u16;
        while x < buf.area.width {
            let sym = buf[(x, y)].symbol();
            s.push_str(sym);
            // 双宽字符(CJK)右半 cell 是占位:ratatui reset 成空格,或被 image overlay
            // 残留 glyph(braille)污染。逐 cell 输出必须按显示宽度推进 x,否则中文之间
            // 会冒出空格 / 残留 braille(真终端按 cell 宽渲染看不到,离线 dump 才暴露)。
            let w = UnicodeWidthStr::width(sym) as u16;
            x += if w >= 1 { w } else { 1 };
        }
        println!("{}", s.trim_end());
    }
}
