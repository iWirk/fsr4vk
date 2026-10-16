#!/usr/bin/env python3
"""Generate the opt-in rewritten-shader variant of the 4.1.1 bundles.

Reads a frozen 4.1.1 asset root (fsr411/general + fsr411/colors), replays the
validated exact-rewrite stacking from the pinned
references/fsr4-shader-performance-overrides tools, and writes a variant tree
with patched manifests plus a provenance record. Deterministic: no timestamps.

Stacking (mirrors upstream tools/build_override.sh):
  prepass       : prepass_route_fma.py (owned; portable FMul/FAdd form of the
                  upstream route: 12 exchanges instead of 32, tail spread
                  over four threads; output-exact)
  generic model : model_clamp.py | model_tail.py            (keep iff changed)
  model_11      : model_clamp.py | model_tail.py             (zconst+stores
                  dropped: the zconst-enabled unroll spills on AMDVLK/RDNA2)
  postpass      : model_tail.py | model_clamp.py | postpass_taps.py
                  -> postpass_direct.py -> [re-disasm] postpass_sharetaps.py
Prepass (pass-01), borders, SPD and bins are copied verbatim (prepass variant
mismatch documented in the validation record).
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF_DEFAULT = (ROOT / "references" / "fsr4-shader-performance-overrides"
               / "tools")
# Model files (Linux exact set) by 4.1.1 model index; file model_N = pass-(2N+1).
MODEL_FILES = ["pass-%02d.spv" % (2 * n + 1)
               for n in (1, 2, 4, 5, 7, 8, 9, 10, 12)]
PASS11_FILE = "pass-23.spv"
POSTPASS_FILE = "pass-27.spv"
PREPASS_FILE = "pass-01.spv"
# Owned prepass router (portable FMul/FAdd form); lives in our tools/, not the
# pinned reference tree, because upstream only routes the native DOT form.
PREPASS_ROUTE = ROOT / "tools" / "prepass_route_fma.py"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def run_text(script: Path, text: str, *args: str):
    """Run a rewrite script on disassembly text. Returns (rc, stdout, note)."""
    p = subprocess.run([sys.executable, str(script), *args], input=text,
                       capture_output=True, text=True)
    err = p.stderr.strip().splitlines()
    return p.returncode, p.stdout, (err[-1] if err else "")


def find_tool(name: str, sdk, explicit):
    if explicit and (explicit / (name + ".exe")).exists():
        return explicit / (name + ".exe")
    found = shutil.which(name)
    if found:
        return Path(found)
    raise SystemExit(f"generate-fsr411-variant: need {name} (--spirv-bin or PATH)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("assets", type=Path,
                   help="frozen 4.1.1 root containing fsr411/")
    p.add_argument("output", type=Path, help="variant tree to create")
    p.add_argument("--scripts", type=Path, default=REF_DEFAULT)
    p.add_argument("--spirv-bin", type=Path, default=None,
                   help="dir with spirv-dis/as/val (default: VULKAN_SDK/Bin or PATH)")
    p.add_argument("--record-into", type=Path, default=None,
                   help="frozen-inputs.json to extend with a variant section")
    a = p.parse_args()
    if (a.output / "fsr411-variant").exists():
        p.error("variant output exists; refusing to overwrite")
    sdk = None
    if a.spirv_bin:
        sdk = a.spirv_bin
    elif "VULKAN_SDK" in os.environ:
        sdk = Path(os.environ["VULKAN_SDK"]) / "Bin"
    dis = find_tool("spirv-dis", sdk, sdk)
    asm = find_tool("spirv-as", sdk, sdk)
    val = find_tool("spirv-val", sdk, sdk)
    base = a.assets / "fsr411"
    if not (base / "general").is_dir():
        p.error("assets root lacks fsr411/general")
    out = a.output / "fsr411-variant"
    shutil.copytree(base, out, symlinks=False)

    def disasm(path: Path) -> str:
        r = subprocess.run([str(dis), str(path)], capture_output=True,
                           text=True)
        if r.returncode:
            raise SystemExit(f"disasm failed for {path}:\n{r.stderr}")
        return r.stdout

    tmp = out / ".work"
    tmp.mkdir()
    stacking = {"model": ["model_clamp.py", "model_tail.py"],
                "pass11": ["model_clamp.py", "model_tail.py"],
                "prepass": ["prepass_route_fma.py"],
                "postpass": ["model_tail.py", "model_clamp.py",
                             "postpass_taps.py", "postpass_direct.py",
                             "postpass_sharetaps.py"]}
    records = []
    rewritten = skipped = 0

    def build(kind: str, text: str, tag: str):
        """Returns final SPIR-V bytes, or None to keep the original."""
        if kind == "model":
            rc1, t1, e1 = run_text(a.scripts / "model_clamp.py", text)
            rc2, t2, e2 = run_text(a.scripts / "model_tail.py", t1)
            note = f"clamp[{e1}] tail[{e2}]"
            if rc2 or t2 == text:
                return None, note + " unchanged"
            return ("AS", t2, note)
        if kind == "pass11":
            # zconst+stores deliberately NOT applied: on AMDVLK/RDNA2 the
            # zconst-enabled full unroll spills (+70% measured on pass-23;
            # stores only partly mitigates). clamp|tail alone is neutral.
            rc1, t1, e1 = run_text(a.scripts / "model_clamp.py", text)
            rc2, t2, e2 = run_text(a.scripts / "model_tail.py", t1)
            note = f"clamp[{e1}] tail[{e2}]"
            if rc2 or t2 == text:
                return None, note + " unchanged"
            return ("AS", t2, note)
        if kind == "prepass":
            # Owned route transform for the portable prepass (32 -> 12 quad
            # exchanges, tail spread over four threads). Refuses loudly on
            # structural surprise; the runtime quad-bounds guard accepts the
            # reassembled module by structure, not baked-in ids.
            rcr, tr, er = run_text(PREPASS_ROUTE, text)
            note = f"route[{er}]"
            if rcr or not tr:
                return None, note + " refused"
            return ("AS", tr, note)
        # postpass: tail|clamp|taps -> direct -> as -> dis -> sharetaps -> as(v1.1)
        rc1, t1, e1 = run_text(a.scripts / "model_tail.py", text)
        rc2, t2, e2 = run_text(a.scripts / "model_clamp.py", t1)
        rc3, t3, e3 = run_text(a.scripts / "postpass_taps.py", t2)
        if rc3:
            return None, "taps refused"
        rc4, t4, e4 = run_text(a.scripts / "postpass_direct.py", t3)
        if rc4:
            return None, "direct refused"
        return ("SHARE", t4, f"tail[{e1}] clamp[{e2}] taps direct")

    def process(kind: str, src: Path, rel: Path):
        nonlocal rewritten, skipped, done, work_out
        data = src.read_bytes()
        h = sha(data)
        if h in done:
            tag0, changed0 = done[h]
            print(f"{rel}: identical input as {tag0}, reuse -> changed={changed0}")
            if changed0:
                shutil.copyfile(work_out[tag0], out / rel)
                records.append({"path": rel.as_posix(), "source_sha256": h,
                                "sha256": sha((out / rel).read_bytes()),
                                "stacking": stacking[kind], "reused_from": tag0})
                return True
            records.append({"path": rel.as_posix(), "source_sha256": h,
                            "sha256": h, "stacking": stacking[kind],
                            "reused_from": tag0, "unchanged": True})
            return True
        text = disasm(src)
        tag = f"{rel.parent.as_posix().replace('/', '_')}_{src.stem}"
        res = build(kind, text, tag)
        if res is None or res[0] is None:
            note = (" " + res[1]) if res is not None else ""
            print(f"{rel}: no rewrite ({tag}){note}")
            done[h] = (tag, False)
            records.append({"path": rel.as_posix(), "source_sha256": h,
                            "sha256": h, "stacking": stacking[kind],
                            "unchanged": True})
            skipped += 1
            return True
        mode, body, note = res
        dst = out / rel
        if mode == "AS":
            (tmp / f"{tag}.spvasm").write_text(body, encoding="utf-8")
            assemble_body = body
            # assemble via temp file (spirv-as needs no stdin subtleties)
            r = subprocess.run(
                [str(asm), "--target-env", "spv1.3",
                 str(tmp / f"{tag}.spvasm"), "-o", str(dst)],
                capture_output=True, text=True)
            if r.returncode:
                print(f"{rel}: ASSEMBLE FAILED\n{r.stderr}")
                return False
            r = subprocess.run([str(val), "--target-env", "vulkan1.3", str(dst)],
                               capture_output=True, text=True)
            if r.returncode:
                print(f"{rel}: VALIDATE FAILED\n{r.stderr}")
                return False
        else:
            (tmp / f"{tag}.direct.spvasm").write_text(body, encoding="utf-8")
            mid = tmp / f"{tag}.direct.spv"
            for args, vt in ((["--target-env", "spv1.3",
                               str(tmp / f"{tag}.direct.spvasm"), "-o", str(mid)], "vulkan1.3"),):
                r = subprocess.run([str(asm)] + args, capture_output=True, text=True)
                if r.returncode:
                    print(f"{rel}: ASSEMBLE FAILED\n{r.stderr}")
                    return False
            r = subprocess.run([str(dis), str(mid), "-o", str(tmp / f"{tag}.sh.spvasm")],
                               capture_output=True, text=True)
            if r.returncode:
                print(f"{rel}: re-disasm FAILED")
                return False
            sh_in, sh_out = tmp / f"{tag}.sh.spvasm", tmp / f"{tag}.sh2.spvasm"
            r = subprocess.run(
                [sys.executable, str(a.scripts / "postpass_sharetaps.py"),
                 str(sh_in), str(sh_out)], capture_output=True, text=True)
            print(f"{rel}: sharetaps(rc={r.returncode})")
            if r.returncode:
                print(r.stdout[-500:] if r.stdout else "")
                return False
            r = subprocess.run(
                [str(asm), "--target-env", "vulkan1.1",
                 str(sh_out), "-o", str(dst)], capture_output=True, text=True)
            if r.returncode:
                print(f"{rel}: share assemble FAILED\n{r.stderr}")
                return False
            r = subprocess.run([str(val), "--target-env", "vulkan1.1", str(dst)],
                               capture_output=True, text=True)
            if r.returncode:
                print(f"{rel}: share validate FAILED\n{r.stderr}")
                return False
            note += " sharetaps"
        print(f"{rel}: {note} changed=True")
        done[h] = (tag, True)
        work_out[tag] = dst
        rewritten += 1
        records.append({"path": rel.as_posix(), "source_sha256": h,
                        "sha256": sha(dst.read_bytes()), "stacking": stacking[kind]})
        return True

    done = {}
    work_out = {}
    ok = True
    # general bundles: prepass + model files + pass11 + postpass
    for b in sorted((out / "general").glob("*/*")):
        if not b.is_dir():
            continue
        ok &= process("prepass", b / PREPASS_FILE,
                      Path("general") / b.relative_to(out / "general") / PREPASS_FILE)
        for m in MODEL_FILES:
            ok &= process("model", b / m,
                          Path("general") / b.relative_to(out / "general") / m)
        ok &= process("pass11", b / PASS11_FILE,
                      Path("general") / b.relative_to(out / "general") / PASS11_FILE)
        ok &= process("postpass", b / POSTPASS_FILE,
                      Path("general") / b.relative_to(out / "general") / POSTPASS_FILE)
    # colors postpass copies
    for pfile in sorted((out / "colors").rglob(POSTPASS_FILE)):
        ok &= process("postpass", pfile,
                      Path("colors") / pfile.relative_to(out / "colors"))
    if not ok:
        raise SystemExit("generate-fsr411-variant: failures above")
    # patch manifests: rewritten files get new sha256
    bypath = {r["path"]: r["sha256"] for r in records if "sha256" in r
              and r["sha256"] != r["source_sha256"]}
    for man in sorted(out.rglob("manifest.json")):
        data = json.loads(man.read_text(encoding="utf-8"))
        changed = False
        for entry in data.get("shaders", []):
            rel = (man.parent.relative_to(out) / entry["file"]).as_posix()
            if rel in bypath and entry.get("sha256") != bypath[rel]:
                entry["sha256"] = bypath[rel]
                changed = True
        if changed:
            man.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    # transform revisions for provenance
    rev = ""
    try:
        rev = subprocess.run(["git", "-C", str(a.scripts.parent), "rev-parse",
                              "HEAD"], capture_output=True, text=True,
                             timeout=30).stdout.strip()
    except Exception:
        pass
    tools = {}
    for name in ("spirv-dis", "spirv-as", "spirv-val"):
        exe = sdk / f"{name}.exe" if sdk and (sdk / f"{name}.exe").exists() \
            else shutil.which(name)
        if exe:
            r = subprocess.run([str(exe), "--version"], capture_output=True,
                               text=True, timeout=30)
            tools[name] = (r.stdout.splitlines() or [""])[0].strip()
    provenance = {
        "schema": 1,
        "base": "fsr411",
        "transform_repo_rev": rev,
        "stacking": stacking,
        "spirv_tools": tools,
        "files": records,
        "rewritten": rewritten,
        "skipped": skipped,
    }
    (out / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)
    if a.record_into is not None:
        frozen = json.loads(a.record_into.read_text(encoding="utf-8"))
        variant_files = {}
        for dirpath, _, filenames in os.walk(out):
            for name in filenames:
                if name in ("provenance.json",):
                    continue
                full = Path(dirpath) / name
                variant_files[full.relative_to(out.parent).as_posix()] = \
                    sha(full.read_bytes())
        frozen.setdefault("variant", {})["fsr411-variant"] = {
            "provenance": provenance,
            "files": variant_files,
        }
        a.record_into.write_text(
            json.dumps(frozen, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    print(f"variant ready: rewritten={rewritten} "
          f"skipped={skipped} files={len(records)}")


if __name__ == "__main__":
    main()
