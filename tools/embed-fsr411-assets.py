#!/usr/bin/env python3
"""Generate deduplicated PE resources from the verified 4.1.1 asset matrix."""
import argparse
import hashlib
import json
from pathlib import Path
import os
import sys
import tempfile
import importlib.util
from asset_compression import compress_bytes

spec = importlib.util.spec_from_file_location('fsr411_assets', Path(__file__).with_name('fsr411-assets.py'))
fsr411_assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fsr411_assets)

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('fsr411', type=Path, help='fsr411/ root with general/ and colors/ children')
p.add_argument('output', type=Path)
p.add_argument('--compression', choices=('none','zstd'), default='none')
p.add_argument('--zstd', default=os.environ.get('ZSTD','zstd'))
p.add_argument('--variant', type=Path, default=None,
               help='generated fsr411-variant tree root (contains fsr411-variant/); '
                    'packed under fsr411-variant/ keys after shape validation')
a = p.parse_args()
verified = fsr411_assets.verify(a.fsr411)
print(f'Verified {len(verified)} FSR 4.1.1 runtime assets.')
a.output.mkdir(parents=True, exist_ok=True)
if (a.output/'embedded-assets.json').exists():
    old = json.loads((a.output/'embedded-assets.json').read_text())
    if old['compression'] != a.compression:
        raise ValueError('refusing to reuse an embedding directory with a different codec mode')
resources, index, entries = {}, [], []
def add_resource(path, key, payload):
    if not payload or len(payload)>64*1024*1024:
        raise ValueError('asset outside runtime size limit: '+key)
    digest = hashlib.sha256(payload).hexdigest()
    if digest not in resources:
        stored=payload; compressed=False
        if a.compression=='zstd':
            # Compress the bytes already hashed, not a second read of a live file.
            candidate=compress_bytes(payload,a.zstd)
            if len(candidate)<len(payload):
                stored=candidate;compressed=True
        directory=a.output/'payloads';directory.mkdir(exist_ok=True)
        stored_path=(directory/(digest+('.zst' if compressed else '.raw'))).resolve()
        # RC must refer only to owned frozen bytes, including raw debug payloads.
        # Replace atomically so repeated deterministic generation cannot expose
        # a partially written payload to a resource compiler.
        with tempfile.NamedTemporaryFile(dir=directory,delete=False) as temporary:
            temporary.write(stored)
            staged=Path(temporary.name)
        staged.replace(stored_path)
        resources[digest] = (len(resources)+100,stored_path,len(stored),compressed,
                             hashlib.sha256(stored).hexdigest())
    rid,_,stored_size,compressed,stored_hash=resources[digest]
    index.append(f'    {{{json.dumps(key)}, {rid}, {len(payload)}, {stored_size}, {str(compressed).lower()}}},')
    entries.append(dict(path=key, resource_id=rid, size=len(payload), sha256=digest,
                        stored_size=stored_size, compressed=compressed, stored_sha256=stored_hash))

for path in verified:
    add_resource(path, 'fsr411/'+path.relative_to(a.fsr411).as_posix(), path.read_bytes())
if a.variant is not None:
    variant_root = a.variant/'fsr411-variant'
    if not (variant_root/'general').is_dir():
        raise ValueError('variant root lacks fsr411-variant/general')
    # Pack every payload file the variant tree carries (mirrors base layout:
    # full general bundles + colors pass-01/pass-27 overlays). Content was
    # assembled + validated at generation time; manifests carry patched hashes
    # and are re-checked here.
    variant_files = sorted((variant_root/'general').rglob('*.spv')) + \
        sorted((variant_root/'general').rglob('*.bin'))
    if (variant_root/'colors').is_dir():
        for colors_manifest in sorted((variant_root/'colors').glob('*/*/*/manifest.json')):
            for entry in json.loads(colors_manifest.read_text()).get('shaders', []):
                if entry['file'] in ('pass-01.spv', 'pass-27.spv'):
                    variant_files.append(colors_manifest.parent/entry['file'])
    for target in variant_files:
        if not target.is_file():
            raise ValueError(f'variant bundle incomplete: {target}')
        key = 'fsr411-variant/'+target.relative_to(variant_root).as_posix()
        add_resource(target, key, target.read_bytes())
    for manifest in sorted(variant_root.rglob('manifest.json')):
        data = json.loads(manifest.read_text())
        for entry in data.get('shaders', []):
            target = manifest.parent/entry['file']
            if target.is_file() and \
                    hashlib.sha256(target.read_bytes()).hexdigest() != entry.get('sha256'):
                raise ValueError(f'variant manifest mismatch: {target}')
(a.output/'embedded-assets.rc').write_text('\n'.join(f'{rid} RCDATA {json.dumps(str(path))}' for rid,path,*_ in resources.values())+'\n')
(a.output/'embedded-asset-index.hpp').write_text(
    '#pragma once\nnamespace fsr4assets {\nstruct EmbeddedEntry { const char* path; unsigned id; unsigned size; unsigned stored_size; bool compressed; };\n'
    'inline constexpr EmbeddedEntry embedded_index[] = {\n'+'\n'.join(index)+'\n};\n}\n')
(a.output/'embedded-assets.json').write_text(json.dumps(dict(schema=2, compression=a.compression, entries=entries), indent=2)+'\n')
print(f'Embedded {len(entries)} paths using {len(resources)} unique payloads.')
