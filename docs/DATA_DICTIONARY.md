# Data Dictionary

All files are CSV in `output/data/` (UTF-8, header row, `\n` line endings). Timestamps are local naive ISO strings `YYYY-MM-DDTHH:MM`; dates are `YYYY-MM-DD`. Distances are km, weights kg, durations minutes. "Req" = required (Y) or optional (N). Every table has exactly the columns listed here.

## Conventions

| Entity | ID format | Example |
|---|---|---|
| Site | `SITE-######` (`SITE-000000` = depot) | SITE-000042 |
| Order | `ORD-2026-######` | ORD-2026-000001 |
| Delivery | `DEL-2026-######` | DEL-2026-000001 |
| Vehicle | `VEH-######` | VEH-000001 |
| Driver | `DRV-######` | DRV-000001 |
| Route | `RTE-2026-######` | RTE-2026-000001 |
| Route stop | `STOP-<route-id>-###` | STOP-RTE-2026-000001-001 |
| Others | `TW-`, `CAP-`, `SHF-`, `OUT-`, `EVT-`, `GT-` + zero-padded counter | SHF-000001 |

IDs are zero-padded counters, assigned once, and never changed. Foreign keys use the exact parent ID string.

## 1. customer_sites (CustomerSite) - PK `site_id`
| Field | Type | Req | Allowed values / unit | Unique | Validation |
|---|---|---|---|---|---|
| site_id | string | Y | `SITE-######` | Y | PK |
| site_name | string | Y | text | N | non-empty |
| site_type | enum | Y | DEPOT, CUSTOMER | N | exactly one DEPOT (`SITE-000000`) |
| latitude | decimal | Y | degrees, 17.20-17.60 | N | inside region (violations are labelled `COORDINATE_OUTSIDE_REGION`) |
| longitude | decimal | Y | degrees, 78.20-78.70 | N | inside region |
| restricted_access | int | Y | 0, 1 | N | 1 = only vehicles with `restricted_access_ok=1` may serve |
| default_service_min | int | Y | minutes, 0-30 | N | 0 for depot |
| status | enum | Y | ACTIVE | N | |
| effective_from | date | Y | date | N | |

## 2. orders (Order) - PK `order_id`, FK `site_id` -> customer_sites
| Field | Type | Req | Allowed / unit | Unique | Validation |
|---|---|---|---|---|---|
| order_id | string | Y | `ORD-2026-######` | Y | PK |
| site_id | string | Y | customer site | N | FK |
| order_date | date | Y | date | N | <= delivery_date |
| total_demand_kg | int | Y | kg, > 0 | N | equals the delivery's `demand_kg` |
| status | enum | Y | FULFILLED, FAILED, UNFULFILLED | N | derived from delivery outcome |

## 3. deliveries (Delivery) - PK `delivery_id`, FKs `order_id`, `site_id`
| Field | Type | Req | Allowed / unit | Unique | Validation |
|---|---|---|---|---|---|
| delivery_id | string | Y | `DEL-2026-######` | Y | PK |
| order_id | string | Y | order | Y (1:1) | FK |
| site_id | string | Y | customer site | N | FK, same as the order's site |
| delivery_date | date | Y | 2026-10-01 to 2026-10-30 | N | |
| demand_kg | int | Y | kg, > 0 | N | |
| service_minutes | int | Y | minutes | N | site default + demand // 50 |
| priority | int | Y | 1 (high), 2, 3 | N | enum |
| status | enum | Y | DELIVERED, FAILED, UNSERVED | N | equals `to_status` of the last event |
| reason_code | enum | N | see Reason codes | N | required iff FAILED or UNSERVED |

## 4. time_windows (TimeWindow) - PK `window_id`, FK `delivery_id` (1:1)
| Field | Type | Req | Allowed / unit | Unique | Validation |
|---|---|---|---|---|---|
| window_id | string | Y | `TW-######` | Y | PK |
| delivery_id | string | Y | delivery | Y | FK |
| window_start | timestamp | Y | 08:00-14:30 on delivery date | N | < window_end |
| window_end | timestamp | Y | max 18:00 | N | width 2, 3 or 4 h (clipped at 18:00) |
| window_type | enum | Y | HARD | N | |

## 5. vehicles (Vehicle) - PK `vehicle_id`
| Field | Type | Req | Allowed / unit | Unique | Validation |
|---|---|---|---|---|---|
| vehicle_id | string | Y | `VEH-######` | Y | PK |
| vehicle_type | enum | Y | MINI, VAN, TRUCK | N | 30% / 40% / 30% mix |
| restricted_access_ok | int | Y | 0, 1 | N | TRUCK = 0, others = 1 |
| status | enum | Y | ACTIVE | N | |

