"""Grid maze router for new wires (A* over 0.1 inch grid nodes, with a bend penalty).

Rules, so that a new wire never looks like (or later becomes) a connection it is not:
- never pass through a pin end, wire end, junction, terminal or a part body (box spanned by its pin anchors);
- never run along an existing wire or pin line; crossing one at right angles is allowed (no dot = no join);
- never turn on top of an existing wire;
- leave a pin in the direction it faces first.
Off-grid pins (some parts sit on half-grid positions) get a short jog onto the grid.
"""
from __future__ import annotations

import heapq
import math

from . import dsn
from .devices import GLYPH_LENGTH, pins

GRID = dsn.GRID
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))
BEND = 4
CROSS = 2


def _g(v: int) -> float:
    return v / GRID


def _is_int(v: float) -> bool:
    return abs(v - round(v)) < 1e-9


class Obstacles:
    def __init__(self, sheet: dsn.Sheet, allow: set[tuple[int, int]] = frozenset(), pad: int = 1):
        self.nodes: set[tuple[int, int]] = set()       # grid nodes a route may not touch
        self.edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()   # unit edges taken by wires/pins
        self.on_wire: set[tuple[int, int]] = set()     # nodes inside existing wires: may cross, not turn
        allow_g = {(_g(x), _g(y)) for x, y in allow}

        def block(pt):
            gx, gy = _g(pt[0]), _g(pt[1])
            if (gx, gy) in allow_g:
                return
            if _is_int(gx) and _is_int(gy):
                self.nodes.add((round(gx), round(gy)))

        def seg(a, b, interior_nodes: set | None):
            ax, ay, bx, by = _g(a[0]), _g(a[1]), _g(b[0]), _g(b[1])
            if ax == bx and _is_int(ax):
                x = round(ax)
                lo, hi = sorted((ay, by))
                ys = range(math.ceil(lo - 1e-9), math.floor(hi + 1e-9) + 1)
                for y in ys:
                    if interior_nodes is not None:
                        interior_nodes.add((x, y))
                    if y + 1 <= hi + 1e-9:
                        self.edges.add(((x, y), (x, y + 1)))
            elif ay == by and _is_int(ay):
                y = round(ay)
                lo, hi = sorted((ax, bx))
                for x in range(math.ceil(lo - 1e-9), math.floor(hi + 1e-9) + 1):
                    if interior_nodes is not None:
                        interior_nodes.add((x, y))
                    if x + 1 <= hi + 1e-9:
                        self.edges.add(((x, y), (x + 1, y)))

        for c in sheet.components():
            try:
                ps = pins(sheet, c)
            except dsn.DsnError:
                # unknown pin table: block a box round the origin so nothing is drawn through it
                ox, oy = round(_g(c["origin"][0])), round(_g(c["origin"][1]))
                self.nodes |= {(ox + i, oy + j) for i in range(-3, 4) for j in range(-3, 4)}
                continue
            vis = [p for p in ps if not p["hidden"]]
            if not vis:
                continue
            anchors = []
            for p in vis:
                L = GLYPH_LENGTH.get(p["glyph"], 2 * GRID)
                a = [p["xy"][0] - p["out"][0] * L, p["xy"][1] - p["out"][1] * L]
                anchors.append(a)
                block(p["xy"])
                pin_nodes: set = set()
                seg(p["xy"], a, pin_nodes)
                self.nodes |= {n for n in pin_nodes if (n[0] * GRID, n[1] * GRID) != tuple(p["xy"])}
            xs = [_g(a[0]) for a in anchors]
            ys = [_g(a[1]) for a in anchors]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            if x1 - x0 < 1:
                x0, x1 = x0 - 1, x1 + 1
            if y1 - y0 < 1:
                y0, y1 = y0 - 1, y1 + 1
            if len(vis) == 1:              # one-pin parts (LOGICSTATE, probes): body opposite the pin
                o = vis[0]["out"]
                x0, x1 = min(x0, x0 - 2 * o[0]), max(x1, x1 - 2 * o[0])
                y0, y1 = min(y0, y0 - 2 * o[1]), max(y1, y1 - 2 * o[1])
            # the drawn body reaches about a grid past its outermost pins (chip outlines, resistor ends)
            x0, x1, y0, y1 = x0 - pad, x1 + pad, y0 - pad, y1 + pad
            for x in range(math.ceil(x0), math.floor(x1) + 1):
                for y in range(math.ceil(y0), math.floor(y1) + 1):
                    self.nodes.add((x, y))
        for w in sheet.wires():
            block(w["points"][0])
            block(w["points"][-1])
            inner: set = set()
            for a, b in zip(w["points"], w["points"][1:]):
                seg(a, b, inner)
            self.on_wire |= inner
        for o in sheet.objects:
            if o["type"] == "junction":
                block(o["origin"])
            elif o["type"] == "terminal":
                block(o["origin"])
            elif o["type"] == "instrument":
                ox, oy = round(_g(o["origin"][0])), round(_g(o["origin"][1]))
                self.nodes |= {(ox + i, oy + j) for i in range(-3, 4) for j in range(-1, 2)}
        for x, y in allow_g:
            if _is_int(x) and _is_int(y):
                self.nodes.discard((round(x), round(y)))

    def edge_free(self, u, v) -> bool:
        a, b = (u, v) if u <= v else (v, u)
        return (a, b) not in self.edges


