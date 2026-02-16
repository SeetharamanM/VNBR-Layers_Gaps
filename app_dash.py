"""
Layer Coverage Viewer - Plotly Dash
X-axis: 0-1000 m · Y-axis: chunks · Bars at stretch chainages · Overlap analysis per layer
"""

import re
import base64
import io
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import dash
from dash import dcc, html, callback, Input, Output, State, dash_table
import pandas as pd
import plotly.graph_objects as go

CHUNK_SIZE = 1000
LAYER_COLORS = {
    "Subgrade": "#238636",
    "Embankment EW": "#8957e5",
}
DEFAULT_COLORS = ["#238636", "#8957e5", "#1f6feb", "#d29922", "#db61a2"]

app = dash.Dash(__name__, title="Layer Coverage Viewer")
server = app.server


def parse_date(s: str) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    s = str(s).strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def parse_stretch(s: str) -> Optional[Tuple[int, int]]:
    if not s or not isinstance(s, str):
        return None
    m = re.match(r"^(\d+)\s*-\s*(\d+)$", str(s).strip())
    if not m:
        return None
    start, end = int(m[1]), int(m[2])
    return (start, end) if start <= end else None


def parse_csv(content: str):
    df = pd.read_csv(io.StringIO(content))
    cols = [c for c in df.columns if c]
    lower = [str(c).lower() for c in cols]
    item_idx = next((i for i, c in enumerate(lower) if c in ("item", "layer")), -1)
    stretch_idx = next((i for i, c in enumerate(lower) if c in ("stretch", "chainage")), -1)
    bill_idx = next((i for i, c in enumerate(lower) if "bill" in c), -1)
    est_idx = next((i for i, c in enumerate(lower) if "est" in c and "length" in c), -1)
    date_idx = next((i for i, c in enumerate(lower) if c in ("date", "end date")), -1)
    if item_idx < 0 or stretch_idx < 0:
        raise ValueError('CSV must have "Item" (or Layer) and "Stretch" columns')
    records = []
    route_extent = 0
    for _, row in df.iterrows():
        item = str(row.iloc[item_idx] or "").strip()
        stretch = str(row.iloc[stretch_idx] or "").strip()
        if not item or not stretch:
            continue
        span = parse_stretch(stretch)
        if not span:
            continue
        rec = {"layer": item, "start": span[0], "end": span[1]}
        if bill_idx >= 0:
            bill = str(row.iloc[bill_idx] or "").strip()
            if bill:
                rec["bill"] = bill
        if est_idx >= 0:
            try:
                est = int(str(row.iloc[est_idx] or "0").replace(",", ""))
                if est > 0:
                    route_extent = est
            except (ValueError, TypeError):
                pass
        if date_idx >= 0:
            dt = parse_date(str(row.iloc[date_idx] or ""))
            if dt:
                rec["month"] = f"{dt.year}-{dt.month:02d}"
        records.append(rec)
    if not route_extent and records:
        route_extent = max(r["end"] for r in records) - min(r["start"] for r in records)
    return records, route_extent or 8000


