"""
Layer Coverage Viewer - Streamlit
X-axis: 0-1000 m · Y-axis: chunks · Bars at stretch chainages · Overlap analysis per layer
"""

import re
import io
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

CHUNK_SIZE = 1000
LAYER_COLORS = {
    "Subgrade": "#238636",
    "Embankment EW": "#8957e5",
}
DEFAULT_COLORS = ["#238636", "#8957e5", "#1f6feb", "#d29922", "#db61a2"]


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


def get_available_data_files() -> list[Path]:
    """Return list of CSV files in the project directory."""
    project_dir = Path(__file__).parent
    return sorted(project_dir.glob("*.csv"))


def load_data_file(filename: str) -> str:
    path = Path(__file__).parent / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "Item,Stretch\nSubgrade,100-150\nSubgrade,600-800\nSubgrade,1400-1600\nEmbankment EW,100-150\nEmbankment EW,600-800\nEmbankment EW,1400-1600"


st.set_page_config(page_title="Layer Coverage Viewer", layout="wide")

st.title("Layer Coverage Viewer")
st.caption("X-axis: 0–1000 · Y-axis: chunks · Overlap & gap analysis per layer")

if "records" not in st.session_state:
    st.session_state.records = None
    st.session_state.route_extent = 8000
if "ignore_upload" not in st.session_state:
    st.session_state.ignore_upload = False

data_files = get_available_data_files()
data_file_names = [f.name for f in data_files]

# Load default data file on first visit
if st.session_state.records is None and data_files:
    text = load_data_file(data_files[0].name)
    try:
        records, route_extent = parse_csv(text)
        st.session_state.records = records
        st.session_state.route_extent = route_extent
        st.rerun()
    except Exception:
        pass  # Fall through to show upload UI

col1, col2 = st.columns([3, 1])
with col1:
    uploaded_file = st.file_uploader(
        "Upload CSV",
        type=["csv"],
        help="Expected columns: Item (layer), Stretch (e.g. 500-1000)",
    )
with col2:
    if data_files:
        if len(data_files) > 1:
            selected = st.selectbox(
                "Data file",
                options=data_file_names,
                index=0,
                key="data_file_select",
            )
        else:
            selected = data_file_names[0]
        if st.button("Load data file", use_container_width=True):
            text = load_data_file(selected)
            try:
                records, route_extent = parse_csv(text)
                st.session_state.records = records
                st.session_state.route_extent = route_extent
                st.session_state.ignore_upload = True
                st.rerun()
            except Exception as e:
                st.error(str(e))
    else:
        if st.button("Try sample data", use_container_width=True):
            text = load_data_file("")
            try:
                records, route_extent = parse_csv(text)
                st.session_state.records = records
                st.session_state.route_extent = route_extent
                st.session_state.ignore_upload = True
                st.rerun()
            except Exception as e:
                st.error(str(e))

if uploaded_file and not st.session_state.ignore_upload:
    text = uploaded_file.read().decode("utf-8")
    try:
        records, route_extent = parse_csv(text)
        st.session_state.records = records
        st.session_state.route_extent = route_extent
    except Exception as e:
        st.error(str(e))
        st.stop()
st.session_state.ignore_upload = False

all_records = st.session_state.records
if not all_records:
    st.info("📂 Upload a CSV or load a data file to get started.")
    st.stop()

route_extent = st.session_state.route_extent

bills = sorted(set(r.get("bill") for r in all_records if r.get("bill")))
months = sorted(set(r.get("month") for r in all_records if r.get("month")))
month_labels = {m: f"{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][int(m.split('-')[1])-1]} {m.split('-')[0]}" for m in months}

filter_bills = []
filter_months = []
if bills or months:
    fc1, fc2 = st.columns(2)
    with fc1:
        if bills:
            filter_bills = st.multiselect("Bill No", options=bills, default=[])
    with fc2:
        if months:
            filter_months = st.multiselect("Month", options=months, default=[], format_func=lambda m: month_labels.get(m, m))

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

