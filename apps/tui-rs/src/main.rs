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
}

struct App {
    mode: Mode,
    sessions: SessionsGrouped,
    flat: Vec<Session>,
    cursor: usize,
    events: HashMap<String, Vec<ObserveEvent>>,
}
impl App {
    fn new() -> Self {
        Self {
            mode: Mode::Flow,
            sessions: Default::default(),
            flat: vec![],
            cursor: 0,
            events: HashMap::new(),
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
}

// ═══ draw ═══════════════════════════════════════════════════════════

fn draw(f: &mut Frame, app: &App) {
    let area = f.size();
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(1), Constraint::Min(1), Constraint::Length(1)])
        .split(area);
    let mode_str = if app.mode == Mode::Flow { "FLOW ◐ 横向轨道" } else { "STACK ☰ 纵向堆叠" };
    let title = Paragraph::new(format!(" v2 harness-bridge(ratatui)· {} · [tab 切换]", mode_str))
        .style(Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD));
    f.render_widget(title, chunks[0]);
    if app.mode == Mode::Flow {
        draw_flow(f, chunks[1], app);
    } else {
        draw_stack(f, chunks[1], app);
    }
    let hint = Paragraph::new(" tab 切视图 · j/k 选 session · r 刷新 · q quit")
        .style(Style::default().fg(Color::DarkGray));
    f.render_widget(hint, chunks[2]);
}

fn draw_flow(f: &mut Frame, area: Rect, app: &App) {
    let mut lines: Vec<Line> = vec![
        Line::from(Span::styled(
            " flow · 横向轨道流(turn 节点 → 时间轴,tool 分支自展开)".to_string(),
            Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD),
        )),
        Line::raw(""),
    ];
    let t = demo();
    lines.extend(flow_lines(&t, 0));
    lines.push(Line::raw(""));
    lines.push(Line::from(Span::styled(
        " observe 真实 turn(openclaw agent:main:main)".to_string(),
        Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD),
    )));
    lines.push(Line::raw(""));
    match app.events.get("openclaw/agent:main:main") {
        Some(evs) => lines.push(observe_lane(evs)),
        None => lines.push(Line::from(Span::raw("(切到该 session 拉 events)").style(Style::default().fg(Color::DarkGray)))),
    }
    f.render_widget(Paragraph::new(lines), area);
}

fn observe_lane(evs: &[ObserveEvent]) -> Line<'static> {
    let mut spans: Vec<Span> = vec![];
    for (i, e) in evs.iter().enumerate() {
        if i > 0 {
            spans.push(Span::raw("───").style(Style::default().fg(Color::DarkGray)));
        }
        let (sym, color, bold) = match e.event_type.as_str() {
            "tick_started" => ("● START", Color::Green, true),
            "tool_call" => ("⚒ TOOL", Color::Blue, false),
            "tool_result" => ("◷ RESULT", Color::Cyan, false),
            "token_delta" => ("δ", Color::DarkGray, false),
            "tick_completed" => ("✓ DONE", Color::Magenta, true),
            other => {
                spans.push(Span::raw(other.to_string()));
                continue;
            }
        };
        let mut st = Style::default().fg(color);
        if bold {
            st = st.add_modifier(Modifier::BOLD);
        }
        spans.push(Span::styled(sym.to_string(), st));
    }
    Line::from(spans)
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

// ═══ run / dump / main ══════════════════════════════════════════════

fn run<B: Backend>(terminal: &mut Terminal<B>, mut app: App) -> io::Result<()> {
    loop {
        terminal.draw(|f| draw(f, &app))?;
        if event::poll(Duration::from_millis(500))? {
            if let Event::Key(k) = event::read()? {
                match k.code {
                    KeyCode::Char('q') => return Ok(()),
                    KeyCode::Tab => app.mode = if app.mode == Mode::Flow { Mode::Stack } else { Mode::Flow },
                    KeyCode::Char('j') | KeyCode::Down => app.cursor_down(),
                    KeyCode::Char('k') | KeyCode::Up => app.cursor_up(),
                    KeyCode::Char('r') => {
                        if let Some(sg) = fetch_sessions() {
                            app.set_sessions(sg);
                        }
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
    if let Some(evs) = fetch_events("openclaw", "agent:main:main") {
        app.events.insert("openclaw/agent:main:main".to_string(), evs);
    }
    println!("═══ ratatui · FLOW 视图(横向轨道流 + mock 分支)═══");
    app.mode = Mode::Flow;
    terminal.draw(|f| draw(f, &app)).unwrap();
    print_buffer(&terminal);
    println!("\n═══ ratatui · STACK 视图(纵向堆叠,observe 真数据)═══");
    app.mode = Mode::Stack;
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
