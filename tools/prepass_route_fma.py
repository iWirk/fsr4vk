#!/usr/bin/env python3
"""Route the portable (FMul/FAdd, no VALVE dot) 4.1.1 prepass like upstream
prepass_route.py routes the native one: each thread finishes its own output
word (12 quad exchanges instead of 32; tail spread over four threads).

Bit-exactness: the channel template is replicated verbatim (same FMul/FAdd
left-fold accumulation, same weight/input addresses by symmetry); every
emitted FAdd keeps the original operand grouping and order up to FAdd
operand order (commutative-exact on finite inputs). Bias add stays
total-first; x64 scale, RoundEven, clamp bounds, pack and store addresses
are the original values. Any structural surprise aborts (die) instead of
emitting a suspect module.

Usage: spirv-dis pass-01.spv | prepass_route_fma.py > out.spvasm
"""
import re
import sys

L = sys.stdin.read().split('\n')
die = lambda m: sys.exit(f'prepass_route_fma: {m}')
name = lambda l: (re.match(r'\s*(%\w+) = ', l) or [None, None])[1]
defs = {name(l): l.strip() for l in L if name(l)}
GLSL = next(m[1] for l in L for m in [re.match(r'\s*(%\w+) = OpExtInstImport "GLSL.std.450"', l)] if m)

# the 16 channels: x, swapH(x), x + that, swapV of the sum
sw = []
for n, l in enumerate(L):
    m = re.match(r'\s*(%\w+) = OpGroupNonUniformQuadSwap %float (%\w+) (%\w+) %uint_0$', l)
    if m:
        a = re.match(rf'\s*(%\w+) = OpFAdd %float {re.escape(m[1])} {re.escape(m[3])}$', L[n + 1]) or die('unexpected sum after a horizontal exchange')
        b = re.match(rf'\s*(%\w+) = OpGroupNonUniformQuadSwap %float {re.escape(m[2])} {re.escape(a[1])} %uint_1$', L[n + 2]) or die('unexpected vertical exchange')
        sw.append(dict(n=n, x=m[3], s=a[1], v=b[1], scope=m[2]))
if len(sw) != 16:
    die(f'{len(sw)} channel sums found, expected 16')
SCOPE = sw[0]['scope']
if any(d['scope'] != SCOPE for d in sw):
    die('mixed quad scopes')


def block(k):
    """lines of channel k's share, from its weight-offset formation to its last product"""
    lines = L[sw[k - 1]['n'] + 3:sw[k]['n']]
    sh = next((m for l in lines for m in [re.match(r'\s*%\w+ = OpShiftRightLogical %uint (%\w+) %uint_2$', l)] if m), None) or die('no weight offset in a channel')
    return lines, sh[1]


def norm(lines, off, drop):
    """the block with its own ids numbered in order, its offset called OFF, and the lines that only form the offset left out"""
    ren, out = {off: 'OFF'}, []
    for l in lines:
        if name(l) in drop:
            continue
        if name(l):
            ren[name(l)] = f'T{len(ren)}'
        out.append(re.sub(r'%\w+', lambda t: ren.get(t[0], t[0]), l.strip()))
    return out


lines1, off1 = block(1)
m = re.match(rf'{re.escape(off1)} = OpIAdd %uint (%\w+) %uint_(\d+)$', defs[off1]) or die('unexpected offset of channel 1')
BASE, STEP = m[1], int(m[2])
T = norm(lines1, off1, {off1})
# portable template: FMul/FAdd left-fold accumulation, ending in its final FAdd
mt = re.match(r'(T\d+) = OpFAdd %float (\S+) (\S+)$', T[-1]) or die('a channel does not end in its last product')
if re.search(r'OpBranch|OpLoopMerge|OpSelectionMerge|OpLabel|OpPhi', '\n'.join(T)):
    die('channel template is not straight-line')
