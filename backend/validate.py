#!/usr/bin/env python3
"""Validate generated data (Step 8) and compare anomaly detections with ground truth (Step 7).
Usage: python validate.py output
Input features are read from data/; ground_truth/ is read ONLY at the end to score detectors.
"""
import csv, json, os, sys
from collections import Counter, defaultdict

D = sys.argv[1] if len(sys.argv) > 1 else "output"
REGION = json.load(open(f"{D}/manifest.json"))["region"]
NAMES = ["customer_sites", "orders", "deliveries", "time_windows", "vehicles", "vehicle_capacity", "drivers", "shifts",
         "travel_time_matrix", "routes", "route_stops", "delivery_outcomes", "delivery_events"]
T = {n: list(csv.DictReader(open(f"{D}/data/{n}.csv", newline=""))) for n in NAMES}
checks = []


def check(name, bad):
    checks.append(dict(check=name, violations=len(bad), sample=[str(b) for b in bad[:3]], passed=not bad))


# 1. primary keys
PK = dict(customer_sites=["site_id"], orders=["order_id"], deliveries=["delivery_id"], time_windows=["window_id"],
          vehicles=["vehicle_id"], vehicle_capacity=["capacity_id"], drivers=["driver_id"], shifts=["shift_id"],
          routes=["route_id"], route_stops=["stop_id"], delivery_outcomes=["outcome_id"], delivery_events=["event_id"],
          travel_time_matrix=["scenario_date", "origin_site_id", "destination_site_id"])
for t, cols in PK.items():
    keys = [tuple(r[c] for c in cols) for r in T[t]]
    check(f"PK unique: {t}", [k for k, c in Counter(keys).items() if c > 1])

# 2. foreign keys
FK = [("orders", "site_id", "customer_sites", "site_id"), ("deliveries", "order_id", "orders", "order_id"),
      ("deliveries", "site_id", "customer_sites", "site_id"), ("time_windows", "delivery_id", "deliveries", "delivery_id"),
      ("vehicle_capacity", "vehicle_id", "vehicles", "vehicle_id"), ("shifts", "driver_id", "drivers", "driver_id"),
      ("shifts", "vehicle_id", "vehicles", "vehicle_id"), ("routes", "shift_id", "shifts", "shift_id"),
      ("routes", "vehicle_id", "vehicles", "vehicle_id"), ("routes", "driver_id", "drivers", "driver_id"),
      ("route_stops", "route_id", "routes", "route_id"), ("route_stops", "delivery_id", "deliveries", "delivery_id"),
      ("route_stops", "site_id", "customer_sites", "site_id"), ("route_stops", "from_site_id", "customer_sites", "site_id"),
      ("delivery_outcomes", "stop_id", "route_stops", "stop_id"), ("delivery_outcomes", "delivery_id", "deliveries", "delivery_id"),
      ("delivery_events", "delivery_id", "deliveries", "delivery_id"),
      ("travel_time_matrix", "origin_site_id", "customer_sites", "site_id"),
      ("travel_time_matrix", "destination_site_id", "customer_sites", "site_id")]
for ct, cc, pt, pc in FK:
    ok = {r[pc] for r in T[pt]}
    check(f"FK {ct}.{cc} -> {pt}.{pc}", [r[cc] for r in T[ct] if r[cc] not in ok])

# 3. enums and reason codes
ENUMS = [("deliveries", "status", {"DELIVERED", "FAILED", "UNSERVED"}), ("deliveries", "priority", {"1", "2", "3"}),
         ("delivery_outcomes", "outcome_status", {"DELIVERED", "FAILED"}), ("vehicles", "vehicle_type", {"MINI", "VAN", "TRUCK"}),
         ("shifts", "shift_type", {"EARLY", "DAY", "LATE"}), ("customer_sites", "site_type", {"DEPOT", "CUSTOMER"}),
         ("routes", "traffic_scenario", {"NORMAL", "RAIN", "EVENT", "HOLIDAY"})]
for t, c, allowed in ENUMS:
    check(f"enum {t}.{c}", [r[c] for r in T[t] if r[c] not in allowed])
