"""Streamlit presentation tier for the delivery-routing API."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import streamlit as st

st.set_page_config(page_title="Delivery Routing Operations", layout="wide")
st.title("Delivery Routing Operations")
st.caption("Generate isolated planning scenarios or inspect the baseline dataset.")

api_url = st.sidebar.text_input(
    "API URL", os.environ.get("DELIVERY_ROUTING_API_URL", "http://localhost:8000")
).rstrip("/")


@st.cache_data(ttl=30)
def request_json(url):
    try:
        with urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        st.error("API returned HTTP {}: {}".format(error.code, error.reason))
    except (URLError, TimeoutError, ValueError) as error:
        st.error("Could not reach the API: {}".format(error))
    except json.JSONDecodeError as error:
        st.error("API returned invalid JSON: {}".format(error))
    return None


def create_scenario(payload):
    request = Request(
        api_url + "/api/generations",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=900) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8")).get("detail", error.reason)
        except json.JSONDecodeError:
            detail = error.reason
        st.error("Scenario generation failed (HTTP {}): {}".format(error.code, detail))
    except (URLError, TimeoutError, ValueError) as error:
        st.error("Could not complete the generation request: {}".format(error))
    except json.JSONDecodeError as error:
        st.error("API returned invalid JSON: {}".format(error))
    return None


generations_result = request_json(api_url + "/api/generations")
generation_runs = generations_result["items"] if generations_result else []
completed_runs = [
    run for run in generation_runs if run.get("status") == "completed"
]
run_labels = {"Baseline dataset": None}
for run in completed_runs:
    label = "{} ({})".format(run["created_at"][:19], run["run_id"][:8])
    run_labels[label] = run["run_id"]

previous_run_id = st.session_state.get("selected_run_id")
labels = list(run_labels)
selected_index = next(
    (
        index
        for index, label in enumerate(labels)
        if run_labels[label] == previous_run_id
    ),
    0,
)
selected_label = st.sidebar.selectbox(
    "Dataset to inspect", labels, index=selected_index
)
selected_run_id = run_labels[selected_label]
st.session_state["selected_run_id"] = selected_run_id


def dataset_url(path):
    if selected_run_id is None:
        return api_url + path
    separator = "&" if "?" in path else "?"
    return api_url + path + separator + urlencode({"run_id": selected_run_id})


summary = request_json(dataset_url("/api/summary"))
if summary is None:
    st.info("Start the API and confirm the SQLite database is available.")
    st.stop()

metric_columns = st.columns(4)
for column, label, key in zip(
    metric_columns,
    ("Deliveries", "Delivered", "Failed", "Unserved"),
    ("deliveries", "delivered", "failed", "unserved"),
):
    column.metric(label, summary[key])

metric_columns = st.columns(4)
metric_columns[0].metric("Routes", summary["routes"])
metric_columns[1].metric("On-time delivered", "{}%".format(summary["on_time_pct_of_delivered"]))
metric_columns[2].metric(
    "Avg. capacity use", "{}%".format(summary["average_capacity_utilisation_pct"])
)
metric_columns[3].metric("Planned distance", "{} km".format(summary["total_planned_km"]))

planner_tab, routes_tab, deliveries_tab, exceptions_tab = st.tabs(
    ("Plan a scenario", "Routes", "Delivery trace", "Exceptions")
)

with planner_tab:
    st.subheader("Generate an isolated scenario")
    st.caption(
        "Each run is validated and stored under output/runs/. The baseline dataset is not changed."
    )
    with st.form("scenario_parameters"):
        columns = st.columns(4)
        sites = columns[0].number_input(
            "Customer sites", min_value=10, max_value=500, value=500, step=10
        )
        deliveries = columns[1].number_input(
            "Deliveries", min_value=10, max_value=2000, value=2000, step=50
        )
        vehicles = columns[2].number_input(
            "Vehicles", min_value=1, max_value=100, value=100, step=5
        )
        days = columns[3].number_input(
            "Planning days", min_value=1, max_value=30, value=30
        )
        columns = st.columns(3)
        vehicles_per_day = columns[0].number_input(
            "Vehicles per day", min_value=1, max_value=100, value=10
        )
        anomalies_per_type = columns[1].number_input(
            "Anomalies per type", min_value=0, max_value=6, value=6
        )
        seed = columns[2].number_input(
            "Random seed", min_value=0, max_value=2147483646, value=20261022
        )
        submitted = st.form_submit_button("Generate, validate, and build scenario")

    if submitted:
        parameters = {
            "sites": int(sites),
            "deliveries": int(deliveries),
            "vehicles": int(vehicles),
            "days": int(days),
            "vehicles_per_day": int(vehicles_per_day),
            "anomalies_per_type": int(anomalies_per_type),
            "seed": int(seed),
        }
        with st.spinner("Generating data, validating it, and building SQLite..."):
            result = create_scenario(parameters)
        st.cache_data.clear()
        if result is not None:
            st.session_state["selected_run_id"] = result["run_id"]
            st.success(
                "Scenario {} completed. It is now selected above.".format(
                    result["run_id"]
                )
            )
            st.json(
                {
                    "summary": result["summary"],
                    "validation": result["validation"],
                    "parameters": result["parameters"],
                }
            )
            st.rerun()

    if generation_runs:
        st.subheader("Generation history")
        st.dataframe(
            [
                {
                    "run_id": run["run_id"],
                    "status": run["status"],
                    "step": run.get("step"),
                    "created_at": run.get("created_at"),
                    "parameters": run.get("parameters"),
                    "error": run.get("error"),
                }
                for run in generation_runs
            ],
            width="stretch",
            hide_index=True,
        )

with routes_tab:
    route_result = request_json(dataset_url("/api/routes?limit=500"))
    if route_result is not None:
        st.subheader("Generated routes")
        st.dataframe(route_result["items"], width="stretch", hide_index=True)
        route_ids = [row["route_id"] for row in route_result["items"]]
        if route_ids:
            selected_route = st.selectbox("Inspect route stops", route_ids)
            route_detail = request_json(
                dataset_url("/api/routes/" + quote(selected_route, safe=""))
            )
            if route_detail is not None:
                st.dataframe(route_detail["stops"], width="stretch", hide_index=True)

with deliveries_tab:
    status_filter = st.selectbox(
        "Delivery status", ("All", "DELIVERED", "FAILED", "UNSERVED")
    )
    query = {"limit": 500}
    if status_filter != "All":
        query["status"] = status_filter
    delivery_result = request_json(
        dataset_url("/api/deliveries?" + urlencode(query))
    )
    if delivery_result is not None:
        st.subheader("Deliveries")
        st.dataframe(delivery_result["items"], width="stretch", hide_index=True)

    delivery_id = st.text_input("Delivery ID for full trace", placeholder="DEL-2026-000001")
    if delivery_id:
        detail = request_json(
            dataset_url(
                "/api/deliveries/" + quote(delivery_id.strip(), safe="")
            )
        )
        if detail is not None:
            st.subheader("Delivery record")
            st.json(detail["delivery"])
            st.subheader("State history")
            st.dataframe(detail["events"], width="stretch", hide_index=True)
            st.subheader("Stops and outcomes")
            st.dataframe(detail["stops"], width="stretch", hide_index=True)

with exceptions_tab:
    exception_result = request_json(dataset_url("/api/exceptions"))
    if exception_result is not None:
        st.subheader("Detected exceptions")
        st.caption(
            "Detections are calculated from operational records; ground-truth labels are not used."
        )
        st.metric("Exception records", exception_result["total"])
        st.dataframe(exception_result["items"], width="stretch", hide_index=True)