st.subheader("X-axis: 0–1000 · Y-axis: chunks × layers (one row per layer per chunk)")
st.plotly_chart(fig, use_container_width=True)

legend_cols = st.columns(min(5, len(stretch_data["layers"])))
for i, layer in enumerate(stretch_data["layers"]):
    with legend_cols[i % 5]:
        st.markdown(f"<span style='display:inline-block;width:14px;height:14px;background:{get_color(layer, i)};border-radius:4px;vertical-align:middle;margin-right:6px;'></span> {layer}", unsafe_allow_html=True)

st.divider()

st.subheader("Progress")
st.caption(f"{len(records)} of {len(all_records)} records")

prog = progress_data
st.markdown(f"**Overall** · {prog['overall_len']:.0f} m / {prog['route_extent']} m · {prog['overall_pct']:.1f}%")
st.progress(min(1.0, prog["overall_pct"] / 100))

st.markdown("**Per layer**")
for i, layer in enumerate(stretch_data["layers"]):
    d = prog["per_layer"].get(layer, {"pct": 0})
    st.markdown(f"{layer} · {d['pct']:.1f}%")
    st.progress(min(1.0, d["pct"] / 100))

if prog["per_layer_per_bill"]:
    st.markdown("**Per layer in each bill**")
    for bill in sorted(set(p["bill"] for p in prog["per_layer_per_bill"])):
        items = [p for p in prog["per_layer_per_bill"] if p["bill"] == bill]
        if not items:
            continue
        with st.expander(bill):
            for p in items:
                st.markdown(f"{p['layer']} · {p['pct']:.1f}%")
                st.progress(min(1.0, p["pct"] / 100))

st.divider()
st.subheader("Overlap & gap analysis")

# Fixed-height card style
st.markdown("""
<style>
.layer-card {
    background: #ffffff;
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 16px;
    height: 280px;
    overflow-y: auto;
    margin-bottom: 16px;
}
.layer-card h4 { margin: 0 0 12px 0; font-size: 1rem; }
.layer-card .summary { 
    display: flex; gap: 16px; margin-bottom: 12px; 
    font-size: 0.9rem; color: #6e7681;
}
.layer-card .summary strong { color: #1f2328; }
.layer-card ul { padding: 0 0 0 16px; margin: 0; font-size: 0.85rem; }
.layer-card li { margin-bottom: 4px; }
.layer-card .subsection { margin-bottom: 12px; }
.layer-card .subsection-title { font-size: 0.8rem; color: #6e7681; margin: 0 0 6px 0; }
</style>
""", unsafe_allow_html=True)

cols = st.columns(min(3, len(stretch_data["layers"])))
for i, layer in enumerate(stretch_data["layers"]):
    with cols[i % 3]:
        ov_within = overlaps_within.get(layer, [])
        gs = gaps.get(layer, [])
        total_overlap_len = sum(o["len"] for o in ov_within)
        total_gap_len = sum(g["len"] for g in gs)
        color = get_color(layer, i)

        overlaps_list = "".join(
            f"<li><code>{o['start']}–{o['end']} m</code> · {o['len']} m</li>"
            for o in ov_within
        ) or "<li class='empty'>None</li>"
        gaps_list = "".join(
            f"<li><code>{g['start']}–{g['end']} m</code> · {g['len']} m</li>"
            for g in gs
        ) or "<li class='empty'>None</li>"

        st.markdown(f"""
        <div class="layer-card" style="border-left: 3px solid {color}">
            <h4>{layer}</h4>
            <div class="summary">
                <span><strong>Overlaps:</strong> {total_overlap_len} m</span>
                <span><strong>Gaps:</strong> {total_gap_len} m</span>
            </div>
            <div class="subsection">
                <p class="subsection-title">Overlaps within layer ({len(ov_within)})</p>
                <ul>{overlaps_list}</ul>
            </div>
            <div class="subsection">
                <p class="subsection-title">Gaps ({len(gs)})</p>
                <ul>{gaps_list}</ul>
            </div>
        </div>
        """, unsafe_allow_html=True)
