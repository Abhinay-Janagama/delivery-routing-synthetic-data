"""API for generating isolated scenarios and inspecting delivery plans and outcomes."""

import json
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Generator, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "output"
RUNS_DIR = OUTPUT_DIR / "runs"
DB_PATH = Path(
    os.environ.get(
        "DELIVERY_ROUTING_DB",
        str(OUTPUT_DIR / "delivery_routing.db"),
    )
).expanduser().resolve()
GENERATION_TIMEOUT_SECONDS = 900

app = FastAPI(
    title="Delivery Routing API",
    description="Generate isolated delivery scenarios and inspect plans, outcomes, and exceptions.",
    version="1.0.0",
)


class GenerationRequest(BaseModel):
    sites: int = Field(default=500, ge=10, le=500)
    deliveries: int = Field(default=2000, ge=10, le=2000)
    vehicles: int = Field(default=100, ge=1, le=100)
    days: int = Field(default=30, ge=1, le=30)
    vehicles_per_day: int = Field(default=10, ge=1, le=100)
    anomalies_per_type: int = Field(default=6, ge=0, le=6)
    seed: int = Field(default=20261022, ge=0, le=2147483646)


def _database_path(run_id: Optional[str]) -> Path:
    if run_id is None:
        return DB_PATH
    if len(run_id) != 32 or any(character not in "0123456789abcdef" for character in run_id):
        raise HTTPException(status_code=404, detail="Generation run not found.")
    return RUNS_DIR / run_id / "delivery_routing.db"


