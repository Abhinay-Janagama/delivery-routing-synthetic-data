import json, subprocess, sys, tempfile, unittest, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN = os.path.join(ROOT, "backend", "generate.py")
VAL = os.path.join(ROOT, "backend", "validate.py")


def run(*args):
    return subprocess.run([sys.executable, *args], capture_output=True, text=True)


def files(d):
    return json.load(open(f"{d}/manifest.json"))["files"]


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

    def test_different_seed_changes_data(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            run(GEN, "--out", a, "--seed", "1"); run(GEN, "--out", b, "--seed", "2")
            self.assertNotEqual(files(a)["data/orders.csv"]["sha256"], files(b)["data/orders.csv"]["sha256"])


if __name__ == "__main__":
    unittest.main()