for k in range(2, 16):
    lk, ok = block(k)
    a = re.match(rf'{re.escape(ok)} = OpIAdd %uint {re.escape(BASE)} (%\w+)$', defs[ok]) or re.match(rf'{re.escape(ok)} = OpIAdd %uint (%\w+) {re.escape(BASE)}$', defs[ok]) or die(f'unexpected offset of channel {k}')
    mul = re.match(rf'{re.escape(a[1])} = OpIMul %uint %uint_{STEP} %uint_{k}$', defs.get(a[1], ''))
    if not (mul or a[1] == f'%uint_{STEP * k}'):
        die(f'channel {k} is not at {STEP} x {k}')
    if norm(lk, ok, {ok, a[1]}) != T:
        die(f'channel {k} is not built like channel 1')
# channel 0 uses the base itself and has no offset line
n0 = sw[0]['n'] - len(T)
if norm(L[n0:sw[0]['n']], BASE, set()) != T:
    die('channel 0 is not built like channel 1')
# the template's final share is channel 0's x; it must feed exactly the
# swap triple (swapH operand + pair sum)
share = sw[0]['x']
if not re.match(rf'\s*{re.escape(share)} = OpFAdd %float \S+ \S+$', L[sw[0]['n'] - 1]):
    die('channel share is not the last product')
trio = L[sw[0]['n']:sw[0]['n'] + 3]
if sum(1 for l in trio if share in l) != 2 or share not in trio[1]:
    die('channel share has unexpected uses')

# the 16 quad totals: FAdd(s, v), s first, channels 15 down to 0.
# Located by pattern (control-flow lines may sit between the last swap and
# the totals), then required consecutive.
first = next((n for n, l in enumerate(L) if re.match(
    rf'\s*%\w+ = OpFAdd %float {re.escape(sw[15]["s"])} {re.escape(sw[15]["v"])}$', l)), None)
if first is None:
    die('channel 15 total not found')
tn = first
tot = []
for i in range(16):
    ch = 15 - i
    m = re.match(r'\s*(%\w+) = OpFAdd %float (\S+) (\S+)$', L[tn + i])
    if not m or m[2] != sw[ch]['s'] or m[3] != sw[ch]['v']:
        die(f'total {i} is not s+v of channel {ch}: {L[tn + i].strip()}')
    tot.append(m[1])
cn = None
for n in range(sw[15]['n'], min(sw[15]['n'] + 60, len(L) - 4)):
    pm = re.match(rf'\s*(%\w+) = OpBitwiseAnd %uint (%\w+) %uint_3$', L[n])
    if pm and re.match(rf'\s*(%\w+) = OpIEqual %bool {re.escape(pm[1])} %uint_0$', L[n + 1]) and \
            'OpSelectionMerge' in L[n + 2]:
        cn = n
        POS = pm[1]
        break
if cn is None:
    die('the first-thread test is not where expected')
MERGE = L[cn + 2].split()[1]
bc = re.match(r'\s*OpBranchConditional %\w+ (%\w+) (%\w+)$', L[cn + 3]) or die('no branch after the first-thread test')
if bc[2] != MERGE:
    die('unexpected shape of the first-thread branch')
t0 = cn + 5
t1 = next(n for n in range(t0, len(L)) if re.match(rf'\s*OpBranch {re.escape(MERGE)}$', L[n]))
# bias vectors load before the first-thread test (all threads); the work
# block holds the stores. needs() spans both so address formation is pulled.
gap = L[tn + 16:cn]
if re.search(r'OpStore|OpBranch|OpLoopMerge|OpSelectionMerge|OpPhi|OpFunctionEnd', '\n'.join(gap)):
    die('unexpected control flow between totals and first-thread test')
tail = L[tn + 16:t1]
tdef = {name(l): n for n, l in enumerate(tail) if name(l)}
work = L[t0:t1]
stores = [mm for l in work for mm in [re.match(r'\s*OpStore (%\w+) (%\w+)$', l)] if mm]
if len(stores) != 4:
    die(f'{len(stores)} stores found, expected 4')
p0 = re.match(r'(%\w+) = OpAccessChain (%\w+) (%\w+) %uint_0 (%\w+)$', defs[stores[0][1]]) or die('unexpected store address')
for i in (1, 2, 3):
    pi = re.match(rf'%\w+ = OpAccessChain {re.escape(p0[2])} {re.escape(p0[3])} %uint_0 (%\w+)$', defs[stores[i][1]]) or die('unexpected store address')
    if not re.match(rf'{re.escape(pi[1])} = OpIAdd %uint {re.escape(p0[4])} %uint_{i}$', defs[pi[1]]):
        die('the four words are not stored side by side')
