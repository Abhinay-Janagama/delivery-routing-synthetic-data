import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN = os.path.join(ROOT, "backend", "generate.py")
VAL = os.path.join(ROOT, "backend", "validate.py")


def run(*args):
    return subprocess.run([sys.executable, *args], capture_output=True, text=True)


def files(d):
    with open(f"{d}/manifest.json", encoding="utf-8") as manifest_file:
        return json.load(manifest_file)["files"]


class PipelineTests(unittest.TestCase):
    def test_same_seed_is_reproducible(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.assertEqual(run(GEN, "--out", a).returncode, 0)
            self.assertEqual(run(GEN, "--out", b).returncode, 0)
            self.assertEqual(files(a), files(b))

    def test_volumes_and_validation(self):
        with tempfile.TemporaryDirectory() as d:
            run(GEN, "--out", d)
            m = files(d)
            self.assertEqual(m["data/customer_sites.csv"]["rows"], 501)  # 500 customers + depot
            self.assertEqual(m["data/deliveries.csv"]["rows"], 2000)
            self.assertEqual(m["data/vehicles.csv"]["rows"], 100)
            r = run(VAL, d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with open(f"{d}/validation_summary.json", encoding="utf-8") as summary_file:
                summary = json.load(summary_file)
            self.assertEqual(summary["checks_failed"], 0)
            self.assertEqual(summary["checks_total"], 58)
            for name in (
                "route.total_load == distinct stop demand",
                "stop.cum_load == running distinct demand",
                "route.total_distance == stop legs + return leg",
                "route.planned_duration == return - departure",
            ):
                check = next(item for item in summary["checks"] if item["check"] == name)
                self.assertTrue(check["passed"], check)
            for scores in summary["anomaly_detection"].values():
                self.assertEqual(scores["expected"], 6)
                self.assertEqual(scores["true_pos"], 6)
                self.assertEqual(scores["false_pos"], 0)
                self.assertEqual(scores["missed"], 0)

    def test_route_arithmetic_corruption_fails_validation(self):
        with tempfile.TemporaryDirectory() as d:
            generated = run(
                GEN,
                "--out",
                d,
                "--sites",
                "20",
                "--deliveries",
                "60",
                "--vehicles",
                "10",
                "--days",
                "5",
                "--vehicles-per-day",
                "5",
                "--anomalies-per-type",
                "0",
            )
            self.assertEqual(generated.returncode, 0, generated.stdout + generated.stderr)

            cases = (
                (
                    "routes.csv",
                    "route.total_load == distinct stop demand",
                    "total_load_kg",
                    1,
                ),
                (
                    "routes.csv",
                    "route.total_distance == stop legs + return leg",
                    "total_distance_km",
                    1.0,
                ),
                (
                    "routes.csv",
                    "route.planned_duration == return - departure",
                    "planned_duration_min",
                    1,
                ),
                (
                    "route_stops.csv",
                    "stop.cum_load == running distinct demand",
                    "cum_load_kg",
                    1,
                ),
            )
            for filename, check_name, field, delta in cases:
                path = Path(d) / "data" / filename
                with path.open(newline="", encoding="utf-8") as source:
                    reader = csv.DictReader(source)
                    fieldnames = reader.fieldnames
                    records = list(reader)
                original_value = records[0][field]
                records[0][field] = str(type(delta)(original_value) + delta)
                with path.open("w", newline="", encoding="utf-8") as destination:
                    writer = csv.DictWriter(destination, fieldnames=fieldnames, lineterminator="\n")
                    writer.writeheader()
                    writer.writerows(records)

                validation = run(VAL, d)
                self.assertNotEqual(validation.returncode, 0, validation.stdout)
                with (Path(d) / "validation_summary.json").open(
                    encoding="utf-8"
                ) as summary_file:
                    summary = json.load(summary_file)
                check = next(item for item in summary["checks"] if item["check"] == check_name)
                self.assertFalse(check["passed"], check)

                records[0][field] = original_value
                with path.open("w", newline="", encoding="utf-8") as destination:
                    writer = csv.DictWriter(destination, fieldnames=fieldnames, lineterminator="\n")
                    writer.writeheader()
                    writer.writerows(records)

    def test_different_seed_changes_data(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            run(GEN, "--out", a, "--seed", "1"); run(GEN, "--out", b, "--seed", "2")
            self.assertNotEqual(files(a)["data/orders.csv"]["sha256"], files(b)["data/orders.csv"]["sha256"])


if __name__ == "__main__":
    unittest.main()