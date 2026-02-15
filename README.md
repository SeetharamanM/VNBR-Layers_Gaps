# Layer Coverage Viewer

A web app to visualize layer coverage in **1000m chunks** and detect **overlaps** between layers from uploaded CSV data.

## Features

- **Chart** – X-axis 0–1000 m, Y-axis chunks × layers, bars at stretch chainages per layer
- **Progress** – Overall %, per-layer %, and per-layer per-bill % (requires Bill No, Est Length in CSV)
- **Overlaps within layer** – Per-layer: stretches where the same layer has overlapping segments
- **Gap analysis** – Per-layer: stretches not covered by that layer within the route extent

## Python (Plotly Dash)

### Run

```bash
pip install -r requirements.txt
python app_dash.py
```

Then open http://localhost:8050

### Usage

1. Upload a CSV or click **Try sample data**
2. View the chart and overlap panels

## Python (Streamlit)

### Run

```bash
pip install -r requirements.txt
streamlit run app_streamlit.py
```

Then open http://localhost:8501

Same features as the Dash version: chart, progress, overlaps, and gap analysis.

## HTML/JavaScript (standalone)

1. Open `index.html` in a browser (or run a local server for sample data)
2. Upload a CSV or click **Try sample data**
3. View the chart and overlap panels

```bash
# Local server for sample data
python -m http.server 8080
# or: npx serve .
# Then open http://localhost:8080
```

## CSV format

Required columns:

- **Item** (or Layer) – Layer name (e.g. `Subgrade`, `Embankment EW`)
- **Stretch** – Chainage range in format `start-end` (e.g. `500-1000`, `1000-1100`)

Optional for progress:

- **Bill No** – Bill identifier for per-bill progress and filtering
- **Est Length** – Route length in m (e.g. 8000) for % calculation
- **Date** – For month filter (DD.MM.YYYY or YYYY-MM-DD)

Progress filters: filter by Bill No and/or Month, then click Apply.

Example:

```
Item,Stretch
Subgrade,100-150
Subgrade,600-800
Subgrade,1400-1600
Embankment EW,100-150
Embankment EW,1400-1600
```
