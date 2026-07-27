from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable


Runner = Callable[[list[str], int], str]


class MilJobsError(RuntimeError):
    pass


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
        raise MilJobsError(f"Slurm command not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MilJobsError(f"Slurm command timed out: {Path(args[0]).name}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise MilJobsError(detail or f"{Path(args[0]).name} failed")
    return result.stdout


def _number(value: object) -> int:
    match = re.search(r"-?\d+", str(value or ""))
    return max(0, int(match.group(0))) if match else 0


def _natural_key(value: str) -> list[object]:
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", value)
    ]


def _gpu_resource(value: str) -> tuple[str, int]:
    match = re.search(
        r"(?:^|,)(?:gres/)?gpu(?::([^:,()]+))?:(\d+)",
        value or "",
    )
    if not match:
        return "", 0
    return (match.group(1) or "").lower(), int(match.group(2))


def _gpu_indices(value: str) -> list[int]:
    match = re.search(r"IDX:([0-9,-]+)", value or "")
    if not match:
        return []
    indices: list[int] = []
    for item in match.group(1).split(","):
        if "-" in item:
            start, end = item.split("-", 1)
            indices.extend(range(int(start), int(end) + 1))
        elif item:
            indices.append(int(item))
    return sorted(set(indices))


def _expand_nodelist(value: str) -> list[str]:
    value = (value or "").strip()
    if not value or value in {"(null)", "None", "N/A"}:
        return []
    if "[" not in value:
        return [item for item in value.split(",") if item]
    match = re.fullmatch(r"([A-Za-z._-]*)(?:\[([0-9,-]+)\])", value)
    if not match:
        return [value]
    prefix, body = match.groups()
    names: list[str] = []
    for item in body.split(","):
        if "-" in item:
            start_text, end_text = item.split("-", 1)
            width = max(len(start_text), len(end_text))
            for number in range(int(start_text), int(end_text) + 1):
                names.append(f"{prefix}{number:0{width}d}")
        else:
            names.append(f"{prefix}{item}")
    return names


def _rows(output: str, fields: int) -> list[list[str]]:
    rows = []
    for raw in output.splitlines():
        parts = [part.strip() for part in raw.split("|")]
        if len(parts) == fields:
            rows.append(parts)
    return rows


def collect_snapshot(
    *,
    runner: Runner = _default_runner,
    slurm_bin: Path | None = None,
    allowed_partitions: set[str] | None = None,
) -> dict:
    binary_root = slurm_bin or Path(os.environ.get("SLURM_BIN", "/TGM/SLURM/bin"))

    def command(name: str) -> str:
        return str(binary_root / name)

    node_output = runner(
        [
            command("sinfo"),
            "--Node",
            "--noheader",
            "--format=%N|%P|%t|%c|%m|%e|%G|%C",
        ],
        10,
    )
    usage_output = runner(
        [
            command("sinfo"),
            "--Node",
            "--noheader",
            "--Format=NodeList:64,GresUsed:128",
        ],
        10,
    )
    usage: dict[str, dict] = {}
    for raw in usage_output.splitlines():
        parts = raw.strip().split(None, 1)
        if not parts:
            continue
        gres_used = parts[1].strip() if len(parts) > 1 else ""
        _, used_count = _gpu_resource(gres_used)
        usage[parts[0]] = {
            "gres_used": gres_used,
            "allocated_gpus": used_count,
            "gpu_indices": _gpu_indices(gres_used),
        }

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
    ) in _rows(node_output, 8):
        normalized_partition = partition.rstrip("*")
        if allowed_partitions and normalized_partition not in allowed_partitions:
            continue
        gpu_type, gpu_total = _gpu_resource(gres)
        if gpu_total < 1:
            continue
        cpu_parts = cpu_state.split("/")
        node_usage = usage.get(name, {})
        gpu_used = min(gpu_total, int(node_usage.get("allocated_gpus", 0)))
        nodes.append(
            {
                "name": name,
                "partition": normalized_partition,
                "state": state.lower().rstrip("*~#"),
                "gpu_type": gpu_type,
                "gpu_total": gpu_total,
                "gpu_used": gpu_used,
                "gpu_indices": node_usage.get("gpu_indices", []),
                "cpu_total": _number(cpus),
                "cpu_used": _number(cpu_parts[0]) if len(cpu_parts) == 4 else 0,
                "memory_total_gb": _number(memory) // 1024,
                "memory_free_gb": _number(free_memory) // 1024,
            }
        )

    job_output = runner(
        [
            command("squeue"),
            "--noheader",
            "--states=RUNNING",
            "--format=%i|%u|%P|%N|%j|%b|%C|%m|%M|%L",
        ],
        10,
    )
    jobs_by_node: dict[str, list[dict]] = {}
    for (
        job_id,
        user,
        partition,
        nodelist,
        name,
        gres,
        cpus,
        memory,
        elapsed,
        remaining,
    ) in _rows(job_output, 10):
        gpu_type, gpu_count = _gpu_resource(gres)
        if gpu_count < 1:
            continue
        for node_name in _expand_nodelist(nodelist):
            jobs_by_node.setdefault(node_name, []).append(
                {
                    "id": job_id,
                    "user": user,
                    "partition": partition,
                    "name": name,
                    "gpu_type": gpu_type,
                    "gpu_count": gpu_count,
                    "cpus": _number(cpus),
                    "memory": memory,
                    "elapsed": elapsed,
                    "remaining": remaining,
                }
            )

    nodes.sort(key=lambda item: (_natural_key(item["partition"]), _natural_key(item["name"])))
    for jobs in jobs_by_node.values():
        jobs.sort(key=lambda item: (_natural_key(item["user"]), _number(item["id"])))
    return {
        "generated_at": datetime.now().astimezone(),
        "nodes": nodes,
        "jobs_by_node": jobs_by_node,
    }


