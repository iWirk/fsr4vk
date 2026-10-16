# FSR 4.1.1 Perf variant (opt-in 3rd upscaler version)

Date: 2026-10-16. Status: implemented, validated on RX 6950 XT / AMD Windows
26.9.2, shipped in the provider build. Default index unchanged everywhere
except Enshrouded (quirk defaults to Perf, still overridable).

## What it is

A third selectable Vulkan provider version, ` 4.1.1 VK INT8 Perf` (index 2;
`[0]=4.0.2`, `[1]=4.1.1` keep their meanings). Same 4.1.1 model, same 28-pass
layout and dispatch path (`is411family`), with the exact-set shader rewrites
from `references/fsr4-shader-performance-overrides@bf009e5` applied to its
SPIR-V. Scripts are only executed, never vendored; outputs are regenerated
from our own captured shaders (AMD-MIT payload posture kept, no prebuilt
binaries lifted).

## Rewrite set (exact only)

Applied per file, in upstream stacking order:

- Generic models (`pass-03/05/09/11/15/17/19/21/25` = model_1,2,4,5,7,8,9,10,12):
  `model_clamp.py | model_tail.py`, kept iff the text changed.
- `pass-23` (model_11): `zconst.py` -> `pass11_stores.py` ->
  `model_clamp.py | model_tail.py`.
- `pass-27` (postpass): `model_tail.py | model_clamp.py | postpass_taps.py`
  -> `postpass_direct.py` -> re-disassemble -> `postpass_sharetaps.py`
  (assembled with `--target-env vulkan1.1` per upstream recipe; module stays
  SPIR-V 1.3).
- Excluded: prepass `pass-01` (our variant accumulates with `FMul`/`FAdd`
  trees; the script requires channels ending in `OpFDot2MixAcc32VALVE` —
  re-deriving for FP reassociation needs its own exactness proof), borders,
  SPD, bins. 4.0.2 assets untouched.

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

## Packaging and provenance

- Built at image time by `tools/generate-fsr411-variant.py` from frozen
  4.1.1 assets (deterministic; content-deduped; manifests patched;
  `provenance.json` records script rev, SPIR-V Tools versions, per-file
  stacking and hashes). No variant payload lives in git.
- Embedded under `fsr411-variant/` keys: 1368 total paths (948 + 420),
  603 unique payloads (exactly +50). `verify-provider-build` binds RCDATA
  hashes and stamps `models=['4.0.2','4.1.1','4.1.1-perf']`.
- Known RX6000 note: the SPIR-V exact path needed no ghost fix here and
  matches AMD bit-for-bit on RDNA2 Windows native; keep watching it per
  driver (the `dot4` split exists only on the DXIL side).

## Limits (explicitly out of scope)

Proton/NVIDIA sanity, release packaging/version bump, validation-doc matrix
record, prepass port, 4.0.2 changes, DX12 paths.
