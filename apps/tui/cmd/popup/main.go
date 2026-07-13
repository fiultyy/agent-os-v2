// BT 弹窗 + 鼠标拖拽 demo:浮层弹窗(ASCII rounded box overlay),左键按住拖拽移动。
// 用法:go run ./cmd/popup          交互(鼠标拖拽)
//      go run ./cmd/popup --dump   静态渲染验证(弹窗 overlay)
package main

import (
	"fmt"
	"os"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

const popupW, popupH = 26, 7

type model struct {
	popupX, popupY     int
	dragging           bool
	dragOffX, dragOffY int
	width, height      int
}

func initialModel() model  { return model{popupX: 8, popupY: 3} }
func (m model) Init() tea.Cmd { return nil }

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
	case tea.MouseMsg:
		in := msg.X >= m.popupX && msg.X < m.popupX+popupW &&
			msg.Y >= m.popupY && msg.Y < m.popupY+popupH
		switch msg.Type {
		case tea.MouseLeft:
			if in {
				m.dragging = true
				m.dragOffX = msg.X - m.popupX
				m.dragOffY = msg.Y - m.popupY
			}
		case tea.MouseMotion:
			if m.dragging {
				m.popupX = clamp(msg.X-m.dragOffX, 0, m.width-popupW)
				m.popupY = clamp(msg.Y-m.dragOffY, 0, m.height-popupH)
			}
		case tea.MouseRelease:
			m.dragging = false
		}
	case tea.KeyMsg:
		if msg.String() == "q" || msg.String() == "ctrl+c" {
			return m, tea.Quit
		}
	}
	return m, nil
}

func (m model) View() string {
	// 背景:淡色重复行
	bgStyle := lipgloss.NewStyle().Foreground(lipgloss.Color("238"))
	bg := make([]string, m.height)
	for i := range bg {
		line := strings.Repeat(fmt.Sprintf("BT popup+drag %2d  ", i), m.width/18+1)
		if len(line) > m.width {
			line = line[:m.width]
		}
		bg[i] = bgStyle.Render(line)
	}
	return overlayASCIIDecorated(bg, popupBox(m.dragging, m.popupX, m.popupY), m.popupX, m.popupY)
}

// popupBox:纯 ASCII rounded border(无 ANSI),overlay 安全。返回多行。
func popupBox(dragging bool, x, y int) []string {
	w := popupW
	pad := func(s string, width int) string {
		if len(s) >= width {
			return s[:width]
		}
		return s + strings.Repeat(" ", width-len(s))
	}
	status := "idle       "
	if dragging {
		status = "DRAGGING ●"
	}
	return []string{
		"╭" + strings.Repeat("─", w-2) + "╮",
		"│ " + pad("✦ Drag me!", w-4) + " │",
		"│" + strings.Repeat(" ", w-2) + "│",
		"│ " + pad("left-click + drag", w-4) + " │",
		"│ " + pad(fmt.Sprintf("pos (%d,%d) %s", x, y, status), w-4) + " │",
		"│" + strings.Repeat(" ", w-2) + "│",
		"╰" + strings.Repeat("─", w-2) + "╯",
	}
}

// overlayASCIIDecorated:背景行可能含 ANSI(淡色),弹窗纯 ASCII 覆盖到 x,y。
// 取每背景行的可见长度(忽略 ANSI)覆盖弹窗 ASCII,再保留原 ANSI。
// ponytail:ANSI 感知 overlay 简化版 — 按 rune 覆盖,假设背景 ANSI 集中在行首(bgStyle 整行)。
func overlayASCIIDecorated(bg []string, pop []string, x, y int) string {
	out := make([]string, len(bg))
	copy(out, bg)
	for i, pl := range pop {
		by := y + i
		if by < 0 || by >= len(out) {
			continue
		}
		// 背景行:剥离 ANSI 得可见部分,覆盖弹窗 rune,重染
		plain := stripAnsi(out[by])
		row := []rune(plain)
		for len(row) < x {
			row = append(row, ' ')
		}
		for j, c := range pl {
			bx := x + j
			for len(row) <= bx {
				row = append(row, ' ')
			}
			row[bx] = c
		}
		// 弹窗部分用高亮色,其余背景淡色
		before := string(row[:min(x, len(row))])
		mid := ""
		if x < len(row) {
			midEnd := min(x+len(pl), len(row))
			mid = string(row[x:midEnd])
		}
		after := ""
		if x+len(pl) < len(row) {
			after = string(row[x+len(pl):])
		}
		line := lipgloss.NewStyle().Foreground(lipgloss.Color("238")).Render(before) +
			lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("51")).Render(mid) +
			lipgloss.NewStyle().Foreground(lipgloss.Color("238")).Render(after)
		out[by] = line
	}
	return strings.Join(out, "\n")
}

func stripAnsi(s string) string {
	var b strings.Builder
	in := false
	for _, r := range s {
		if r == '\x1b' {
			in = true
			continue
		}
		if in {
			if r == 'm' {
				in = false
			}
			continue
		}
		b.WriteRune(r)
	}
	return b.String()
}

func clamp(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}
func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func main() {
	if len(os.Args) > 1 && os.Args[1] == "--dump" {
		m := initialModel()
		m.width, m.height = 80, 18
		fmt.Println(m.View())
		return
	}
	p := tea.NewProgram(initialModel(), tea.WithAltScreen(), tea.WithMouseCellMotion())
	if _, err := p.Run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
