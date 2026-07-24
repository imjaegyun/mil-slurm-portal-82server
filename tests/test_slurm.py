import tempfile
import unittest
from pathlib import Path

from slurm_portal.slurm import PortalError, SlurmClient, validate_allocation


G2_NODE = {
    "name": "n003",
    "partition": "g2",
    "state": "mix",
    "cpus": 128,
    "memory_mb": 1024000,
    "free_memory_mb": 433250,
    "gres": "gpu:a6000:8(S:0-1)",
    "gpus": 8,
    "free_gpus": 6,
    "cpu_idle": 112,
}


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append((args, timeout))
        command = Path(args[0]).name
        if command == "sinfo":
            if any(arg.startswith("--Format=") for arg in args):
                return (
                    "n001  gpu:a6000:6(IDX:0-5)\n"
                    "n003  gpu:a6000:2(IDX:4-5)\n"
                    "n005  gpu:h200:3(IDX:0-2)\n"
                )
            return (
                "n001|g1|alloc|32|256000|140725|gpu:a6000:8(S:0-1)|32/0/0/32\n"
                "n003|g2|mix|128|1024000|433250|gpu:a6000:8(S:0-1)|16/112/0/128\n"
                "n005|g3*|idle|32|1024000|48498|gpu:h200:3(S:0-1)|0/32/0/32\n"
            )
        if command == "sbatch":
            return "7001;tgmv2\n"
        if command == "scancel":
            return ""
        if command == "scontrol":
            return (
                "JobId=7001 JobName=ui-session-a1b2c3d4 "
                "UserId=ijg2603(1060) JobState=RUNNING RunTime=00:10:00 "
                "TimeLimit=04:00:00 Partition=g2 NodeList=n003 "
                "Nodes=n003 CPU_IDs=0-7 Mem=0 "
                "GRES=gpu:a6000:2(IDX:4-5)\n"
            )
        if command == "squeue" and any(arg == "--jobs=7001" for arg in args):
            return "7001|ijg2603|ui-session-a1b2c3d4|RUNNING\n"
        if command == "squeue" and any(arg == "--nodes=n003" for arg in args):
            return "7001\n"
        if command == "squeue":
            return (
                "7001|RUNNING|g2|n003|ui-session-a1b2c3d4|00:10|03:50:00|"
                "None|gpu:a6000:1|8|64G|ijg2603\n"
            )
        raise AssertionError(args)


class AllocationValidationTests(unittest.TestCase):
    def test_valid_request_derives_partition_and_gpu_type_from_node(self):
        result = validate_allocation(
            {
                "gpu_count": 1,
                "cpus": 8,
                "memory_gb": 64,
                "hours": 4,
            },
            G2_NODE,
        )
        self.assertEqual(result["node_name"], "n003")
        self.assertEqual(result["partition"], "g2")
        self.assertEqual(result["gpu_type"], "a6000")

    def test_rejects_node_without_gpu(self):
        with self.assertRaises(PortalError):
            validate_allocation(
                {
                    "gpu_count": 1,
                    "cpus": 1,
                    "memory_gb": 1,
                    "hours": 1,
                },
                {**G2_NODE, "gres": "(null)", "gpus": 0},
            )

    def test_allows_no_time_limit(self):
        result = validate_allocation(
            {
                "gpu_count": 1,
                "cpus": 8,
                "memory_gb": 64,
                "hours": 0,
            },
            G2_NODE,
        )
        self.assertEqual(result["hours"], 0)

    def test_allows_more_than_24_hours(self):
        result = validate_allocation(
            {
                "gpu_count": 1,
                "cpus": 8,
                "memory_gb": 64,
                "hours": 48,
            },
            G2_NODE,
        )
        self.assertEqual(result["hours"], 48)

    def test_rejects_negative_time(self):
        with self.assertRaises(PortalError):
            validate_allocation(
                {
                    "gpu_count": 1,
                    "cpus": 8,
                    "memory_gb": 64,
                    "hours": -1,
                },
                G2_NODE,
            )

    def test_rejects_request_above_current_free_gpus(self):
        with self.assertRaisesRegex(PortalError, "6 GPUs available"):
            validate_allocation(
                {
                    "gpu_count": 7,
                    "cpus": 8,
                    "memory_gb": 64,
                    "hours": 4,
                },
                G2_NODE,
            )

    def test_rejects_request_above_current_idle_cpus(self):
        with self.assertRaisesRegex(PortalError, "112 CPU cores available"):
            validate_allocation(
                {
                    "gpu_count": 1,
                    "cpus": 113,
                    "memory_gb": 64,
                    "hours": 4,
                },
                G2_NODE,
            )

    def test_rejects_request_above_current_free_memory(self):
        with self.assertRaisesRegex(PortalError, "423 GB memory available"):
            validate_allocation(
                {
                    "gpu_count": 1,
                    "cpus": 8,
                    "memory_gb": 424,
                    "hours": 4,
                },
                G2_NODE,
            )

    def test_rejects_node_without_currently_available_gpu(self):
        with self.assertRaisesRegex(PortalError, "no GPU available"):
            validate_allocation(
                {
                    "gpu_count": 1,
                    "cpus": 8,
                    "memory_gb": 64,
                    "hours": 4,
                },
                {**G2_NODE, "free_gpus": 0},
            )

    def test_waiting_request_can_use_node_capacity_above_current_free(self):
        result = validate_allocation(
            {
                "gpu_count": 8,
                "cpus": 128,
                "memory_gb": 950,
                "hours": 4,
                "wait_for_resources": True,
            },
            {**G2_NODE, "free_gpus": 0, "cpu_idle": 0, "free_memory_mb": 0},
        )
        self.assertTrue(result["wait_for_resources"])
        self.assertEqual(result["gpu_count"], 8)

    def test_waiting_request_rejects_above_node_capacity(self):
        with self.assertRaisesRegex(PortalError, "at most 8 GPUs"):
            validate_allocation(
                {
                    "gpu_count": 9,
                    "cpus": 8,
                    "memory_gb": 64,
                    "hours": 4,
                    "wait_for_resources": True,
                },
                G2_NODE,
            )