## 6. vehicle_capacity (VehicleCapacity) - PK `capacity_id`, FK `vehicle_id` (1:N, effective-dated)
| Field | Type | Req | Allowed / unit | Unique | Validation |
|---|---|---|---|---|---|
| capacity_id | string | Y | `CAP-######` | Y | PK |
| vehicle_id | string | Y | vehicle | N | FK |
| capacity_kg | int | Y | kg: MINI 800, VAN 1500, TRUCK 3000 (+100 in the historical row) | N | > 0 |
| effective_from | date | Y | date | N | |
| effective_to | date | N | date or blank (blank = current) | N | >= effective_from; no overlaps per vehicle |

History is kept: a capacity change closes the old row and adds a new one.

## 7. drivers (Driver) - PK `driver_id`
| Field | Type | Req | Allowed / unit | Unique | Validation |
|---|---|---|---|---|---|
| driver_id | string | Y | `DRV-######` | Y | PK |
| driver_name | string | Y | synthetic label ("Driver 001"), no personal data | N | |
| license_class | enum | Y | LMV, HGV | N | HGV for trucks |
| status | enum | Y | ACTIVE | N | |

## 8. shifts (Shift) - PK `shift_id`, FKs `driver_id`, `vehicle_id`
| Field | Type | Req | Allowed / unit | Unique | Validation |
|---|---|---|---|---|---|
| shift_id | string | Y | `SHF-######` | Y | PK |
| driver_id | string | Y | driver | N | FK |
| vehicle_id | string | Y | vehicle | N | FK |
| shift_date | date | Y | date | N | |
| shift_type | enum | Y | EARLY 06:00-15:00, DAY 08:00-17:00, LATE 10:00-19:00 | N | |
| shift_start | timestamp | Y | | N | < shift_end |
| shift_end | timestamp | Y | | N | |
| status | enum | Y | COMPLETED, UNUSED | N | COMPLETED iff a route exists |

## 9. travel_time_matrix (TravelTimeMatrix) - composite PK (`scenario_date`, `origin_site_id`, `destination_site_id`)
One matrix per day over the depot and that day's delivery sites (the daily traffic scenario).
| Field | Type | Req | Allowed / unit | Unique | Validation |
|---|---|---|---|---|---|
| scenario_date | date | Y | date | part of PK | |
| scenario_type | enum | Y | NORMAL, RAIN, EVENT, HOLIDAY | N | one per day |
| origin_site_id | string | Y | site | part of PK | FK |
| destination_site_id | string | Y | site | part of PK | FK (origin = destination gives 0 km) |
| traffic_factor | decimal | Y | NORMAL 1.0, RAIN 1.25, EVENT 1.4, HOLIDAY 0.85 | N | |
| distance_km | decimal | Y | km | N | 1.3 x haversine distance |
| travel_minutes | int | Y | minutes | N | max(1, round(km / 35 x 60 x traffic_factor)) |

## 10. routes (Route) - PK `route_id`, FKs `shift_id`, `vehicle_id`, `driver_id`
| Field | Type | Req | Allowed / unit | Unique | Validation |
|---|---|---|---|---|---|
| route_id | string | Y | `RTE-2026-######` | Y | PK |
| shift_id | string | Y | shift | Y (1:1) | FK |
| vehicle_id | string | Y | vehicle | N | FK |
| driver_id | string | Y | driver | N | FK |
| route_date | date | Y | date | N | = shift_date |
| depot_departure | timestamp | Y | = shift_start | N | |
| planned_return | timestamp | Y | | N | <= shift_end (violations labelled `DRIVER_SHIFT_EXCEEDED`) |
| capacity_kg_at_plan | int | Y | kg | N | capacity effective on route_date |
| total_load_kg | int | Y | kg | N | = sum of stop demands = last stop `cum_load_kg`; <= capacity (violations labelled `ROUTE_EXCEEDS_CAPACITY`) |
| total_distance_km | decimal | Y | km | N | sum of legs + return leg |
| planned_duration_min | int | Y | minutes | N | |
| traffic_scenario | enum | Y | NORMAL, RAIN, EVENT, HOLIDAY | N | |
| status | enum | Y | COMPLETED | N | |

## 11. route_stops (RouteStop) - PK `stop_id`, FKs `route_id`, `delivery_id`, `site_id`, `from_site_id`
| Field | Type | Req | Allowed / unit | Unique | Validation |
|---|---|---|---|---|---|
| stop_id | string | Y | `STOP-<route-id>-###` | Y | PK |
| route_id | string | Y | route | N | FK |
| stop_sequence | int | Y | 1..n | unique per route | contiguous (gaps labelled `STOP_OMITTED`) |
| delivery_id | string | Y | delivery | once overall | FK (repeats labelled `STOP_DUPLICATED`) |
| site_id | string | Y | site | N | FK |
| from_site_id | string | Y | site (depot for stop 1) | N | FK |
| leg_distance_km | decimal | Y | km | N | equals the matrix value for that day and pair |
| leg_travel_min | int | Y | minutes | N | equals the matrix value |
| planned_arrival | timestamp | Y | | N | >= previous stop's planned_departure (violations labelled `ARRIVAL_BEFORE_PREV_DEPARTURE`) |
| planned_service_start | timestamp | Y | | N | inside the delivery's time window |
| planned_departure | timestamp | Y | | N | = service start + service_minutes |
| cum_load_kg | int | Y | kg | N | running total, non-decreasing |

