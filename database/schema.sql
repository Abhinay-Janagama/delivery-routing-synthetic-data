-- Delivery routing schema (SQLite). Tier 3: data tier.
-- Every table has a primary key; every relationship has a foreign key.
-- Business-rule violations (capacity, shift, coordinates, duplicate stops) are deliberately NOT
-- blocked by constraints, because the dataset contains labelled anomalies detected by backend/validate.py.
PRAGMA foreign_keys = ON;

CREATE TABLE customer_sites (
    site_id TEXT PRIMARY KEY,
    site_name TEXT NOT NULL,
    site_type TEXT NOT NULL CHECK (site_type IN ('DEPOT','CUSTOMER')),
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    restricted_access INTEGER NOT NULL CHECK (restricted_access IN (0,1)),
    default_service_min INTEGER NOT NULL,
    status TEXT NOT NULL,
    effective_from TEXT NOT NULL
);

CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES customer_sites(site_id),
    order_date TEXT NOT NULL,
    total_demand_kg INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('OPEN','FULFILLED','FAILED','UNFULFILLED'))
);

CREATE TABLE deliveries (
    delivery_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL UNIQUE REFERENCES orders(order_id),          -- 1:1 with order
    site_id TEXT NOT NULL REFERENCES customer_sites(site_id),
    delivery_date TEXT NOT NULL,
    demand_kg INTEGER NOT NULL,
    service_minutes INTEGER NOT NULL,
    priority INTEGER NOT NULL CHECK (priority IN (1,2,3)),
    status TEXT NOT NULL CHECK (status IN ('DELIVERED','FAILED','UNSERVED')),
    reason_code TEXT
);

CREATE TABLE time_windows (
    window_id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL UNIQUE REFERENCES deliveries(delivery_id), -- 1:1
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    window_type TEXT NOT NULL,
    CHECK (window_start < window_end)
);

CREATE TABLE vehicles (
    vehicle_id TEXT PRIMARY KEY,
    vehicle_type TEXT NOT NULL CHECK (vehicle_type IN ('MINI','VAN','TRUCK')),
    restricted_access_ok INTEGER NOT NULL CHECK (restricted_access_ok IN (0,1)),
    status TEXT NOT NULL
);

CREATE TABLE vehicle_capacity (               -- effective-dated history, 1:N per vehicle
    capacity_id TEXT PRIMARY KEY,
    vehicle_id TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    capacity_kg INTEGER NOT NULL CHECK (capacity_kg > 0),
    effective_from TEXT NOT NULL,
    effective_to TEXT
);

CREATE TABLE drivers (
    driver_id TEXT PRIMARY KEY,
    driver_name TEXT NOT NULL,
    license_class TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE shifts (                         -- bridge: driver <-> vehicle per day (N:M)
    shift_id TEXT PRIMARY KEY,
    driver_id TEXT NOT NULL REFERENCES drivers(driver_id),
    vehicle_id TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    shift_date TEXT NOT NULL,
    shift_type TEXT NOT NULL CHECK (shift_type IN ('EARLY','DAY','LATE')),
    shift_start TEXT NOT NULL,
    shift_end TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('COMPLETED','UNUSED')),
    CHECK (shift_start < shift_end)
);

CREATE TABLE travel_time_matrix (
    scenario_date TEXT NOT NULL,
    scenario_type TEXT NOT NULL CHECK (scenario_type IN ('NORMAL','RAIN','EVENT','HOLIDAY')),
    origin_site_id TEXT NOT NULL REFERENCES customer_sites(site_id),
    destination_site_id TEXT NOT NULL REFERENCES customer_sites(site_id),
    traffic_factor REAL NOT NULL,
    distance_km REAL NOT NULL,
    travel_minutes INTEGER NOT NULL,
    PRIMARY KEY (scenario_date, origin_site_id, destination_site_id)
);

CREATE TABLE routes (
    route_id TEXT PRIMARY KEY,
    shift_id TEXT NOT NULL UNIQUE REFERENCES shifts(shift_id),           -- 1:0..1
    vehicle_id TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    driver_id TEXT NOT NULL REFERENCES drivers(driver_id),
    route_date TEXT NOT NULL,
    depot_departure TEXT NOT NULL,
    planned_return TEXT NOT NULL,
    capacity_kg_at_plan INTEGER NOT NULL,
    total_load_kg INTEGER NOT NULL,
    total_distance_km REAL NOT NULL,
    planned_duration_min INTEGER NOT NULL,
    traffic_scenario TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE route_stops (
    stop_id TEXT PRIMARY KEY,
    route_id TEXT NOT NULL REFERENCES routes(route_id),
    stop_sequence INTEGER NOT NULL,
    delivery_id TEXT NOT NULL REFERENCES deliveries(delivery_id),         -- NOT unique: duplicates are labelled anomalies
    site_id TEXT NOT NULL REFERENCES customer_sites(site_id),
    from_site_id TEXT NOT NULL REFERENCES customer_sites(site_id),
    leg_distance_km REAL NOT NULL,
    leg_travel_min INTEGER NOT NULL,
    planned_arrival TEXT NOT NULL,
    planned_service_start TEXT NOT NULL,
    planned_departure TEXT NOT NULL,
    cum_load_kg INTEGER NOT NULL,
    UNIQUE (route_id, stop_sequence)
);

CREATE TABLE delivery_outcomes (
    outcome_id TEXT PRIMARY KEY,
    stop_id TEXT NOT NULL UNIQUE REFERENCES route_stops(stop_id),         -- 1:1
    delivery_id TEXT NOT NULL REFERENCES deliveries(delivery_id),
    actual_arrival TEXT NOT NULL,
    actual_service_start TEXT NOT NULL,
    actual_departure TEXT NOT NULL,
    outcome_status TEXT NOT NULL CHECK (outcome_status IN ('DELIVERED','FAILED')),
    failure_reason_code TEXT,
    late_minutes INTEGER NOT NULL,
    arrival_delay_min INTEGER NOT NULL,
    CHECK ((outcome_status = 'FAILED') = (failure_reason_code IS NOT NULL))
);

CREATE TABLE delivery_events (                -- append-only state history
    event_id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL REFERENCES deliveries(delivery_id),
    from_status TEXT,
    to_status TEXT NOT NULL CHECK (to_status IN ('CREATED','ASSIGNED','UNSERVED','DELIVERED','FAILED')),
    event_ts TEXT NOT NULL,
    reason_code TEXT,
    ref_id TEXT
);

CREATE INDEX idx_stops_route ON route_stops(route_id);
CREATE INDEX idx_stops_delivery ON route_stops(delivery_id);
CREATE INDEX idx_events_delivery ON delivery_events(delivery_id);
CREATE INDEX idx_deliveries_date ON deliveries(delivery_date);