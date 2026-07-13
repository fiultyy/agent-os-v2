//! v2 harness-bridge TUI(ratatui / Rust)— 双视图,对比 Bubble Tea 版
//!
//! flow:横向轨道流(turn 节点 → 时间轴,tool 分支自展开向下)
//! stack:纵向堆叠(session 树 + turn 事件 list,连 observe 真数据)
//! tab 切换。`--dump` 非交互渲染输出(验证)。

use ratatui::{
    backend::{Backend, CrosstermBackend, TestBackend},
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{List, ListItem, Paragraph},
    Frame, Terminal,
};
use crossterm::{
    event::{self, Event, KeyCode},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use serde::Deserialize;
use std::{collections::HashMap, io, time::Duration};

const OBSERVE: &str = "http://localhost:8002";
const ORCH: &str = "http://localhost:8001";
const CLAW_SESSION: &str = "agent:main:main";

// ═══ observe 数据 ════════════════════════════════════════════════════

#[derive(Deserialize, Clone)]
struct Session {
    harness_type: String,
    session_id: String,
    #[allow(dead_code)]
    harness_id: String,
}
#[derive(Deserialize, Default)]
struct SessionsGrouped {
    sessions_by_harness: HashMap<String, Vec<Session>>,
}
#[derive(Deserialize, Clone)]
struct ObserveEvent {
    event_type: String,
    #[allow(dead_code)]
    tick_id: String,
    data: HashMap<String, serde_json::Value>,
}
#[derive(Deserialize)]
struct EventsResp {
    events: Vec<ObserveEvent>,
}

fn fetch_sessions() -> Option<SessionsGrouped> {
    ureq::get(&format!("{}/sessions/grouped", OBSERVE))
        .call()
        .ok()?
        .into_json::<SessionsGrouped>()
        .ok()
}
fn fetch_events(h: &str, sid: &str) -> Option<Vec<ObserveEvent>> {
    ureq::get(&format!("{}/sessions/{}/{}/events?limit=50", OBSERVE, h, sid))
        .call()
        .ok()?
        .into_json::<EventsResp>()
        .ok()
        .map(|e| e.events)
}

/// 触发一个 turn(POST /h/claw/sessions/{sid}/turn)。返回 server 回的 status 文本。
fn trigger_turn(sid: &str, message: &str) -> Option<String> {
    let resp = ureq::post(&format!("{}/h/claw/sessions/{}/turn", ORCH, sid))
        .send_json(serde_json::json!({ "message": message }))
        .ok()?;
    resp.into_string().ok()
}

// ═══ mock turn(flow 演示)════════════════════════════════════════════

struct FNode {
    kind: &'static str,
    label: &'static str,
}
struct FTurn {
    nodes: Vec<FNode>,
    branches: Vec<FTurn>,
}
fn demo() -> FTurn {
    FTurn {
        nodes: vec![
            FNode { kind: "START", label: "build the feature" },
            FNode { kind: "TOOL", label: "plan" },
            FNode { kind: "TOOL", label: "edit" },
            FNode { kind: "RESULT", label: "files written" },
            FNode { kind: "TOOL", label: "test" },
            FNode { kind: "DELTA", label: "all tests pass" },
            FNode { kind: "DONE", label: "ok" },
        ],
        branches: vec![
            FTurn { nodes: vec![
                FNode{kind:"START",label:"plan"}, FNode{kind:"TOOL",label:"analyze"},
                FNode{kind:"RESULT",label:"scope"}, FNode{kind:"DONE",label:"ok"}], branches: vec![] },
            FTurn { nodes: vec![
                FNode{kind:"START",label:"edit"}, FNode{kind:"TOOL",label:"write"},
                FNode{kind:"RESULT",label:"diff"}, FNode{kind:"DONE",label:"ok"}], branches: vec![] },
        ],
    }
}

fn fnode_span(n: &FNode) -> Span<'static> {
    let (sym, color, bold) = match n.kind {
        "START" => ("● ", Color::Green, true),
        "TOOL" => ("⚒ ", Color::Blue, false),
        "RESULT" => ("◷ ", Color::Cyan, false),
        "DELTA" => ("δ ", Color::DarkGray, false),
        "DONE" => ("✓ ", Color::Magenta, true),
        _ => ("", Color::White, false),
    };
    let mut st = Style::default().fg(color);
    if bold {
        st = st.add_modifier(Modifier::BOLD);
    }
    Span::styled(format!("{}{}", sym, trunc(n.label, 16)), st)
}