def build_stretch_segments(records: list[dict]) -> dict:
    segments = []
    layers = set()
    chunks = set()

    for r in records:
        layers.add(r["layer"])
        c_start = (r["start"] // CHUNK_SIZE) * CHUNK_SIZE
        c_end = (r["end"] // CHUNK_SIZE) * CHUNK_SIZE
        c = c_start
        while c <= c_end:
            chunks.add(c)
            seg_start = max(r["start"], c)
            seg_end = min(r["end"], c + CHUNK_SIZE)
            if seg_start < seg_end:
                rel_start = seg_start - c
                rel_end = seg_end - c
                segments.append({
                    "chunk_start": c,
                    "chunk_label": f"{c}-{c + CHUNK_SIZE}",
                    "layer": r["layer"],
                    "rel_start": rel_start,
                    "rel_end": rel_end,
                    "abs_start": seg_start,
                    "abs_end": seg_end,
                })
            c += CHUNK_SIZE

    segments.sort(key=lambda s: (s["chunk_start"], s["layer"], s["rel_start"]))
    layers = sorted(layers)
    chunks = sorted(chunks)
    return {"segments": segments, "layers": layers, "chunks": chunks}


def merge_intervals(intervals: list[dict]) -> list[dict]:
    if not intervals:
        return []
    sorted_i = sorted(intervals, key=lambda x: x["start"])
    merged = [dict(sorted_i[0])]
    for cur in sorted_i[1:]:
        last = merged[-1]
        if cur["start"] <= last["end"]:
            last["end"] = max(last["end"], cur["end"])
        else:
            merged.append(dict(cur))
    return merged


def find_overlaps_within_layer(records: list) -> dict:
    by_layer: dict = {}
    for r in records:
        by_layer.setdefault(r["layer"], []).append({"start": r["start"], "end": r["end"]})
    result = {}
    for layer, intervals in by_layer.items():
        overlaps = []
        for i in range(len(intervals)):
            for j in range(i + 1, len(intervals)):
                a, b = intervals[i], intervals[j]
                start = max(a["start"], b["start"])
                end = min(a["end"], b["end"])
                if start < end:
                    overlaps.append({"start": start, "end": end})
        merged = merge_intervals(overlaps)
        result[layer] = [{"start": m["start"], "end": m["end"], "len": m["end"] - m["start"]} for m in merged]
    return result


def find_gaps_per_layer(records: list) -> dict:
    min_start = min(r["start"] for r in records)
    max_end = max(r["end"] for r in records)
    by_layer: dict = {}
    for r in records:
        by_layer.setdefault(r["layer"], []).append({"start": r["start"], "end": r["end"]})
    result = {}
    for layer, intervals in by_layer.items():
        merged = merge_intervals(intervals)
        gaps = []
        pos = min_start
        for seg in merged:
            if pos < seg["start"]:
                gaps.append({"start": pos, "end": seg["start"], "len": seg["start"] - pos})
            pos = max(pos, seg["end"])
        if pos < max_end:
            gaps.append({"start": pos, "end": max_end, "len": max_end - pos})
        result[layer] = gaps
    return result


def get_color(layer: str, index: int) -> str:
    return LAYER_COLORS.get(layer, DEFAULT_COLORS[index % len(DEFAULT_COLORS)])


def build_chart_figure(segments: list, layers: list, chunks: list) -> go.Figure:
    labels = []
    for c in chunks:
        for layer in layers:
            labels.append(f"{c}-{c + CHUNK_SIZE} | {layer}")

    def row_index(chunk_start: int, layer: str) -> int:
        return chunks.index(chunk_start) * len(layers) + layers.index(layer)

    layer_idx = {l: i for i, l in enumerate(layers)}

    fig = go.Figure()
    for seg in segments:
        row_idx = row_index(seg["chunk_start"], seg["layer"])
        y_label = labels[row_idx]
        color = get_color(seg["layer"], layer_idx.get(seg["layer"], 0))
        fig.add_trace(go.Bar(
            name=seg["layer"],
            x=[seg["rel_end"] - seg["rel_start"]],
            y=[y_label],
            base=[seg["rel_start"]],
            orientation="h",
            marker_color=color,
            marker_line_width=1,
            marker_line_color=color,
            hovertext=f"{seg['layer']}: {seg['abs_start']}–{seg['abs_end']} m ({seg['rel_end'] - seg['rel_start']} m)",
            hoverinfo="text",
            showlegend=False,
        ))

    fig.update_layout(
        barmode="overlay",
        xaxis=dict(
            range=[0, CHUNK_SIZE],
            title="Chainage (0–1000 m)",
            dtick=200,
            gridcolor="rgba(128,128,128,0.3)",
        ),
        yaxis=dict(
            title="Chunk (m)",
            categoryorder="array",
            categoryarray=labels,
            gridcolor="rgba(128,128,128,0.3)",
        ),
        plot_bgcolor="#161b22",
        paper_bgcolor="#161b22",
        font=dict(color="#e6edf3", size=12),
        margin=dict(l=80, r=40, t=40, b=60),
        height=max(360, min(600, len(labels) * 28)),
    )
    return fig


def filter_records(records: list, filter_bills: list, filter_months: list) -> list:
    out = []
    for r in records:
        if filter_bills and r.get("bill") and r["bill"] not in filter_bills:
            continue
        if filter_months and r.get("month") and r["month"] not in filter_months:
            continue
        out.append(r)
    return out


def compute_progress(records: list, route_extent: int) -> dict:
    by_layer_intervals: dict = {}
    by_bill_layer: dict = {}
    for r in records:
        if r["layer"] not in by_layer_intervals:
            by_layer_intervals[r["layer"]] = []
        by_layer_intervals[r["layer"]].append({"start": r["start"], "end": r["end"]})
        if r.get("bill"):
            key = f"{r['bill']}|{r['layer']}"
            by_bill_layer[key] = by_bill_layer.get(key, 0) + (r["end"] - r["start"])
    per_layer = {}
    for layer, intervals in by_layer_intervals.items():
        merged = merge_intervals(intervals)
        total = sum(m["end"] - m["start"] for m in merged)
        per_layer[layer] = {"len": total, "pct": (total / route_extent * 100) if route_extent else 0}
    all_intervals = [{"start": r["start"], "end": r["end"]} for r in records]
    merged_all = merge_intervals(all_intervals)
    overall_len = sum(m["end"] - m["start"] for m in merged_all)
    overall_pct = (overall_len / route_extent * 100) if route_extent else 0
    per_layer_per_bill = []
    for key, ln in by_bill_layer.items():
        bill, layer = key.split("|", 1)
        per_layer_per_bill.append({
            "bill": bill, "layer": layer, "len": ln,
            "pct": (ln / route_extent * 100) if route_extent else 0,
        })
    per_layer_per_bill.sort(key=lambda x: (x["bill"], x["layer"]))
    return {
        "route_extent": route_extent,
        "overall_len": overall_len,
        "overall_pct": overall_pct,
        "per_layer": per_layer,
        "per_layer_per_bill": per_layer_per_bill,
    }


def get_available_data_files() -> list:
    return sorted(Path(__file__).parent.glob("*.csv"))


def load_data_file(filename: str) -> str:
    path = Path(__file__).parent / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "Item,Stretch\nSubgrade,100-150\nSubgrade,600-800\nSubgrade,1400-1600\nEmbankment EW,100-150\nEmbankment EW,600-800\nEmbankment EW,1400-1600"


DATA_FILES = get_available_data_files()
DATA_FILE_OPTIONS = [{"label": f.name, "value": f.name} for f in DATA_FILES]
DEFAULT_DATA_FILE = DATA_FILES[0].name if DATA_FILES else None


# Layout
app.layout = html.Div(
    style={
        "fontFamily": "Outfit, system-ui, sans-serif",
        "backgroundColor": "#0d1117",
        "color": "#e6edf3",
        "minHeight": "100vh",
        "padding": "24px",
    },
    children=[
        html.Div(
            style={"maxWidth": "1200px", "margin": "0 auto"},
            children=[
                html.Header(
                    style={"marginBottom": "32px", "borderBottom": "1px solid #30363d", "paddingBottom": "20px"},
                    children=[
                        html.H1("Layer Coverage Viewer", style={"margin": "0 0 6px 0", "fontSize": "1.75rem"}),
                        html.P(
                            "X-axis: 0–1000 · Y-axis: chunks · Overlap & gap analysis per layer",
                            style={"color": "#8b949e", "margin": 0},
                        ),
                    ],
                ),
                dcc.Upload(
                    id="upload-data",
                    children=html.Div(
                        [
                            "📂 Drop CSV here or click to upload",
                            html.Br(),
                            html.Small("Expected columns: Item (layer), Stretch (e.g. 500-1000)", style={"opacity": 0.8}),
                        ]
                    ),
                    style={
                        "border": "2px dashed #30363d",
                        "borderRadius": "12px",
                        "padding": "40px",
                        "textAlign": "center",
                        "cursor": "pointer",
                        "backgroundColor": "#161b22",
                        "marginBottom": "24px",
                    },
                ),
                html.Div(
                    id="data-file-section",
                    style={"marginBottom": "24px", "display": "flex", "flexWrap": "wrap", "alignItems": "center", "gap": "12px"},
                    children=[
                        dcc.Dropdown(
                            id="sample-file-dropdown",
                            options=DATA_FILE_OPTIONS,
                            value=DEFAULT_DATA_FILE,
                            style={"minWidth": "180px"} if DATA_FILES else {"display": "none"},
                        ),
                        html.Button(
                            "Load data file" if DATA_FILES else "Try sample data",
                            id="load-sample",
                            n_clicks=0,
                            style={
                                "color": "#58a6ff",
                                "fontSize": "0.9rem",
                                "background": "none",
                                "border": "none",
                                "cursor": "pointer",
                                "padding": 0,
                            },
                        ),
                    ],
                ),
                html.Div(
                    id="progress-filters",
                    style={"display": "none", "marginBottom": "16px"},
                    children=[
                        html.Div(
                            style={"display": "flex", "flexWrap": "wrap", "alignItems": "flex-end", "gap": "12px", "padding": "12px", "backgroundColor": "#21262d", "borderRadius": "8px"},
                            children=[
                                html.Div([
                                    html.Label("Bill No", style={"fontSize": "0.8rem", "color": "#8b949e", "display": "block", "marginBottom": "4px"}),
                                    dcc.Dropdown(id="filter-bill", options=[], value=[], multi=True, style={"minWidth": "140px"}),
                                ]),
                                html.Div([
                                    html.Label("Month", style={"fontSize": "0.8rem", "color": "#8b949e", "display": "block", "marginBottom": "4px"}),
                                    dcc.Dropdown(id="filter-month", options=[], value=[], multi=True, style={"minWidth": "120px"}),
                                ]),
                                html.Button("Apply", id="apply-progress-filter", n_clicks=0, style={"padding": "8px 16px", "backgroundColor": "#58a6ff", "color": "#fff", "border": "none", "borderRadius": "6px", "cursor": "pointer"}),
                            ],
                        ),
                    ],
                ),
                html.Div(id="output-container", children=[]),
            ],
        ),
        dcc.Location(id="url", refresh=False),
        dcc.Store(id="stored-data"),
        dcc.Store(id="records-store", data={}),
    ],
)


@callback(
    Output("stored-data", "data"),
    Output("records-store", "data"),
    Output("output-container", "children"),
    Output("filter-bill", "options"),
    Output("filter-month", "options"),
    Output("filter-bill", "value"),
    Output("filter-month", "value"),
    Output("progress-filters", "style"),
    Input("url", "pathname"),
    Input("upload-data", "contents"),
    Input("load-sample", "n_clicks"),
    Input("apply-progress-filter", "n_clicks"),
    State("records-store", "data"),
    State("filter-bill", "value"),
    State("filter-month", "value"),
    State("sample-file-dropdown", "value"),
    prevent_initial_call=False,
)
def process_upload(pathname, contents, n_clicks, apply_clicks, stored_records, filter_bill, filter_month, selected_file):
    ctx = dash.callback_context
    if not ctx.triggered:
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update
    prop = ctx.triggered[0]["prop_id"]
    if "apply-progress-filter" in prop:
        pass
    elif "load-sample" in prop or (prop == "url.pathname" and pathname and not stored_records.get("records") and DATA_FILES):
        filename = (selected_file or DEFAULT_DATA_FILE) or ""
        text = load_data_file(filename)
    elif contents:
        _, content = contents.split(",", 1)
        text = base64.b64decode(content).decode("utf-8")
    else:
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update
    if "apply-progress-filter" not in prop:
        try:
            records, route_extent = parse_csv(text)
            if not records:
                return None, {}, html.Div("No valid records found. Ensure Stretch uses format like 500-1000.", style={"color": "#f85149"}), [], [], [], [], {"display": "none"}
            all_records = records
            records = all_records
        except Exception as e:
            return None, {}, html.Div(f"Error: {e}", style={"color": "#f85149"}), [], [], [], [], {"display": "none"}
    else:
        all_records = stored_records.get("records", [])
        route_extent = stored_records.get("route_extent", 8000)
        filter_bills = filter_bill if isinstance(filter_bill, list) else ([filter_bill] if filter_bill else [])
        filter_months = filter_month if isinstance(filter_month, list) else ([filter_month] if filter_month else [])
        records = filter_records(all_records, filter_bills, filter_months)
    stretch_data = build_stretch_segments(all_records)
    overlaps_within = find_overlaps_within_layer(all_records)
    gaps = find_gaps_per_layer(all_records)
    progress_data = compute_progress(records, route_extent)
    fig = build_chart_figure(
        stretch_data["segments"],
        stretch_data["layers"],
        stretch_data["chunks"],
    )
    legend_html = html.Div(
        style={"display": "flex", "gap": "20px", "flexWrap": "wrap", "marginTop": "16px", "fontSize": "0.85rem", "color": "#8b949e"},
        children=[
            html.Div(
                style={"display": "flex", "alignItems": "center", "gap": "8px"},
                children=[
                    html.Span(style={"width": "14px", "height": "14px", "backgroundColor": get_color(l, i), "borderRadius": "4px"}),
                    html.Span(l),
                ],
            )
            for i, l in enumerate(stretch_data["layers"])
        ],
    )
    analysis_panels = []
    for i, layer in enumerate(stretch_data["layers"]):
        ov_within = overlaps_within.get(layer, [])
        gs = gaps.get(layer, [])
        color = get_color(layer, i)
        analysis_panels.append(
            html.Div(
                style={
                    "backgroundColor": "#161b22",
                    "border": "1px solid #30363d",
                    "borderRadius": "12px",
                    "borderLeft": f"3px solid {color}",
                    "padding": "20px",
                },
                children=[
                    html.H2(layer, style={"fontSize": "1rem", "margin": "0 0 16px 0"}),
                    html.Div(
                        style={"marginBottom": "16px"},
                        children=[
                            html.H3(
                                [f"Overlaps within layer ", html.Span(str(len(ov_within)), style={"background": "#d29922", "color": "#0d1117", "padding": "2px 8px", "borderRadius": "999px", "fontSize": "0.7rem"})],
                                style={"fontSize": "0.8rem", "color": "#8b949e", "margin": "0 0 8px 0"},
                            ),
                            html.Ul(
                                children=[
                                    html.Li(
                                        style={
                                            "fontFamily": "monospace",
                                            "fontSize": "0.85rem",
                                            "padding": "10px 12px",
                                            "backgroundColor": "#21262d",
                                            "borderRadius": "8px",
                                            "marginBottom": "6px",
                                            "listStyle": "none",
                                            "display": "flex",
                                            "justifyContent": "space-between",
                                        },
                                        children=[
                                            html.Span(f"{o['start']}–{o['end']} m", style={"color": "#58a6ff"}),
                                            html.Span(f"{o['len']} m", style={"color": "#8b949e", "fontSize": "0.8rem"}),
                                        ],
                                    )
                                    for o in ov_within
                                ]
                                if ov_within
                                else [html.Li("None", style={"listStyle": "none", "color": "#8b949e"})],
                                style={"padding": 0, "margin": 0, "maxHeight": "140px", "overflowY": "auto"},
                            ),
                        ],
                    ),
                    html.Div(
                        children=[
                            html.H3(
                                [f"Gaps ", html.Span(str(len(gs)), style={"background": "#f0883e", "color": "#0d1117", "padding": "2px 8px", "borderRadius": "999px", "fontSize": "0.7rem"})],
                                style={"fontSize": "0.8rem", "color": "#8b949e", "margin": "0 0 8px 0"},
                            ),
                            html.Ul(
                                children=[
                                    html.Li(
                                        style={
                                            "fontFamily": "monospace",
                                            "fontSize": "0.85rem",
                                            "padding": "10px 12px",
                                            "backgroundColor": "#21262d",
                                            "borderRadius": "8px",
                                            "marginBottom": "6px",
                                            "listStyle": "none",
                                            "display": "flex",
                                            "justifyContent": "space-between",
                                        },
                                        children=[
                                            html.Span(f"{g['start']}–{g['end']} m", style={"color": "#58a6ff"}),
                                            html.Span(f"{g['len']} m", style={"color": "#8b949e", "fontSize": "0.8rem"}),
                                        ],
                                    )
                                    for g in gs
                                ]
                                if gs
                                else [html.Li("None", style={"listStyle": "none", "color": "#8b949e"})],
                                style={"padding": 0, "margin": 0, "maxHeight": "140px", "overflowY": "auto"},
                            ),
                        ],
                    ),
                ],
            )
        )
    prog = progress_data
    bills = sorted(set(r.get("bill") for r in all_records if r.get("bill")))
    months = sorted(set(r.get("month") for r in all_records if r.get("month")))
    month_labels = {m: f"{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][int(m.split('-')[1])-1]} {m.split('-')[0]}" for m in months}
    filter_bill_val = filter_bill if isinstance(filter_bill, list) else ([filter_bill] if filter_bill else [])
    filter_month_val = filter_month if isinstance(filter_month, list) else ([filter_month] if filter_month else [])
    filter_bill_opts = [{"label": b, "value": b} for b in bills]
    filter_month_opts = [{"label": month_labels.get(m, m), "value": m} for m in months]
    filters_style = {"display": "block", "marginBottom": "16px"} if (bills or months) else {"display": "none", "marginBottom": "16px"}
    progress_children = [
        html.H2("Progress", style={"fontSize": "1rem", "margin": "0 0 16px 0"}),
        html.P(f"{len(records)} of {len(all_records)} records", style={"fontSize": "0.85rem", "color": "#8b949e", "margin": "-4px 0 16px 0"}),
    ]
    progress_children.extend([
        html.Div(
            style={"marginBottom": "16px"},
            children=[
                html.Div(
                    style={"display": "flex", "justifyContent": "space-between", "fontSize": "0.9rem", "marginBottom": "4px"},
                    children=[
                        html.Span("Overall"),
                        html.Span(f"{prog['overall_len']:.0f} m / {prog['route_extent']} m · {prog['overall_pct']:.1f}%"),
                    ],
                ),
                html.Div(
                    style={"height": "12px", "backgroundColor": "#21262d", "borderRadius": "6px", "overflow": "hidden"},
                    children=html.Div(
                        style={"width": f"{min(100, prog['overall_pct'])}%", "height": "100%", "backgroundColor": "#58a6ff", "borderRadius": "6px"},
                    ),
                ),
            ],
        ),
        html.H3("Per layer", style={"fontSize": "0.9rem", "color": "#8b949e", "margin": "16px 0 12px 0"}),
    ])
    for i, layer in enumerate(stretch_data["layers"]):
        d = prog["per_layer"].get(layer, {"pct": 0})
        progress_children.append(
            html.Div(
                style={"marginBottom": "12px"},
                children=[
                    html.Span(layer, style={"paddingLeft": "8px", "borderLeft": f"3px solid {get_color(layer, i)}", "fontSize": "0.9rem", "minWidth": "120px", "display": "inline-block"}),
                    html.Span(f"{d['pct']:.1f}%", style={"fontSize": "0.9rem", "color": "#8b949e", "marginLeft": "8px"}),
                    html.Div(
                        style={"height": "8px", "backgroundColor": "#21262d", "borderRadius": "6px", "overflow": "hidden", "marginTop": "4px"},
                        children=html.Div(style={"width": f"{min(100, d['pct'])}%", "height": "100%", "backgroundColor": "#58a6ff", "borderRadius": "6px"}),
                    ),
                ],
            )
        )
    bill_sections = []
    if prog["per_layer_per_bill"]:
        bill_sections.append(html.H3("Per layer in each bill", style={"fontSize": "0.9rem", "color": "#8b949e", "margin": "16px 0 12px 0"}))
        for bill in sorted(set(p["bill"] for p in prog["per_layer_per_bill"])):
            items = [p for p in prog["per_layer_per_bill"] if p["bill"] == bill]
            if not items:
                continue
            bill_items = []
            for p in items:
                li = stretch_data["layers"].index(p["layer"]) if p["layer"] in stretch_data["layers"] else 0
                bill_items.append(
                    html.Div(
                        style={"marginBottom": "8px"},
                        children=[
                            html.Span(p["layer"], style={"paddingLeft": "8px", "borderLeft": f"3px solid {get_color(p['layer'], li)}", "fontSize": "0.85rem", "display": "inline-block", "minWidth": "100px"}),
                            html.Span(f"{p['pct']:.1f}%", style={"fontSize": "0.85rem", "color": "#8b949e"}),
                            html.Div(
                                style={"height": "6px", "backgroundColor": "#161b22", "borderRadius": "4px", "overflow": "hidden", "marginTop": "2px"},
                                children=html.Div(style={"width": f"{min(100, p['pct'])}%", "height": "100%", "backgroundColor": "#58a6ff", "borderRadius": "4px"}),
                            ),
                        ],
                    )
                )
            bill_sections.append(
                html.Div(
                    style={"marginBottom": "16px", "padding": "12px", "backgroundColor": "#21262d", "borderRadius": "8px"},
                    children=[html.H4(bill, style={"fontSize": "0.85rem", "margin": "0 0 10px 0", "color": "#58a6ff"}), *bill_items],
                )
            )
    progress_children.extend(bill_sections)
    progress_div = html.Div(
        style={"backgroundColor": "#161b22", "border": "1px solid #30363d", "borderRadius": "12px", "padding": "24px", "marginBottom": "24px"},
        children=progress_children,
    )
    output_children = [
        html.Div(
            style={"backgroundColor": "#161b22", "border": "1px solid #30363d", "borderRadius": "12px", "padding": "24px", "marginBottom": "24px"},
            children=[
                html.H2("X-axis: 0–1000 · Y-axis: chunks × layers (one row per layer per chunk)", style={"fontSize": "1rem", "margin": "0 0 20px 0"}),
                dcc.Graph(figure=fig, config={"displayModeBar": True, "responsive": True}, style={"width": "100%"}),
                legend_html,
            ],
        ),
        progress_div,
    ]
    analysis_div = html.Div(
        style={"display": "grid", "gridTemplateColumns": "repeat(auto-fill, minmax(340px, 1fr))", "gap": "24px"},
        children=analysis_panels,
    )
    output_children.append(analysis_div)
    records_store_data = {"records": all_records, "route_extent": route_extent}
    return stretch_data, records_store_data, html.Div(children=output_children), filter_bill_opts, filter_month_opts, filter_bill_val, filter_month_val, filters_style


if __name__ == "__main__":
    app.run(debug=True, port=8050)