def path_clear(ob: Obstacles, pts: list[list[int]]) -> bool:
    """True if an existing path (raw points) touches no blocked grid node other than its two ends and runs
    along no existing wire or pin line."""
    ends = {(_g(pts[0][0]), _g(pts[0][1])), (_g(pts[-1][0]), _g(pts[-1][1]))}
    for a, b in zip(pts, pts[1:]):
        ax, ay, bx, by = _g(a[0]), _g(a[1]), _g(b[0]), _g(b[1])
        if ax == bx and _is_int(ax):
            lo, hi = sorted((ay, by))
            line = [(round(ax), y) for y in range(math.ceil(lo - 1e-9), math.floor(hi + 1e-9) + 1)]
        elif ay == by and _is_int(ay):
            lo, hi = sorted((ax, bx))
            line = [(x, round(ay)) for x in range(math.ceil(lo - 1e-9), math.floor(hi + 1e-9) + 1)]
        else:
            continue
        for n in line:
            if n in ob.nodes and n not in ends:
                return False
        for u, v in zip(line, line[1:]):
            if not ob.edge_free(u, v):
                return False
    return True


def entry(xy: list[int], out) -> list[list[int]]:
    """Points from a pin end onto the nearest grid node, moving out of the pin first."""
    gx, gy = _g(xy[0]), _g(xy[1])
    if out[0]:
        nx = math.floor(gx) + 1 if out[0] > 0 else math.ceil(gx) - 1
        if _is_int(gx):
            nx = round(gx) + out[0]
        p1 = [nx, gy]
        p2 = [nx, round(gy)]
    else:
        ny = math.floor(gy) + 1 if out[1] > 0 else math.ceil(gy) - 1
        if _is_int(gy):
            ny = round(gy) + out[1]
        p1 = [gx, ny]
        p2 = [round(gx), ny]
    pts = [xy, [round(p1[0] * GRID), round(p1[1] * GRID)], [round(p2[0] * GRID), round(p2[1] * GRID)]]
    return [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]


def _sign(v):
    return (v > 0) - (v < 0)


