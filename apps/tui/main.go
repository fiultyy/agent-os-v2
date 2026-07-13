// Package main: v2 harness-bridge TUI(Bubble Tea)— v1 双视图
//
// 两种 turn 可视化(tab 切换):
//   - stack:纵向堆叠(v0,连 observe 真数据)
//   - flow :横向轨道流(turn 节点沿时间轴左→右,tool 分支在节点处自展开向下)
//
// flow 视图用 mock turn 演示 session 推进 + 分支自展开(展示能力)。
//
// 用法:
//   go run .          交互式(tab 切视图, j/k 选 session, </> 横向滚, r 刷新, q 退出)
//   go run . --dump   非交互:渲染两视图输出 stdout(验证用)
package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

const observeURL = "http://localhost:8002"

// ═══ observe 数据(stack 视图用)═══════════════════════════════════════

type Session struct {
	HarnessType string `json:"harness_type"`
	SessionID   string `json:"session_id"`
	HarnessID   string `json:"harness_id"`
}
type SessionsGrouped struct {
	SessionsByHarness map[string][]Session `json:"sessions_by_harness"`
}
type ObserveEvent struct {
	EventType string                 `json:"event_type"`
	TickID    string                 `json:"tick_id"`
	Data      map[string]interface{} `json:"data"`
}
type EventsResp struct {
	Events []ObserveEvent `json:"events"`
}

func fetchSessionsSync() (SessionsGrouped, error) {
	r, err := http.Get(observeURL + "/sessions/grouped")
	if err != nil {
		return SessionsGrouped{}, err
	}
	defer r.Body.Close()
	var sg SessionsGrouped
	return sg, json.NewDecoder(r.Body).Decode(&sg)
}
func fetchEventsSync(h, sid string) ([]ObserveEvent, error) {
	r, err := http.Get(fmt.Sprintf("%s/sessions/%s/%s/events?limit=50", observeURL, h, sid))
	if err != nil {
		return nil, err
	}
	defer r.Body.Close()
	var er EventsResp
	if err := json.NewDecoder(r.Body).Decode(&er); err != nil {
		return nil, err
	}
	return er.Events, nil
}

// ═══ mock turn(flow 视图演示:session 推进 + 分支自展开)═══════════════

type fnode struct {
	kind  string // START / TOOL / RESULT / DELTA / DONE
	label string
}
type fturn struct {
	id       string
	nodes    []fnode
	branches []fturn // tool 触发的子 turn(分支)
}

// demoTurn:模拟一个 session 的 turn 推进 —— 主 turn 跑 plan→edit→test,
// 其中 plan 和 edit 各自 spawn 一个子 turn(分支自展开)。
func demoTurn() fturn {
	return fturn{
		id: "tick_build",
		nodes: []fnode{
			{"START", "build the feature"},
			{"TOOL", "plan"},
			{"TOOL", "edit"},
			{"RESULT", "files written"},
			{"TOOL", "test"},
			{"DELTA", "all tests pass"},
			{"DONE", "ok"},
		},
		branches: []fturn{
			{id: "sub_plan", nodes: []fnode{
				{"START", "plan"}, {"TOOL", "analyze"}, {"RESULT", "scope"}, {"DONE", "ok"},
			}},
			{id: "sub_edit", nodes: []fnode{
				{"START", "edit"}, {"TOOL", "write"}, {"RESULT", "diff"}, {"DONE", "ok"},
			}},
		},
	}
}

// ═══ tea Msg/Cmd ═════════════════════════════════════════════════════

type errMsg struct{ err error }

func (e errMsg) Error() string { return e.err.Error() }

type sessionsMsg SessionsGrouped
type eventsMsg struct {
	events      []ObserveEvent
	harnessType string
	sessionID   string
}
type tickMsg time.Time

