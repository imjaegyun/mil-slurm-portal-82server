from __future__ import annotations

import getpass
import os
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable


SLURM_BIN = Path(os.environ.get("SLURM_BIN", "/TGM/SLURM/bin"))
SERVER_NAME = os.environ.get("PORTAL_SERVER_NAME", "82server")
CLUSTER_NAME = os.environ.get("PORTAL_CLUSTER_NAME", "tgmv2")
ALLOWED_PARTITIONS = {
    item.strip()
    for item in os.environ.get("PORTAL_ALLOWED_PARTITIONS", "").split(",")
    if item.strip()
}
PORTAL_JOB_PREFIX = "ui-session-"
PORTAL_COMMENT = "slurm-portal"
ACCEPTING_NODE_STATES = {"idle", "mix", "alloc"}


class PortalError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _default_runner(args: list[str], timeout: int) -> str:
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
        )
    except FileNotFoundError as exc:
        raise PortalError(f"Slurm command not found: {args[0]}", 503) from exc
    except subprocess.TimeoutExpired as exc:
        raise PortalError("Slurm controller response timed out.", 504) from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if len(detail) > 300:
            detail = detail[:300] + "…"
        raise PortalError(detail or "Slurm command failed.", 502)
    return result.stdout


def _int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise PortalError(f"{label} must be a number.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PortalError(f"{label} must be a number.") from exc
    return parsed


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _available_resource_limits(node: dict) -> dict:
    total_gpus = _nonnegative_int(node.get("gpus"))
    total_cpus = _nonnegative_int(node.get("cpus"))
    total_memory_mb = _nonnegative_int(node.get("memory_mb"))
    free_memory_mb = min(
        total_memory_mb,
        _nonnegative_int(node.get("free_memory_mb")),
    )
    return {
        "max_gpus": min(total_gpus, _nonnegative_int(node.get("free_gpus"))),
        "max_cpus": min(total_cpus, _nonnegative_int(node.get("cpu_idle"))),
        "max_memory_gb": free_memory_mb // 1024,
    }


def _waiting_resource_limits(node: dict) -> dict:
    return {
        "max_gpus": _nonnegative_int(node.get("gpus")),
        "max_cpus": _nonnegative_int(node.get("cpus")),
        "max_memory_gb": int(
            (_nonnegative_int(node.get("memory_mb")) / 1024) * 0.95
        ),
    }


def validate_allocation(payload: dict, node: dict) -> dict:
    partition = node["partition"]
    gpu_type = node.get("gpu_type") or _gpu_type(node.get("gres", ""))
    if not gpu_type or node.get("gpus", 0) < 1:
        raise PortalError("The selected node does not provide a supported GPU resource.")

    gpu_count = _int(payload.get("gpu_count"), "GPU count")
    cpus = _int(payload.get("cpus"), "CPU count")
    memory_gb = _int(payload.get("memory_gb"), "Memory")
    hours = _int(payload.get("hours"), "Time limit")
    wait_for_resources = payload.get("wait_for_resources") is True
    limits = (
        _waiting_resource_limits(node)
        if wait_for_resources
        else _available_resource_limits(node)
    )

    if wait_for_resources:
        if not 1 <= gpu_count <= limits["max_gpus"]:
            raise PortalError(
                f"{node['name']} supports at most {limits['max_gpus']} GPUs "
                "for a waiting request."
            )
        if not 1 <= cpus <= limits["max_cpus"]:
            raise PortalError(
                f"{node['name']} supports at most {limits['max_cpus']} CPU "
                "cores for a waiting request."
            )
        if not 1 <= memory_gb <= limits["max_memory_gb"]:
            raise PortalError(
                f"{node['name']} supports at most {limits['max_memory_gb']} GB "
                "memory for a waiting request."
            )
    else:
        if limits["max_gpus"] < 1:
            raise PortalError(
                f"{node['name']} has no GPU available right now. Use waiting "
                "request mode or select another node."
            )
        if not 1 <= gpu_count <= limits["max_gpus"]:
            raise PortalError(
                f"{node['name']} currently has {limits['max_gpus']} GPUs "
                f"available. Request at most {limits['max_gpus']} or use "
                "waiting request mode."
            )
        if limits["max_cpus"] < 1:
            raise PortalError(
                f"{node['name']} has no CPU core available right now. Use "
                "waiting request mode or select another node."
            )
        if not 1 <= cpus <= limits["max_cpus"]:
            raise PortalError(
                f"{node['name']} currently has {limits['max_cpus']} CPU cores "
                f"available. Request at most {limits['max_cpus']} or use "
                "waiting request mode."
            )
        if limits["max_memory_gb"] < 1:
            raise PortalError(
                f"{node['name']} has less than 1 GB memory available right now. "
                "Use waiting request mode or select another node."
            )
        if not 1 <= memory_gb <= limits["max_memory_gb"]:
            raise PortalError(
                f"{node['name']} currently has {limits['max_memory_gb']} GB "
                f"memory available. Request at most {limits['max_memory_gb']} "
                "GB or use waiting request mode."
            )
    if hours < 0:
        raise PortalError("Time limit must be zero (no limit) or a positive number.")

    return {
        "node_name": node["name"],
        "partition": partition,
        "gpu_type": gpu_type,
        "gpu_count": gpu_count,
        "cpus": cpus,
        "memory_gb": memory_gb,
        "hours": hours,
        "wait_for_resources": wait_for_resources,
    }


def _gpu_count(gres: str) -> int:
    match = re.search(r"gpu:[^:,()]+:(\d+)", gres or "")
    return int(match.group(1)) if match else 0


def _gpu_type(gres: str) -> str:
    match = re.search(r"gpu:([^:,()]+):\d+", gres or "")
    return match.group(1).lower() if match else ""


def _metric_int(value: str) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _split_lines(output: str, expected: int) -> list[list[str]]:
    parsed = []
    for raw in output.splitlines():
        if not raw.strip():
            continue
        parts = raw.rstrip().split("|", expected - 1)
        if len(parts) < expected:
            parts.extend([""] * (expected - len(parts)))
        parsed.append(parts)
    return parsed


def _expand_gpu_indices(value: str) -> list[int]:
    match = re.search(r"\(IDX:([0-9,\-]+)\)", value or "")
    if not match:
        return []
    indices = []
    for part in match.group(1).split(","):
        if "-" in part:
            start, end = part.split("-", 1)
            indices.extend(range(int(start), int(end) + 1))
        elif part:
            indices.append(int(part))
    return sorted(set(indices))


def _job_field(line: str, name: str) -> str:
    match = re.search(rf"(?:^|\s){re.escape(name)}=(\S+)", line)
    return match.group(1) if match else ""


def _parse_detailed_jobs(output: str) -> list[dict]:
    jobs = []
    for line in output.splitlines():
        if not line.strip():
            continue
        job_id = _job_field(line, "JobId")
        if not job_id:
            continue
        user_id = _job_field(line, "UserId")
        allocations = []
        pattern = re.compile(
            r"(?:^|\s)Nodes=(\S+)\s+CPU_IDs=(\S+)\s+Mem=(\S+)\s+GRES=(\S+)"
        )
        for match in pattern.finditer(line):
            gres = match.group(4)
            allocations.append(
                {
                    "node": match.group(1),
                    "cpu_ids": match.group(2),
                    "gres": gres,
                    "gpu_indices": _expand_gpu_indices(gres),
                }
            )
        jobs.append(
            {
                "id": job_id,
                "name": _job_field(line, "JobName"),
                "user": user_id.split("(", 1)[0],
                "state": _job_field(line, "JobState"),
                "runtime": _job_field(line, "RunTime"),
                "time_limit": _job_field(line, "TimeLimit"),
                "partition": _job_field(line, "Partition"),
                "nodes": _job_field(line, "NodeList"),
                "allocations": allocations,
            }
        )
    return jobs


class SlurmClient:
    def __init__(
        self,
        runner: Callable[[list[str], int], str] = _default_runner,
        user: str | None = None,
        state_dir: Path | None = None,
    ):
        self.runner = runner
        self.user = user or getpass.getuser()
        self.state_dir = state_dir or Path.home() / "slurm-portal" / ".state"
        self.log_dir = self.state_dir / "job-logs"
        self._node_detail_cache: dict[str, tuple[float, list[dict]]] = {}
        self._detail_lock = threading.Lock()

    def _command(self, name: str) -> str:
        return str(SLURM_BIN / name)

    def _gpu_usage(self) -> dict[str, dict]:
        output = self.runner(
            [
                self._command("sinfo"),
                "--Node",
                "--noheader",
                "--Format=NodeList:64,GresUsed:128",
            ],
            8,
        )
        usage = {}
        for raw in output.splitlines():
            parts = raw.strip().split(None, 1)
            if not parts:
                continue
            node_name = parts[0]
            used_gres = parts[1].strip() if len(parts) > 1 else ""
            indices = _expand_gpu_indices(used_gres)
            usage[node_name] = {
                "gres_used": used_gres,
                "allocated_gpus": _gpu_count(used_gres),
                "used_gpu_indices": indices,
            }
        return usage

    def list_nodes(self) -> list[dict]:
        output = self.runner(
            [
                self._command("sinfo"),
                "--Node",
                "--noheader",
                "--format=%N|%P|%t|%c|%m|%e|%G|%C",
            ],
            8,
        )
        usage = self._gpu_usage()
        nodes = []
        for (
            name,
            partition,
            state,
            cpus,
            memory,
            free_memory,
            gres,
            cpu_state,
        ) in _split_lines(
            output, 8
        ):
            cpu_parts = cpu_state.split("/")
            normalized_partition = partition.rstrip("*")
            normalized_state = state.lower().rstrip("*~#")
            gpu_count = _gpu_count(gres)
            if gpu_count < 1:
                continue
            if ALLOWED_PARTITIONS and normalized_partition not in ALLOWED_PARTITIONS:
                continue
            node_usage = usage.get(name, {})
            allocated_gpus = min(
                gpu_count,
                int(node_usage.get("allocated_gpus", 0)),
            )
            nodes.append(
                {
                    "name": name,
                    "partition": normalized_partition,
                    "default_partition": partition.endswith("*"),
                    "state": normalized_state,
                    "cpus": int(cpus or 0),
                    "memory_mb": int(memory or 0),
                    "free_memory_mb": _metric_int(free_memory),
                    "gres": gres,
                    "gres_used": node_usage.get("gres_used", ""),
                    "gpu_type": _gpu_type(gres),
                    "gpus": gpu_count,
                    "allocated_gpus": allocated_gpus,
                    "free_gpus": max(0, gpu_count - allocated_gpus),
                    "used_gpu_indices": node_usage.get("used_gpu_indices", []),
                    "cpu_allocated": int(cpu_parts[0]) if len(cpu_parts) == 4 else 0,
                    "cpu_idle": int(cpu_parts[1]) if len(cpu_parts) == 4 else 0,
                }
            )
        nodes.sort(key=lambda item: (item["partition"], item["name"]))
        return nodes

    def list_jobs(self) -> list[dict]:
        output = self.runner(
            [
                self._command("squeue"),
                "--noheader",
                f"--user={self.user}",
                "--format=%i|%T|%P|%N|%j|%M|%L|%R|%b|%C|%m|%u",
            ],
            8,
        )
        jobs = []
        for (
            job_id,
            state,
            partition,
            nodes,
            name,
            elapsed,
            remaining,
            reason,
            gres,
            cpus,
            memory,
            owner,
        ) in _split_lines(output, 12):
            jobs.append(
                {
                    "id": job_id,
                    "state": state,
                    "partition": partition,
                    "nodes": nodes,
                    "name": name,
                    "elapsed": elapsed,
                    "remaining": remaining,
                    "reason": reason,
                    "gres": gres,
                    "cpus": int(cpus or 0),
                    "memory": memory,
                    "owner": owner,
                    "portal_managed": name.startswith(PORTAL_JOB_PREFIX),
                }
            )
        return jobs

    def overview(self) -> dict:
        nodes = self.list_nodes()
        jobs = self.list_jobs()
        partitions = {}
        for node in nodes:
            item = partitions.setdefault(
                node["partition"],
                {
                    "name": node["partition"],
                    "nodes": 0,
                    "gpus": 0,
                    "gpu_types": set(),
                },
            )
            item["nodes"] += 1
            item["gpus"] += node["gpus"]
            item["gpu_types"].add(node["gpu_type"])
        running = sum(1 for job in jobs if job["state"] == "RUNNING")
        pending = sum(1 for job in jobs if job["state"] == "PENDING")
        return {
            "cluster": CLUSTER_NAME,
            "server": SERVER_NAME,
            "user": self.user,
            "nodes": nodes,
            "jobs": jobs,
            "partitions": [
                {
                    **item,
                    "gpu_types": sorted(item["gpu_types"]),
                }
                for item in partitions.values()
            ],
            "summary": {
                "nodes": len(nodes),
                "total_gpus": sum(node["gpus"] for node in nodes),
                "jobs": len(jobs),
                "running": running,
                "pending": pending,
            },
        }

    def _detailed_jobs_for_node(self, node_name: str) -> list[dict]:
        with self._detail_lock:
            now = time.monotonic()
            cached = self._node_detail_cache.get(node_name)
            if cached and now - cached[0] < 5:
                return cached[1]
            job_ids_output = self.runner(
                [
                    self._command("squeue"),
                    "--noheader",
                    f"--nodes={node_name}",
                    "--states=RUNNING",
                    "--format=%i",
                ],
                8,
            )
            job_ids = [
                item.strip()
                for item in job_ids_output.splitlines()
                if item.strip().isdigit()
            ]
            jobs = []
            for job_id in job_ids:
                output = self.runner(
                    [
                        self._command("scontrol"),
                        "show",
                        "job",
                        job_id,
                        "--details",
                        "--oneliner",
                    ],
                    8,
                )
                for job in _parse_detailed_jobs(output):
                    if job["allocations"]:
                        for allocation in job["allocations"]:
                            allocation["node"] = node_name
                    jobs.append(job)
            self._node_detail_cache[node_name] = (now, jobs)
            return jobs

    def node_detail(self, node_name: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", node_name):
            raise PortalError("Invalid node name.")

        node = next(
            (item for item in self.list_nodes() if item["name"] == node_name),
            None,
        )
        if not node:
            raise PortalError("Node not found.", 404)

        node_jobs = []
        gpu_assignments: dict[int, dict] = {}
        for job in self._detailed_jobs_for_node(node_name):
            matching = [
                allocation
                for allocation in job["allocations"]
                if allocation["node"] == node_name
            ]
            if not matching:
                continue
            indices = sorted(
                {
                    index
                    for allocation in matching
                    for index in allocation["gpu_indices"]
                }
            )
            if not indices:
                continue
            job_summary = {
                "id": job["id"],
                "name": job["name"],
                "user": job["user"],
                "state": job["state"],
                "runtime": job["runtime"],
                "time_limit": job["time_limit"],
                "gpu_indices": indices,
                "gpu_count": len(indices),
                "is_current_user": job["user"] == self.user,
            }
            node_jobs.append(job_summary)
            for index in indices:
                gpu_assignments[index] = job_summary

        gpu_type = node["gpu_type"]
        allocated_gpu_indices = set(node.get("used_gpu_indices", []))
        allocated_gpu_indices.update(gpu_assignments)
        gpu_slots = []
        for index in range(node["gpus"]):
            job = gpu_assignments.get(index)
            gpu_slots.append(
                {
                    "index": index,
                    "type": gpu_type,
                    "allocated": index in allocated_gpu_indices,
                    "job": job,
                }
            )

        return {
            "node": node,
            "gpu_type": gpu_type,
            "request_limits": _available_resource_limits(node),
            "wait_limits": _waiting_resource_limits(node),
            "gpu_slots": gpu_slots,
            "jobs": sorted(node_jobs, key=lambda item: int(item["id"])),
            "summary": {
                "total_gpus": node["gpus"],
                "allocated_gpus": len(allocated_gpu_indices),
                "free_gpus": max(0, node["gpus"] - len(allocated_gpu_indices)),
                "job_count": len(node_jobs),
            },
        }

    def submit_allocation(self, payload: dict) -> dict:
        node_name = str(payload.get("node_name", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", node_name):
            raise PortalError("Select a valid node.")
        node = next(
            (item for item in self.list_nodes() if item["name"] == node_name),
            None,
        )
        if not node:
            raise PortalError("The selected node no longer exists.", 404)
        if node["state"] not in ACCEPTING_NODE_STATES:
            raise PortalError(f"{node_name} is not accepting new Jobs.")

        request = validate_allocation(payload, node)
        time_limit = "0" if request["hours"] == 0 else f"{request['hours']}:00:00"
        session_id = secrets.token_hex(4)
        job_name = f"{PORTAL_JOB_PREFIX}{session_id}"
        self.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        args = [
            self._command("sbatch"),
            "--parsable",
            f"--job-name={job_name}",
            f"--comment={PORTAL_COMMENT}",
            f"--partition={request['partition']}",
            f"--nodelist={request['node_name']}",
            "--nodes=1",
            "--ntasks=1",
            f"--cpus-per-task={request['cpus']}",
            f"--mem={request['memory_gb']}G",
            f"--gres=gpu:{request['gpu_type']}:{request['gpu_count']}",
            f"--time={time_limit}",
            f"--output={self.log_dir}/session-%j.out",
            f"--error={self.log_dir}/session-%j.err",
            "--wrap=sleep infinity",
        ]
        output = self.runner(args, 12).strip()
        job_id = output.split(";", 1)[0].strip()
        if not job_id.isdigit():
            raise PortalError("Slurm returned an invalid Job ID.", 502)
        self._node_detail_cache.clear()
        return {
            "job_id": job_id,
            "job_name": job_name,
            "request": request,
        }

    def cancel_allocation(self, job_id: str) -> dict:
        if not re.fullmatch(r"\d+", str(job_id)):
            raise PortalError("Invalid Job ID.")

        output = self.runner(
            [
                self._command("squeue"),
                f"--jobs={job_id}",
                "--noheader",
                "--format=%i|%u|%j|%T",
            ],
            8,
        )
        rows = _split_lines(output, 4)
        if not rows:
            raise PortalError("The Job no longer exists.", 404)
        _, owner, name, state = rows[0]
        if owner != self.user:
            raise PortalError("You can only cancel your own Job.", 403)
        if not name.startswith(PORTAL_JOB_PREFIX):
            raise PortalError(
                "For safety, this test portal only cancels Jobs it created.", 403
            )

        self.runner([self._command("scancel"), str(job_id)], 8)
        self._node_detail_cache.clear()
        return {"job_id": str(job_id), "previous_state": state, "cancelled": True}
