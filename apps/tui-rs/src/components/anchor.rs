//! 锚点连接线:可复用 Anchor + Connection(ratatui 0.28 canvas 薄封装)。
//!
//! ratatui canvas 提供 Canvas + Line + Points + ctx.print,Marker::Braille 给最细对角线
//! (2x4 dots/cell)。本控件包薄层 Anchor{id,x,y}+Connection{from,to,color,label?},
//! render 成 Canvas widget。调用方给 anchors + connections,控件画连线(+ 可选标签)。
//!
//! 0.28 陷阱(已核实 ratatui-0.28.1/src/widgets/canvas.rs):方法是 .paint(|ctx| ctx.draw(&Line{..})),
//! NOT .draw(|ctx|...)(那是 0.29+ 幻觉,不编译);ctx.print 的 TextLine 实为 text::Line 别名
//! (canvas.rs:33),&str 可直接传;Line/Points 是 pub 字段 struct(非 builder);闭包需 'static+Fn,
//! 把数据 clone 进去(Anchor 实现 Copy 最省)。坐标超出 x_bounds/y_bounds 静默裁剪。
//! ponytail: 无社区 graph crate 采纳(tui-nodes 失维;ratatui_flow 太重)。canvas 即边渲染器。

#![allow(dead_code)]

use ratatui::{
    style::Color,
    symbols::Marker,
    widgets::canvas::{Canvas, Line, Points},
};

/// 锚点:id + 世界坐标(x,y)。
#[derive(Clone, Copy, Debug)]
pub struct Anchor {
    pub id: u32,
    pub x: f64,
    pub y: f64,
}

/// 连接:from→to 锚点 id + 色 + 可选标签(画在线中点)。
#[derive(Clone, Debug)]
pub struct Connection {
    pub from: u32,
    pub to: u32,
    pub color: Color,
    pub label: Option<String>,
}

/// 锚点图:anchors + connections + 世界边界。
#[derive(Clone, Debug, Default)]
pub struct AnchorGraph {
    pub anchors: Vec<Anchor>,
    pub connections: Vec<Connection>,
    pub x_bounds: [f64; 2],
    pub y_bounds: [f64; 2],
}

impl AnchorGraph {
    pub fn new(x_bounds: [f64; 2], y_bounds: [f64; 2]) -> Self {
        Self { anchors: vec![], connections: vec![], x_bounds, y_bounds }
    }
    pub fn anchor(mut self, id: u32, x: f64, y: f64) -> Self {
        self.anchors.push(Anchor { id, x, y });
        self
    }
    pub fn edge(mut self, from: u32, to: u32, color: Color) -> Self {
        self.connections.push(Connection { from, to, color, label: None });
        self
    }
    pub fn edge_labeled(mut self, from: u32, to: u32, color: Color, label: impl Into<String>) -> Self {
        self.connections.push(Connection { from, to, color, label: Some(label.into()) });
        self
    }

    /// 渲染成 Canvas widget(Braille marker + Line + Points + 标签)。
    /// 调用方:f.render_widget(graph.canvas(), area)。
    /// ponytail: 每帧 clone anchors/conns(小图 O(n));anchor 多了上 Arc<[Anchor]>。
    pub fn canvas(&self) -> Canvas<'static, impl Fn(&mut ratatui::widgets::canvas::Context)> {
        let anchors = self.anchors.clone();
        let conns = self.connections.clone();
        let xb = self.x_bounds;
        let yb = self.y_bounds;
        Canvas::default()
            .marker(Marker::Braille)
            .x_bounds(xb)
            .y_bounds(yb)
            .paint(move |ctx| {
                for c in &conns {
                    let (Some(f), Some(t)) = (anchors.iter().find(|a| a.id == c.from), anchors.iter().find(|a| a.id == c.to))
                    else { continue };
                    ctx.draw(&Line { x1: f.x, y1: f.y, x2: t.x, y2: t.y, color: c.color });
                    if let Some(lbl) = &c.label {
                        let mx = (f.x + t.x) / 2.0;
                        let my = (f.y + t.y) / 2.0;
                        // 传 owned String(Line: From<String>,Span Cow::Owned → 满足任意 'a),
                        // 断开对闭包捕获 conns 的借用——ctx.print 把 Line 存进 Vec<Label<'a>>,
                        // Fn 闭包 + invariant 下 &str 借用会逃逸失败。
                        ctx.print(mx, my, lbl.clone());
                    }
                }
                let coords: Vec<(f64, f64)> = anchors.iter().map(|a| (a.x, a.y)).collect();
                ctx.draw(&Points { coords: &coords, color: Color::Yellow });
            })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn graph_builder_edges_and_labels() {
        let g = AnchorGraph::new([0.0, 100.0], [0.0, 100.0])
            .anchor(1, 10.0, 10.0)
            .anchor(2, 80.0, 80.0)
            .edge(1, 2, Color::Cyan)
            .edge_labeled(2, 1, Color::Red, "back");
        assert_eq!(g.anchors.len(), 2);
        assert_eq!(g.connections.len(), 2);
        assert!(g.connections[0].label.is_none());
        assert_eq!(g.connections[1].label.as_deref(), Some("back"));
    }

    #[test]
    fn canvas_builds_without_panic() {
        let g = AnchorGraph::new([0.0, 50.0], [0.0, 50.0])
            .anchor(1, 5.0, 5.0)
            .anchor(2, 40.0, 40.0)
            .edge(1, 2, Color::Green);
        // canvas() 返回 Canvas widget(闭包捕获 clone 数据);构造不渲染。
        let _ = g.canvas();
    }
}
