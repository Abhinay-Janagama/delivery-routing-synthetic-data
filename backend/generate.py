"""Deterministic synthetic generator: delivery routing with time windows, capacities and shifts.

Usage: python generate.py --seed 20261022 --out output
Same seed + same parameters => byte-identical files (see manifest.json checksums).
"""
import argparse, csv, hashlib, json, math, os, random
from datetime import date, datetime, timedelta

REGION = dict(lat_min=17.20, lat_max=17.60, lon_min=78.20, lon_max=78.70)
DEPOT_ID, DEPOT_XY = "SITE-000000", (17.385, 78.4867)
START = date(2026, 10, 1)
VTYPES = {"MINI": (800, 1), "VAN": (1500, 1), "TRUCK": (3000, 0)}  # capacity kg, restricted access allowed
SHIFTS = {"EARLY": (360, 900), "DAY": (480, 1020), "LATE": (600, 1140)}  # minutes after midnight
SCENARIOS = [("NORMAL", 1.0, 6), ("RAIN", 1.25, 2), ("EVENT", 1.4, 1), ("HOLIDAY", 0.85, 1)]
GRACE_MIN = 15  # rule: arriving more than 15 min after window end => customer refuses


def ts(day, m):
    return (datetime.combine(day, datetime.min.time()) + timedelta(minutes=int(m))).strftime("%Y-%m-%dT%H:%M")


