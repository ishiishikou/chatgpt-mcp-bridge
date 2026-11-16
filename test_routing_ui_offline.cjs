"use strict";
// Offline only: VM-hosted HTML, an in-memory ledger and a shared virtual clock.
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const crypto=require("node:crypto");
const code=mode=>fs.readFileSync(__dirname+"/routing-auto-"+mode+"-v1.html","utf8").match(/<script>([\s\S]*?)<\/script>/)[1];
const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};

class VirtualClock {
 constructor(){this.now=0;this.seq=0;this.timers=new Map();}
 setTimeout(fn,delay){const id=++this.seq;this.timers.set(id,{fn,time:this.now+delay});return id;}
 clearTimeout(id){this.timers.delete(id);}
 // Wall time passes while the host suspends JS timers. No callbacks run here.
 elapse(ms){this.now+=ms;}
 async advance(ms){
  await flush();
  const end=this.now+ms;
  for(let loops=0;loops<10000;loops++){
   const next=[...this.timers.entries()].filter(([,t])=>t.time<=end).sort((a,b)=>a[1].time-b[1].time||a[0]-b[0])[0];
   if(!next){this.now=end;await flush();return;}
   this.now=Math.max(this.now,next[1].time);
   this.timers.delete(next[0]);next[1].fn();await flush();
  }
  throw Error("virtual timer loop did not settle");
 }
}

class MockStore {
 constructor(clock){this.clock=clock;this.runs=new Map();this.lastClaim=new Map();}
 claim(args){
  const key=[args.mode,args.device,args.phase].join(":");
  if(this.runs.has(args.run_id))return {granted:false,reason:"already_claimed"};
  const last=this.lastClaim.get(key);
  const remaining=last===undefined?0:30000-(this.clock.now-last);
  if(remaining>0)return {granted:false,reason:"cooldown",retry_after_seconds:Math.ceil(remaining/1000)};
  const token=(this.runs.size+1).toString(16).padStart(64,"0");
  this.runs.set(args.run_id,{...args,claim_token:token,outcome:"unknown",reported:false});
  this.lastClaim.set(key,this.clock.now);
  return {granted:true,claim_token:token,run_id:args.run_id};
 }
 report({claim_token,outcome}){
  const run=[...this.runs.values()].find(r=>r.claim_token===claim_token);
  if(!run||run.reported)return {recorded:false};
  run.outcome=outcome;run.reported=true;return {recorded:true};
 }
}

function createHarness(mode,{
 ua="Mozilla/5.0 (iPhone; CPU iPhone OS 26_6 like Mac OS X)",platform="iPhone",
 hidden=false,hostCaps=true,clock=new VirtualClock(),store=new MockStore(clock),
 claimReply="normal",messageReply={result:{}},reportReply="normal",randomId=crypto.randomUUID()
}={}){
 let visible=hidden?"hidden":"visible";
 const origin=clock.now;
 const sent=[],logs=[],claims=[],reports=[],requests=[],heldClaims=[],heldMessages=[];
 const listeners={},winListeners={},nodes=new Map();
 const getElementById=id=>{if(!nodes.has(id))nodes.set(id,{textContent:""});return nodes.get(id);};
 const doc={get visibilityState(){return visible;},getElementById,documentElement:{},addEventListener(name,cb){listeners[name]=cb;}};
 const deliver=data=>Promise.resolve().then(()=>winListeners.message({source:host,data:{jsonrpc:"2.0",...data}}));
 const host={postMessage(data){
  if(data.method===undefined||data.method==="ui/notifications/initialized")return;
  requests.push(data);
  let result={};
  if(data.method==="ui/initialize"){
   result={hostCapabilities:hostCaps===true?{
    serverTools:{},message:{text:true},experimental:{"openai/message":{}}
   }:hostCaps||{}};
  }else if(data.method==="tools/call"){
   const {name,arguments:args}=data.params;
   if(name==="claim_routing_probe"){
    const grant=store.claim(args);
    claims.push({...args,...grant});
    result={content:[{type:"text",text:JSON.stringify(grant)}]};
    if(claimReply!=="normal"){
     if(claimReply==="hold")heldClaims.push({id:data.id,result});
     return;
    }
   }else if(name==="record_routing_event"){
    logs.push(args);result={content:[{type:"text",text:JSON.stringify({recorded:true})}]};
   }else if(name==="report_routing_probe"){
    reports.push(args);
    result={content:[{type:"text",text:JSON.stringify(store.report(args))}]};
    if(reportReply==="drop")return;
   }else{throw Error("unknown tool "+name);}
  }else if(data.method==="ui/message"){
   sent.push(data.params);
   if(messageReply.throw)throw new Error("simulated disconnected transport");
   if(messageReply.hold){heldMessages.push({id:data.id});return;}
   if(messageReply.drop)return;
   void deliver({id:data.id,...messageReply});return;
  }else{throw Error("unexpected method "+data.method);}
  void deliver({id:data.id,result});
 }};
 const runtime={
  window:{parent:host,addEventListener(name,cb){winListeners[name]=cb;}},
  document:doc,navigator:{userAgent:ua,platform,maxTouchPoints:0},
  crypto:{randomUUID:()=>randomId},performance:{now:()=>clock.now-origin},
  Date:class extends Date {constructor(...args){super(...(args.length?args:[Date.UTC(2026,10,16)+clock.now]));}},
  setTimeout:(fn,delay)=>clock.setTimeout(fn,delay),clearTimeout:id=>clock.clearTimeout(id),
  IntersectionObserver:class {constructor(cb){this.cb=cb;}observe(){this.cb([{isIntersecting:true,intersectionRatio:1}]);}disconnect(){}}
 };
 vm.runInNewContext(code(mode),runtime,{timeout:5000});
 async function setHidden(value){
  const next=value?"hidden":"visible";
  if(next!==visible){visible=next;listeners.visibilitychange();}
  await flush();
 }
 async function pagehide(){winListeners.pagehide();await flush();}
 async function teardown(){
  winListeners.message({source:host,data:{jsonrpc:"2.0",method:"ui/resource-teardown",id:414}});
  await flush();
 }
 async function releaseClaim(){assert.equal(heldClaims.length,1);await deliver(heldClaims.shift());await flush();}
 async function releaseMessage(reply){assert.equal(heldMessages.length,1);await deliver({...heldMessages.shift(),...reply});await flush();}
 return {clock,store,runId:randomId,advance:ms=>clock.advance(ms),setHidden,pagehide,teardown,releaseClaim,releaseMessage,
  sent,claims,logs,reports,requests,status:()=>getElementById("status").textContent,
  localLog:()=>getElementById("log").textContent};
}