check("failed outcomes have reason code, delivered have none",
      [r["outcome_id"] for r in T["delivery_outcomes"] if (r["outcome_status"] == "FAILED") != bool(r["failure_reason_code"])])
check("unserved deliveries have UNSERVED_* reason",
      [r["delivery_id"] for r in T["deliveries"] if (r["status"] == "UNSERVED") != r["reason_code"].startswith("UNSERVED_")])

# 4. date ordering
check("window_start < window_end", [r["window_id"] for r in T["time_windows"] if r["window_start"] >= r["window_end"]])
check("shift_start < shift_end", [r["shift_id"] for r in T["shifts"] if r["shift_start"] >= r["shift_end"]])
dl = {r["delivery_id"]: r for r in T["deliveries"]}
od0 = {o["order_id"]: o for o in T["orders"]}
check("order_date <= delivery_date", [r["delivery_id"] for r in T["deliveries"] if od0[r["order_id"]]["order_date"] > r["delivery_date"]])
check("stop: arrival <= service_start <= departure",
      [s["stop_id"] for s in T["route_stops"] if not (s["planned_arrival"] <= s["planned_service_start"] <= s["planned_departure"])])

# 5. arithmetic reconciliation
od = {o["order_id"]: o for o in T["orders"]}
check("order.total_demand_kg == delivery.demand_kg", [d["delivery_id"] for d in T["deliveries"] if d["demand_kg"] != od[d["order_id"]]["total_demand_kg"]])
by_route = defaultdict(list)
for s in T["route_stops"]:
    by_route[s["route_id"]].append(s)
for v in by_route.values():
    v.sort(key=lambda s: int(s["stop_sequence"]))
check("route.total_load == last stop cum_load", [r["route_id"] for r in T["routes"] if int(r["total_load_kg"]) != int(by_route[r["route_id"]][-1]["cum_load_kg"])])
check("cum_load non-decreasing", [rid for rid, v in by_route.items() if any(int(a["cum_load_kg"]) > int(b["cum_load_kg"]) for a, b in zip(v, v[1:]))])

# 6. lifecycle
ALLOWED = {("", "CREATED"), ("CREATED", "ASSIGNED"), ("CREATED", "UNSERVED"), ("ASSIGNED", "DELIVERED"), ("ASSIGNED", "FAILED")}
ev = defaultdict(list)
for e in T["delivery_events"]:
    ev[e["delivery_id"]].append(e)
bad_trans, bad_chain, bad_final, bad_cover = [], [], [], []
for did, lst in ev.items():
    lst.sort(key=lambda e: e["event_id"])
    if any((e["from_status"], e["to_status"]) not in ALLOWED for e in lst): bad_trans.append(did)
    if any(a["to_status"] != b["from_status"] or a["event_ts"] > b["event_ts"] for a, b in zip(lst, lst[1:])): bad_chain.append(did)
    if dl[did]["status"] != lst[-1]["to_status"]: bad_final.append(did)
    if not {"ASSIGNED", "UNSERVED"} & {e["to_status"] for e in lst}: bad_cover.append(did)
check("lifecycle: allowed transitions", bad_trans); check("lifecycle: chain + timestamps ordered", bad_chain)
check("delivery.status == last event", bad_final); check("every delivery assigned or explicitly unserved", bad_cover + [d for d in dl if d not in ev])

# 7. constraint rules (planned schedule respects the business rules)
veh = {v["vehicle_id"]: v for v in T["vehicles"]}; site = {s["site_id"]: s for s in T["customer_sites"]}
route = {r["route_id"]: r for r in T["routes"]}; tw = {w["delivery_id"]: w for w in T["time_windows"]}
check("restricted sites only served by restricted-capable vehicles",
      [s["stop_id"] for s in T["route_stops"] if site[s["site_id"]]["restricted_access"] == "1" and veh[route[s["route_id"]]["vehicle_id"]]["restricted_access_ok"] != "1"])
check("planned service start inside time window",
      [s["stop_id"] for s in T["route_stops"] if not (tw[s["delivery_id"]]["window_start"] <= s["planned_service_start"] <= tw[s["delivery_id"]]["window_end"])])
