#pragma once
#include <algorithm>
#include <cstdint>
#include <stdexcept>
#include <vector>
namespace fsr4 {
// The 4.1.1 prepass reduces each 2x2 quad into one packed tensor element.
// Its postpass already leaves an unmatched odd output row/column untouched.
// Do not enter the reduction with inactive quad partners: those shuffle values
// are undefined. Complete quads retain exactly the original arithmetic;
// incomplete edge tensors retain the zero from the per-frame scratch clear.
// Matching is structural (opcode shapes + type/position relations), never by
// baked-in numeric ids, so reassembled modules (e.g. exact rewrites such as
// the routed prepass, whose ids spirv-as compacts differently) are accepted
// exactly like the stock shader. The emitted patch is byte-identical to the
// original id-pinned version on the stock module.
inline void guard_fsr411_prepass_quads(std::vector<uint32_t>& w) {
    const auto fail=[] {throw std::runtime_error("unsupported 4.1.1 prepass quad-bounds contract");};
    if(w.size()<5 || w[0]!=0x07230203 || w[3]>0xffffff00u)fail();
    struct Ins { size_t off; uint32_t op; };
    std::vector<Ins> ins;
    for(size_t i=5;i<w.size();) {
        auto n=w[i]>>16,op=w[i]&65535;
        if(!n || n>w.size()-i)fail();
        ins.push_back({i,op});
        i+=n;
    }
    const auto words=[&](size_t off){ return w.data()+off; };
    bool local=false,quad=false;
    size_t function=0,first_quad=0;
    for(const auto& in:ins) {
        const auto* d=words(in.off);
        const auto n=d[0]>>16;
        if(in.op==16 && n==6 && d[2]==17 && d[3]==256 && d[4]==1 && d[5]==1)local=true;
        if(in.op==54 && !function)function=in.off;
        if(in.op==366 && !first_quad)first_quad=in.off;
        if(in.op==366)quad=true;
    }
    if(!local || !quad || !function || !first_quad)fail();
    // uint type, v4uint vector types, and v4uint loads (memory operands allowed).
    uint32_t uint_type=0;
    for(const auto& in:ins) {
        const auto* d=words(in.off);
        if(in.op==21 && (d[0]>>16)==4 && d[2]==32 && d[3]==0){ uint_type=d[1]; break; }
    }
    if(!uint_type)fail();
    std::vector<uint32_t> vec4;
    for(const auto& in:ins) {
        const auto* d=words(in.off);
        if(in.op==23 && (d[0]>>16)==4 && d[2]==uint_type && d[3]==4)vec4.push_back(d[1]);
    }
    if(vec4.empty())fail();
    std::vector<uint32_t> loads;
    for(const auto& in:ins) {
        const auto* d=words(in.off);
        const auto n=d[0]>>16;
        if(in.op==61 && (n==4 || n==5 || n==6) &&
           std::find(vec4.begin(),vec4.end(),d[1])!=vec4.end())loads.push_back(d[2]);
    }
    struct Ext { size_t off; uint32_t id,comp,idx,type; };
    std::vector<Ext> exts;
    for(const auto& in:ins) {
        const auto* d=words(in.off);
        if(in.op==81 && (d[0]>>16)==5 && (d[4]==0 || d[4]==1) &&
           std::find(loads.begin(),loads.end(),d[3])!=loads.end())exts.push_back({in.off,d[2],d[3],d[4],d[1]});
    }
    // ULessThan(bool, bound, extent) before the first quad swap, with exactly
    // one operand an extent extract. The x check (idx 0) precedes the y check.
    struct Check { size_t off; uint32_t ext,type; };
    std::vector<Check> checks;
    for(const auto& in:ins) {
        const auto* d=words(in.off);
        if(in.op!=176 || (d[0]>>16)!=5 || in.off>=first_quad)continue;
        int count=0; const Ext* found=nullptr;
        for(const auto& e:exts) if(d[3]==e.id || d[4]==e.id){ ++count; found=&e; }
        if(count==1)checks.push_back({in.off,found->id,found->type});
    }
    if(checks.size()!=2)fail();
    const Ext* xe=nullptr,*ye=nullptr;
    for(const auto& e:exts) if(e.id==checks[0].ext)xe=&e;
    for(const auto& e:exts) if(e.id==checks[1].ext)ye=&e;
    if(!xe || !ye || xe->idx!=0 || ye->idx!=1 || xe->comp!=ye->comp || checks[0].type!=checks[1].type)fail();
    const uint32_t u=checks[0].type;
    const auto mask=w[3]++,width=w[3]++,height=w[3]++;
    std::vector<uint32_t> out(w.begin(),w.begin()+5);
    for(size_t i=5;i<w.size();) {
        auto n=w[i]>>16;
        if(i==function)out.insert(out.end(),{(4u<<16)|43,u,mask,0xfffffffeu});
        if(i==checks[0].off || i==checks[1].off) {
            const bool isx=i==checks[0].off;
            out.insert(out.end(),{(5u<<16)|199,u,isx?width:height,isx?checks[0].ext:checks[1].ext,mask});
        }
        const auto start=out.size();out.insert(out.end(),w.begin()+i,w.begin()+i+n);
        if(i==checks[0].off || i==checks[1].off)out[start+4]=i==checks[0].off?width:height;
        i+=n;
    }
    w=std::move(out);
}
}
