//! 基础控件演示场:--widgets flag 串联全部 5 控件(GUI 重构准备验证)。
//!
//! 5 控件同屏:
//! - tabs::TabBar     顶栏 6 tab(窄屏触发 < / > 分页)+ 鼠标点击切 tab
//! - mouse::ClickMap  右栏 2 按钮(原生 Rect::contains 命中)
//! - anchor::AnchorGraph 左栏菱形 DAG(A→B,A→C,B→D,C→D)+ 标签(ratatui canvas Braille)
//! - popup::PopupWindow  p 开/esc 关,鼠标拖拽(tui-popup 全区域抓取)
//! - mouse::MouseCursor  悬停跟随(?1003h Moved),帧末黑底黄字高亮
//!
//! 用法:`v2-tui-rs --widgets`(交互,鼠标)/ `v2-tui-rs --widgets-dump`(静态 TestBackend)。
//! ponytail: 演示场是模块(bin crate 内 mod,共享 components),非独立 bin——免 lib 重构。

use crate::components::{
    anchor::AnchorGraph,
    markdown::md_to_text,
    mouse::{ClickMap, MouseCursor},
    popup::PopupWindow,
    tabs::TabBar,
};
use crossterm::{
    event::{self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, MouseButton, MouseEventKind},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{
    backend::{CrosstermBackend, TestBackend},
    layout::{Constraint, Direction, Layout, Position, Rect},
    style::{Color, Modifier, Style},
    text::Line,
    widgets::{Block, Borders, Paragraph},
    Frame, Terminal,
};
use std::{io, time::Duration};

/// 演示状态:5 控件各一。
struct WidgetsApp {
    tabbar: TabBar,
    clickmap: ClickMap<&'static str>,
    popup: Option<PopupWindow>,
    cursor: MouseCursor,
    graph: AnchorGraph,
    last_hit: String,
}

fn demo_graph() -> AnchorGraph {
    // 菱形 DAG:A(顶)→B(左),A→C(右),B→D,C→D(合并)。canvas y 轴上为正(底起)。
    AnchorGraph::new([0.0, 100.0], [0.0, 100.0])
        .anchor(1, 50.0, 88.0) // A 顶
        .anchor(2, 22.0, 50.0) // B 左
        .anchor(3, 78.0, 50.0) // C 右
        .anchor(4, 50.0, 12.0) // D 底(合并)
        .edge_labeled(1, 2, Color::Cyan, "A→B")
        .edge_labeled(1, 3, Color::Cyan, "A→C")
        .edge_labeled(2, 4, Color::Magenta, "B→D")
        .edge_labeled(3, 4, Color::Magenta, "C→D")
}

fn demo_popup() -> PopupWindow {
    // 弹窗正文用 markdown 渲染(演示 md_to_text 控件)。
    PopupWindow::centered(
        " PopupWindow · 可拖拽 ",
        md_to_text(
            "# 浮动弹窗\n\n这是 **markdown** 渲染的弹窗正文(`tui-popup` 0.5.1)。\n\n- 鼠标左键按住任意位置拖拽(全区域抓取)\n- `esc` / `enter` 关闭 · `p` 重开\n- modal=false:底层可交互\n",
        ),
    )
    .at(8, 6)
}

impl WidgetsApp {
    fn new() -> Self {
        Self {
            tabbar: TabBar::new(vec![
                "FLOW".into(), "STACK".into(), "CONTROL".into(),
                "ANCHOR".into(), "MOUSE".into(), "POPUP".into(),
            ]),
            clickmap: ClickMap::new(),
            popup: None,
            cursor: MouseCursor::default(),
            graph: demo_graph(),
            last_hit: "(未点击)".into(),
        }
    }
}

fn draw(f: &mut Frame, app: &mut WidgetsApp) {
    let area = f.area();

    // 布局:顶 tab 栏(3) / 中(Min)/ 底状态(3)
    let outer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(3), Constraint::Min(1), Constraint::Length(3)])
        .split(area);
    let tab_area = outer[0];
    let mid_area = outer[1];
    let status_area = outer[2];

    // 1. tag分页:TabBar(窄屏 < / > 分页)
    app.tabbar.render(f, tab_area);

    // 中:左 anchor canvas(60%)/ 右按钮面板(40%)
    let mid = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(60), Constraint::Percentage(40)])
        .split(mid_area);
    let canvas_area = mid[0];
    let right_area = mid[1];

    // 2. 锚点连接线:AnchorGraph 菱形 DAG(Braille 连线 + 标签)
    f.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .title(" AnchorGraph · 锚点连接线(ratatui canvas Braille) ")
            .border_style(Style::default().fg(Color::Cyan)),
        canvas_area,
    );
    f.render_widget(app.graph.canvas(), Block::default().borders(Borders::ALL).inner(canvas_area));

    // 右栏:2 按钮(ClickMap 命中区)+ 信息
    app.clickmap.clear();
    let right_inner = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(3), Constraint::Length(3), Constraint::Min(1)])
        .split(right_area);
    // 3. 鼠标点击:ClickMap 注册 2 按钮区
    let btn_a = right_inner[0];
    let btn_b = right_inner[1];
    app.clickmap.register(btn_a, "btn:trigger");
    app.clickmap.register(btn_b, "btn:spawn");
    for (rect, label, hot) in [(btn_a, " [t] trigger turn ", app.tabbar.active == 0), (btn_b, " [s] spawn instance ", false)] {
        let mut st = Style::default().fg(Color::Black).bg(if hot { Color::Yellow } else { Color::DarkGray });
        if hot { st = st.add_modifier(Modifier::BOLD); }
        f.render_widget(Paragraph::new(label).style(st), rect);
    }
    let info = vec![
        Line::from(" ClickMap 命中:").style(Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD)),
        Line::raw(format!("  last = {}", app.last_hit)),
        Line::raw(""),
        Line::from(" 鼠标光标(MouseCursor):").style(Style::default().fg(Color::LightMagenta).add_modifier(Modifier::BOLD)),
        Line::raw(format!("  ({},{}) visible={}", app.cursor.x, app.cursor.y, app.cursor.visible)),
        Line::raw("  ?1003h Moved 悬停跟随(黑底黄字)"),
    ];
    f.render_widget(Paragraph::new(info), right_inner[2]);

    // 底状态
    let status = Paragraph::new(format!(
        " tab:{}/{} · 鼠标:移动跟随光标/左键切tab或按钮/拖拽弹窗 · p 弹窗 · 1-6 选tab · q 退出",
        app.tabbar.active + 1,
        app.tabbar.len(),
    ))
    .style(Style::default().fg(Color::DarkGray));
    f.render_widget(status, status_area);

    // 4. 浮动弹窗:PopupWindow(若开)
    if let Some(p) = app.popup.as_mut() {
        p.render(f, area);
    }

    // 5. 鼠标光标:最后画(最上层,黑底黄字高亮)
    app.cursor.render(f);
}

