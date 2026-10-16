# FSR 4.1.1 Perf variant (opt-in 3rd upscaler version)

Date: 2026-10-16. Status: implemented, validated on RX 6950 XT / AMD Windows
26.9.2, shipped in the provider build. Default index unchanged everywhere
except Enshrouded (quirk defaults to Perf, still overridable).

## What it is

A second selectable Vulkan provider version, ` 4.1.1 VK INT8 Perf` (index 1;
`[0]=4.1.1` keeps its meaning). Same 4.1.1 model, same 28-pass
layout and dispatch path (`is411family`), with the exact-set shader rewrites
from `references/fsr4-shader-performance-overrides@bf009e5` applied to its
SPIR-V. Scripts are only executed, never vendored; outputs are regenerated
from our own captured shaders (AMD-MIT payload posture kept, no prebuilt
binaries lifted).

## Rewrite set (exact only)

Applied per file, in upstream stacking order:

- `pass-01` (prepass): owned `tools/prepass_route_fma.py`, the portable
  FMul/FAdd form of the upstream route (each thread finishes its own output
  word: 12 quad exchanges instead of 32, tail spread over four threads).
  Same additions in the same grouping up to FAdd operand order; output-exact.
- Generic models (`pass-03/05/09/11/15/17/19/21/25` = model_1,2,4,5,7,8,9,10,12):
  `model_clamp.py | model_tail.py`, kept iff the text changed.
- `pass-23` (model_11): `model_clamp.py | model_tail.py` only. The upstream
  `zconst.py -> pass11_stores.py` prefix is deliberately skipped: on
  AMDVLK/RDNA2 the zconst-enabled full unroll spills registers (measured
  +70% on pass-23; stores only partly mitigates). Clamp+tail alone is neutral.
- `pass-27` (postpass): `model_tail.py | model_clamp.py | postpass_taps.py`
  -> `postpass_direct.py` -> re-disassemble -> `postpass_sharetaps.py`
  (assembled with `--target-env vulkan1.1` per upstream recipe; module stays
  SPIR-V 1.3).
- Excluded: borders, SPD, bins.

`spirv-as` compacts all IDs (e.g. bound 2992 to 2991) and stamps its own
generator word; this is normal and semantically neutral. Pipe transforms
through `cmd` redirection, never PowerShell string piping (collapses newlines
into one line and produces a bogus module).

## Validation (RX 6950 XT, native Windows)

- Every transformed file: preconditions fired, `spirv-as` clean,
  `spirv-val --target-env vulkan1.1/1.3` clean, interface decorations
  (DescriptorSet/Binding, entry points, modes) preserved.
- A/B pixels via provider + asset-root override, harness proven sound with a
  broken-shader control: 4 bundle groups (1080/2160 x quality/ultraperf) +
  `linear`/`nonlinear`/`srgb`/`pq` (colors path proven exercised — spaces
  differ from each other) + odd dims (`1919x1079`, edge behavior preserved
  exactly) — all byte-identical, 28 passes recorded every run.
- Perf (35-frame medians, interleaved repeats): post **-35.6%**, small models
  -1 to -3%, total about **-3.7%** at 3840x1600. Single-run model_9/10/11
  swings were shown to be power-state noise, not regressions.
- Round 2 (per-pass GPU timestamps, order-controlled repeats, tight spreads):
  the pass-11 zconst prefix measured **+70%** (spill), reverted to clamp+tail
  (-0.09ms); the routed prepass measured **-11%** (-0.05ms) with byte-identical
  A/B on even (`2560x1066`) and odd (`1919x1079`) dims under spatially-varying
  inputs (every quad sees four distinct pixels, so exchange routing is genuinely
  exercised). Variant total 3.28 -> 2.94ms locally at 2560x1066 -> 3840x1600.
- The runtime prepass quad-bounds guard (`native/fsr411-quad-bounds.hpp`) now
  matches structurally instead of by baked-in ids (reassembled modules
  renumber ids); proven byte-identical to the old guard on the stock shader,
  and it accepts the routed prepass.

## Packaging and provenance

- Built at image time by `tools/generate-fsr411-variant.py` from frozen
  4.1.1 assets (deterministic; content-deduped; manifests patched;
  `provenance.json` records script rev, SPIR-V Tools versions, per-file
  stacking and hashes). No variant payload lives in git.
- Embedded under `fsr411-variant/` keys alongside the base `fsr411/` tree
  (4.0.2 payloads removed with the 4.0.2 retirement).
  `verify-provider-build` binds RCDATA hashes and stamps
  `models=['4.1.1','4.1.1-perf']`.
- Known RX6000 note: the SPIR-V exact path needed no ghost fix here and
  matches AMD bit-for-bit on RDNA2 Windows native; keep watching it per
  driver (the `dot4` split exists only on the DXIL side).

## Limits (explicitly out of scope)

Proton/NVIDIA sanity, release packaging/version bump, validation-doc matrix
record, DX12 paths.