bias = [mm for l in tail for mm in [re.match(r'\s*(%\w+) = OpAccessChain (%\w+) (%\w+) %uint_0 %uint_(\d+)$', l)] if mm and 'v4uint' in mm[2]]
if len(bias) != 4 or [int(b[4]) for b in bias] != [int(bias[0][4]) + i for i in range(4)]:
    die('unexpected bias loads')
BIAS0 = int(bias[0][4])
cl = next((mm for l in tail for mm in [re.match(rf'\s*%\w+ = OpExtInst %v4int {re.escape(GLSL)} SClamp %\w+ (%\w+) (%\w+)$', l)] if mm), None) or die('no clamp in the tail')
if len(re.findall(r'OpFMul %float %\w+ %float_64', '\n'.join(tail))) != 16 or len(re.findall('RoundEven', '\n'.join(tail))) != 16:
    die('unexpected rounding code in the tail')
# word p covers channels 4p..4p+3 with biases v4uint[BIAS0+p]: verify the
# bias-add pairing (total-first) for every word from the tail itself
for p in range(4):
    for j in range(4):
        ch = 4 * p + j
        total = tot[15 - ch]
        bload = bias[p][1]
        # bias extract j of this word's vector, bitcast to float, added total-first
        pat = [l for l in tail if re.match(rf'\s*%\w+ = OpFAdd %float {re.escape(total)} %\w+$', l)]
        if len(pat) != 1:
            die(f'word {p} channel {ch}: total-add not found exactly once')


lnum = {name(l): n for n, l in enumerate(L) if name(l)}
# ids used verbatim in the emission must be module/prefix scope (hoisted
# values go through hoist() instead and are renamed)
for keep in (BASE, pm[2], p0[2], bias[0][2]):
    if lnum.get(keep, n0) >= n0:
        die(f'{keep} is not module/prefix scope')


def hoist(targets):
    """re-emit pure address formation for targets with region ids renamed"""
    remap, emitted = {}, []

    def visit(i):
        if i in remap:
            return remap[i]
        n = lnum.get(i)
        if n is None:
            die(f'unknown id {i}')
        if n < n0:
            return i
        if n >= t1 or (sw[0]['n'] - len(T) <= n < sw[15]['n'] + 3):
            die(f'{i} is channel-varying, cannot hoist')
        line = L[n]
        if re.search(r'OpStore|OpBranch|OpLoopMerge|OpSelectionMerge|OpPhi|OpFunction', line):
            die(f'cannot hoist {line.strip()[:60]}')
        parts = line.split('=', 1)
        if len(parts) != 2:
            die(f'cannot hoist {line.strip()[:60]}')
        deps = [visit(d) for d in re.findall(r'%\w+', parts[1])]
        table = dict(zip(re.findall(r'%\w+', parts[1]), deps))
        new = f'%rt_h{len(remap)} = ' + re.sub(r'%\w+', lambda t: table[t[0]], parts[1].strip())
        remap[i] = new.split(' = ')[0]
        emitted.append(P + new)
        return remap[i]

    return [visit(t) for t in targets], emitted


# SSA safety: nothing defined in the replaced region may be used after it
defined_here = {name(l) for l in L[n0:t1] if name(l)}
after = re.findall(r'%\w+', '\n'.join(L[t1:]))
if [i for i in after if i in defined_here]:
    die('replaced region leaks SSA values past the merge')