fn flow_lines(t: &FTurn, depth: usize) -> Vec<Line<'static>> {
    let indent = "  ".repeat(depth);
    let mut spans: Vec<Span> = vec![Span::raw(indent.clone())];
    for (i, n) in t.nodes.iter().enumerate() {
        if i > 0 {
            spans.push(Span::raw("───").style(Style::default().fg(Color::DarkGray)));
        }
        spans.push(fnode_span(n));
    }
    let mut lines = vec![Line::from(spans)];
    for (j, b) in t.branches.iter().enumerate() {
        let conn = if j < t.branches.len() - 1 { "├─" } else { "└─" };
        let mut sub = flow_lines(b, depth + 1);
        let first = sub.remove(0);
        let mut head = vec![
            Span::raw(format!("{}   ", indent)),
            Span::raw(conn).style(Style::default().fg(Color::DarkGray)),
        ];
        head.extend(first.spans.iter().cloned());
        lines.push(Line::from(head));
        lines.extend(sub);
    }
    lines
}

fn trunc(s: &str, n: usize) -> String {
    let cnt = s.chars().count();
    if cnt <= n {
        s.to_string()
    } else {
        format!("{}…", s.chars().take(n).collect::<String>())
    }
}
fn fmt_val(d: &HashMap<String, serde_json::Value>, k: &str) -> String {
    match d.get(k) {
        Some(serde_json::Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
        None => String::new(),
    }
}

// ═══ app state ══════════════════════════════════════════════════════

#[derive(Clone, Copy, PartialEq)]
enum Mode {
    Flow,
    Stack,
    Control,
}

struct App {
    mode: Mode,
    sessions: SessionsGrouped,
    flat: Vec<Session>,
    cursor: usize,
    events: HashMap<String, Vec<ObserveEvent>>,
    /// 上次触发 turn 的 server 回执(渲染到 control 栏)。
    turn_status: Option<String>,
    /// control 栏可编辑的 message(默认固定,可手动改)。
    turn_msg: String,
}
impl App {
    fn new() -> Self {
        Self {
            mode: Mode::Flow,
            sessions: Default::default(),
            flat: vec![],
            cursor: 0,
            events: HashMap::new(),
            turn_status: None,
            turn_msg: "what is 8+8?".to_string(),
        }
    }
    fn set_sessions(&mut self, sg: SessionsGrouped) {
        let mut harnesses: Vec<String> = sg.sessions_by_harness.keys().cloned().collect();
        harnesses.sort();
        self.flat.clear();
        for h in &harnesses {
            if let Some(ss) = sg.sessions_by_harness.get(h) {
                self.flat.extend(ss.iter().cloned());
            }
        }
        self.sessions = sg;
        if self.cursor >= self.flat.len() {
            self.cursor = 0;
        }
        self.fetch_current();
    }
    fn fetch_current(&mut self) {
        if let Some(s) = self.flat.get(self.cursor) {
            if let Some(evs) = fetch_events(&s.harness_type, &s.session_id) {
                self.events
                    .insert(format!("{}/{}", s.harness_type, s.session_id), evs);
            }
        }
    }
    fn cursor_down(&mut self) {
        if self.cursor + 1 < self.flat.len() {
            self.cursor += 1;
            self.fetch_current();
        }
    }
    fn cursor_up(&mut self) {
        if self.cursor > 0 {
            self.cursor -= 1;
            self.fetch_current();
        }
    }
    /// 拉 claw session 的 observe events(harness_type 在 observe 侧 = openclaw)。
    fn fetch_claw_events(&mut self) {
        if let Some(evs) = fetch_events("openclaw", CLAW_SESSION) {
            self.events
                .insert("openclaw/agent:main:main".to_string(), evs);
        }
    }
    /// control 栏触发 turn:POST /h/claw/sessions/agent:main:main/turn,然后拉新 events。
    fn do_turn(&mut self) {
        let st = trigger_turn(CLAW_SESSION, &self.turn_msg);
        self.turn_status = st;
        // turn 异步,稍后刷新会看到新 tick_started/tick_completed。
        self.fetch_claw_events();
    }
}

// ═══ draw ═══════════════════════════════════════════════════════════

fn draw(f: &mut Frame, app: &App) {
    let area = f.size();
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(1), Constraint::Min(1), Constraint::Length(1)])
        .split(area);
    let mode_str = match app.mode {
        Mode::Flow => "FLOW ◐ 横向轨道",
        Mode::Stack => "STACK ☰ 纵向堆叠",
        Mode::Control => "CONTROL ⌘ orchestrator",
    };
    let title = Paragraph::new(format!(" v2 harness-bridge(ratatui)· {} · [tab 切换]", mode_str))
        .style(Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD));
    f.render_widget(title, chunks[0]);
    match app.mode {
        Mode::Flow => draw_flow(f, chunks[1], app),
        Mode::Stack => draw_stack(f, chunks[1], app),
        Mode::Control => draw_control(f, chunks[1], app),
    }
    let hint = Paragraph::new(" tab 切视图 · c control · j/k 选 session · t trigger turn · r 刷新 · q quit")
        .style(Style::default().fg(Color::DarkGray));
    f.render_widget(hint, chunks[2]);
}