def get_connection(
    run_id: Optional[str] = Query(
        None, pattern="^[0-9a-f]{32}$", description="Inspect an isolated generation run."
    ),
) -> Generator[sqlite3.Connection, None, None]:
    database_path = _database_path(run_id)
    if not database_path.is_file():
        raise HTTPException(
            status_code=503,
            detail="Database not found. Generate the data and build the SQLite database first.",
        )

    connection = sqlite3.connect(str(database_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    try:
        yield connection
    finally:
        connection.close()


def _region_bounds(run_id: Optional[str] = None) -> Dict[str, float]:
    manifest_path = _database_path(run_id).parent / "manifest.json"
    try:
        with manifest_path.open(encoding="utf-8") as manifest_file:
            region = json.load(manifest_file)["region"]
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=503,
            detail="Dataset manifest not found; coordinate exceptions cannot be evaluated.",
        ) from error
    except (json.JSONDecodeError, KeyError) as error:
        raise HTTPException(
            status_code=503,
            detail="Dataset manifest is invalid; coordinate exceptions cannot be evaluated.",
        ) from error
    return region


def _write_generation_status(run_dir: Path, status: Dict[str, object]) -> None:
    with (run_dir / "generation_status.json").open("w", encoding="utf-8") as status_file:
        json.dump(status, status_file, indent=2)


def _run_generation_step(
    run_id: str,
    run_dir: Path,
    status: Dict[str, object],
    step: str,
    command: List[str],
) -> str:
    status["step"] = step
    _write_generation_status(run_dir, status)
    try:
        result = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=GENERATION_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        status.update(
            status="failed",
            error="{} exceeded the {} second time limit.".format(
                step, GENERATION_TIMEOUT_SECONDS
            ),
        )
        _write_generation_status(run_dir, status)
        raise HTTPException(
            status_code=504,
            detail={
                "run_id": run_id,
                "step": step,
                "message": status["error"],
                "stdout": str(error.stdout or "")[-4000:],
                "stderr": str(error.stderr or "")[-4000:],
            },
        ) from error

    if result.returncode:
        status.update(
            status="failed",
            error="{} failed with exit code {}.".format(step, result.returncode),
        )
        _write_generation_status(run_dir, status)
        raise HTTPException(
            status_code=500,
            detail={
                "run_id": run_id,
                "step": step,
                "message": status["error"],
                "stdout": result.stdout[-4000:],
                "stderr": result.stderr[-4000:],
            },
        )
    return result.stdout


@app.post("/api/generations", status_code=201)
def create_generation(request: GenerationRequest) -> Dict[str, object]:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir()
    parameters = request.model_dump()
    status: Dict[str, object] = {
        "run_id": run_id,
        "status": "running",
        "step": "starting",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "parameters": parameters,
    }
    _write_generation_status(run_dir, status)

    generator_command = [
        sys.executable,
        str(PROJECT_ROOT / "backend" / "generate.py"),
        "--out",
        str(run_dir),
    ]
    for argument, value in parameters.items():
        generator_command.extend(["--" + argument.replace("_", "-"), str(value)])
    _run_generation_step(
        run_id, run_dir, status, "generation", generator_command
    )

    _run_generation_step(
        run_id,
        run_dir,
        status,
        "validation",
        [sys.executable, str(PROJECT_ROOT / "backend" / "validate.py"), str(run_dir)],
    )
    try:
        with (run_dir / "validation_summary.json").open(
            encoding="utf-8"
        ) as validation_file:
            validation = json.load(validation_file)
    except (OSError, json.JSONDecodeError) as error:
        status.update(status="failed", error="Validation summary could not be read.")
        _write_generation_status(run_dir, status)
        raise HTTPException(
            status_code=500,
            detail={"run_id": run_id, "message": status["error"]},
        ) from error

    _run_generation_step(
        run_id,
        run_dir,
        status,
        "database build",
        [
            sys.executable,
            str(PROJECT_ROOT / "database" / "build_db.py"),
            str(run_dir),
            "--seed-path",
            str(run_dir / "seed.sql"),
        ],
    )

    connection = sqlite3.connect(str(run_dir / "delivery_routing.db"))
    connection.row_factory = sqlite3.Row
    try:
        result_summary = _summary(connection)
    finally:
        connection.close()

    status.update(
        status="completed",
        step="completed",
        summary=result_summary,
        validation={
            "checks_total": validation["checks_total"],
            "checks_failed": validation["checks_failed"],
        },
    )
    _write_generation_status(run_dir, status)
    return status


@app.get("/api/generations")
def generations() -> Dict[str, object]:
    if not RUNS_DIR.exists():
        return {"items": []}

    items = []
    for run_dir in sorted(RUNS_DIR.iterdir(), key=lambda path: path.name, reverse=True):
        if not run_dir.is_dir() or len(run_dir.name) != 32:
            continue
        status_path = run_dir / "generation_status.json"
        if not status_path.is_file():
            continue
        with status_path.open(encoding="utf-8") as status_file:
            items.append(json.load(status_file))
    return {"items": items}


@app.get("/api/generations/{run_id}")
def generation_detail(run_id: str) -> Dict[str, object]:
    if len(run_id) != 32 or any(character not in "0123456789abcdef" for character in run_id):
        raise HTTPException(status_code=404, detail="Generation run not found.")
    status_path = RUNS_DIR / run_id / "generation_status.json"
    if not status_path.is_file():
        raise HTTPException(status_code=404, detail="Generation run not found.")
    with status_path.open(encoding="utf-8") as status_file:
        return json.load(status_file)


@app.get("/api/health")
def health(connection: sqlite3.Connection = Depends(get_connection)) -> Dict[str, str]:
    connection.execute("SELECT 1").fetchone()
    database_path = connection.execute("PRAGMA database_list").fetchone()["file"]
    return {"status": "ok", "database": database_path}


@app.get("/api/summary")
def summary(connection: sqlite3.Connection = Depends(get_connection)) -> Dict[str, object]:
    return _summary(connection)


def _summary(connection: sqlite3.Connection) -> Dict[str, object]:
    status_counts = {
        row["status"]: row["count"]
        for row in connection.execute(
            "SELECT status, COUNT(*) AS count FROM deliveries GROUP BY status"
        )
    }
    outcome_metrics = connection.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN outcome_status = 'DELIVERED' THEN 1 ELSE 0 END), 0)
                   AS delivered,
               COALESCE(SUM(CASE WHEN outcome_status = 'DELIVERED' AND late_minutes = 0
                                 THEN 1 ELSE 0 END), 0) AS on_time
        FROM delivery_outcomes
        """
    ).fetchone()
    route_metrics = connection.execute(
        """
        SELECT COUNT(*) AS route_count,
               COALESCE(AVG(total_load_kg * 100.0 / NULLIF(capacity_kg_at_plan, 0)), 0)
                   AS average_capacity_utilisation_pct,
               COALESCE(SUM(total_distance_km), 0) AS total_planned_km
        FROM routes
        """
    ).fetchone()
    delivery_total = sum(status_counts.values())

    return {
        "deliveries": delivery_total,
        "delivered": status_counts.get("DELIVERED", 0),
        "failed": status_counts.get("FAILED", 0),
        "unserved": status_counts.get("UNSERVED", 0),
        "on_time_pct_of_delivered": round(
            100.0 * outcome_metrics["on_time"] / max(1, outcome_metrics["delivered"]), 1
        ),
        "routes": route_metrics["route_count"],
        "average_capacity_utilisation_pct": round(
            route_metrics["average_capacity_utilisation_pct"], 1
        ),
        "total_planned_km": round(route_metrics["total_planned_km"], 1),
    }


@app.get("/api/deliveries")
def deliveries(
    status: Optional[str] = Query(
        None, pattern="^(DELIVERED|FAILED|UNSERVED)$"
    ),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    connection: sqlite3.Connection = Depends(get_connection),
) -> Dict[str, object]:
    where = "WHERE d.status = ?" if status else ""
    parameters: List[object] = [status] if status else []
    total = connection.execute(
        f"SELECT COUNT(*) FROM deliveries d {where}", parameters
    ).fetchone()[0]
    rows = connection.execute(
        f"""
        SELECT d.delivery_id, d.order_id, s.site_name, d.delivery_date,
               d.demand_kg, d.priority, d.status, d.reason_code
        FROM deliveries d
        JOIN customer_sites s ON s.site_id = d.site_id
        {where}
        ORDER BY d.delivery_date, d.delivery_id
        LIMIT ? OFFSET ?
        """,
        parameters + [limit, offset],
    )
    return {"total": total, "items": [dict(row) for row in rows]}


@app.get("/api/routes")
def routes(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    connection: sqlite3.Connection = Depends(get_connection),
) -> Dict[str, object]:
    total = connection.execute("SELECT COUNT(*) FROM routes").fetchone()[0]
    rows = connection.execute(
        """
        SELECT r.route_id, r.route_date, r.vehicle_id, v.vehicle_type,
               r.driver_id, d.driver_name, r.total_load_kg, r.capacity_kg_at_plan,
               r.total_distance_km, r.planned_return, r.traffic_scenario, r.status,
               COUNT(s.stop_id) AS stop_count
        FROM routes r
        JOIN vehicles v ON v.vehicle_id = r.vehicle_id
        JOIN drivers d ON d.driver_id = r.driver_id
        LEFT JOIN route_stops s ON s.route_id = r.route_id
        GROUP BY r.route_id
        ORDER BY r.route_date, r.route_id
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    return {"total": total, "items": [dict(row) for row in rows]}


