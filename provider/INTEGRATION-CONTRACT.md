# Current provider integration contract

Updated 2026-10-17. This is the setup/ownership contract, not a claim that every
device/game in the audit's proposed validation matrix has been retested.

## Device creation

Minimum API: Vulkan 1.1 plus the required extension-backed functionality.
Enable before `vkCreateDevice`; physical support queries cannot recover logical
enablement after creation. Generic `ffxQuery` device-requirements prepare/release
preserves the caller's complete chain in owned storage. Unknown structures fail
closed with their sType; they are never silently dropped. Pass-through nodes
with no consumed state: `VK_EXT_pageable_device_local_memory`,
`VK_EXT_image_robustness`, `VK_EXT_mesh_shader` (Enshrouded on AMD Windows;
spoofing to NVIDIA for DLSS inputs is still required and unchanged).

Mandatory unpromoted extensions:

- `VK_EXT_mutable_descriptor_type` / `mutableDescriptorType`.
- `VK_EXT_descriptor_buffer` / `descriptorBuffer`.
- `VK_KHR_compute_shader_derivatives` / `computeDerivativeGroupLinear`.

Mandatory core features: `shaderInt16`, `shaderStorageImageReadWithoutFormat`,
`shaderStorageImageWriteWithoutFormat`. Vulkan 1.2 features: `shaderFloat16`,
`shaderInt8`, `storageBuffer8BitAccess`, `runtimeDescriptorArray`,
`descriptorBindingVariableDescriptorCount`, `bufferDeviceAddress` (variable-count
is still requested for compatibility, but descriptor heap layouts use flags=0
with fixed 128 descriptors per GH issue #1: AMD Windows reports a smaller
layout size for VARIABLE_DESCRIPTOR_COUNT_BIT heaps). Below 1.2,
enable the corresponding `VK_KHR_shader_float16_int8`, `VK_KHR_8bit_storage`,
`VK_EXT_descriptor_indexing`, `VK_KHR_buffer_device_address`, and
`VK_KHR_shader_float_controls` extensions. Vulkan 1.3 features:
`synchronization2`, `shaderIntegerDotProduct`; below 1.3 enable
`VK_KHR_synchronization2` and `VK_KHR_shader_integer_dot_product`.

4.1.1 always uses the portable INT8 bundle. No Cooperative Matrix path exists.

The optional version-1 `ffx_vk_create_device_features.h` query/create descriptor
copies flags from the *successful final* VkDeviceCreateInfo while its storage is
alive. It reports robust-buffer access and native-backend eligibility plus the
effective API and, only if known, recording queue family. Old/other providers
receive no descriptor unless they advertise it. Old hosts keep the established
path when robust/nonrobust descriptor sizes are unambiguous; ambiguous sizing
fails with a specific request for the snapshot rather than guessed addressing.
Unknown queue family disables optional profiling, not the upscaler.

## Input, API, and lifetime

Dimensions are bounded to `64..3840 x 64..2160`, input no larger than output,
with equal-size FSRAA accepted. Inputs match the active render extent exactly;
oversized DRS allocations/subrects are not yet supported. Resources are 2D,
single-layer, non-MSAA, base-mip/base-layer zero. FFX cannot fully attest original
image format, sample count, or subresource identity; the host must preserve that
contract. Original motion format can be supplied through [native inputs](NATIVE-INPUTS.md).
Representable multilayer metadata is rejected; zero depth/mip fields in legacy
host descriptions remain unspecified, not proof of a multilayer image.

Known 4.1.1 limitation: its captured post-pass does not write the unmatched last
row/column when an output dimension is odd. Acceptance of that dimension is not
a guarantee of fully defined edge pixels. Poisoned-output tests reproduced this
on 2026-10-01; 4.0.2 passed the same tests. The shader policy is deliberately
unchanged until an original-provider edge oracle establishes the intended
result. Prefer even output dimensions for 4.1.1 in the meantime. See the
[release verification report](../docs/RELEASE-0.4.3-VALIDATION.md)
for the failing cases; they are not passing regressions.

Input states are COMPUTE_READ; output is UNORDERED_ACCESS. Inverted depth and
low-resolution motion are required. Auto or external R16/R32 exposure is
supported. External exposure samples must be finite/positive; invalid samples
and invalid previous history become neutral 1.0/current exposure respectively.
Jitter and motion-vector scale must be finite. Optional masks are not consumed;
host RCAS is independent. Unsupported key/value configuration returns an error,
not silently successful application. Auto and forced presets 0-5 remain available.