/// 鼠标事件分发:弹窗开→仅 area 内 Down 路由拖拽(Drag/Up 延续);否则 tab/clickmap 命中。
fn handle_mouse(app: &mut WidgetsApp, tab_area: Rect, m: crossterm::event::MouseEvent) {
    // 光标总是先跟踪(弹窗拖拽时也更新位置)。
    app.cursor.track(m);
    // 弹窗开:Down(Left) 仅在弹窗 area 内才路由(开始拖拽);Drag/Up(Left) 延续/结束拖拽。
    // 弹窗外的 Down fall-through 到 tab/clickmap(modal=false,底层可交互)。
    // 修复(verify confirmed):旧实现弹窗开时无条件吞所有左键 → 非模态表现得像模态。
    if let Some(p) = app.popup.as_mut() {
        match m.kind {
            MouseEventKind::Down(MouseButton::Left) => {
                let inside = p
                    .area()
                    .map(|a| a.contains(Position { x: m.column, y: m.row }))
                    .unwrap_or(false);
                if inside {
                    p.handle_mouse(m);
                    return;
                }
            }
            MouseEventKind::Drag(MouseButton::Left) | MouseEventKind::Up(MouseButton::Left) => {
                p.handle_mouse(m);
                return;
            }
            _ => {}
        }
    }
    if m.kind == MouseEventKind::Down(MouseButton::Left) {
        if let Some(i) = app.tabbar.hit(tab_area, m.column, m.row) {
            app.tabbar.select(i);
            app.last_hit = format!("tab:{} (idx {})", app.tabbar.titles[i], i);
            return;
        }
        if let Some(id) = app.clickmap.hit(m.column, m.row) {
            app.last_hit = (*id).to_string();
        } else {
            app.last_hit = "(空白)".into();
        }
    }
}

pub fn run() -> io::Result<()> {
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let mut app = WidgetsApp::new();
    // tab_area 在 run_loop 的 draw 闭包内每帧从 layout 重算(不依赖死字段)。
    let res = run_loop(&mut terminal, &mut app);

    disable_raw_mode()?;
    execute!(io::stdout(), LeaveAlternateScreen, DisableMouseCapture)?;
    res
}