@app.get("/api/routes/{route_id}")
def route_detail(
    route_id: str, connection: sqlite3.Connection = Depends(get_connection)
) -> Dict[str, object]:
    route = connection.execute(
        """
        SELECT r.*, v.vehicle_type, d.driver_name
        FROM routes r
        JOIN vehicles v ON v.vehicle_id = r.vehicle_id
        JOIN drivers d ON d.driver_id = r.driver_id
        WHERE r.route_id = ?
        """,
        (route_id,),
    ).fetchone()
    if route is None:
        raise HTTPException(status_code=404, detail="Route not found.")

    stops = connection.execute(
        """
        SELECT s.stop_id, s.stop_sequence, s.delivery_id, s.site_id, site.site_name,
               s.from_site_id, s.leg_distance_km, s.leg_travel_min,
               s.planned_arrival, s.planned_service_start, s.planned_departure,
               s.cum_load_kg
        FROM route_stops s
        JOIN customer_sites site ON site.site_id = s.site_id
        WHERE s.route_id = ?
        ORDER BY s.stop_sequence
        """,
        (route_id,),
    )
    return {"route": dict(route), "stops": [dict(stop) for stop in stops]}


@app.get("/api/deliveries/{delivery_id}")
def delivery_detail(
    delivery_id: str, connection: sqlite3.Connection = Depends(get_connection)
) -> Dict[str, object]:
    delivery = connection.execute(
        """
        SELECT d.*, s.site_name, o.total_demand_kg, tw.window_start, tw.window_end
        FROM deliveries d
        JOIN customer_sites s ON s.site_id = d.site_id
        JOIN orders o ON o.order_id = d.order_id
        LEFT JOIN time_windows tw ON tw.delivery_id = d.delivery_id
        WHERE d.delivery_id = ?
        """,
        (delivery_id,),
    ).fetchone()
    if delivery is None:
        raise HTTPException(status_code=404, detail="Delivery not found.")

    events = connection.execute(
        """
        SELECT event_id, from_status, to_status, event_ts, reason_code, ref_id
        FROM delivery_events
        WHERE delivery_id = ?
        ORDER BY event_ts, event_id
        """,
        (delivery_id,),
    )
    stops = connection.execute(
        """
        SELECT s.stop_id, s.route_id, s.stop_sequence, s.planned_arrival,
               s.planned_service_start, s.planned_departure, r.vehicle_id,
               r.driver_id, o.outcome_status, o.failure_reason_code,
               o.actual_arrival, o.actual_service_start, o.actual_departure
        FROM route_stops s
        JOIN routes r ON r.route_id = s.route_id
        LEFT JOIN delivery_outcomes o ON o.stop_id = s.stop_id
        WHERE s.delivery_id = ?
        ORDER BY r.route_date, r.route_id, s.stop_sequence
        """,
        (delivery_id,),
    )
    return {
        "delivery": dict(delivery),
        "events": [dict(event) for event in events],
        "stops": [dict(stop) for stop in stops],
    }