P = '        '
out = L[:n0]
out += [f'{P}%rt_p = OpBitwiseAnd %uint {pm[2]} %uint_3', f'{P}%rt_c1 = OpBitwiseXor %uint %rt_p %uint_1', f'{P}%rt_c2 = OpBitwiseXor %uint %rt_p %uint_2', f'{P}%rt_c3 = OpBitwiseXor %uint %rt_p %uint_3']
consts = [f'%rt_k{STEP * 4} = OpConstant %uint {STEP * 4}'] + [f'%rt_j{STEP * j} = OpConstant %uint {STEP * j}' for j in range(1, 4)]
X = {}
for r, wid in (('B', '%rt_p'), ('A', '%rt_c1'), ('D', '%rt_c2'), ('C', '%rt_c3')):
    out += [f'{P}%rt_w{r} = OpIMul %uint {wid} %rt_k{STEP * 4}', f'{P}%rt_o{r}0 = OpIAdd %uint {BASE} %rt_w{r}'] + [f'{P}%rt_o{r}{j} = OpIAdd %uint %rt_o{r}0 %rt_j{STEP * j}' for j in range(1, 4)]
    for j in range(4):
        sub = lambda t: f'%rt_o{r}{j}' if t[0] == 'OFF' else f'%rt_{r}{j}_{t[0][1:]}'
        code = [re.sub(r'\b(OFF|T\d+)\b', sub, l) for l in T]
        out += [P + c for c in code]; X[r, j] = code[-1].split(' = ')[0]
tot = []
for j in range(4):
    out += [f'{P}%rt_ha{j} = OpGroupNonUniformQuadSwap %float {SCOPE} {X["A", j]} %uint_0', f'{P}%rt_s1{j} = OpFAdd %float %rt_ha{j} {X["B", j]}',
            f'{P}%rt_hc{j} = OpGroupNonUniformQuadSwap %float {SCOPE} {X["C", j]} %uint_0', f'{P}%rt_s2{j} = OpFAdd %float %rt_hc{j} {X["D", j]}']
for j in range(4):
    out += [f'{P}%rt_v{j} = OpGroupNonUniformQuadSwap %float {SCOPE} %rt_s2{j} %uint_1', f'{P}%rt_t{j} = OpFAdd %float %rt_s1{j} %rt_v{j}']
    tot.append(f'%rt_t{j}')
# every thread finishes its own word: no first-thread branch remains, all
# threads store side by side (same addresses and values as before)
(heap, wbase, btable), hoisted_lines = hoist([p0[3], p0[4], bias[0][3]])
out += hoisted_lines
out += [f'{P}%rt_bi = OpIAdd %uint %uint_{BIAS0} %rt_p', f'{P}%rt_bp = OpAccessChain {bias[0][2]} {btable} %uint_0 %rt_bi', f'{P}%rt_bv = OpLoad %v4uint %rt_bp']
for j in range(4):
    out += [f'{P}%rt_bu{j} = OpCompositeExtract %uint %rt_bv {j}', f'{P}%rt_bf{j} = OpBitcast %float %rt_bu{j}', f'{P}%rt_a{j} = OpFAdd %float {tot[j]} %rt_bf{j}',
            f'{P}%rt_m{j} = OpFMul %float %rt_a{j} %float_64', f'{P}%rt_r{j} = OpExtInst %float {GLSL} RoundEven %rt_m{j}', f'{P}%rt_i{j} = OpConvertFToS %uint %rt_r{j}']
out += [f'{P}%rt_vec = OpCompositeConstruct %v4uint %rt_i0 %rt_i1 %rt_i2 %rt_i3', f'{P}%rt_cl = OpExtInst %v4int {GLSL} SClamp %rt_vec {cl[1]} {cl[2]}', f'{P}%rt_uc = OpUConvert %v4uchar %rt_cl',
        f'{P}%rt_word = OpBitcast %uint %rt_uc', f'{P}%rt_sa = OpIAdd %uint {wbase} %rt_p', f'{P}%rt_sp = OpAccessChain {p0[2]} {heap} %uint_0 %rt_sa', f'{P}OpStore %rt_sp %rt_word']
out += L[t1:]
# channel order inside a word: our word 0 holds channels 0..3 in that order
first = re.search(r'OpCompositeConstruct %v4uint (%\w+) (%\w+) (%\w+) (%\w+)', '\n'.join(tail))
main = next(n for n, l in enumerate(out) if '= OpFunction' in l)
out[main:main] = [c for c in consts if c.split(' = ')[0] not in defs]
for c in ('%uint_1', '%uint_2', '%uint_3', '%float_64', f'%uint_{BIAS0}'):
    if c not in defs:
        die(f'{c} is not a constant of this shader')
sys.stdout.write('\n'.join(out))