let scenarios=0;
async function scenario(name,test){
 try{await test();scenarios++;}catch(error){error.message=name+": "+error.message;throw error;}
}
function assertOutcome(h,outcome){
 assert.equal(h.sent.length,1,"one attempt");assert.equal(h.reports.length,1,"one result report");
 assert.equal(h.reports[0].outcome,outcome);
 assert.equal(h.store.runs.get(h.runId).outcome,outcome);
}
function assertSkipped(h){
 assert.equal(h.sent.length,0);assert.equal(h.reports.length,1);
 assert.equal(h.reports[0].outcome,"skipped");
 assert.equal(h.store.runs.get(h.runId).outcome,"skipped");
 assert(h.logs.some(e=>e.event==="skipped"));
}

(async()=>{
 await scenario("ledger: permanent Run ID, 29.999s / 30s cooldown, phase/mode/OS independence",async()=>{
  const clock=new VirtualClock(),store=new MockStore(clock);
  const args={mode:"active",device:"iOS",phase:"visible",run_id:crypto.randomUUID()};
  assert.equal(store.claim(args).granted,true);
  const next={...args,run_id:crypto.randomUUID()};
  clock.elapse(29999);
  assert.equal(store.claim(next).reason,"cooldown");
  assert.equal(store.claim({...next,run_id:crypto.randomUUID()}).retry_after_seconds,1);
  clock.elapse(1);
  assert.equal(store.claim(next).granted,true,"denied Run ID remains eligible");
  for(const mode of ["active","new"])for(const device of ["iOS","Windows"])for(const phase of ["visible","hidden"]){
   assert.equal(store.claim({...args,mode,device,phase}).reason,"already_claimed");
   if(mode==="active"&&device==="iOS"&&phase==="visible")continue;
   assert.equal(store.claim({...args,mode,device,phase,run_id:crypto.randomUUID()}).granted,true);
  }
  clock.elapse(86400000);
  assert.equal(store.claim(args).reason,"already_claimed");
 });
 for(const mode of ["active","new"]){
  for(const device of ["iOS","Windows"])for(const hidden of [false,true]){
   await scenario(`${mode} ${device} initially hidden=${hidden}: 15 seconds, routing and fixed payload`,async()=>{
    const h=createHarness(mode,{hidden,...(device==="Windows"?{ua:"Windows NT 10.0",platform:"Win32"}:{})});
    await h.advance(14999);assert.equal(h.sent.length,0);assert.equal(h.claims.length,0);
    await h.advance(1);assert.equal(h.claims.length,1);assertOutcome(h,"host_accepted");
    assert.equal(h.claims[0].phase,hidden?"hidden":"visible");assert.equal(h.claims[0].device,device);
    assert.equal(h.sent[0]._meta["openai/message"].target,mode);assert.equal(h.sent[0]._meta["openai/message"].send,true);
    assert(h.sent[0].content[0].text.includes(h.runId));
    await h.advance(90000);assertOutcome(h,"host_accepted");
   });
  }
  await scenario(`${mode}: remount burst, fresh run after cooldown, original run never regrants`,async()=>{
   const clock=new VirtualClock(),store=new MockStore(clock);
   const a=createHarness(mode,{clock,store}),b=createHarness(mode,{clock,store});
   await clock.advance(15000);assert.equal(a.sent.length+b.sent.length,1);assert.equal(b.claims[0].reason,"cooldown");
   await clock.advance(15000);
   const c=createHarness(mode,{clock,store});
   await clock.advance(15000);assertOutcome(c,"host_accepted");
   const replay=createHarness(mode,{clock,store,randomId:a.runId,hidden:true});
   await clock.advance(60000);assert.equal(replay.sent.length,0);assert.equal(replay.claims[0].reason,"already_claimed");
  });
  await scenario(`${mode}: capabilities unavailable`,async()=>{
   const h=createHarness(mode,{hostCaps:false});await h.advance(60000);
   assert.equal(h.sent.length,0);assert.equal(h.claims.length,0);
  });
  await scenario(`${mode}: object capability supported`,async()=>{
   const h=createHarness(mode,{hostCaps:{serverTools:{},message:{text:{}},experimental:{"openai/message":{}}}});
   await h.advance(15000);assertOutcome(h,"host_accepted");
  });
  for(const event of ["pagehide","teardown"]){
   await scenario(`${mode}: ${event} before timer and after return`,async()=>{
    const h=createHarness(mode);await h.advance(1000);await h.setHidden(true);await h[event]();
    h.clock.elapse(60000);await h.setHidden(false);await h.advance(60000);
    assert.equal(h.sent.length,0);assert.equal(h.claims.length,0);assert(h.logs.some(e=>e.event==="skipped"));
   });
   await scenario(`${mode}: ${event} while awaiting committed claim`,async()=>{
    const h=createHarness(mode,{claimReply:"hold"});await h.advance(15000);
    assert.equal(h.claims[0].granted,true);await h[event]();await h.releaseClaim();
    await h.setHidden(true);await h.setHidden(false);await h.advance(60000);
    assert.equal(h.sent.length,0);assert(h.logs.some(e=>e.event==="skipped"));
    assert(h.store.runs.has(h.runId),"claim stays consumed");
    if(event==="pagehide")assertSkipped(h);
    else {assert.equal(h.reports.length,0);assert.equal(h.store.runs.get(h.runId).outcome,"unknown");}
    const replay=createHarness(mode,{clock:h.clock,store:h.store,randomId:h.runId,hidden:true});
    await replay.advance(15000);assert.equal(replay.claims[0].reason,"already_claimed");assert.equal(replay.sent.length,0);
   });
  }
  for(const hidden of [false,true])for(const roundTrip of [false,true]){
   await scenario(`${mode}: claim phase transition hidden=${hidden}, roundTrip=${roundTrip}`,async()=>{
    const h=createHarness(mode,{hidden,claimReply:"hold"});await h.advance(15000);
    await h.setHidden(!hidden);if(roundTrip)await h.setHidden(hidden);
    await h.releaseClaim();assertSkipped(h);await h.advance(60000);assertSkipped(h);
   });
  }
  await scenario(`${mode}: P2 reproduction: visible claim changes to hidden, independent hidden claim`,async()=>{
   const clock=new VirtualClock(),store=new MockStore(clock);
   const a=createHarness(mode,{clock,store,claimReply:"hold"});await clock.advance(15000);
   await a.setHidden(true);await a.releaseClaim();assertSkipped(a);
   const b=createHarness(mode,{clock,store,hidden:true});await clock.advance(15000);
   assertOutcome(b,"host_accepted");assert.equal(a.sent.length+b.sent.length,1,"only one hidden attempt within 30 seconds");
  });
  await scenario(`${mode}: background before deadline does not cancel hidden trial`,async()=>{
   const h=createHarness(mode);await h.advance(1000);await h.setHidden(true);await h.advance(14000);
   assertOutcome(h,"host_accepted");assert.equal(h.claims[0].phase,"hidden");
   await h.setHidden(false);await h.advance(60000);assertOutcome(h,"host_accepted");
  });
  await scenario(`${mode}: suspended hidden timer can fire on return`,async()=>{
   const h=createHarness(mode);await h.advance(1000);await h.setHidden(true);h.clock.elapse(60000);
   assert.equal(h.sent.length,0);assert.equal(h.claims.length,0);
   await h.setHidden(false);assert.equal(h.sent.length,0,"no callback has resumed yet");
   await h.advance(0);assertOutcome(h,"host_accepted");assert.equal(h.claims[0].phase,"visible");
   await h.advance(60000);assertOutcome(h,"host_accepted");
  });
  await scenario(`${mode}: suspended timer can resume while still hidden`,async()=>{
   const h=createHarness(mode,{hidden:true});await h.advance(1000);h.clock.elapse(60000);
   assert.equal(h.sent.length,0);await h.advance(0);assertOutcome(h,"host_accepted");assert.equal(h.claims[0].phase,"hidden");
  });
  await scenario(`${mode}: lost claim response consumes run without message or retry`,async()=>{
   const h=createHarness(mode,{claimReply:"drop"});await h.advance(27000);
   assert.equal(h.sent.length,0);assert.equal(h.claims.length,1);assert.equal(h.claims[0].granted,true);
   assert.equal(h.store.runs.get(h.runId).outcome,"unknown");
   await h.setHidden(true);await h.setHidden(false);await h.advance(60000);
   assert.equal(h.claims.length,1);assert.equal(h.sent.length,0);
   const replay=createHarness(mode,{clock:h.clock,store:h.store,randomId:h.runId});
   await replay.advance(15000);assert.equal(replay.claims[0].reason,"already_claimed");
  });
  await scenario(`${mode}: late claim response after timeout cannot authorize sending`,async()=>{
   const h=createHarness(mode,{claimReply:"hold"});await h.advance(27000);await h.releaseClaim();await h.advance(60000);
   assert.equal(h.sent.length,0);assert.equal(h.claims.length,1);assert.equal(h.reports.length,0);
  });
  for(const errorCode of [-32600,-32601,-32602,-32603,-32000,12345,"-32602",undefined]){
   await scenario(`${mode}: JSON-RPC code ${errorCode} classification and privacy`,async()=>{
    const secret="PRIVATE_HOST_ERROR_SENTINEL";
    const h=createHarness(mode,{messageReply:{error:{code:errorCode,message:secret+" target not supported",data:{private:secret}}}});
    await h.advance(15000);
    assertOutcome(h,[-32600,-32601,-32602].includes(errorCode)?"host_rejected":"unknown");
    assert(!JSON.stringify({logs:h.logs,reports:h.reports,requests:h.requests,status:h.status(),local:h.localLog()}).includes(secret));
    for(const entry of h.logs)assert.deepEqual(Object.keys(entry).sort(),["device","elapsed_ms","event","mode","run_id","visibility"]);
    await h.advance(60000);assert.equal(h.sent.length,1);assert.equal(h.reports.length,1);
   });
  }
  for(const [name,messageReply] of [["generic isError",{result:{isError:true}}],["missing result",{}],["transport disconnect",{throw:true}],["message timeout",{drop:true}]]){
   await scenario(`${mode}: ${name} stays unknown without retry`,async()=>{
    const h=createHarness(mode,{messageReply});await h.advance(27000);assertOutcome(h,"unknown");
    await h.setHidden(true);await h.setHidden(false);await h.advance(60000);assertOutcome(h,"unknown");
   });
  }
  await scenario(`${mode}: late message success does not overwrite timeout`,async()=>{
   const h=createHarness(mode,{messageReply:{hold:true}});await h.advance(27000);assertOutcome(h,"unknown");
   await h.releaseMessage({result:{}});await h.advance(60000);assertOutcome(h,"unknown");
  });
  await scenario(`${mode}: teardown during message wait leaves unknown, never retries`,async()=>{
   const h=createHarness(mode,{messageReply:{hold:true}});await h.advance(15000);await h.teardown();
   await h.releaseMessage({result:{}});await h.advance(60000);
   assert.equal(h.sent.length,1);assert.equal(h.reports.length,0);assert.equal(h.store.runs.get(h.runId).outcome,"unknown");
  });
  await scenario(`${mode}: lost result-report acknowledgement does not retry`,async()=>{
   const h=createHarness(mode,{reportReply:"drop"});await h.advance(60000);assertOutcome(h,"host_accepted");
   assert.match(h.status(),/結果記録は不明/);
  });
 }
 console.log("PASS offline mock host UI scenarios: "+scenarios);
})().catch(error=>{console.error("FAIL",error);process.exitCode=1;});