func fetchSessionsCmd() tea.Cmd {
	return func() tea.Msg {
		sg, err := fetchSessionsSync()
		if err != nil {
			return errMsg{err}
		}
		return sessionsMsg(sg)
	}
}
func fetchEventsCmd(h, sid string) tea.Cmd {
	return func() tea.Msg {
		evs, err := fetchEventsSync(h, sid)
		if err != nil {
			return errMsg{err}
		}
		return eventsMsg{events: evs, harnessType: h, sessionID: sid}
	}
}
func tickAfter(d time.Duration) tea.Cmd {
	return tea.Tick(d, func(t time.Time) tea.Msg { return tickMsg(t) })
}

// ═══ model ══════════════════════════════════════════════════════════

type viewMode int

const (
	viewStack viewMode = iota // 纵向堆叠(observe 真数据)
	viewFlow                  // 横向轨道流(mock 演示 + 分支)
)

type model struct {
	mode       viewMode
	sessions   SessionsGrouped
	harnesses  []string
	flat       []Session
	cursor     int
	events     map[string][]ObserveEvent
	currentKey string
	flowScroll int // flow 横向滚动
	width      int
	height     int
	err        string
	loading    bool
}

func initialModel() model {
	return model{events: make(map[string][]ObserveEvent), loading: true, mode: viewFlow}
}

func (m model) Init() tea.Cmd {
	return tea.Batch(fetchSessionsCmd(), tickAfter(3*time.Second))
}

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
	case errMsg:
		m.err, m.loading = msg.Error(), false
	case sessionsMsg:
		m.sessions = SessionsGrouped(msg)
		m.flat, m.harnesses = m.flat[:0], m.harnesses[:0]
		for h := range m.sessions.SessionsByHarness {
			m.harnesses = append(m.harnesses, h)
		}
		sortStrings(m.harnesses)
		for _, h := range m.harnesses {
			m.flat = append(m.flat, m.sessions.SessionsByHarness[h]...)
		}
		m.loading = false
		if m.cursor >= len(m.flat) {
			m.cursor = 0
		}
		if len(m.flat) > 0 {
			s := m.flat[m.cursor]
			m.currentKey = s.HarnessType + "/" + s.SessionID
			return m, fetchEventsCmd(s.HarnessType, s.SessionID)
		}
	case eventsMsg:
		k := msg.harnessType + "/" + msg.sessionID
		m.events[k] = msg.events
		m.currentKey = k
	case tickMsg:
		return m, tea.Batch(fetchSessionsCmd(), tickAfter(3*time.Second))
	case tea.KeyMsg:
		switch msg.String() {
		case "q", "ctrl+c":
			return m, tea.Quit
		case "tab":
			if m.mode == viewStack {
				m.mode = viewFlow
			} else {
				m.mode = viewStack
			}
		case "j", "down":
			if m.cursor < len(m.flat)-1 {
				m.cursor++
				return m.selectCurrent()
			}
		case "k", "up":
			if m.cursor > 0 {
				m.cursor--
				return m.selectCurrent()
			}
		case "<":
			m.flowScroll -= 8
			if m.flowScroll < 0 {
				m.flowScroll = 0
			}
		case ">":
			m.flowScroll += 8
		case "r":
			return m, fetchSessionsCmd()
		}
	}
	return m, nil
}

func (m model) selectCurrent() (tea.Model, tea.Cmd) {
	if len(m.flat) == 0 {
		return m, nil
	}
	s := m.flat[m.cursor]
	m.currentKey = s.HarnessType + "/" + s.SessionID
	return m, fetchEventsCmd(s.HarnessType, s.SessionID)
}

// ═══ 样式(无 border)════════════════════════════════════════════════

