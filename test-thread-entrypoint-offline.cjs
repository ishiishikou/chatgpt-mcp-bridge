// Offline-only simulator: two independent thread entrypoint instances.
const assert=require("node:assert/strict");
const fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const html=fs.readFileSync(path.join(__dirname,"thread-entrypoint-v1.html"),"utf8");
const script=html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script);
for(const phrase of ["ui/message","tools/call","ui/update-model-context","fetch(",
                     "setTimeout(","setInterval(","XMLHttpRequest","WebSocket"]){
  assert.equal(script.includes(phrase),false,phrase);
}
function mount(seed){
 const out=[],nodes=new Map(),listeners={},docListeners={};
 const parent={postMessage(message){out.push(message)}};
 const document={getElementById(id){
   if(!nodes.has(id)) nodes.set(id,{textContent:""});
   return nodes.get(id);
 },addEventListener(type,callback){docListeners[type]=callback}};
 const window={parent,addEventListener(type,callback){listeners[type]=callback}};
 const MathCopy=Object.create(Math); MathCopy.random=()=>seed;
 vm.runInNewContext(script,{window,document,Date,Math:MathCopy,String,Object}, {timeout:1000});
 return {out,parent,document,nodes,listeners,docListeners,value:id=>nodes.get(id)?.textContent};
}
const a=mount(.123456), b=mount(.876543);
assert.notEqual(a.value("instance"),b.value("instance"));
assert.deepEqual(a.out.map(p=>p.method),["ui/initialize"]);
assert.deepEqual(b.out.map(p=>p.method),["ui/initialize"]);
const init={jsonrpc:"2.0",id:1,result:{
 protocolVersion:"2026-01-26",
 hostInfo:{name:"simulated-host",version:"1"},
 hostCapabilities:{},
 hostContext:{
  theme:"light",locale:"ja-JP",toolInfo:{id:123},
  "openai/deepLink":{url:"/private"}
 }
}};
a.listeners.message({source:a.parent,data:init});
b.listeners.message({source:b.parent,data:init});
assert.equal(a.value("init"),"成功");
assert.ok(a.value("context").includes("theme"));
assert.ok(!a.value("context").includes("/private"));
assert.ok(!a.value("context").includes("123"));
assert.deepEqual(a.out.map(p=>p.method),["ui/initialize","ui/notifications/initialized"]);
a.listeners.pageshow();
a.document.visibilityState="hidden";
a.docListeners.visibilitychange();
assert.equal(a.value("pages"),"1");
assert.equal(a.value("visibility"),"1");
assert.equal(b.value("pages"),"0");
a.listeners.message({source:a.parent,data:{jsonrpc:"2.0",id:97,method:"ui/resource-teardown"}});
assert.equal(a.value("teardown"),"受信");
assert.equal(a.out.length,3);
assert.equal(a.out[2].id,97);
assert.equal(b.out.length,2);
const error=mount(.55555);
error.listeners.message({source:error.parent,data:{jsonrpc:"2.0",id:1,error:{message:"denied"}}});
assert.equal(error.value("init"),"エラー");
assert.equal(error.out.length,1);

const malformed=mount(.4444);
malformed.listeners.message({source:malformed.parent,
  data:{jsonrpc:"2.0",id:1,result:{hostContext:{theme:"dark"}}}});
assert.equal(malformed.value("init"),"エラー");
assert.deepEqual(malformed.out.map(x=>x.method),["ui/initialize"]);

const invalidCapabilities=mount(.7777);
invalidCapabilities.listeners.message({source:invalidCapabilities.parent,
  data:{jsonrpc:"2.0",id:1,result:{
    protocolVersion:"2026-01-26",hostInfo:{name:"host"},hostCapabilities:[]
  }}});
assert.equal(invalidCapabilities.value("init"),"エラー");
assert.deepEqual(invalidCapabilities.out.map(x=>x.method),["ui/initialize"]);

for(const version of ["", "2025-01-01", "unsupported"]){
 const invalidVersion=mount(.7788);
 invalidVersion.listeners.message({source:invalidVersion.parent,
   data:{jsonrpc:"2.0",id:1,result:{
     protocolVersion:version,hostInfo:{name:"host"},hostCapabilities:{}
   }}});
 assert.equal(invalidVersion.value("init"),"エラー");
 assert.deepEqual(invalidVersion.out.map(x=>x.method),["ui/initialize"]);
}

const earlyTeardown=mount(.3333);
earlyTeardown.listeners.message({source:earlyTeardown.parent,
  data:{jsonrpc:"2.0",id:90,method:"ui/resource-teardown"}});
earlyTeardown.listeners.message({source:earlyTeardown.parent,data:init});
assert.equal(earlyTeardown.value("teardown"),"受信");
assert.equal(earlyTeardown.value("init"),"応答待ち");
assert.deepEqual(earlyTeardown.out.map(x=>x.method),["ui/initialize",undefined]);

for(const check of [a,b,error,malformed,invalidCapabilities,earlyTeardown]){
 assert.equal(check.out.some(p=>p.method==="ui/message"||p.method==="tools/call"),false);
}
console.log("PASS independent instance IDs, per-instance counters, and teardown");
console.log("PASS initialize and host context key-only display, no sensitive values");
console.log("PASS no sending, server tools, network, or timers");
console.log("PASS initialize failure safe");