fn run_loop(
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
    app: &mut WidgetsApp,
) -> io::Result<()> {
    loop {
        let mut tab_area = Rect::default();
        terminal.draw(|f| {
            tab_area = Layout::default()
                .direction(Direction::Vertical)
                .constraints([Constraint::Length(3), Constraint::Min(1), Constraint::Length(3)])
                .split(f.area())[0];
            draw(f, app);
        })?;
        if event::poll(Duration::from_millis(200))? {
            match event::read()? {
                Event::Mouse(m) => handle_mouse(app, tab_area, m),
                Event::Key(k) => match k.code {
                    KeyCode::Char('q') => return Ok(()),
                    KeyCode::Char('p') => {
                        if app.popup.is_some() {
                            app.popup = None;
                        } else {
                            app.popup = Some(demo_popup());
                        }
                    }
                    KeyCode::Esc | KeyCode::Enter => {
                        app.popup = None;
                    }
                    KeyCode::Tab => app.tabbar.next(),
                    KeyCode::BackTab => app.tabbar.prev(),
                    KeyCode::Char(c @ ('1'..='6')) => {
                        app.tabbar.select((c as u8 - b'1') as usize);
                    }
                    _ => {}
                },
                _ => {}
            }
        }
    }
}

/// 渲染第 2 批新 5 控件(markdown + ScrollView[scroll+wrap] + position[breadcrumb/list/percent] + HSplit)。
fn draw_new_controls(f: &mut Frame) {
    use crate::components::{position, scrollbar::ScrollView, split::HSplit};
    use ratatui::widgets::{ListItem, ListState};

    let area = f.area();
    let outer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(1), Constraint::Min(1)])
        .split(area);

    // 位置指示器 c:breadcrumb 顶栏
    f.render_widget(
        Paragraph::new(position::breadcrumb(&["widgets", "scroll", "md", "split"], 2)),
        outer[0],
    );

    // 分区:HSplit resizable(60%)—— 左 ScrollView(md+scroll+wrap),右 list+percent
    let h = HSplit::new(60);
    let [left, bar, right] = h.rects(outer[1]);

    // 左:ScrollView 含 markdown 内容(md 渲染 + 自动换行 + 滚动卷轴)
    let md = "# 基础控件 demo\n\n这是 **markdown** 经 `pulldown-cmark` 渲染(零 ratatui 耦合)。\n\n- 滚动卷轴 Scrollbar\n- 自动换行 Wrap(CJK 不断字)\n- md 渲染\n- 位置指示器\n- 分区 resizable\n";
    let md_lines = md_to_text(md).lines;
    let mut sv = ScrollView::new(md_lines).wrap(true).trim(false);
    sv.render(f, left);

    // 分隔条(resizable 拖拽命中区)
    f.render_widget(Block::default().style(Style::default().fg(Color::DarkGray)), bar);

    // 右:位置指示器 b—— highlighted list + a percent
    let right_split = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(1), Constraint::Length(1)])
        .split(right);
    let items: Vec<ListItem> = ["scrollbar", "position", "markdown", "wrap", "split"]
        .iter()
        .map(|s| ListItem::new(*s))
        .collect();
    let mut state = ListState::default();
    state.select(Some(2));
    let list = position::highlighted_list(items);
    f.render_stateful_widget(list, right_split[0], &mut state);
    f.render_widget(
        Paragraph::new(position::percent_line(state.selected(), 5)),
        right_split[1],
    );
}

// ── --widgets-dump:静态 TestBackend 验证(非 TTY / CI) ───────────────

pub fn run_dump() {
    let mut terminal = Terminal::new(TestBackend::new(110, 32)).unwrap();
    let mut app = WidgetsApp::new();
    app.tabbar.select(3); // ANCHOR tab 高亮
    app.popup = Some(demo_popup()); // 弹窗叠层
    // 模拟光标停在 canvas 区(显黑底黄字高亮)。
    app.cursor = MouseCursor { x: 30, y: 14, visible: true };
    terminal.draw(|f| draw(f, &mut app)).unwrap();
    print_buffer(&terminal);
    println!();
    println!("── 5 控件验证 ──");
    println!("  tabs    : 顶栏 6 tab + < / > 分页(窄屏)/ 点击命中(tab.hit)");
    println!("  anchor  : 左栏菱形 DAG A→B,A→C,B→D,C→D(Braille 连线 + 标签)");
    println!("  click   : 右栏 2 按钮(ClickMap Rect::contains 命中)");
    println!("  popup   : 浮动弹窗(tui-popup Clear+Block+拖拽)");
    println!("  cursor  : (30,14) 黑底黄字高亮(MouseCursor ?1003h Moved)");

    // ── 第 2 批基础控件(markdown/scroll/wrap/position/split)──
    let mut t2 = Terminal::new(TestBackend::new(110, 24)).unwrap();
    t2.draw(|f| draw_new_controls(f)).unwrap();
    println!();
    println!("═══ 第 2 批基础控件(markdown / scroll / wrap / position / split)═══");
    print_buffer(&t2);
    println!();
    println!("── 第 2 批 5 控件 ──");
    println!("  scrollbar : 左栏 ScrollView(Paragraph::scroll + Scrollbar 指示器)");
    println!("  wrap      : ScrollView.wrap(true)(CJK 双宽按 grapheme 断,不切字)");
    println!("  markdown  : md_to_text(pulldown-cmark)渲染 #/列表/**/`代码`");
    println!("  position  : breadcrumb(顶)+ highlighted list(▶+REVERSED)+ percent(右下)");
    println!("  split     : HSplit 60%(resizable API+单测已实现;拖拽交互 demo 待接 ClickMap)");
    println!("  (第 2 批 5 控件仅 --widgets-dump 渲染验证;--widgets 交互集成待后续)");
}