@app.get("/api/exceptions")
def exceptions(
    run_id: Optional[str] = None,
    connection: sqlite3.Connection = Depends(get_connection),
) -> Dict[str, object]:
    found = []

    def add(category: str, entity_id: str, description: str) -> None:
        found.append(
            {
                "category": category,
                "entity_id": entity_id,
                "description": description,
            }
        )

    for row in connection.execute(
        """
        SELECT route_id, total_load_kg, capacity_kg_at_plan
        FROM routes
        WHERE total_load_kg > capacity_kg_at_plan
        """
    ):
        add(
            "ROUTE_EXCEEDS_CAPACITY",
            row["route_id"],
            "Route load {} kg exceeds capacity {} kg.".format(
                row["total_load_kg"], row["capacity_kg_at_plan"]
            ),
        )

    for row in connection.execute(
        """
        SELECT r.route_id, r.planned_return, sh.shift_end
        FROM routes r
        JOIN shifts sh ON sh.shift_id = r.shift_id
        WHERE r.planned_return > sh.shift_end
        """
    ):
        add(
            "DRIVER_SHIFT_EXCEEDED",
            row["route_id"],
            "Planned return {} is after shift end {}.".format(
                row["planned_return"], row["shift_end"]
            ),
        )

    for row in connection.execute(
        """
        SELECT e.delivery_id
        FROM delivery_events e
        WHERE e.to_status = 'ASSIGNED'
          AND NOT EXISTS (
              SELECT 1 FROM route_stops s WHERE s.delivery_id = e.delivery_id
          )
        """
    ):
        add("STOP_OMITTED", row["delivery_id"], "Assigned delivery has no route stop.")

    for row in connection.execute(
        """
        SELECT delivery_id, COUNT(*) AS stop_count
        FROM route_stops
        GROUP BY delivery_id
        HAVING COUNT(*) > 1
        """
    ):
        add(
            "STOP_DUPLICATED",
            row["delivery_id"],
            "Delivery appears in {} route stops.".format(row["stop_count"]),
        )

    previous_departures = connection.execute(
        """
        SELECT route_id, stop_id, planned_arrival, planned_departure,
               LAG(planned_departure) OVER (
                   PARTITION BY route_id ORDER BY stop_sequence
               ) AS previous_departure
        FROM route_stops
        """
    )
    for row in previous_departures:
        if row["previous_departure"] and row["planned_arrival"] < row["previous_departure"]:
            add(
                "ARRIVAL_BEFORE_PREV_DEPARTURE",
                row["stop_id"],
                "Stop arrival precedes the previous stop's departure.",
            )

    region = _region_bounds(run_id)
    for row in connection.execute(
        """
        SELECT site_id, latitude, longitude
        FROM customer_sites
        WHERE site_type = 'CUSTOMER'
        """
    ):
        if not (
            region["lat_min"] <= row["latitude"] <= region["lat_max"]
            and region["lon_min"] <= row["longitude"] <= region["lon_max"]
        ):
            add(
                "COORDINATE_OUTSIDE_REGION",
                row["site_id"],
                "Customer site coordinates are outside the configured region.",
            )

    for row in connection.execute(
        """
        SELECT delivery_id, status, reason_code
        FROM deliveries
        WHERE status IN ('FAILED', 'UNSERVED')
        ORDER BY delivery_date, delivery_id
        """
    ):
        add(
            row["status"],
            row["delivery_id"],
            "Delivery is {} (reason: {}).".format(
                row["status"].lower(), row["reason_code"] or "not provided"
            ),
        )

    found.sort(key=lambda item: (item["category"], item["entity_id"]))
    return {"total": len(found), "items": found}