class SlurmClientTests(unittest.TestCase):
    def setUp(self):
        self.runner = FakeRunner()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.client = SlurmClient(
            runner=self.runner,
            user="ijg2603",
            state_dir=Path(self.temp_dir.name),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_overview_parses_nodes_and_jobs(self):
        overview = self.client.overview()
        self.assertEqual(overview["summary"]["nodes"], 3)
        self.assertEqual(overview["summary"]["total_gpus"], 19)
        self.assertEqual(overview["summary"]["running"], 1)
        self.assertTrue(overview["jobs"][0]["portal_managed"])
        self.assertEqual(overview["nodes"][1]["allocated_gpus"], 2)
        self.assertEqual(overview["nodes"][1]["free_gpus"], 6)
        self.assertEqual(overview["nodes"][1]["free_memory_mb"], 433250)
        self.assertEqual(overview["partitions"][1]["gpu_types"], ["a6000"])

    def test_submit_uses_fixed_wrap_and_structured_arguments(self):
        result = self.client.submit_allocation(
            {
                "node_name": "n003",
                "gpu_count": 1,
                "cpus": 8,
                "memory_gb": 64,
                "hours": 4,
            }
        )
        self.assertEqual(result["job_id"], "7001")
        args = self.runner.calls[-1][0]
        self.assertIn("--gres=gpu:a6000:1", args)
        self.assertIn("--partition=g2", args)
        self.assertIn("--nodelist=n003", args)
        self.assertIn("--wrap=sleep infinity", args)
        self.assertFalse(any(";" in arg for arg in args))

    def test_submit_allows_no_time_limit(self):
        self.client.submit_allocation(
            {
                "node_name": "n003",
                "gpu_count": 1,
                "cpus": 8,
                "memory_gb": 64,
                "hours": 0,
            }
        )
        args = self.runner.calls[-1][0]
        self.assertIn("--time=0", args)

    def test_submit_rejects_unknown_node(self):
        with self.assertRaises(PortalError):
            self.client.submit_allocation(
                {
                    "node_name": "n999",
                    "gpu_count": 1,
                    "cpus": 8,
                    "memory_gb": 64,
                    "hours": 4,
                }
            )

    def test_submit_rejects_request_above_live_availability(self):
        with self.assertRaisesRegex(PortalError, "6 GPUs available"):
            self.client.submit_allocation(
                {
                    "node_name": "n003",
                    "gpu_count": 7,
                    "cpus": 8,
                    "memory_gb": 64,
                    "hours": 4,
                }
            )
        self.assertFalse(
            any(Path(args[0]).name == "sbatch" for args, _ in self.runner.calls)
        )

    def test_submit_allows_explicit_waiting_request_above_free(self):
        result = self.client.submit_allocation(
            {
                "node_name": "n003",
                "gpu_count": 8,
                "cpus": 128,
                "memory_gb": 950,
                "hours": 4,
                "wait_for_resources": True,
            }
        )
        self.assertEqual(result["job_id"], "7001")
        args = self.runner.calls[-1][0]
        self.assertIn("--gres=gpu:a6000:8", args)
        self.assertIn("--cpus-per-task=128", args)
        self.assertIn("--mem=950G", args)

    def test_cancel_only_managed_job(self):
        result = self.client.cancel_allocation("7001")
        self.assertTrue(result["cancelled"])
        self.assertEqual(Path(self.runner.calls[-1][0][0]).name, "scancel")

    def test_cancel_rejects_non_numeric_id(self):
        with self.assertRaises(PortalError):
            self.client.cancel_allocation("7001; rm")

    def test_node_detail_maps_gpu_indices_to_job_owner(self):
        detail = self.client.node_detail("n003")
        self.assertEqual(detail["summary"]["allocated_gpus"], 2)
        self.assertEqual(detail["summary"]["free_gpus"], 6)
        self.assertEqual(detail["node"]["free_memory_mb"], 433250)
        self.assertEqual(detail["request_limits"]["max_gpus"], 6)
        self.assertEqual(detail["request_limits"]["max_cpus"], 112)
        self.assertEqual(detail["request_limits"]["max_memory_gb"], 423)
        self.assertEqual(detail["wait_limits"]["max_gpus"], 8)
        self.assertEqual(detail["wait_limits"]["max_cpus"], 128)
        self.assertEqual(detail["wait_limits"]["max_memory_gb"], 950)
        self.assertFalse(detail["gpu_slots"][3]["allocated"])
        self.assertEqual(detail["gpu_slots"][4]["job"]["user"], "ijg2603")
        self.assertEqual(detail["gpu_slots"][5]["job"]["id"], "7001")

    def test_node_detail_rejects_path_like_name(self):
        with self.assertRaises(PortalError):
            self.client.node_detail("../../etc")


if __name__ == "__main__":
    unittest.main()