def route(sheet: dsn.Sheet, start: dict, goals: dict, allow: set[tuple[int, int]],
          margin: int = 25, obstacles: Obstacles | None = None) -> tuple[list[list[int]], object]:
    """start: pin dict (xy, out).  goals: grid node -> (tail points in raw units, goal payload).
    Returns (raw points from the pin to the end of the tail, payload of the goal reached)."""
    ob = obstacles or Obstacles(sheet, allow)
    head = entry(start["xy"], start["out"])
    s = (round(_g(head[-1][0])), round(_g(head[-1][1])))
    if s in ob.nodes and s not in goals:
        raise dsn.DsnError("the pin's first grid point is blocked")
    xs = [s[0]] + [g[0] for g in goals]
    ys = [s[1]] + [g[1] for g in goals]
    box = (min(xs) - margin, max(xs) + margin, min(ys) - margin, max(ys) + margin)
    few = len(goals) <= 40
    goal_list = list(goals)

    def h(n):
        if not few:
            return 0
        return min(abs(n[0] - g[0]) + abs(n[1] - g[1]) for g in goal_list)

    first_dir = tuple(start["out"])
    if len(head) == 3:                       # entry ends with a jog: the jog sets the direction
        first_dir = (_sign(head[2][0] - head[1][0]), _sign(head[2][1] - head[1][1]))

    def tail_ok(node, d, tail):
        if not tail:
            return True
        t0 = (_sign(tail[0][0] - node[0] * GRID), _sign(tail[0][1] - node[1] * GRID))
        return t0 == (0, 0) or t0 != (-d[0], -d[1])

    open_ = [(h(s), 0, s, first_dir)]
    best = {(s, first_dir): 0}
    prev = {}
    found = None
    while open_:
        f, cost, n, d = heapq.heappop(open_)
        if best.get((n, d), 1e18) < cost:
            continue
        if n in goals and tail_ok(n, d, goals[n][0]):
            found = (n, d)
            break
        for nd in DIRS:
            if nd == (-d[0], -d[1]):
                continue
            if nd != d and n in ob.on_wire and n != s:
                continue                      # no corners on top of another wire
            m = (n[0] + nd[0], n[1] + nd[1])
            if not (box[0] <= m[0] <= box[1] and box[2] <= m[1] <= box[3]):
                continue
            if m in ob.nodes and m not in goals:
                continue
            if not ob.edge_free(n, m):
                continue
            c = cost + 1 + (BEND if nd != d else 0) + (CROSS if m in ob.on_wire and m not in goals else 0)
            if c < best.get((m, nd), 1e18):
                best[(m, nd)] = c
                prev[(m, nd)] = (n, d)
                heapq.heappush(open_, (c + h(m), c, m, nd))
    if found is None:
        raise dsn.DsnError("no free route")
    nodes = []
    k = found
    while k in prev:
        nodes.append(k[0])
        k = prev[k]
    nodes.append(s)
    nodes.reverse()
    pts = head[:-1] + [[x * GRID, y * GRID] for x, y in nodes] + list(goals[found[0]][0])
    out = []
    for q in pts:
        if out and q == out[-1]:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            if (a[0] == b[0] == q[0] or a[1] == b[1] == q[1]) and \
                    not ((b[0] - a[0]) * (q[0] - b[0]) < 0 or (b[1] - a[1]) * (q[1] - b[1]) < 0):
                out[-1] = q
                continue
        out.append(q)
    return out, goals[found[0]][1]


def pin_goal(pin: dict) -> dict:
    """Goal for routing onto a free pin: its entry node, with the entry reversed as the tail."""
    e = entry(pin["xy"], pin["out"])
    node = (round(_g(e[-1][0])), round(_g(e[-1][1])))
    return {node: (e[:-1][::-1], None)}


def wire_goals(wires: list[dict], skip: set[tuple[int, int]]) -> dict:
    """Goals on existing wires: every grid node inside their segments (raw points in `skip` excluded), and
    for off-grid segments the neighbouring grid nodes with a short last step onto the wire."""
    out = {}
    for w in wires:
        for a, b in zip(w["points"], w["points"][1:]):
            ax, ay, bx, by = _g(a[0]), _g(a[1]), _g(b[0]), _g(b[1])
            if ax == bx:
                lo, hi = sorted((ay, by))
                for y in range(math.ceil(lo), math.floor(hi) + 1):
                    raw = (a[0], y * GRID)
                    if raw in skip:
                        continue
                    if _is_int(ax):
                        out.setdefault((round(ax), y), ([], (list(raw), w)))
                    else:
                        for x in (math.floor(ax), math.ceil(ax)):
                            out.setdefault((x, y), ([list(raw)], (list(raw), w)))
            elif ay == by:
                lo, hi = sorted((ax, bx))
                for x in range(math.ceil(lo), math.floor(hi) + 1):
                    raw = (x * GRID, a[1])
                    if raw in skip:
                        continue
                    if _is_int(ay):
                        out.setdefault((x, round(ay)), ([], (list(raw), w)))
                    else:
                        for y in (math.floor(ay), math.ceil(ay)):
                            out.setdefault((x, y), ([list(raw)], (list(raw), w)))
    return out
