# Profiling llama-server

How to find where llama.cpp spends its time when it runs under LlamaWebUI. Profiling looks at llama.cpp's own code, not at model settings. Reports for individual captures are kept in this folder.

## What you need

- **Nsight Systems** (the Windows host install). The app finds the newest version under `C:\Program Files\NVIDIA Corporation\` on its own.
- **A llama.cpp build with debug symbols**, so the profiler can name functions and source lines. The release zips have no symbols.
- **Administrator rights for CPU call stacks.** Windows only lets administrators sample CPU stacks, whichever profiler you use. Without them a capture still records the full GPU and CUDA timeline. To check that sampling works on a machine, run `scripts\profiling\check-cpu-sampling.ps1` from an administrator PowerShell.

## 1. Build a profiling runtime

Clone a release tag into the git-ignored `temp` folder, then build and deploy it:

```powershell
git clone --depth 1 --branch b11815 https://github.com/ggml-org/llama.cpp temp\llama.cpp-b11815-cuda
.\scripts\profiling\build-llama-runtime.ps1
```

`build-llama-runtime.ps1` sets up the Visual Studio C++ environment (with the CMake and Ninja that ship with VS) and builds `llama-server` and `llama-bench` with CUDA. It then deploys them to `data\runtimes\<tag>-cuda-debugsym`. The build:

- is **fully optimised** (`/O2 /Ob2`), with a `.pdb` symbols file for every exe and DLL. Benchmarks run at release speed: the symbols load only in a debugger or profiler;
- adds `-lineinfo` to the CUDA kernels, so Nsight Compute can show their source lines;
- takes its build number from the tag (a shallow clone would otherwise report `build 1`);
- targets one GPU architecture (`-CudaArchitectures`, default `89` for RTX 40xx) to keep the build short. A clean build takes about 4½ minutes.

The script copies the CUDA runtime DLLs in, so the folder is self-contained. It refuses to write over a folder it didn't create, or one with a `llama-server` running from it.

Register `data\runtimes\<tag>-cuda-debugsym\llama-server.exe` once under **Runtimes**. After a rebuild the path is the same, so re-probe it; there's no need to register it again.

## 2. Capture

Everything is under **Settings › Debug**:

| Setting | What it does |
|---|---|
| Profiling | The master switch. On: the router starts under `nsys profile --start-later` as a named session, which follows it into the llama-server process it starts for each model, and nothing is recorded until a capture starts. Off: the router runs normally and the rest of the tab is hidden (reports stay listed). Takes effect the next time the router starts. |
| Capture from start | Records from the moment the router is ready until it stops. Benchmarks start and stop the router, so each benchmark run gives one report. Turn it off to use **Start capture** / **Stop and save** around part of a run. |
| Kernels inside CUDA graphs | llama.cpp runs most of its generation work as CUDA graphs, which otherwise show as one block per graph. This records every kernel inside them (`--cuda-graph-trace=node`), but adds overhead: turn it on to see *which* kernels cost time, and off when timings matter. |
| CPU call stacks | Samples llama.cpp's threads with call stacks. Only works when the backend runs as administrator, and the tab shows whether it does. Like the switches above, it's fixed when the router starts. |
| CPU samples per second | 100–8,000, default 250. nsys's own default of 1,000 made the first CPU capture's report take 2½ minutes to save; 250 cuts the samples to a quarter and still gives plenty of detail over a whole benchmark. Fixed when the router starts. |
| nsys.exe path, Trace | Defaults: the newest install, and `cuda,nvtx,cublas`. |

Then run a benchmark on the debug-symbol runtime. The report appears under **Reports** on the same tab, saved in `data\debug\captures\`.

Before the router stops, the app writes the report with `nsys stop`. This happens for every stop: manual, benchmark, restart or app shutdown. If the router stopped first, nsys would exit without writing anything.

The app uses `nsys profile` sessions, not `nsys launch` with `start` / `stop`. On this machine `nsys launch` recorded no CPU samples at all, even elevated; `nsys profile` did. `scripts\profiling\check-cpu-sampling.ps1` tests every mode (see the [first report](2026-10-29-b11815-qwen3.8-27b-mtp.md#cpu-samples-are-missing)).

## 3. Read the report

Open it in the Nsight Systems GUI (`host-windows-x64\nsys-ui.exe`, File › Open). For a text summary, the kind the reports in this folder are based on, run:

```powershell
.\.venv\Scripts\python.exe scripts\profiling\analyze-capture.py data\debug\captures\<report>.nsys-rep --out temp\<report>.md --symbols data\runtimes\<runtime>
```

`--symbols` is only needed for captures with CPU call stacks. On Windows, nsys stores CPU samples as bare addresses. The script runs nsys's `ResolveSymbols.exe` against the runtime folder's PDBs first, which takes a few minutes for a full benchmark. The script keeps only llama-server's samples, because Windows samples the whole machine.

A capture with CPU call stacks is large. At 1,000 samples per second, a 2½-minute benchmark gave 5.3 M samples, a 346 MB report and a 4 GB SQLite export, and the router stop waited 2½ minutes while nsys wrote the report. While it writes, the top bar shows **Saving · m:ss** and the Debug tab shows the report being saved. Leave the stop to finish: restarting the backend at that point loses the report.

While profiled, the router runs in a console of its own, so it also writes its log to `data\debugouter.log`. The app follows that file, so the Server log, context and speed stay live.

The script exports the report to SQLite (in the folder of `--out`, never next to the report) and summarises:

- GPU busy time, idle gaps by length, and utilisation per second;
- the steady-state step: CUDA graph replay time, the GPU gap after each replay, and the copies and CUDA calls the CPU makes in between;
- the top kernels, CUDA API time on the CPU, and, when the capture has them, the hottest CPU functions.

## Reports

- [2026-10-29: b11815, Qwen3.8 27B with MTP](2026-10-29-b11815-qwen3.8-27b-mtp.md)