fn draw_flow(f: &mut Frame, area: Rect, app: &App) {
    let mut lines: Vec<Line> = vec![
        Line::from(Span::styled(
            " flow · 横向轨道流(turn 节点 → 时间轴)".to_string(),
            Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD),
        )),
        Line::raw(""),
    ];
    // 主:observe 真实 lane;observe 不可达时 mock demo 作 fallback。
    match app.events.get("openclaw/agent:main:main") {
        Some(evs) => {
            lines.push(Line::from(Span::styled(
                format!(" observe 真实 turn · openclaw/{} · {} events", CLAW_SESSION, evs.len()),
                Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD),
            )));
            lines.push(Line::raw(""));
            lines.extend(observe_lanes(evs));
        }
        None => {
            lines.push(Line::from(Span::styled(
                " (observe 不可达 · mock demo fallback)".to_string(),
                Style::default().fg(Color::DarkGray),
            )));
            lines.push(Line::raw(""));
            let t = demo();
            lines.extend(flow_lines(&t, 0));
        }
    }
    f.render_widget(Paragraph::new(lines), area);
}

/// 把 observe 真事件渲染成横向 flow lane:每个 tick 一行,
/// `● request ── δdelta ── ✓ response`。按 tick_id 分组,同 tick 横向排开。
fn observe_lanes(evs: &[ObserveEvent]) -> Vec<Line<'static>> {
    // 按 tick_id 分组,保持首次出现顺序。
    let mut order: Vec<String> = vec![];
    let mut groups: std::collections::HashMap<String, Vec<&ObserveEvent>> = std::collections::HashMap::new();
    for e in evs {
        let key = if e.tick_id.is_empty() { format!("__noid_{}", order.len()) } else { e.tick_id.clone() };
        if !groups.contains_key(&key) {
            order.push(key.clone());
        }
        groups.entry(key).or_default().push(e);
    }
    let mut out: Vec<Line> = vec![];
    for k in &order {
        let Some(grp) = groups.get(k) else { continue };
        let mut spans: Vec<Span> = vec![];
        for (i, e) in grp.iter().enumerate() {
            if i > 0 {
                spans.push(Span::raw("───").style(Style::default().fg(Color::DarkGray)));
            }
            let (sym, color, bold, body) = match e.event_type.as_str() {
                "tick_started" => ("●", Color::Green, true, fmt_val(&e.data, "request")),
                "tool_call" => ("⚒", Color::Blue, false, fmt_val(&e.data, "tool_name")),
                "tool_result" => ("◷", Color::Cyan, false, fmt_val(&e.data, "result")),
                "token_delta" => ("δ", Color::DarkGray, false, fmt_val(&e.data, "delta_text")),
                "tick_completed" => ("✓", Color::Magenta, true, fmt_val(&e.data, "response")),
                other => (other, Color::DarkGray, false, String::new()),
            };
            let mut st = Style::default().fg(color);
            if bold {
                st = st.add_modifier(Modifier::BOLD);
            }
            let label = if body.is_empty() { sym.to_string() } else { format!("{} {}", sym, trunc(&body, 20)) };
            spans.push(Span::styled(label, st));
        }
        out.push(Line::from(spans));
    }
    out
}

#[allow(dead_code)]
fn observe_lane(evs: &[ObserveEvent]) -> Line<'static> {
    // 兼容旧调用;control 视图用 observe_lanes[0] 风格,这里保留单行聚合。
    let lanes = observe_lanes(evs);
    lanes.into_iter().next().unwrap_or_else(|| Line::raw("(no events)"))
}