def shift_ts(iso, m):
    return (datetime.fromisoformat(iso) + timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M")


def road_km(a, b):
    p1, l1, p2, l2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2
    return round(1.3 * 2 * 6371 * math.asin(math.sqrt(h)), 2)  # 1.3 = road detour factor


def minutes(km, factor):
    return max(1, round(km / 35 * 60 * factor))  # 35 km/h base speed


def generate(a):
    R = random.Random(a.seed)
    # ---------- Tier-3 master data ----------
    sites = [dict(site_id=DEPOT_ID, site_name="Central Depot", site_type="DEPOT", latitude=DEPOT_XY[0],
                  longitude=DEPOT_XY[1], restricted_access=0, default_service_min=0, status="ACTIVE",
                  effective_from="2026-01-01")]
    for i in range(1, a.sites + 1):
        sites.append(dict(site_id=f"SITE-{i:06d}", site_name=f"Customer Site {i:03d}", site_type="CUSTOMER",
                          latitude=round(R.uniform(17.28, 17.50), 6), longitude=round(R.uniform(78.35, 78.60), 6),
                          restricted_access=int(R.random() < 0.12), default_service_min=R.choice([10, 15, 20, 30]),
                          status="ACTIVE", effective_from="2026-01-01"))
    S = {s["site_id"]: s for s in sites}
    xy = {s["site_id"]: (s["latitude"], s["longitude"]) for s in sites}  # frozen before anomaly injection
    dist = lambda p, q: road_km(xy[p], xy[q])

    vehicles, caps, drivers = [], [], []
    for i in range(1, a.vehicles + 1):
        vt = "MINI" if i % 10 < 3 else "VAN" if i % 10 < 7 else "TRUCK"
        cap, ok = VTYPES[vt]
        vid = f"VEH-{i:06d}"
        vehicles.append(dict(vehicle_id=vid, vehicle_type=vt, restricted_access_ok=ok, status="ACTIVE"))
        if i % 10 == 0:  # capacity history: old row is closed, never overwritten
            caps.append(dict(capacity_id=f"CAP-{len(caps) + 1:06d}", vehicle_id=vid, capacity_kg=cap + 100,
                             effective_from="2026-01-01", effective_to="2026-10-09"))
            caps.append(dict(capacity_id=f"CAP-{len(caps) + 1:06d}", vehicle_id=vid, capacity_kg=cap,
                             effective_from="2026-10-10", effective_to=""))
        else:
            caps.append(dict(capacity_id=f"CAP-{len(caps) + 1:06d}", vehicle_id=vid, capacity_kg=cap,
                             effective_from="2026-01-01", effective_to=""))
        drivers.append(dict(driver_id=f"DRV-{i:06d}", driver_name=f"Driver {i:03d}",
                            license_class="HGV" if vt == "TRUCK" else "LMV", status="ACTIVE"))

    def cap_on(vid, day):
        for c in caps:
            if c["vehicle_id"] == vid and c["effective_from"] <= str(day) and (not c["effective_to"] or str(day) <= c["effective_to"]):
                return c["capacity_kg"]

    # ---------- orders / deliveries / time windows ----------
    dates = sorted(R.randrange(a.days) for _ in range(a.deliveries))
    orders, deliveries, windows, tw = [], [], [], {}
    for i, di in enumerate(dates, 1):
        day = START + timedelta(di)
        s = R.choice(sites[1:])
        demand = R.randint(20, 200) if R.random() < 0.85 else R.randint(300, 1200)
        ws = R.randrange(480, 900, 30)
        we = min(ws + R.choice([120, 180, 240]), 1080)
        oid, did = f"ORD-2026-{i:06d}", f"DEL-2026-{i:06d}"
        orders.append(dict(order_id=oid, site_id=s["site_id"], order_date=str(day - timedelta(R.randint(1, 3))),
                           total_demand_kg=demand, status="OPEN"))
        deliveries.append(dict(delivery_id=did, order_id=oid, site_id=s["site_id"], delivery_date=str(day),
                               demand_kg=demand, service_minutes=s["default_service_min"] + demand // 50,
                               priority=R.choices([1, 2, 3], [2, 3, 5])[0], status="CREATED", reason_code=""))
        windows.append(dict(window_id=f"TW-{i:06d}", delivery_id=did, window_start=ts(day, ws),
                            window_end=ts(day, we), window_type="HARD"))
        tw[did] = (ws, we)
    DEL = {d["delivery_id"]: d for d in deliveries}
    ORD = {o["order_id"]: o for o in orders}
    by_day = {}
    for d in deliveries:
        by_day.setdefault(d["delivery_date"], []).append(d)

    # ---------- Tier-2 logic: plan routes, then simulate actuals ----------
    shifts, routes, stops, outcomes, matrix = [], [], [], [], []
    final, day_info, stops_of = {}, {}, {}
    for di in range(a.days):
        day = START + timedelta(di)
        todays = by_day.get(str(day), [])
        sc, factor, _ = R.choices(SCENARIOS, weights=[w for *_, w in SCENARIOS])[0]
        pending = sorted(todays, key=lambda x: (x["priority"], tw[x["delivery_id"]][0], x["delivery_id"]))
        day_v = R.sample(range(a.vehicles), min(a.vehicles_per_day, a.vehicles))
        caps_today = [cap_on(vehicles[v]["vehicle_id"], day) for v in day_v]
        day_info[str(day)] = (max(caps_today), any(vehicles[v]["restricted_access_ok"] for v in day_v))
        for vi in day_v:
            veh, drv = vehicles[vi], drivers[vi]
            stype = R.choice(list(SHIFTS))
            s0, e0 = SHIFTS[stype]
            sh = dict(shift_id=f"SHF-{len(shifts) + 1:06d}", driver_id=drv["driver_id"], vehicle_id=veh["vehicle_id"],
                      shift_date=str(day), shift_type=stype, shift_start=ts(day, s0), shift_end=ts(day, e0), status="UNUSED")
            shifts.append(sh)
            cap = cap_on(veh["vehicle_id"], day)
            t, pos, load, rows = s0, DEPOT_ID, 0, []
            while True:  # greedy insertion: best feasible next stop
                best = None
                for x in pending:
                    site = S[x["site_id"]]
                    if site["restricted_access"] and not veh["restricted_access_ok"]:
                        continue
                    if load + x["demand_kg"] > cap:
                        continue
                    km = dist(pos, x["site_id"]); tt = minutes(km, factor)
                    ws, we = tw[x["delivery_id"]]
                    arr = t + tt; start = max(arr, ws)
                    if start > we:
                        continue
                    dep = start + x["service_minutes"]
                    if dep + minutes(dist(x["site_id"], DEPOT_ID), factor) > e0:
                        continue  # driver shift rule
                    score = (start - t) + (x["priority"] - 3) * 20
                    if best is None or score < best[0]:
                        best = (score, x, km, tt, arr, start, dep, pos)
                if not best:
                    break
                _, x, km, tt, arr, start, dep, frm = best
                rows.append((x, km, tt, arr, start, dep, frm))
                pending.remove(x); load += x["demand_kg"]; t, pos = dep, x["site_id"]
            if not rows:
                continue
            sh["status"] = "COMPLETED"
            rid = f"RTE-2026-{len(routes) + 1:06d}"
            back_km = dist(pos, DEPOT_ID); back = minutes(back_km, factor)
            routes.append(dict(route_id=rid, shift_id=sh["shift_id"], vehicle_id=veh["vehicle_id"], driver_id=drv["driver_id"],
                               route_date=str(day), depot_departure=ts(day, s0), planned_return=ts(day, t + back),
                               capacity_kg_at_plan=cap, total_load_kg=load,
                               total_distance_km=round(sum(r[1] for r in rows) + back_km, 2),
                               planned_duration_min=t + back - s0, traffic_scenario=sc, status="COMPLETED"))
            stops_of[rid] = []
            cum, prev_dep = 0, s0
            for seq, (x, km, tt, arr, start, dep, frm) in enumerate(rows, 1):
                cum += x["demand_kg"]
                st = dict(stop_id=f"STOP-{rid}-{seq:03d}", route_id=rid, stop_sequence=seq, delivery_id=x["delivery_id"],
                          site_id=x["site_id"], from_site_id=frm, leg_distance_km=km, leg_travel_min=tt,
                          planned_arrival=ts(day, arr), planned_service_start=ts(day, start),
                          planned_departure=ts(day, dep), cum_load_kg=cum)
                stops.append(st); stops_of[rid].append(st)
                # simulated actuals: rule-based outcome (not random labels)
                ws, we = tw[x["delivery_id"]]
                a_arr = prev_dep + round(tt * R.uniform(0.9, 1.3))
                a_start = max(a_arr, ws)
                late = max(0, a_start - we)
                restricted = S[x["site_id"]]["restricted_access"]
                if late > GRACE_MIN:
                    status, reason = "FAILED", "WINDOW_MISSED"
                elif R.random() < (0.05 if restricted else 0.03):
                    status, reason = "FAILED", "ACCESS_BLOCKED" if restricted else "CUSTOMER_ABSENT"
                else:
                    status, reason = "DELIVERED", ""
                a_dep = a_start + (x["service_minutes"] + R.randint(0, 5) if status == "DELIVERED" else 5)
                outcomes.append(dict(outcome_id=f"OUT-{len(outcomes) + 1:06d}", stop_id=st["stop_id"],
                                     delivery_id=x["delivery_id"], actual_arrival=ts(day, a_arr),
                                     actual_service_start=ts(day, a_start), actual_departure=ts(day, a_dep),
                                     outcome_status=status, failure_reason_code=reason, late_minutes=late,
                                     arrival_delay_min=a_arr - arr))
                final[x["delivery_id"]] = (status, reason, ts(day, a_dep), rid)
                prev_dep = a_dep
        nodes = [DEPOT_ID] + sorted({x["site_id"] for x in todays})  # daily travel-time matrix / traffic scenario
        for o in nodes:
            for d in nodes:
                km = dist(o, d)
                matrix.append(dict(scenario_date=str(day), scenario_type=sc, origin_site_id=o, destination_site_id=d,
                                   traffic_factor=factor, distance_km=km, travel_minutes=minutes(km, factor)))

    # ---------- event history (append-only) + final states derived from events ----------
    events = []
    def ev(did, frm, to, when, reason="", ref=""):
        events.append(dict(event_id=f"EVT-{len(events) + 1:07d}", delivery_id=did, from_status=frm, to_status=to,
                           event_ts=when, reason_code=reason, ref_id=ref))
    for x in deliveries:
        did = x["delivery_id"]; day = date.fromisoformat(x["delivery_date"]); o = ORD[x["order_id"]]
        ev(did, "", "CREATED", o["order_date"] + "T09:00")
        plan_ts = ts(day - timedelta(1), 1080)
        if did in final:
            status, reason, when, rid = final[did]
            ev(did, "CREATED", "ASSIGNED", plan_ts, "", rid); ev(did, "ASSIGNED", status, when, reason, rid)
            x["status"], x["reason_code"] = status, reason
            o["status"] = "FULFILLED" if status == "DELIVERED" else "FAILED"
        else:
            mx, any_ok = day_info[x["delivery_date"]]
            reason = ("UNSERVED_CAPACITY" if x["demand_kg"] > mx else
                      "UNSERVED_RESTRICTED_SITE" if S[x["site_id"]]["restricted_access"] and not any_ok else
                      "UNSERVED_NO_SHIFT_TIME")
            ev(did, "CREATED", "UNSERVED", plan_ts, reason)
            x["status"], x["reason_code"], o["status"] = "UNSERVED", reason, "UNFULFILLED"

    # ---------- labelled anomaly injection (ground truth kept separate) ----------
    R2 = random.Random(a.seed + 1)
    k = a.anomalies_per_type
    elig = [r for r in routes if len(stops_of[r["route_id"]]) >= 3]
    picks = R2.sample(elig, min(len(elig), 5 * k))
    SH = {s["shift_id"]: s for s in shifts}
    gt = []
    def label(kind, etype, eid, rid, note):
        gt.append(dict(gt_id=f"GT-{len(gt) + 1:04d}", issue_type=kind, entity_type=etype, entity_id=eid, route_id=rid, details=note))
    for r in picks[:k]:
        sts = stops_of[r["route_id"]]; i = len(sts) // 2; d = DEL[sts[i]["delivery_id"]]
        delta = r["capacity_kg_at_plan"] - r["total_load_kg"] + R2.randint(50, 200)
        d["demand_kg"] += delta; ORD[d["order_id"]]["total_demand_kg"] += delta; r["total_load_kg"] += delta
        for s in sts[i:]:
            s["cum_load_kg"] += delta
        label("ROUTE_EXCEEDS_CAPACITY", "route", r["route_id"], r["route_id"], f"load {r['total_load_kg']} > capacity {r['capacity_kg_at_plan']}")
    for r in picks[k:2 * k]:
        sts = stops_of[r["route_id"]]; s = sts.pop(1)
        stops.remove(s); outcomes[:] = [o for o in outcomes if o["stop_id"] != s["stop_id"]]
        label("STOP_OMITTED", "delivery", s["delivery_id"], r["route_id"], f"stop {s['stop_id']} removed though delivery was ASSIGNED")
    for r in picks[2 * k:3 * k]:
        sts = stops_of[r["route_id"]]; s = sts[-1]; c = dict(s); n = len(sts) + 1
        c.update(stop_id=f"STOP-{r['route_id']}-{n:03d}", stop_sequence=n, planned_arrival=shift_ts(s["planned_departure"], 5))
        c["planned_service_start"] = c["planned_arrival"]
        c["planned_departure"] = shift_ts(c["planned_arrival"], DEL[c["delivery_id"]]["service_minutes"])
        stops.append(c); sts.append(c)
        label("STOP_DUPLICATED", "route_stop", c["stop_id"], r["route_id"], f"delivery {c['delivery_id']} visited twice")
    for r in picks[3 * k:4 * k]:
        sts = stops_of[r["route_id"]]; i = len(sts) // 2
        sts[i]["planned_arrival"] = shift_ts(sts[i - 1]["planned_departure"], -10)
        label("ARRIVAL_BEFORE_PREV_DEPARTURE", "route_stop", sts[i]["stop_id"], r["route_id"], "arrival set 10 min before previous departure")
    for r in picks[4 * k:5 * k]:
        sh = SH[r["shift_id"]]
        r["planned_return"] = shift_ts(sh["shift_end"], R2.randint(30, 90))
        stops_of[r["route_id"]][-1]["planned_departure"] = shift_ts(sh["shift_end"], 15)
        label("DRIVER_SHIFT_EXCEEDED", "route", r["route_id"], r["route_id"], f"return {r['planned_return']} after shift end {sh['shift_end']}")
    for s in R2.sample(sites[1:], k):
        s["latitude"] = round(R2.uniform(18.0, 18.6), 6)
        label("COORDINATE_OUTSIDE_REGION", "customer_site", s["site_id"], "", f"latitude {s['latitude']} outside region")

    # ---------- export ----------
    stops.sort(key=lambda s: (s["route_id"], s["stop_sequence"]))
    tables = dict(customer_sites=sites, orders=orders, deliveries=deliveries, time_windows=windows, vehicles=vehicles,
                  vehicle_capacity=caps, drivers=drivers, shifts=shifts, travel_time_matrix=matrix, routes=routes,
                  route_stops=stops, delivery_outcomes=outcomes, delivery_events=events)
    os.makedirs(f"{a.out}/data", exist_ok=True); os.makedirs(f"{a.out}/ground_truth", exist_ok=True)
    manifest = dict(seed=a.seed, params=vars(a), region=REGION, files={})
    def write(path, rows, fieldnames=None):
        with open(f"{a.out}/{path}", "w", newline="") as f:
            columns = fieldnames if fieldnames is not None else list(rows[0])
            w = csv.DictWriter(f, fieldnames=columns, lineterminator="\n"); w.writeheader(); w.writerows(rows)
        manifest["files"][path] = dict(rows=len(rows), sha256=hashlib.sha256(open(f"{a.out}/{path}", "rb").read()).hexdigest())
    for n, rows in tables.items():
        write(f"data/{n}.csv", rows)
    write(
        "ground_truth/ground_truth.csv",
        gt,
        ["gt_id", "issue_type", "entity_type", "entity_id", "route_id", "details"],
    )
    json.dump(manifest, open(f"{a.out}/manifest.json", "w"), indent=2)
    return manifest


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--sites", type=int, default=500); p.add_argument("--deliveries", type=int, default=2000)
    p.add_argument("--vehicles", type=int, default=100); p.add_argument("--days", type=int, default=30)
    p.add_argument("--vehicles-per-day", type=int, default=10, dest="vehicles_per_day")
    p.add_argument("--anomalies-per-type", type=int, default=6, dest="anomalies_per_type")
    p.add_argument("--seed", type=int, default=20261022); p.add_argument("--out", default="output")
    m = generate(p.parse_args())
    for f, v in m["files"].items():
        print(f"{f:40s} {v['rows']:>8} rows  {v['sha256'][:12]}")