var (
	styleTitle      = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("99")).Padding(0, 1)
	styleHarness    = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("213")).PaddingLeft(1)
	styleSess       = lipgloss.NewStyle().PaddingLeft(4).Foreground(lipgloss.Color("252"))
	styleSessActive = lipgloss.NewStyle().PaddingLeft(3).PaddingRight(1).Background(lipgloss.Color("57")).Foreground(lipgloss.Color("255")).Bold(true)
	styleHint       = lipgloss.NewStyle().Faint(true).PaddingLeft(1)

	tagStart = lipgloss.NewStyle().Background(lipgloss.Color("22")).Foreground(lipgloss.Color("255")).Padding(0, 1)
	tagTool  = lipgloss.NewStyle().Background(lipgloss.Color("26")).Foreground(lipgloss.Color("255")).Padding(0, 1)
	tagDone  = lipgloss.NewStyle().Background(lipgloss.Color("53")).Foreground(lipgloss.Color("255")).Padding(0, 1)
	tagDelta = lipgloss.NewStyle().Foreground(lipgloss.Color("240")).Padding(0, 1)
	evBody   = lipgloss.NewStyle().Foreground(lipgloss.Color("252")).PaddingLeft(1)

	// flow 节点样式(横向轨道流)
	fnStart  = lipgloss.NewStyle().Background(lipgloss.Color("22")).Foreground(lipgloss.Color("255")).Bold(true).Padding(0, 1)
	fnTool   = lipgloss.NewStyle().Background(lipgloss.Color("26")).Foreground(lipgloss.Color("255")).Padding(0, 1)
	fnResult = lipgloss.NewStyle().Background(lipgloss.Color("17")).Foreground(lipgloss.Color("255")).Padding(0, 1)
	fnDelta  = lipgloss.NewStyle().Foreground(lipgloss.Color("245")).Padding(0, 1)
	fnDone   = lipgloss.NewStyle().Background(lipgloss.Color("53")).Foreground(lipgloss.Color("255")).Bold(true).Padding(0, 1)
)

// ═══ view ═══════════════════════════════════════════════════════════

func (m model) View() string {
	if m.loading {
		return lipgloss.NewStyle().Foreground(lipgloss.Color("99")).Render("⟳ loading…")
	}
	if m.err != "" {
		return lipgloss.NewStyle().Foreground(lipgloss.Color("203")).Render("✗ " + m.err)
	}
	var content string
	if m.mode == viewFlow {
		content = m.viewFlow()
	} else {
		content = m.viewStack()
	}
	mode := "FLOW ◐ 横向轨道"
	if m.mode == viewStack {
		mode = "STACK ☰ 纵向堆叠"
	}
	top := styleTitle.Render("v2 harness-bridge") +
		lipgloss.NewStyle().Foreground(lipgloss.Color("241")).Padding(0, 1).Render("· "+mode+" · [tab 切换]")
	hint := styleHint.Render("tab 切视图 · j/k 选 session · </> 横向滚 · r 刷新 · q quit")
	return top + "\n" + content + "\n" + hint
}

// ── stack 视图(纵向堆叠,v0)──────────────────────────────────────────

func (m model) viewStack() string {
	var left strings.Builder
	ci := 0
	for _, h := range m.harnesses {
		ss := m.sessions.SessionsByHarness[h]
		left.WriteString(styleHarness.Render(fmt.Sprintf("▾ %s · %d", h, len(ss))) + "\n")
		for _, s := range ss {
			label := trunc(s.SessionID, 22)
			if ci == m.cursor {
				left.WriteString(styleSessActive.Render("▸ " + label) + "\n")
			} else {
				left.WriteString(styleSess.Render(label) + "\n")
			}
			ci++
		}
		left.WriteString("\n")
	}
	var right strings.Builder
	right.WriteString(styleTitle.Render("turn stream") + "\n\n")
	if m.currentKey != "" {
		for _, e := range m.events[m.currentKey] {
			right.WriteString(renderStackEvent(e) + "\n")
		}
	}
	leftW := clampInt(m.width/3, 32, m.width-20)
	return lipgloss.JoinHorizontal(lipgloss.Top,
		lipgloss.NewStyle().Width(leftW).Render(left.String()),
		lipgloss.NewStyle().Width(m.width-leftW-1).PaddingLeft(1).Render(right.String()))
}