fn draw_stack(f: &mut Frame, area: Rect, app: &App) {
    let h = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(35), Constraint::Percentage(65)])
        .split(area);
    let mut items: Vec<ListItem> = vec![];
    let mut harnesses: Vec<String> = app.sessions.sessions_by_harness.keys().cloned().collect();
    harnesses.sort();
    let mut ci = 0;
    for hs in &harnesses {
        let n = app.sessions.sessions_by_harness.get(hs).map(|v| v.len()).unwrap_or(0);
        items.push(ListItem::new(format!(" ▾ {} · {}", hs, n)).style(Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD)));
        if let Some(ss) = app.sessions.sessions_by_harness.get(hs) {
            for s in ss {
                let label = trunc(&s.session_id, 22);
                let (prefix, st) = if ci == app.cursor {
                    ("▸ ", Style::default().fg(Color::White).bg(Color::Blue).add_modifier(Modifier::BOLD))
                } else {
                    ("  ", Style::default().fg(Color::White))
                };
                items.push(ListItem::new(format!("   {}{}", prefix, label)).style(st));
                ci += 1;
            }
        }
        items.push(ListItem::new(""));
    }
    f.render_widget(List::new(items), h[0]);

    let key = app.flat.get(app.cursor).map(|s| format!("{}/{}", s.harness_type, s.session_id)).unwrap_or_default();
    let mut ev_lines: Vec<Line> = vec![
        Line::from(Span::styled(" turn stream".to_string(), Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD))),
        Line::raw(""),
    ];
    if let Some(evs) = app.events.get(&key) {
        for e in evs {
            ev_lines.push(stack_event_line(e));
        }
    }
    f.render_widget(Paragraph::new(ev_lines), h[1]);
}

fn stack_event_line(e: &ObserveEvent) -> Line<'static> {
    let (tag, color, body) = match e.event_type.as_str() {
        "tick_started" => ("START", Color::Green, fmt_val(&e.data, "request")),
        "tool_call" => ("TOOL▸", Color::Blue, fmt_val(&e.data, "tool_name")),
        "tool_result" => ("TOOL◂", Color::Blue, fmt_val(&e.data, "result")),
        "tick_completed" => ("DONE ", Color::Magenta, fmt_val(&e.data, "response")),
        "token_delta" => ("δ", Color::DarkGray, fmt_val(&e.data, "delta_text")),
        other => (other, Color::DarkGray, String::new()),
    };
    Line::from(vec![
        Span::styled(format!(" {} ", tag), Style::default().fg(Color::Black).bg(color).add_modifier(Modifier::BOLD)),
        Span::raw(format!(" {}", trunc(&body, 60))),
    ])
}

// ═══ control 视图(orchestrator 原语控制)══════════════════════════════

fn draw_control(f: &mut Frame, area: Rect, app: &App) {
    // 上:控制栏(session + message + 上次 turn 回执),下:最近 turn 的 flow lane。
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(6), Constraint::Min(1)])
        .split(area);

    let status_disp = app.turn_status.clone().unwrap_or_else(|| "(未触发)".to_string());
    let bar = vec![
        Line::from(Span::styled(
            " CONTROL · orchestrator 原语".to_string(),
            Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD),
        )),
        Line::from(vec![
            Span::styled(" session   ", Style::default().fg(Color::DarkGray)),
            Span::styled(CLAW_SESSION.to_string(), Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
        ]),
        Line::from(vec![
            Span::styled(" message   ", Style::default().fg(Color::DarkGray)),
            Span::styled(format!("\"{}\"", app.turn_msg), Style::default().fg(Color::Cyan)),
            Span::styled("  [t 触发 turn]", Style::default().fg(Color::Green)),
        ]),
        Line::from(vec![
            Span::styled(" last turn ", Style::default().fg(Color::DarkGray)),
            Span::styled(trunc(&status_disp, 80), Style::default().fg(Color::White)),
        ]),
        Line::raw(""),
    ];
    f.render_widget(Paragraph::new(bar), chunks[0]);

    // 下:最近 turn 的真实 flow lane(openclaw 真事件)。
    let mut lane_lines: Vec<Line> = vec![Line::from(Span::styled(
        " flow lane · openclaw 真实 turn(tick_started → token_delta → tick_completed)"
            .to_string(),
        Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD),
    ))];
    lane_lines.push(Line::raw(""));
    match app.events.get("openclaw/agent:main:main") {
        Some(evs) => {
            lane_lines.push(observe_lane(evs));
            lane_lines.push(Line::raw(""));
            lane_lines.push(Line::from(Span::styled(
                format!(" ({} events)", evs.len()),
                Style::default().fg(Color::DarkGray),
            )));
        }
        None => lane_lines.push(Line::from(Span::raw("(observe 不可达,按 r 重试)").style(Style::default().fg(Color::DarkGray)))),
    }
    f.render_widget(Paragraph::new(lane_lines), chunks[1]);
}

