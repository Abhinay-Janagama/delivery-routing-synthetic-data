# delivery-routing-synthetic-data
# Delivery Routing Synthetic Data

Synthetic dataset generator for last-mile delivery planning: customer sites, orders,
vehicles with capacities, driver shifts, time windows, routes, stops and delivery outcomes.

**Status:** work in progress.

## Architecture (3-tier)
- **Data tier:** CSV/SQL seed data and SQLite schema (`database/`, `output/`)
- **Application tier:** generator, validator and business rules (`backend/`)
- **Presentation tier:** reports/dashboard of KPIs and exceptions (`frontend/`)

## Fixed seed
`20261022`