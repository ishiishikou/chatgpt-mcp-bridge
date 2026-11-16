"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const crypto=require("node:crypto");
const root=__dirname;
const code=mode=>fs.readFileSync(root+"/routing-auto-"+mode+"-v1.html","utf8").match(/<script>([\s\S]*?)<\/script>/)[1];

function createHarness(mode,{ua="Mozilla/5.0 (iPhone; CPU iPhone OS 26_6 like Mac OS X)",platform="iPhone",hidden=false,hostCaps=true,claimSet=new Set(),hostResult={},randomId=crypto.randomUUID()}={}){
 let time=0,seq=0,visible=hidden?"hidden":"visible",tornDown=false;
 let sent=[],logs=[],claims=[],timeouts=new Map(),listeners={},winListeners={};
 const nodes=new Map();
 const getElementById=id=>{if(!nodes.has(id))nodes.set(id,{textContent:""});return nodes.get(id)};
 const doc={get visibilityState(){return visible;},getElementById,documentElement:{},addEventListener(name,cb){listeners[name]=cb;}};
 const host={postMessage(data){
   if(data.method===undefined)return; // teardown acknowledgement
   if(data.method==="ui/notifications/initialized")return;
   let result={};
   if(data.method==="ui/initialize"){
     result={hostCapabilities:hostCaps?{
       serverTools:{},message:{text:true},experimental:{"openai/message":{}}
     }:{}};
   } else if(data.method==="tools/call"){
     const {name,arguments:args}=data.params;
     if(name==="claim_routing_probe"){
       const key=[args.mode,args.device,args.phase].join(":");
       let granted=!claimSet.has(key); if(granted)claimSet.add(key);
       claims.push({...args,granted});
       result={content:[{type:"text",text:JSON.stringify(granted?{granted:true,claim_token:"a".repeat(64),run_id:args.run_id}:{granted:false,reason:"already_claimed"})}]};
     }else if(name==="record_routing_event"){
       logs.push(args);result={content:[{type:"text",text:JSON.stringify({recorded:true})}]};
     }else if(name==="report_routing_probe"){
       result={content:[{type:"text",text:JSON.stringify({recorded:true})}]};
     }else{throw Error("unknown tool "+name)}
   }else if(data.method==="ui/message"){
     sent.push(data.params);result=hostResult;
   } else {throw Error("unexpected method "+data.method)}
   Promise.resolve().then(()=>winListeners.message({source:host,data:{jsonrpc:"2.0",id:data.id,result}}));
 }};
 const runtime={
    window:{parent:host,addEventListener(name,cb){winListeners[name]=cb}},
    document:doc, navigator:{userAgent:ua,platform,maxTouchPoints:0},
    crypto:{randomUUID:()=>randomId},
    performance:{now:()=>time},
    Date,Math,Map,Promise,Error,JSON,Number,String,Array,Object,
    setTimeout(fn,delay){const k=++seq;timeouts.set(k,{fn,time:time+delay});return k},
    clearTimeout(k){timeouts.delete(k)},
    IntersectionObserver:class {constructor(cb){this.cb=cb;}observe(){this.cb([{isIntersecting:true,intersectionRatio:1}]);}disconnect(){}}
 };
 vm.runInNewContext(code(mode),runtime,{timeout:5000});
 async function advance(ms){
   const end=time+ms;
   for(let loops=0;loops<2000;loops++){
     await Promise.resolve();await Promise.resolve();await Promise.resolve();
     const next=[...timeouts.entries()].filter(([_,t])=>t.time<=end).sort((a,b)=>a[1].time-b[1].time)[0];
     if(!next)break;
     time=next[1].time;timeouts.delete(next[0]);next[1].fn();
   }
   time=end;
   for(let i=0;i<25;i++)await Promise.resolve();
 }
 async function setHidden(v){visible=v?"hidden":"visible";listeners.visibilitychange();await advance(0)}
 async function teardown(){tornDown=true;winListeners.message({source:host,data:{jsonrpc:"2.0",method:"ui/resource-teardown",id:414}});await advance(0)}
 return {advance,setHidden,teardown,sent,claims,logs,status:()=>getElementById("status").textContent};
}
(async()=>{
 let n=0;
 for(const mode of ["active","new"]){
  for(const hidden of [false,true]){
   const h=createHarness(mode,{hidden});
   await h.advance(16000);
   assert.equal(h.claims.length,1,mode+" hidden="+hidden+" claim");
   assert.equal(h.sent.length,1,mode+" hidden="+hidden+" sent");
   assert.equal(h.claims[0].phase,hidden?"hidden":"visible");
   assert.equal(h.sent[0]._meta["openai/message"].target,mode);
   assert.match(h.sent[0].content[0].text,/Run ID/);
   n++;
  }
 }
 {
  const seen=new Set(), a=createHarness("new",{claimSet:seen}), b=createHarness("new",{claimSet:seen});
  await Promise.all([a.advance(16000),b.advance(16000)]);
  assert.equal(a.sent.length+b.sent.length,1,"re-render allows just one");
  n++;
 }
 {
  const h=createHarness("active",{hostCaps:false});
  await h.advance(16000);assert.equal(h.sent.length,0);assert.equal(h.claims.length,0);n++;
 }
 {
  const h=createHarness("active");
  await h.advance(1000);await h.teardown();await h.advance(16000);
  assert.equal(h.sent.length,0);n++;
 }
 {
  const h=createHarness("new");
  await h.advance(1000);await h.setHidden(true);await h.advance(16000);
  assert.equal(h.sent.length,1);assert.equal(h.claims[0].phase,"hidden");n++;
 }
 {
  const h=createHarness("active");
  await h.advance(1000);h.setHidden(true);await h.teardown();await h.advance(16000);
  assert.equal(h.sent.length,0);n++;
 }
 console.log("PASS offline mock host UI scenarios: "+n);
})().catch(e=>{console.error("FAIL",e);process.exitCode=1});