FFX descriptor chains are limited to 64 nodes and must be acyclic. Exported API
exceptions are translated into return codes. Internal CPU calls serialize per
context. This does **not** synchronize GPU submissions: record/submit on one
ordered queue, do not abandon a recorded frame, and finish GPU work before
exclusive context destruction. Callbacks may query, but must not recursively
dispatch or destroy their own context.
Eight descriptor/constant/view slots remain independent; the ninth outstanding
frame is rejected. Preset cores stay alive until context destruction.

## Non-upscale requests and frame-generation pass-through

Only `UPSCALE` create requests are implemented here. Other create types
(frame generation `0x00020001`, HUD-less frame generation `0x00020009`, and the
Vulkan frame-generation swapchain `0x00040001`) are forwarded, together with
their contexts' later query/configure/dispatch/destroy calls, to a sibling
`amd_fidelityfx_vk.dll` next to this module. The sibling is accepted only when
it resolves all five FFX entry points and advertises upscale versions other
than this provider's `4.1.1` IDs, so the stock AMD library (FSR 3.1.4 family,
shipped with OptiScaler) is accepted while a byte copy of this provider is
rejected and never called back into itself. Hosts that intercept the stock
`amd_fidelityfx_vk.dll` name at the loader level (OptiScaler's `LdrLoadDll`
hook serves its already-loaded upscaler module for that name) make the plain
load resolve to this provider; the provider then loads a renamed sibling copy
(`fsr4vk-fg-host.dll`, a hard link beside the sibling, falling back to a byte
copy in the same directory or `%TEMP%`) whose name no loader hook matches.
Non-upscale `GET_VERSIONS` queries
are also forwarded; without an accepted sibling they keep returning an empty
result, and non-upscale creation keeps returning `FFX_API_RETURN_NO_PROVIDER`.
The pass-through decision and forwarded request types are written to the
provider diagnostic log. Deployment requirement: keep the stock
`amd_fidelityfx_vk.dll` beside `amd_fidelityfx_upscaler_vk.dll` in
`OptiScaler/` to use FSR frame generation together with the FSR4 upscaler.

## Diagnostics and measurements

Logging remains optional and game-directory placement is unchanged. Negative
event VkResults retain their identity. Opt-in pipeline progress logs name the
pass and CPU creation duration. GPU-memory-usage v1 reports owned VkDeviceMemory
allocation bytes, excluding caller images and driver pipeline/cache internals.

Pipeline caches start empty every context; the driver-owned cache object is
still used for creation. `pipeline_cache_data()` remains available to the
resource probe as a diagnostic aid.

Advertised upscaler versions: `4.1.1`, and opt-in `4.1.1 Perf`
(same 4.1.1 model with rewritten shaders, generated at build time by
`tools/generate-fsr411-variant.py` from frozen 4.1.1 assets; see its provenance
record and the validation report). Order is stable (`[0]=4.1.1,
[1]=Perf`) so saved indices keep their meanings; unknown future indices clamp
to `4.1.1` on the host side as before. The retired `4.0.2` ID is rejected
(`FFX_API_RETURN_NO_PROVIDER`); the stock FSR 3.1.4 alias maps to 4.1.1.

`FSR4_VK_PROFILE_PATH` is now a filename prefix, not an overwritable final-frame
CSV. Each core/session writes `<prefix>.<identity>.jsonl`, with per-frame model,
backend, preset, color, dimensions, queue family, timestamp period/valid bits,
availability, and pass times. Each of eight slots has a separate query range;
collection occurs only after retirement, without WAIT_BIT or queue waits.
Unavailable data is explicit, never zero. `tools/optimization/profiles.py`
accepts these streams and historical CSVs. File I/O/profiling overhead is opt-in
and must be excluded or measured separately in production performance claims.

## Reproduction/builds

Run `python3 tools/test-codebase.py --vulkan-include <include> --opti-reader
<OptiScaler/upscalers/ffx>` for the owned CPU gate. It records source hashes and
retains logs in a fresh build directory; sanitizers are on by default. GPU and
Windows/Proton checks remain separate gates.

Windows builds freeze owned compiler inputs, Vulkan SDK headers and assets into
a unique stage, record compiler/resource/strip/compressor versions and executable
hashes, and lock only
their chosen output directory, validate PE exports/payload hashes and normal/debug
identity, then atomically replace individual output files. A final provenance
manifest identifies the complete verified set; do not consume a set during its
publication. Frozen stages are retained for diagnosis. This does not create a
GitHub release, change exact release notes/readme text, or sync the public repo.
The system compiler/sysroot is version-recorded, not a hermetically frozen
toolchain image. The Linux CPU CI also compiles the Windows-only research
collector with MinGW; a macOS run without --mingw-cxx records that check as
skipped instead of pretending Windows headers are available.