class Palette:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def paint(self, code: str, value: object) -> str:
        text = str(value)
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, value: object) -> str:
        return self.paint("1", value)

    def blue(self, value: object) -> str:
        return self.paint("38;5;75", value)

    def green(self, value: object) -> str:
        return self.paint("38;5;78", value)

    def amber(self, value: object) -> str:
        return self.paint("38;5;215", value)

    def muted(self, value: object) -> str:
        return self.paint("38;5;245", value)


def _bar(used: int, total: int, width: int = 10) -> str:
    filled = 0 if total < 1 else min(width, round((used / total) * width))
    return "█" * filled + "░" * (width - filled)


def _clip(value: object, width: int) -> str:
    text = str(value)
    if width < 2 or len(text) <= width:
        return text
    return text[: width - 1] + "…"


def render_snapshot(
    snapshot: dict,
    *,
    server_name: str,
    cluster_name: str,
    include_all: bool = False,
    partition: str = "",
    user: str = "",
    color: bool = True,
    terminal_width: int | None = None,
) -> str:
    palette = Palette(color)
    jobs_by_node = snapshot["jobs_by_node"]
    visible = []
    for node in snapshot["nodes"]:
        if partition and node["partition"] != partition:
            continue
        jobs = jobs_by_node.get(node["name"], [])
        if user:
            jobs = [job for job in jobs if job["user"] == user]
        active = node["gpu_used"] > 0 or bool(jobs)
        if user and not jobs:
            continue
        if not include_all and not active:
            continue
        visible.append((node, jobs))

    width = terminal_width or shutil.get_terminal_size((110, 24)).columns
    width = max(78, min(150, width))
    all_jobs = [job for _, jobs in visible for job in jobs]
    users = {job["user"] for job in all_jobs}
    gpu_used = sum(node["gpu_used"] for node, _ in visible)
    gpu_total = sum(node["gpu_total"] for node, _ in visible)
    generated = snapshot["generated_at"].strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        palette.bold(
            f"Machine Intelligence Lab · {server_name} · {cluster_name}"
        ),
        (
            f"{palette.blue(f'{gpu_used}/{gpu_total} GPUs')}  "
            f"{len(visible)} nodes  {len(all_jobs)} jobs  {len(users)} users  "
            f"{palette.muted(generated)}"
        ),
        palette.muted("─" * width),
    ]

    if not visible:
        message = (
            f"{user} 사용자의 실행 중인 GPU Job이 없습니다."
            if user
            else "현재 실행 중인 GPU Job이 없습니다."
        )
        if not include_all:
            message += " 모든 GPU 노드는 mil-jobs --all 로 확인할 수 있습니다."
        lines.append(message)
        return "\n".join(lines)

    current_partition = ""
    for node, jobs in visible:
        if node["partition"] != current_partition:
            current_partition = node["partition"]
            partition_nodes = sum(
                1 for item, _ in visible if item["partition"] == current_partition
            )
            lines.extend(
                [
                    "",
                    palette.blue(
                        f"[{current_partition}] {partition_nodes} node"
                        f"{'s' if partition_nodes != 1 else ''}"
                    ),
                ]
            )
        state = node["state"].upper()
        state_label = (
            palette.green(f"{state:<6}")
            if state in {"IDLE", "MIX"}
            else palette.amber(f"{state:<6}")
        )
        gpu_label = (node["gpu_type"] or "gpu").upper()
        slots = (
            " idx " + ",".join(str(index) for index in node["gpu_indices"])
            if node["gpu_indices"]
            else ""
        )
        memory = (
            f"MEM free {node['memory_free_gb']}/{node['memory_total_gb']}G"
        )
        node_name_label = palette.bold(f"{node['name']:<8}")
        lines.append(
            f"{node_name_label} {state_label} "
            f"GPU {_bar(node['gpu_used'], node['gpu_total'])} "
            f"{node['gpu_used']}/{node['gpu_total']} {gpu_label}{slots}  "
            f"CPU {node['cpu_used']}/{node['cpu_total']}  {memory}"
        )
        if not jobs:
            lines.append(
                "  "
                + palette.muted(
                    "GPU allocation exists, but matching squeue details were not found."
                )
            )
            continue
        for job in jobs:
            job_gpu = (job["gpu_type"] or node["gpu_type"] or "gpu").upper()
            name_width = max(12, width - 72)
            job_id_label = palette.muted(f"#{job['id']:<9}")
            job_user_label = palette.bold(
                f"{_clip(job['user'], 14):<14}"
            )
            lines.append(
                "  "
                f"{job_id_label} "
                f"{job_user_label} "
                f"{job_gpu} ×{job['gpu_count']:<2}  "
                f"{job['elapsed']:>10} / {_clip(job['remaining'], 10):<10}  "
                f"{_clip(job['name'], name_width)}"
            )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mil-jobs",
        description="Show GPU nodes, users, and running Slurm Jobs.",
    )
    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="include idle GPU nodes",
    )
    parser.add_argument("-p", "--partition", default="", help="filter partition")
    parser.add_argument("-u", "--user", default="", help="filter user")
    parser.add_argument(
        "-w",
        "--watch",
        nargs="?",
        const=5.0,
        type=float,
        metavar="SECONDS",
        help="refresh continuously (default: 5 seconds)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="disable ANSI colors",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.watch is not None and args.watch < 1:
        raise SystemExit("--watch interval must be at least 1 second")
    allowed_partitions = {
        item.strip()
        for item in os.environ.get("PORTAL_ALLOWED_PARTITIONS", "").split(",")
        if item.strip()
    }
    server_name = os.environ.get("PORTAL_SERVER_NAME", "Slurm")
    cluster_name = os.environ.get("PORTAL_CLUSTER_NAME", "cluster")
    color = sys.stdout.isatty() and not args.no_color and "NO_COLOR" not in os.environ
    try:
        while True:
            snapshot = collect_snapshot(
                allowed_partitions=allowed_partitions,
            )
            output = render_snapshot(
                snapshot,
                server_name=server_name,
                cluster_name=cluster_name,
                include_all=args.all,
                partition=args.partition,
                user=args.user,
                color=color,
            )
            if args.watch is not None and sys.stdout.isatty():
                print("\033[2J\033[H", end="")
            print(output, flush=True)
            if args.watch is None:
                return 0
            if not sys.stdout.isatty():
                print()
            time.sleep(args.watch)
    except KeyboardInterrupt:
        return 0
    except MilJobsError as exc:
        print(f"mil-jobs: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
