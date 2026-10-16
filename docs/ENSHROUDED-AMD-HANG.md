# Enshrouded FSR 4.0.2 hang on AMD Windows — conclusion

Date: 2026-10-16. GPU/driver: RX 6950 XT / AMD Windows 26.9.2 (AMDVLK).
Game needs NVIDIA spoofing for DLSS inputs; spoofing stays enabled throughout.

## Symptom

Selecting FSR 3.X (fsr4vk provider) hangs the game (`Application Hang 1002`).
`OptiScaler.log` ends right after the first `Dispatch!!`; the provider log ends
at `pipeline_module model_6` with no `pipeline_compile` — inside
`vkCreateComputePipelines` for `pass-07.portable.spv` (4.0.2, quality/2160,
56254 words). Process memory balloons ~2GB to ~13GB while hung.

## Proven root cause: AMD shader-compiler hang, not host state

A 14.5GB hang dump was triaged with a CLI-only MiniDump + manual `.pdata`
unwinder (no GUI debugger on the box). Hung render thread, 32 validated frames:

- `amdvlk64.dll` x25 (driver internals, stuck at `+0x1d51499`)
- Steam fossilize layer x2, `vulkan-1.dll` loader, then our provider:
  `dispatchImpl` calls `QualityContext` ctor calls `create_pipeline` calls the
  Vulkan import stub — each return address verified preceded by a `call` in the
  shipped binary. The other 62 threads idle in `ntdll` waits (no deadlock).

Committed memory 13.9GB confirms runaway compiler-heap growth, not a leak in
provider-owned allocations (~207MB).

## Ruled out (each isolated natively on the same GPU/driver)

- Device negotiation failure (`unhandled sType=1000412000` pageable): fixed via
  copy-through allowlist (+imageRobustness, +meshShader). Negotiation now
  succeeds; hang persists.
- Descriptor sizes/strides: identical game vs native (`16/32`, robust equal).
- Spoofed NVIDIA IDs: trial build with consistent real IDs still hangs.
- API 1.1/1.2/1.3, robust 0/1, unknown queue family, `max_render`, HDR flags,
  full 17-node/30-extension game-like device: all pass natively.
- Steam overlay + implicit layers (`VK_LOADER_LAYERS_DISABLE=~implicit~`):
  identical hang.
- Pipeline-cache seed: accepted (`592,536` bytes, header valid) yet missed —
  driver recompiles anyway on this path.

## Fixes landed (this change set)

- Provider: negotiation allowlist; `heap_binding_flags=0` per GH issue #1 with
  contract note; split `pipeline_module`/`pipeline_compile` progress logging;
  hang-surviving early `descriptor_properties_early` logging (ctor + dispatch);
  spoof-aware `context api/queue` logging; opt-in pipeline-cache seed support
  (`<bundle>/pipeline_cache.portable.bin`, `FSR4_VK_PIPELINE_CACHE_PATH`,
  UUID-validated, safe empty fallback) + probe capture hook.
- OptiScaler (submodule): `PreferFsr411PerfUpscaler` quirk for `enshrouded.exe`
  (unset `UpscalerIndex` only, still overridable; picks the `4.1.1 Perf`
  variant); skip spoofing during Vulkan dispatch (matches `InitFFX`;
  steady-state no-op).
- Tests: Enshrouded device-chain regression, smoke/probe env gates
  (`FSR4_TEST_VULKAN_1_2`, Enshrouded-device emulation, `NO_CAPTURE`,
  force-robust), Windows `_WIN32 setenv` shims.

## Workaround (validated in-game): default 4.1.1 Perf for Enshrouded

4.1.1 is disjoint shaders (no shared `model_6`), always portable, smaller hot
set; `3840x1600` is even/even so the known odd-edge limitation does not apply.
Validated natively (28 passes) and in-game via `UpscalerIndex=1`, then made the
quirk default; later moved to the `4.1.1 Perf` variant (index 2, byte-identical
pixels, ~3.7% faster at 3840x1600) once it validated. Stock 4.1.1 and 4.0.2
remain selectable.

## Decision: keep 4.0.2, no full strip

Full removal from the Vulkan port was costed (~83MB assets/589 files,
~25-35 files, provider/core/tests/docs/provenance) and rejected for now: it
would void the only odd-edge-passing oracle (4.1.1 fails 6/10 poison cases),
the only `native-mixed-dot` consumer path (NVIDIA), half the paired `RMSE 0`
matrix, and the RDR2 native context — while fixing no open crash (Enshrouded is
safe via the quirk default). Revisit only as a versioned breaking change with
fresh paired captures.

### 2026-10-16 reversal: 4.0.2 retired by owner approval

The removal was executed anyway: provider advertises `[0]=4.1.1,[1]=Perf`,
the 4.0.2 version ID is rejected, 4.0.2 asset trees and the cache-seed support
are gone, and the Enshrouded quirk defaults to Perf at index 1. Consciously
lost versus the above: the odd-edge oracle, the native-mixed-dot consumer
path, the 4.0.2 half of the paired matrix, and the RDR2 native context. Stale
explicit `UpscalerIndex=2` configs clamp to `4.1.1` (index 0), not Perf.

## Open (out of scope)

- AMD driver report with repro package (deferred by owner).
- Validation-layer game run (expect no VUID before `model_6`).
- Driver-version sensitivity (W6400 evidence is Mesa RADV, different compiler).
- Hang dump retained outside the repo (`%TEMP%\enshrouded.DMP`, 14.5GB).
