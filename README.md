# Delivery Routing Synthetic Data

Synthetic dataset generator for last-mile delivery planning: customer sites, orders,
vehicles with capacities, driver shifts, time windows, routes, stops and delivery outcomes.

## Three-tier architecture

- **Presentation tier:** Streamlit operations dashboard in `frontend/`.
- **Application tier:** FastAPI API in `backend/api.py`, plus the generator and validator.
- **Data tier:** SQLite schema and build scripts in `database/`; generated CSVs, manifest,
  validation report, and database in `output/`.

The dashboard can generate a scenario, validate it, and build its SQLite database without
changing the baseline dataset. Each run is stored in its own `output/runs/<run_id>/` directory
and can then be selected in the dashboard. The dashboard also exposes routes and ordered stops,
delivery outcomes and lifecycle history, KPIs, and exceptions detected from operational records.
It does not read ground-truth labels to produce detections.

## Setup and run

Use Python 3.11 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Generate the default synthetic dataset (seed `20261022`; 500 sites, 2,000 deliveries,
100 vehicles, and 30 days), validate it, and build the SQLite database:

```powershell
python backend\generate.py --seed 20261022 --out output
python backend\validate.py output
python database\build_db.py output
```

Start the application tier and presentation tier in separate terminals:

```powershell
python -m uvicorn backend.api:app --reload
```

```powershell
python -m streamlit run frontend\dashboard.py
```

Open the Streamlit URL printed in the terminal (normally `http://localhost:8501`).
The API is available at `http://localhost:8000`; interactive API documentation is at
`http://localhost:8000/docs`.

The API reads `output\delivery_routing.db` by default. To use a different database, set
`DELIVERY_ROUTING_DB` before starting the API. The dashboard can target a different API
with `DELIVERY_ROUTING_API_URL`.

Use the **Plan a scenario** tab to set generation parameters and click **Generate, validate,
and build scenario**. The API accepts the same workflow at `POST /api/generations`; generated
runs are available from `GET /api/generations` and can be inspected with the existing data
endpoints by passing `run_id` as a query parameter. Scenario folders are ignored by Git.

## Reproducibility and tests

The generator is deterministic for the same seed and parameters. To run the pipeline tests:

```powershell
python -m unittest discover -s tests
```

The checked-in data dictionary is in `docs/DATA_DICTIONARY.md`. The generator keeps
ground-truth labels in `output/ground_truth/ground_truth.csv`, separate from detector inputs.
Validation also reconciles each route's load and duration, cumulative stop loads, and total
distance including the return leg. For these reconciliation checks, routes affected by the
corresponding deliberate omitted-stop, duplicate-stop, or shift-overrun scenarios are identified
from operational records; ground-truth labels remain reserved for scoring detector results.