fn print_buffer(term: &Terminal<TestBackend>) {
    let buf = term.backend().buffer();
    for y in 0..buf.area.height {
        let mut s = String::new();
        for x in 0..buf.area.width {
            s.push_str(buf[(x, y as u16)].symbol());
        }
        println!("{}", s.trim_end());
    }
}

// ── 非 TTY 测试:handle_mouse 分发(含 popup-gate 修复验证)──────────────
// verify finding:--widgets-dump 只验渲染,handle_mouse 仅 TTY 路径跑,CI 不可见 → 补非 TTY 测试。

#[cfg(test)]
mod tests {
    use super::*;
    use crossterm::event::{KeyModifiers, MouseButton, MouseEventKind};

    fn ev(kind: MouseEventKind, col: u16, row: u16) -> crossterm::event::MouseEvent {
        crossterm::event::MouseEvent { kind, column: col, row, modifiers: KeyModifiers::empty() }
    }

    /// draw 一次(填充 clickmap region + popup area),返回 tab 栏 area(顶 3 行)。
    fn drawn_app(popup: bool) -> (WidgetsApp, Rect) {
        let mut app = WidgetsApp::new();
        if popup {
            app.popup = Some(demo_popup());
        }
        let mut terminal = Terminal::new(TestBackend::new(110, 32)).unwrap();
        terminal.draw(|f| draw(f, &mut app)).unwrap();
        // outer = [Length(3), Min, Length(3)] → tab 栏 = (0,0,110,3)。
        let tab_area = Rect::new(0, 0, 110, 3);
        (app, tab_area)
    }

    #[test]
    fn tab_click_updates_last_hit() {
        let (mut app, tab_area) = drawn_app(false);
        // 点 tab 栏 inner(y=1)靠左 → 命中 FLOW(idx 0)。
        handle_mouse(&mut app, tab_area, ev(MouseEventKind::Down(MouseButton::Left), 3, 1));
        assert!(app.last_hit.contains("tab"), "tab click updates last_hit: {}", app.last_hit);
        assert_eq!(app.tabbar.active, 0);
    }

    #[test]
    fn clickmap_button_hit_updates_last_hit() {
        let (mut app, tab_area) = drawn_app(false);
        // 右栏第一个按钮 btn:trigger 在 x≈66+ y≈4(mid 右 40%,right_inner[0])。
        handle_mouse(&mut app, tab_area, ev(MouseEventKind::Down(MouseButton::Left), 70, 4));
        assert!(
            app.last_hit.contains("btn:trigger") || app.last_hit.contains("btn:"),
            "button click hits ClickMap region: {}", app.last_hit
        );
    }

    /// A 修复验证:弹窗开时,点弹窗外的 tab 仍应命中(非 modal 不吞外点击)。
    /// 旧实现此测试会 fail(last_hit 停在 "(未点击)")。
    #[test]
    fn popup_open_does_not_swallow_outside_click() {
        let (mut app, tab_area) = drawn_app(true);
        assert!(app.popup.is_some());
        // demo_popup at (8,6);点 tab 栏(y=1,弹窗外)应 fall-through 命中 tab。
        handle_mouse(&mut app, tab_area, ev(MouseEventKind::Down(MouseButton::Left), 3, 1));
        assert!(
            app.last_hit.contains("tab"),
            "outside-popup click must hit tab (non-modal): {}", app.last_hit
        );
    }

    /// 弹窗内的 Down(Left) 路由给弹窗(拖拽),不落到 tab/clickmap。
    #[test]
    fn popup_inside_click_routes_to_popup() {
        let (mut app, tab_area) = drawn_app(true);
        let before = app.last_hit.clone();
        // 用实际 popup area 取一个确定在内的点(鲁棒,不依赖弹窗宽度估算)。
        let area = app
            .popup
            .as_ref()
            .and_then(|p| p.area())
            .expect("popup area backfilled after draw");
        let (ix, iy) = (area.x + 2, area.y + 2);
        assert!(area.contains(Position { x: ix, y: iy }), "picked point inside popup");
        handle_mouse(&mut app, tab_area, ev(MouseEventKind::Down(MouseButton::Left), ix, iy));
        assert_eq!(
            app.last_hit, before,
            "inside-popup click consumed by popup (last_hit unchanged): {}", app.last_hit
        );
    }
}
