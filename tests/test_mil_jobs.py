import unittest
from datetime import datetime, timezone
from pathlib import Path

from slurm_portal.mil_jobs import (
    _duration_short,
    _expand_nodelist,
    collect_snapshot,
    render_snapshot,
)


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append((args, timeout))
        command = Path(args[0]).name
        if command == "sinfo" and any(
            argument.startswith("--Format=") for argument in args
        ):
            return (
                "n003  gpu:a6000:2(IDX:4-5)\n"
                "n004  (null)\n"
                "n080  gpu:h200:1(IDX:0)\n"
            )
        if command == "sinfo":
            return (
                "n003|g2|mix|128|1024000|433250|gpu:a6000:8(S:0-1)|16/112/0/128\n"
                "n004|g2|idle|128|1024000|900000|gpu:a6000:8(S:0-1)|0/128/0/128\n"
                "n080|g4|alloc|64|512000|200000|gpu:h200:4|16/48/0/64\n"
            )
        if command == "squeue":
            return (
                "7001|alice|g2|n003|train|gres/gpu:a6000:2|8|64G|"
                "00:10:00|03:50:00\n"
                "7002|bob|g4|n080|inference|gres/gpu:h200:1|16|128G|"
                "01:00:00|01:00:00\n"
            )
        raise AssertionError(args)


class MilJobsTests(unittest.TestCase):
    def test_formats_slurm_durations_for_terminal(self):
        self.assertEqual(_duration_short("1-12:32:29"), "1d 12h")
        self.assertEqual(_duration_short("03:50:00"), "3h 50m")
        self.assertEqual(_duration_short("UNLIMITED"), "∞")

    def test_expand_nodelist(self):
        self.assertEqual(
            _expand_nodelist("n[072-074,080]"),
            ["n072", "n073", "n074", "n080"],
        )

    def test_collects_nodes_users_and_gpu_jobs(self):
        snapshot = collect_snapshot(
            runner=FakeRunner(),
            slurm_bin=Path("/slurm"),
            allowed_partitions={"g2"},
        )

        self.assertEqual([node["name"] for node in snapshot["nodes"]], ["n003", "n004"])
        self.assertEqual(snapshot["nodes"][0]["gpu_used"], 2)
        self.assertEqual(snapshot["nodes"][0]["gpu_indices"], [4, 5])
        self.assertEqual(snapshot["jobs_by_node"]["n003"][0]["user"], "alice")
        self.assertEqual(snapshot["jobs_by_node"]["n003"][0]["gpu_count"], 2)

    def test_render_defaults_to_active_nodes(self):
        snapshot = collect_snapshot(
            runner=FakeRunner(),
            slurm_bin=Path("/slurm"),
            allowed_partitions={"g2"},
        )
        snapshot["generated_at"] = datetime(2026, 7, 27, tzinfo=timezone.utc)

        output = render_snapshot(
            snapshot,
            server_name="82server",
            cluster_name="tgmv2",
            color=False,
            terminal_width=110,
        )
        self.assertIn("n003", output)
        self.assertNotIn("n004", output)
        self.assertIn("alice", output)
        self.assertIn("A6000 ×2", output)
        self.assertIn("idx 4,5", output)
        self.assertIn("JOB ID", output)
        self.assertIn("▼ G2", output)

        all_output = render_snapshot(
            snapshot,
            server_name="82server",
            cluster_name="tgmv2",
            include_all=True,
            color=False,
            terminal_width=110,
        )
        self.assertIn("n004", all_output)


if __name__ == "__main__":
    unittest.main()