## 12. delivery_outcomes (DeliveryOutcome) - PK `outcome_id`, FKs `stop_id`, `delivery_id`
Simulated actuals. Actual leg time = planned x uniform(0.9, 1.3).
| Field | Type | Req | Allowed / unit | Unique | Validation |
|---|---|---|---|---|---|
| outcome_id | string | Y | `OUT-######` | Y | PK |
| stop_id | string | Y | stop | Y (1:1) | FK |
| delivery_id | string | Y | delivery | N | FK |
| actual_arrival | timestamp | Y | | N | |
| actual_service_start | timestamp | Y | = max(arrival, window_start) | N | |
| actual_departure | timestamp | Y | | N | |
| outcome_status | enum | Y | DELIVERED, FAILED | N | |
| failure_reason_code | enum | N | see Reason codes | N | required iff FAILED |
| late_minutes | int | Y | minutes | N | max(0, service_start - window_end) |
| arrival_delay_min | int | Y | minutes | N | actual - planned arrival |

Rule: `late_minutes` > 15 gives FAILED/WINDOW_MISSED. Otherwise failure chance is 3% (CUSTOMER_ABSENT) at normal sites and 5% (ACCESS_BLOCKED) at restricted sites.

## 13. delivery_events (state history, append-only) - PK `event_id`, FK `delivery_id`
| Field | Type | Req | Allowed / unit | Unique | Validation |
|---|---|---|---|---|---|
| event_id | string | Y | `EVT-#######` | Y | PK |
| delivery_id | string | Y | delivery | N | FK |
| from_status | enum | N | blank, CREATED, ASSIGNED | N | must equal the previous event's `to_status` |
| to_status | enum | Y | CREATED, ASSIGNED, UNSERVED, DELIVERED, FAILED | N | |
| event_ts | timestamp | Y | | N | non-decreasing per delivery |
| reason_code | enum | N | see Reason codes | N | |
| ref_id | string | N | route id | N | set on ASSIGNED, DELIVERED, FAILED |

Lifecycle: `(none) -> CREATED -> ASSIGNED -> DELIVERED | FAILED`, or `CREATED -> UNSERVED`. Rows are never updated or deleted.

## 14. ground_truth (separate file: `output/ground_truth/ground_truth.csv`)
Never used as an input by the generator's checks. Read only to score the detectors.
| Field | Type | Description |
|---|---|---|
| gt_id | string | `GT-####` |
| issue_type | enum | ROUTE_EXCEEDS_CAPACITY, STOP_OMITTED, STOP_DUPLICATED, ARRIVAL_BEFORE_PREV_DEPARTURE, DRIVER_SHIFT_EXCEEDED, COORDINATE_OUTSIDE_REGION |
| entity_type | enum | route, delivery, route_stop, customer_site |
| entity_id | string | ID of the affected record |
| route_id | string | Route involved (blank for site anomalies) |
| details | string | Human-readable explanation |

## Reason codes
- **Failed delivery:** WINDOW_MISSED, CUSTOMER_ABSENT, ACCESS_BLOCKED
- **Unserved:** UNSERVED_CAPACITY (demand exceeds the day's largest vehicle), UNSERVED_RESTRICTED_SITE (no capable vehicle that day), UNSERVED_NO_SHIFT_TIME (no feasible slot within windows and shifts)

## Relationships and cardinality
| Parent | Child | Cardinality | Notes |
|---|---|---|---|
| customer_sites | orders | 1:N | |
| orders | deliveries | 1:1 | |
| deliveries | time_windows | 1:1 | |
| deliveries | route_stops | 1:0..1 | 0 when UNSERVED |
| deliveries | delivery_events | 1:N | history |
| vehicles | vehicle_capacity | 1:N | effective-dated |
| vehicles, drivers | shifts | 1:N each | a shift pairs one driver with one vehicle for a day |
| shifts | routes | 1:0..1 | 0 when the shift is UNUSED |
| routes | route_stops | 1:N | ordered by `stop_sequence` |
| route_stops | delivery_outcomes | 1:1 | |
| customer_sites | travel_time_matrix | 1:N | as origin and as destination |

Drivers and vehicles relate N:M over time, resolved through the `shifts` bridge table.