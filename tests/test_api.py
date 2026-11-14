import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend import api


ROOT = Path(__file__).resolve().parents[1]


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.database_path = Path(cls.temp_dir.name) / "delivery_routing.db"
        connection = sqlite3.connect(str(cls.database_path))
        connection.executescript((ROOT / "database" / "schema.sql").read_text())
        connection.executescript((ROOT / "database" / "seed.sql").read_text())
        connection.close()

        with (Path(cls.temp_dir.name) / "manifest.json").open(
            "w", encoding="utf-8"
        ) as manifest_file:
            json.dump(
                {
                    "region": {
                        "lat_min": 17.20,
                        "lat_max": 17.60,
                        "lon_min": 78.20,
                        "lon_max": 78.70,
                    }
                },
                manifest_file,
            )

        cls.previous_db_path = api.DB_PATH
        api.DB_PATH = cls.database_path

    @classmethod
    def tearDownClass(cls):
        api.DB_PATH = cls.previous_db_path
        cls.temp_dir.cleanup()

    def setUp(self):
        self.connection = sqlite3.connect(str(self.database_path))
        self.connection.row_factory = sqlite3.Row
        self.previous_runs_dir = api.RUNS_DIR
        self.generation_temp = tempfile.TemporaryDirectory()
        api.RUNS_DIR = Path(self.generation_temp.name) / "runs"

    def tearDown(self):
        self.connection.close()
        api.RUNS_DIR = self.previous_runs_dir
        self.generation_temp.cleanup()

    def test_summary_reconciles_delivery_statuses(self):
        result = api.summary(connection=self.connection)

        self.assertEqual(
            result["deliveries"],
            result["delivered"] + result["failed"] + result["unserved"],
        )
        self.assertEqual(result["routes"], 282)
        self.assertGreaterEqual(result["on_time_pct_of_delivered"], 0)
        self.assertLessEqual(result["on_time_pct_of_delivered"], 100)

    def test_exceptions_detect_each_injected_anomaly_type(self):
        result = api.exceptions(connection=self.connection)
        detected_types = {item["category"] for item in result["items"]}

        self.assertTrue(
            {
                "ROUTE_EXCEEDS_CAPACITY",
                "STOP_OMITTED",
                "STOP_DUPLICATED",
                "ARRIVAL_BEFORE_PREV_DEPARTURE",
                "DRIVER_SHIFT_EXCEEDED",
                "COORDINATE_OUTSIDE_REGION",
            }.issubset(detected_types)
        )

    def test_delivery_detail_includes_lifecycle_and_stop_trace(self):
        delivery_id = self.connection.execute(
            "SELECT delivery_id FROM deliveries ORDER BY delivery_id LIMIT 1"
        ).fetchone()["delivery_id"]

        result = api.delivery_detail(
            delivery_id=delivery_id, connection=self.connection
        )

        self.assertEqual(result["delivery"]["delivery_id"], delivery_id)
        self.assertTrue(result["events"])

    def test_api_connection_rejects_writes(self):
        connection_generator = api.get_connection(run_id=None)
        connection = next(connection_generator)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM deliveries")
        finally:
            connection_generator.close()

    def test_scenario_generation_isolated_from_baseline_and_seed(self):
        baseline_deliveries = self.connection.execute(
            "SELECT COUNT(*) FROM deliveries"
        ).fetchone()[0]
        seed_path = ROOT / "database" / "seed.sql"
        original_seed = seed_path.read_bytes()

        result = api.create_generation(
            api.GenerationRequest(
                sites=20,
                deliveries=60,
                vehicles=10,
                days=5,
                vehicles_per_day=5,
                anomalies_per_type=0,
                seed=123,
            )
        )

        run_id = result["run_id"]
        run_database = api.RUNS_DIR / run_id / "delivery_routing.db"
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["validation"]["checks_failed"], 0)
        self.assertEqual(result["summary"]["deliveries"], 60)
        self.assertTrue(run_database.is_file())
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0],
            baseline_deliveries,
        )
        self.assertEqual(seed_path.read_bytes(), original_seed)
        self.assertEqual(api.generations()["items"][0]["run_id"], run_id)

        connection_generator = api.get_connection(run_id=run_id)
        run_connection = next(connection_generator)
        try:
            self.assertEqual(
                run_connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0],
                60,
            )
        finally:
            connection_generator.close()

    def test_unknown_run_id_is_rejected(self):
        with self.assertRaises(api.HTTPException) as error:
            api._database_path("../outside")

        self.assertEqual(error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
