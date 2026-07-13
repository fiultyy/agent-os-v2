//! ratatui 弹窗 + 鼠标拖拽 demo:浮层弹窗(Clear + Block overlay),左键按住拖拽。
//! 用法:cargo run --bin popup            交互(鼠标拖拽)
//!       cargo run --bin popup -- --dump  静态渲染验证

use ratatui::{
    backend::{Backend, CrosstermBackend, TestBackend},
    layout::{Rect, Size},
    style::{Color, Modifier, Style},
    text::Line,
    widgets::{Block, Borders, Clear, Paragraph},
    Frame, Terminal,
};
use crossterm::{
    event::{self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, MouseButton, MouseEventKind},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use std::{io, time::Duration};

struct App {
    popup: Rect,
    dragging: bool,
    drag_off: (u16, u16),
}

fn contains(r: Rect, x: u16, y: u16) -> bool {
    x >= r.x && x < r.x + r.width && y >= r.y && y < r.y + r.height
}

fn draw(f: &mut Frame, app: &App) {
    let area = f.size();
    // 背景
    let bg = Paragraph::new(vec![
        Line::from(Styled(" ratatui · popup + mouse drag demo".to_string(), Color::LightCyan, true)),
        Line::from(Styled(" 左键按住弹窗拖拽 · q 退出".to_string(), Color::DarkGray, false)),
        Line::raw(""),
        Line::from(Styled(
            format!(" popup @ ({},{}) {}", app.popup.x, app.popup.y, if app.dragging { "[DRAGGING ●]" } else { "[idle]" }),
            Color::DarkGray, false,
        )),
        Line::raw(""),
        Line::from(Styled(" 背景区(clear + block overlay 浮层在背景之上,拖拽时弹窗实时跟随鼠标)".to_string(), Color::DarkGray, false)),
    ]);
    f.render_widget(bg, area);

    // 弹窗 overlay:Clear 擦该区域背景,再画 Block(边框) + 内文
    f.render_widget(Clear, app.popup);
    let title = if app.dragging { " ✦ Dragging… " } else { " ✦ Drag me! " };
    let block = Block::default().borders(Borders::ALL).title(title).style(Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD));
    f.render_widget(block, app.popup);
    let inner = Rect { x: app.popup.x + 1, y: app.popup.y + 1, width: app.popup.width.saturating_sub(2), height: app.popup.height.saturating_sub(2) };
    let body = Paragraph::new(vec![
        Line::from(Styled("left-drag me".to_string(), Color::Yellow, false)),
        Line::from(Styled("around the screen".to_string(), Color::Yellow, false)),
    ]);
    f.render_widget(body, inner);
}

fn Styled(s: String, fg: Color, bold: bool) -> ratatui::text::Span<'static> {
    let mut st = Style::default().fg(fg);
    if bold { st = st.add_modifier(Modifier::BOLD); }
    ratatui::text::Span::styled(s, st)
}

fn run<B: Backend>(terminal: &mut Terminal<B>, mut app: App, area: Size) -> io::Result<()> {
    loop {
        terminal.draw(|f| draw(f, &app))?;
        if event::poll(Duration::from_millis(200))? {
            match event::read()? {
                Event::Mouse(m) => match m.kind {
                    MouseEventKind::Down(MouseButton::Left) => {
                        if contains(app.popup, m.column, m.row) {
                            app.dragging = true;
                            app.drag_off = (m.column - app.popup.x, m.row - app.popup.y);
                        }
                    }
                    MouseEventKind::Drag(MouseButton::Left) if app.dragging => {
                        let nx = m.column.saturating_sub(app.drag_off.0).min(area.width.saturating_sub(app.popup.width));
                        let ny = m.row.saturating_sub(app.drag_off.1).min(area.height.saturating_sub(app.popup.height));
                        app.popup.x = nx;
                        app.popup.y = ny;
                    }
                    MouseEventKind::Up(MouseButton::Left) => app.dragging = false,
                    _ => {}
                },
                Event::Key(k) => {
                    if k.code == KeyCode::Char('q') { return Ok(()); }
                }
                _ => {}
            }
        }
    }
}

fn print_buffer(t: &Terminal<TestBackend>) {
    let buf = t.backend().buffer();
    for y in 0..buf.area.height {
        let mut s = String::new();
        for x in 0..buf.area.width {
            s.push_str(buf[(x as u16, y as u16)].symbol());
        }
        println!("{}", s.trim_end());
    }
}

fn run_dump() {
    let backend = TestBackend::new(80, 18);
    let mut terminal = Terminal::new(backend).unwrap();
    let app = App { popup: Rect::new(8, 3, 28, 7), dragging: false, drag_off: (0, 0) };
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
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;
    let area = terminal.size()?;
    let app = App { popup: Rect::new(8, 3, 28, 7), dragging: false, drag_off: (0, 0) };
    let res = run(&mut terminal, app, area);
    disable_raw_mode()?;
    execute!(io::stdout(), LeaveAlternateScreen, DisableMouseCapture)?;
    res
}
