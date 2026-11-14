-- Example analytics queries (SQLite). Run: sqlite3 output/delivery_routing.db < database/queries.sql

-- 1. Daily service level: delivered / failed / unserved
SELECT delivery_date,
       COUNT(*) AS deliveries,
       SUM(status = 'DELIVERED') AS delivered,
       SUM(status = 'FAILED')    AS failed,
       SUM(status = 'UNSERVED')  AS unserved,
       ROUND(100.0 * SUM(status = 'DELIVERED') / COUNT(*), 1) AS delivered_pct
FROM deliveries GROUP BY delivery_date ORDER BY delivery_date;

-- 2. Exception report: every failed or unserved delivery by reason code
SELECT status, reason_code, COUNT(*) AS n
FROM deliveries WHERE status <> 'DELIVERED'
GROUP BY status, reason_code ORDER BY n DESC;

-- 3. Capacity utilisation per vehicle type
SELECT v.vehicle_type, COUNT(*) AS routes,
       ROUND(100.0 * AVG(r.total_load_kg * 1.0 / r.capacity_kg_at_plan), 1) AS avg_utilisation_pct,
       ROUND(SUM(r.total_distance_km), 1) AS total_km
FROM routes r JOIN vehicles v ON v.vehicle_id = r.vehicle_id
GROUP BY v.vehicle_type;

-- 4. Traffic impact: average lateness by traffic scenario
SELECT r.traffic_scenario, COUNT(*) AS stops,
       ROUND(AVG(o.arrival_delay_min), 1) AS avg_delay_min,
       SUM(o.outcome_status = 'FAILED') AS failed
FROM delivery_outcomes o
JOIN route_stops s ON s.stop_id = o.stop_id
JOIN routes r ON r.route_id = s.route_id
GROUP BY r.traffic_scenario;

-- 5. Full audit trail (history is never overwritten) for one delivery
SELECT event_id, from_status, to_status, event_ts, reason_code, ref_id
FROM delivery_events WHERE delivery_id = 'DEL-2026-000001' ORDER BY event_id;

-- 6. Traceability: delivery -> stop -> route -> vehicle -> driver
SELECT d.delivery_id, s.stop_id, r.route_id, r.vehicle_id, r.driver_id, o.outcome_status
FROM deliveries d
JOIN route_stops s ON s.delivery_id = d.delivery_id
JOIN routes r ON r.route_id = s.route_id
LEFT JOIN delivery_outcomes o ON o.stop_id = s.stop_id
WHERE d.delivery_id = 'DEL-2026-000001';