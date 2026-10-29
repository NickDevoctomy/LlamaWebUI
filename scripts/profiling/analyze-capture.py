"""Summarise an Nsight Systems report of the llama.cpp router as Markdown.

    .venv\\Scripts\\python.exe scripts\\profiling\\analyze-capture.py <report.nsys-rep> [--out summary.md]
        [--symbols data\\runtimes\\<runtime>]

Exports the report to SQLite (next to the summary, not next to the report), then reports:
GPU busy time and idle gaps, per-second utilisation, the steady-state decode step (graph
replay, the gap after it and the host calls in it), the top kernels, CUDA API time on the
CPU, copies by direction and size, and the hottest CPU functions when the capture has
CPU samples.
"""

from __future__ import annotations

import argparse
import collections
import os
import sqlite3
import statistics
import subprocess
import sys
from pathlib import Path

NSYS_ROOT = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "NVIDIA Corporation"
COPY_KINDS = {1: "H2D", 2: "D2H", 8: "D2D", 10: "P2P"}


def find_nsys() -> Path:
    installs = sorted(NSYS_ROOT.glob("Nsight Systems */target-windows-x64/nsys.exe"))
    if not installs:
        sys.exit("Nsight Systems not found")
    return installs[-1]


def resolve_symbols(report: Path, symbols: Path, out_dir: Path) -> Path:
    """A copy of the report with CPU sample addresses named from the PDBs in ``symbols``.

    nsys does not resolve CPU samples on Windows, so without this they are bare addresses.
    """
    resolved = out_dir / (report.stem + "-resolved.nsys-rep")
    if resolved.exists() and resolved.stat().st_mtime >= report.stat().st_mtime:
        return resolved
    tool = find_nsys().parent.parent / "host-windows-x64" / "ResolveSymbols.exe"
    subprocess.run(
        [str(tool), "--resolve-all", "--directories", str(symbols), "--report", str(report), "--output", str(resolved)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return resolved


def export(report: Path, out_dir: Path) -> Path:
    database = out_dir / (report.stem + ".sqlite")
    if database.exists() and database.stat().st_mtime >= report.stat().st_mtime:
        return database
    subprocess.run(
        [str(find_nsys()), "export", "--type", "sqlite", "--force-overwrite", "true",
         "-o", str(database), str(report)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return database


def ms(ns: float) -> str:
    return f"{ns / 1e6:,.1f} ms"


def us(ns: float) -> str:
    return f"{ns / 1e3:,.0f} µs"


def pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "-"


def table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def short(name: str, width: int = 90) -> str:
    name = name.replace("|", "\\|")
    return name if len(name) <= width else name[: width - 1] + "…"


class Report:
    def __init__(self, database: Path) -> None:
        self.db = sqlite3.connect(database)
        self.tables = {row[0] for row in self.db.execute("select name from sqlite_master where type='table'")}
        self.strings = dict(self.db.execute("select id, value from StringIds")) if "StringIds" in self.tables else {}

    def rows(self, sql: str, *args: object) -> list[tuple]:
        return self.db.execute(sql, args).fetchall()

    def has(self, table: str) -> bool:
        return table in self.tables


def gpu_intervals(report: Report) -> list[tuple[int, int, str]]:
    intervals: list[tuple[int, int, str]] = []
    for table, kind in (("CUPTI_ACTIVITY_KIND_KERNEL", "kernel"), ("CUPTI_ACTIVITY_KIND_GRAPH_TRACE", "graph"),
                        ("CUPTI_ACTIVITY_KIND_MEMCPY", "copy"), ("CUPTI_ACTIVITY_KIND_MEMSET", "memset")):
        if report.has(table):
            intervals += [(start, end, kind) for start, end in report.rows(f"select start, end from {table}")]
    return sorted(intervals)


def overview(report: Report, intervals: list[tuple[int, int, str]]) -> tuple[str, int, int]:
    first, last = intervals[0][0], max(end for _, end, _ in intervals)
    busy, gaps = 0, []
    run_start, run_end = intervals[0][0], intervals[0][1]
    for start, end, _ in intervals[1:]:
        if start > run_end:
            busy += run_end - run_start
            gaps.append(start - run_end)
            run_start, run_end = start, end
        else:
            run_end = max(run_end, end)
    busy += run_end - run_start
    span = last - first

    by_kind = collections.Counter()
    for start, end, kind in intervals:
        by_kind[kind] += end - start

    seconds = int(span // 1e9) + 1
    per_second = [0] * seconds
    for start, end, _ in intervals:
        at = start
        while at < end:
            index = int((at - first) // 1e9)
            step = min(end, first + (index + 1) * 1e9)
            per_second[index] += step - at
            at = step

    bands = [(0, 5e3), (5e3, 20e3), (20e3, 1e5), (1e5, 1e6), (1e6, 1e8), (1e8, float("inf"))]
    gap_rows = []
    for low, high in bands:
        inside = [gap for gap in gaps if low <= gap < high]
        label = f"{us(low)} – {us(high)}" if high != float("inf") else f"≥ {us(low)}"
        gap_rows.append([label, len(inside), ms(sum(inside))])

    text = [
        "## Overview",
        f"- GPU activity span: **{span / 1e9:.1f} s**, GPU busy **{busy / 1e9:.1f} s ({pct(busy, span)})**, idle {ms(span - busy)}",
        "- Time by GPU activity: " + ", ".join(f"{kind} {ms(total)}" for kind, total in by_kind.most_common()),
        "- GPU busy per second (%, overlapping work can exceed 100): "
        + " ".join(str(min(999, round(100 * value / 1e9))) for value in per_second),
        "",
        "### Idle gaps between GPU work",
        table(["Gap length", "Count", "Total"], gap_rows),
        "",
        "Many short gaps during generation are host-side overhead between steps; gaps of 100 ms or more are",
        "usually the router waiting for the next request.",
    ]
    return "\n".join(text), first, last


def decode_steps(report: Report) -> str:
    if not report.has("CUPTI_ACTIVITY_KIND_GRAPH_TRACE"):
        return ""
    graphs = report.rows("select start, end from CUPTI_ACTIVITY_KIND_GRAPH_TRACE order by start")
    if len(graphs) < 30:
        return ""
    # The middle third is steady generation, away from model load and the last request.
    middle = graphs[len(graphs) // 3: 2 * len(graphs) // 3]
    window_start, window_end = middle[0][0], middle[-1][1]
    window = window_end - window_start
    durations = sorted(end - start for start, end in middle)
    gaps = sorted(after[0] - before[1] for before, after in zip(middle, middle[1:]))

    lines = [
        "## Steady-state steps (CUDA graph replays, middle third of the capture)",
        f"- {len(middle)} replays in {window / 1e9:.1f} s = {len(middle) / (window / 1e9):.0f}/s; "
        f"replay median {us(statistics.median(durations))}, p90 {us(durations[int(.9 * len(durations))])}",
        f"- GPU gap after a replay until the next one: median {us(statistics.median(gaps))}, "
        f"p90 {us(gaps[int(.9 * len(gaps))])}; replays cover {pct(sum(durations), window)} of the window",
    ]

    if report.has("CUPTI_ACTIVITY_KIND_MEMCPY"):
        copies = report.rows(
            "select copyKind, bytes, end - start from CUPTI_ACTIVITY_KIND_MEMCPY where start between ? and ?",
            window_start, window_end,
        )
        rows = []
        by_kind: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
        for kind, size, duration in copies:
            by_kind[COPY_KINDS.get(kind, str(kind))].append((size, duration))
        for kind, items in by_kind.items():
            sizes = collections.Counter(size for size, _ in items).most_common(4)
            rows.append([kind, len(items), f"{len(items) / len(middle):.1f}",
                         ", ".join(f"{size:,} B ×{count}" for size, count in sizes), ms(sum(d for _, d in items))])
        lines += ["", "Copies in the window:", table(["Direction", "Count", "Per replay", "Common sizes", "GPU time"], rows)]

    if report.has("CUPTI_ACTIVITY_KIND_RUNTIME"):
        calls = report.rows(
            "select nameId, end - start from CUPTI_ACTIVITY_KIND_RUNTIME where start between ? and ?",
            window_start, window_end,
        )
        totals: dict[str, list[int]] = collections.defaultdict(list)
        for name_id, duration in calls:
            totals[report.strings.get(name_id, str(name_id)).split("_v")[0]].append(duration)
        rows = [[name, len(items), f"{len(items) / len(middle):.1f}", ms(sum(items)), us(statistics.median(items))]
                for name, items in sorted(totals.items(), key=lambda item: -sum(item[1]))[:10]]
        lines += ["", "CUDA API calls on the CPU in the window:",
                  table(["Call", "Count", "Per replay", "Total", "Median"], rows)]

    if report.has("CUPTI_ACTIVITY_KIND_KERNEL"):
        loose = report.rows(
            "select count(*), coalesce(sum(end - start), 0) from CUPTI_ACTIVITY_KIND_KERNEL "
            "where start between ? and ? and (graphId is null or graphId = 0)",
            window_start, window_end,
        )[0]
        lines.append(f"\n- Kernels launched outside graphs in the window: {loose[0]} "
                     f"({loose[0] / len(middle):.1f} per replay), GPU time {ms(loose[1])}")
    return "\n".join(lines)


def kernels(report: Report, limit: int) -> str:
    if not report.has("CUPTI_ACTIVITY_KIND_KERNEL"):
        return ""
    rows = report.rows(
        "select shortName, demangledName, count(*), sum(end - start), "
        "sum(case when graphId is not null and graphId != 0 then 1 else 0 end) "
        "from CUPTI_ACTIVITY_KIND_KERNEL group by demangledName order by sum(end - start) desc"
    )
    total = sum(row[3] for row in rows)
    in_graphs = sum(row[4] for row in rows)
    note = ("Kernels inside CUDA graphs are included." if in_graphs
            else "No kernels inside CUDA graphs were recorded: graph replays are opaque here. "
                 "Turn on 'Kernels inside CUDA graphs' in the Debug tab to see them.")
    body = [[pct(duration, total), ms(duration), count, us(duration / count), short(report.strings.get(name, str(name)))]
            for _, name, count, duration, _ in rows[:limit]]
    return "\n".join([f"## Top kernels by GPU time ({len(rows)} distinct, {ms(total)} total)", note, "",
                      table(["Share", "Total", "Count", "Avg", "Kernel"], body)])


def api_summary(report: Report, limit: int) -> str:
    if not report.has("CUPTI_ACTIVITY_KIND_RUNTIME"):
        return ""
    rows = report.rows(
        "select nameId, count(*), sum(end - start), max(end - start) from CUPTI_ACTIVITY_KIND_RUNTIME "
        "group by nameId order by sum(end - start) desc limit ?", limit,
    )
    body = [[report.strings.get(name, str(name)), count, ms(total), us(total / count), ms(longest)]
            for name, count, total, longest in rows]
    return "\n".join(["## CUDA API time on the CPU (whole capture)",
                      "cudaStreamSynchronize is the CPU waiting for the GPU.", "",
                      table(["Call", "Count", "Total", "Avg", "Longest"], body)])


def sampling_requested(report: Report) -> str | None:
    """The sampling rate the capture asked for, or None when it did not ask."""
    if not report.has("META_DATA_CAPTURE"):
        return None
    meta = report.rows("select name, value from META_DATA_CAPTURE")
    rate = next((value for name, value in meta if name == "RATE_HZ"), "0")
    asked = any(name == "CAPTURE_EVENT_TYPE" and value == "CpuCycles" for name, value in meta)
    return rate if asked and rate not in ("", "0") else None


def cpu_samples(report: Report, limit: int) -> str:
    total = report.rows("select count(*) from COMPOSITE_EVENTS")[0][0] if report.has("COMPOSITE_EVENTS") else 0
    if not total or not report.has("SAMPLING_CALLCHAINS"):
        rate = sampling_requested(report)
        if rate:
            return (f"## CPU samples\nCPU sampling was requested ({rate} Hz, with call stacks), but nsys "
                    "recorded no samples and logged no error. Run scripts\\profiling\\check-cpu-sampling.ps1 "
                    "from an administrator PowerShell to see whether sampling works on this machine.")
        return ("## CPU samples\nNot requested. Start the backend as administrator and leave "
                "'CPU call stacks' on to record llama.cpp's CPU call stacks.")

    # Windows samples the whole machine; keep llama-server's processes (the router and the
    # model processes it starts). A global thread id carries its process id from bit 24 up.
    processes = report.rows("select globalPid, pid from PROCESSES where name like 'llama-server%'") \
        if report.has("PROCESSES") else []
    scope = "llama-server processes " + ", ".join(str(pid) for _, pid in processes) if processes else "all processes"
    db = report.db
    db.execute("drop table if exists temp.samples")
    if processes:
        keys = ",".join(str(global_pid >> 24) for global_pid, _ in processes)
        db.execute(f"create temp table samples as select id, globalTid from COMPOSITE_EVENTS where (globalTid >> 24) in ({keys})")
    else:
        db.execute("create temp table samples as select id, globalTid from COMPOSITE_EVENTS")
    db.execute("create index temp.samples_id on samples(id)")
    db.execute("create index if not exists callchain_id on SAMPLING_CALLCHAINS(id)")
    kept = report.rows("select count(*) from samples")[0][0]
    if not kept:
        return f"## CPU samples\n{total:,} samples in the capture, none in {scope}."

    def frames(where: str) -> list[tuple]:
        return report.rows(
            "select c.symbol, c.module, count(distinct c.id) from SAMPLING_CALLCHAINS c "
            f"join samples s on s.id = c.id where {where} group by c.symbol, c.module order by 3 desc limit ?",
            limit,
        )

    def render(rows: list[tuple]) -> list[list[object]]:
        return [[pct(count, kept), count, short(report.strings.get(symbol, str(symbol)), 80),
                 Path(report.strings.get(module, str(module))).name] for symbol, module, count in rows]

    names: dict[int, str] = {}
    if report.has("ThreadNames"):
        columns = [row[1] for row in report.rows("pragma table_info(ThreadNames)")]
        if {"globalTid", "nameId"} <= set(columns):
            names = {tid: report.strings.get(name, "") for tid, name in report.rows("select globalTid, nameId from ThreadNames")}
    threads = report.rows("select globalTid, count(*) from samples group by globalTid order by 2 desc limit 12")
    thread_rows = [[f"{(tid >> 24) & 0xFFFFFF}", f"{tid & 0xFFFFFF}", names.get(tid, ""), f"{count:,}", pct(count, kept)]
                   for tid, count in threads]
    llama_modules = ("c.module in (select id from StringIds where value like '%llama%' "
                     "or value like '%ggml%' or value like '%mtmd%')")
    return "\n".join([
        f"## CPU samples ({kept:,} in {scope}, of {total:,} machine-wide)",
        "", "### Samples by thread",
        table(["Process", "Thread", "Name", "Samples", "Share"], thread_rows), "",
        "### Hottest functions (self: the sample was in this function)",
        table(["Share", "Samples", "Function", "Module"], render(frames("c.stackDepth = 0"))), "",
        "### Hottest llama.cpp functions (inclusive: anywhere on the stack)",
        table(["Share", "Samples", "Function", "Module"], render(frames(llama_modules))),
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report", type=Path)
    parser.add_argument("--out", type=Path, help="write the summary here as well as printing it")
    parser.add_argument("--work-dir", type=Path, help="where the SQLite export goes (default: next to --out, else the temp folder)")
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--symbols", type=Path,
                        help="folder of PDBs (the runtime folder) to name CPU samples from; takes a few minutes")
    args = parser.parse_args()

    work_dir = args.work_dir or (args.out.parent if args.out else Path(os.environ.get("TEMP", ".")))
    work_dir.mkdir(parents=True, exist_ok=True)
    source = args.report.resolve()
    if args.symbols:
        source = resolve_symbols(source, args.symbols.resolve(), work_dir)
    report = Report(export(source, work_dir))
    intervals = gpu_intervals(report)
    if not intervals:
        sys.exit("The report has no GPU activity.")
    summary, _, _ = overview(report, intervals)
    sections = [f"# {args.report.name}", summary, decode_steps(report), kernels(report, args.top),
                api_summary(report, 12), cpu_samples(report, args.top)]
    text = "\n\n".join(section for section in sections if section) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