mx = {(m["scenario_date"], m["origin_site_id"], m["destination_site_id"]): (m["distance_km"], m["travel_minutes"]) for m in T["travel_time_matrix"]}
check("leg distance/time reproducible from travel-time matrix",
      [s["stop_id"] for s in T["route_stops"] if mx[(route[s["route_id"]]["route_date"], s["from_site_id"], s["site_id"])] != (s["leg_distance_km"], s["leg_travel_min"])])

# 8. anomaly detectors (rule-based, never see ground truth)
found = defaultdict(set)
for r in T["routes"]:
    if int(r["total_load_kg"]) > int(r["capacity_kg_at_plan"]): found["ROUTE_EXCEEDS_CAPACITY"].add(r["route_id"])
    if r["planned_return"] > next(s for s in T["shifts"] if s["shift_id"] == r["shift_id"])["shift_end"]: found["DRIVER_SHIFT_EXCEEDED"].add(r["route_id"])
stop_count = Counter(s["delivery_id"] for s in T["route_stops"])
for did, lst in ev.items():
    if any(e["to_status"] == "ASSIGNED" for e in lst) and stop_count[did] == 0: found["STOP_OMITTED"].add(did)
seen = defaultdict(list)
for s in T["route_stops"]:
    seen[s["delivery_id"]].append(s)
for lst in seen.values():
    for s in sorted(lst, key=lambda s: (s["route_id"], int(s["stop_sequence"])))[1:]: found["STOP_DUPLICATED"].add(s["stop_id"])
for v in by_route.values():
    for a, b in zip(v, v[1:]):
        if b["planned_arrival"] < a["planned_departure"]: found["ARRIVAL_BEFORE_PREV_DEPARTURE"].add(b["stop_id"])
for s in T["customer_sites"]:
    if s["site_type"] == "CUSTOMER" and not (REGION["lat_min"] <= float(s["latitude"]) <= REGION["lat_max"] and REGION["lon_min"] <= float(s["longitude"]) <= REGION["lon_max"]):
        found["COORDINATE_OUTSIDE_REGION"].add(s["site_id"])
gt = defaultdict(set)
for g in csv.DictReader(open(f"{D}/ground_truth/ground_truth.csv")):
    gt[g["issue_type"]].add(g["entity_id"])
detect = {k: dict(expected=len(gt[k]), detected=len(found[k]), true_pos=len(gt[k] & found[k]), false_pos=len(found[k] - gt[k]),
                  missed=len(gt[k] - found[k])) for k in sorted(set(gt) | set(found))}

# 9. KPIs (measurable outputs)
outs = T["delivery_outcomes"]; dv = [o for o in outs if o["outcome_status"] == "DELIVERED"]
kpi = dict(deliveries=len(dl), served_pct=round(100 * sum(d["status"] != "UNSERVED" for d in dl.values()) / len(dl), 1),
           unserved=sum(d["status"] == "UNSERVED" for d in dl.values()), failed=sum(o["outcome_status"] == "FAILED" for o in outs),
           on_time_pct_of_delivered=round(100 * sum(o["late_minutes"] == "0" for o in dv) / max(1, len(dv)), 1),
           avg_capacity_utilisation_pct=round(100 * sum(int(r["total_load_kg"]) / int(r["capacity_kg_at_plan"]) for r in T["routes"]) / len(T["routes"]), 1),
           total_planned_km=round(sum(float(r["total_distance_km"]) for r in T["routes"]), 1),
           reason_codes=dict(Counter(d["reason_code"] for d in dl.values() if d["reason_code"])))

failed = [c for c in checks if not c["passed"]]
summary = dict(checks_total=len(checks), checks_failed=len(failed), checks=checks, anomaly_detection=detect, kpis=kpi)
json.dump(summary, open(f"{D}/validation_summary.json", "w"), indent=2)
print(f"Baseline checks: {len(checks) - len(failed)}/{len(checks)} passed")
for c in failed: print("  FAIL", c["check"], c["violations"], c["sample"])
print("Anomaly detection vs ground truth:")
for k, v in detect.items(): print(f"  {k:32s} {v}")
print("KPIs:", json.dumps(kpi, indent=1))
sys.exit(1 if failed or any(v["missed"] or v["false_pos"] for v in detect.values()) else 0)