func renderStackEvent(e ObserveEvent) string {
	var tag, body string
	switch e.EventType {
	case "tick_started":
		tag, body = tagStart.Render("START"), fmt.Sprintf("%v", e.Data["request"])
	case "tool_call":
		tag, body = tagTool.Render("TOOL▸"), fmt.Sprintf("%v", e.Data["tool_name"])
	case "tool_result":
		tag, body = tagTool.Render("TOOL◂"), fmt.Sprintf("%v", e.Data["result"])
	case "tick_completed":
		tag, body = tagDone.Render("DONE "), fmt.Sprintf("%v", e.Data["response"])
	case "token_delta":
		tag, body = tagDelta.Render("δ"), fmt.Sprintf("%v", e.Data["delta_text"])
	default:
		tag, body = tagDelta.Render(e.EventType), ""
	}
	return tag + evBody.Render(trunc(body, 70))
}

// ── flow 视图(横向轨道流 + 分支自展开)────────────────────────────────

func (m model) viewFlow() string {
	t := demoTurn()
	var b strings.Builder
	b.WriteString(styleTitle.Render("flow · 横向轨道流(turn 节点 → 时间轴,tool 分支自展开)") + "\n\n")
	// 渲染 lane(支持横向滚动:按 m.flowScroll 截断)
	lane := renderFlowLane(t, 0)
	lane = scrollHoriz(lane, m.flowScroll, m.width)
	b.WriteString(lane)

	// 附:observe 真实 session 的简版横向 lane(对比 mock)
	b.WriteString("\n\n" + styleTitle.Render("observe 真实 turn(openclaw agent:main:main)") + "\n\n")
	if evs := m.events["openclaw/agent:main:main"]; len(evs) > 0 {
		b.WriteString(renderObserveLane(evs))
	} else {
		b.WriteString(styleHint.Render("(切到该 session 拉 events)"))
	}
	return b.String()
}

// renderFlowLane:横向渲染一个 turn + 其分支递归自展开。
// ponytail:分支对齐用固定缩进(不精确列对齐到 tool 节点),终端字符宽度+ANSI
// 精确对齐复杂;视觉上"tool 后向下展开分支"已够清晰,v2 再精确。
func renderFlowLane(t fturn, depth int) string {
	indent := strings.Repeat("  ", depth)
	var lane strings.Builder
	for i, n := range t.nodes {
		if i > 0 {
			lane.WriteString(lipgloss.NewStyle().Foreground(lipgloss.Color("240")).Render("───"))
		}
		lane.WriteString(renderFNode(n))
	}
	out := indent + lane.String()
	for j, br := range t.branches {
		conn := "└─"
		if j < len(t.branches)-1 {
			conn = "├─"
		}
		connS := lipgloss.NewStyle().Foreground(lipgloss.Color("240")).Render(conn)
		branchLine := renderFlowLane(br, depth+1)
		// 分支首行加 conn,后续行缩进对齐
		lines := strings.Split(branchLine, "\n")
		out += "\n" + indent + "   " + connS + lines[0]
		for _, ln := range lines[1:] {
			out += "\n" + ln
		}
	}
	return out
}

func renderFNode(n fnode) string {
	switch n.kind {
	case "START":
		return fnStart.Render("● " + trunc(n.label, 18))
	case "TOOL":
		return fnTool.Render("⚒ " + trunc(n.label, 14))
	case "RESULT":
		return fnResult.Render("◷ " + trunc(n.label, 16))
	case "DELTA":
		return fnDelta.Render("δ " + trunc(n.label, 18))
	case "DONE":
		return fnDone.Render("✓ " + trunc(n.label, 12))
	}
	return n.label
}