// ═══ run / dump / main ══════════════════════════════════════════════

fn run<B: Backend>(terminal: &mut Terminal<B>, mut app: App) -> io::Result<()> {
    let mut tick = 0u32;
    loop {
        terminal.draw(|f| draw(f, &app))?;
        // flow/control 模式下周期性拉 claw events,让 lane 实时跟进 turn。
        // ponytail: 固定 4 tick(~2s)轮询,observe 挂了静默跳过。
        if app.mode == Mode::Flow || app.mode == Mode::Control {
            tick = tick.wrapping_add(1);
            if tick % 4 == 0 {
                app.fetch_claw_events();
            }
        }
        if event::poll(Duration::from_millis(500))? {
            if let Event::Key(k) = event::read()? {
                match k.code {
                    KeyCode::Char('q') => return Ok(()),
                    KeyCode::Tab => app.mode = match app.mode {
                        Mode::Flow => Mode::Stack,
                        Mode::Stack => Mode::Control,
                        Mode::Control => Mode::Flow,
                    },
                    KeyCode::Char('1') => app.mode = Mode::Flow,
                    KeyCode::Char('2') => app.mode = Mode::Stack,
                    KeyCode::Char('3') => app.mode = Mode::Control,
                    KeyCode::Char('c') => app.mode = Mode::Control,
                    KeyCode::Char('j') | KeyCode::Down => app.cursor_down(),
                    KeyCode::Char('k') | KeyCode::Up => app.cursor_up(),
                    KeyCode::Char('t') => app.do_turn(),
                    KeyCode::Char('r') => {
                        if let Some(sg) = fetch_sessions() {
                            app.set_sessions(sg);
                        }
                        app.fetch_claw_events();
                    }
                    _ => {}
                }
            }
        }
    }
}

fn print_buffer(term: &Terminal<TestBackend>) {
    let buf = term.backend().buffer();
    let mut s = String::new();
    for y in 0..buf.area.height {
        for x in 0..buf.area.width {
            s.push_str(&buf[(x as u16, y as u16)].symbol());
        }
        s.push('\n');
    }
    print!("{}", s);
}

fn run_dump() {
    let backend = TestBackend::new(132, 36);
    let mut terminal = Terminal::new(backend).unwrap();
    let mut app = App::new();
    if let Some(sg) = fetch_sessions() {
        app.set_sessions(sg);
    }
    if let Some(evs) = fetch_events("openclaw", CLAW_SESSION) {
        app.events
            .insert("openclaw/agent:main:main".to_string(), evs);
    }
    println!("═══ ratatui · FLOW 视图(横向轨道流,observe 真数据 lane)═══");
    app.mode = Mode::Flow;
    terminal.draw(|f| draw(f, &app)).unwrap();
    print_buffer(&terminal);
    println!("\n═══ ratatui · STACK 视图(纵向堆叠,observe 真数据)═══");
    app.mode = Mode::Stack;
    terminal.draw(|f| draw(f, &app)).unwrap();
    print_buffer(&terminal);
    println!("\n═══ ratatui · CONTROL 视图(orchestrator 原语 + flow lane)═══");
    app.mode = Mode::Control;
    terminal.draw(|f| draw(f, &app)).unwrap();
    print_buffer(&terminal);
}

fn main() -> io::Result<()> {
    if std::env::args().any(|a| a == "--dump") {
        run_dump();
        return Ok(());
    }
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;
    let mut app = App::new();
    if let Some(sg) = fetch_sessions() {
        app.set_sessions(sg);
    }
    let res = run(&mut terminal, app);
    disable_raw_mode()?;
    execute!(io::stdout(), LeaveAlternateScreen)?;
    if let Err(e) = res {
        eprintln!("error: {}", e);
    }
    Ok(())
}