// renderObserveLane:把 observe 真实 events 渲染成单条横向 lane(简化,无分支)
func renderObserveLane(evs []ObserveEvent) string {
	var parts []string
	for i, e := range evs {
		if i > 0 {
			parts = append(parts, lipgloss.NewStyle().Foreground(lipgloss.Color("240")).Render("───"))
		}
		var s lipgloss.Style
		var tag string
		switch e.EventType {
		case "tick_started":
			s, tag = fnStart, "● START"
		case "tool_call":
			s, tag = fnTool, "⚒ TOOL"
		case "tool_result":
			s, tag = fnResult, "◷ RESULT"
		case "token_delta":
			s, tag = fnDelta, "δ"
		case "tick_completed":
			s, tag = fnDone, "✓ DONE"
		default:
			s, tag = fnDelta, e.EventType
		}
		parts = append(parts, s.Render(tag))
	}
	return strings.Join(parts, "")
}

// ═══ 辅助 ══════════════════════════════════════════════════════════

func trunc(s string, n int) string {
	s = strings.ReplaceAll(s, "\n", " ")
	s = strings.TrimSpace(s)
	if len(s) > n {
		return s[:n] + "…"
	}
	return s
}
func clampInt(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}
func sortStrings(a []string) {
	for i := 1; i < len(a); i++ {
		for j := i; j > 0 && a[j-1] > a[j]; j-- {
			a[j-1], a[j] = a[j], a[j-1]
		}
	}
}
func scrollHoriz(s string, off, width int) string {
	// 粗糙横向滚动:按行裁剪前 off 字符(忽略 ANSI 宽度,演示够用)
	// ponytail:ANSI 感知的横向裁剪 v2 再做
	lines := strings.Split(s, "\n")
	maxw := width - 2
	if maxw < 40 {
		maxw = 40
	}
	for i, ln := range lines {
		// 去 ANSI 后裁剪(演示用;生产用 lipgloss.Width)
		plain := stripAnsiApprox(ln)
		if off > 0 && len(plain) > off {
			ln = "…" + ln[len(ln)-(len(plain)-off):]
		}
		if len(plain) > maxw {
			ln = ln[:maxw] + "…"
		}
		lines[i] = ln
	}
	return strings.Join(lines, "\n")
}
func stripAnsiApprox(s string) string {
	var b strings.Builder
	inEsc := false
	for _, r := range s {
		if r == '\x1b' {
			inEsc = true
			continue
		}
		if inEsc {
			if r == 'm' {
				inEsc = false
			}
			continue
		}
		b.WriteRune(r)
	}
	return b.String()
}

// ═══ dump ══════════════════════════════════════════════════════════

func runDump() {
	m := initialModel()
	sg, err := fetchSessionsSync()
	if err != nil {
		fmt.Fprintln(os.Stderr, "fetch sessions:", err)
		os.Exit(1)
	}
	m.sessions = sg
	for h := range sg.SessionsByHarness {
		m.harnesses = append(m.harnesses, h)
		for _, s := range sg.SessionsByHarness[h] {
			m.flat = append(m.flat, s)
		}
	}
	sortStrings(m.harnesses)
	m.loading = false
	// 拉 openclaw agent:main 真实 events(flow 视图附显)
	evs, _ := fetchEventsSync("openclaw", "agent:main:main")
	m.events["openclaw/agent:main:main"] = evs
	m.width, m.height = 132, 40
	fmt.Println("═══ FLOW 视图(横向轨道流 + mock 分支)═══")
	m.mode = viewFlow
	fmt.Println(m.View())
	fmt.Println("\n═══ STACK 视图(纵向堆叠,observe 真数据)═══")
	m.mode = viewStack
	fmt.Println(m.View())
}

func main() {
	if len(os.Args) > 1 && os.Args[1] == "--dump" {
		runDump()
		return
	}
	p := tea.NewProgram(initialModel(), tea.WithAltScreen())
	if _, err := p.Run(); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}
