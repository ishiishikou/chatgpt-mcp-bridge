// Executes the actual HTML's JavaScript inside a no-network simulated host.
const assert=require("node:assert/strict");
const fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
const html=fs.readFileSync(path.join(__dirname,"thread-capabilities-20261114-v1.html"),"utf8");
const script=html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script,"embedded JavaScript must exist");
for (const keyword of ["ui/message","tools/call","fetch(","WebSocket","XMLHttpRequest",
 "setTimeout(","setInterval(","ui/update-model-context","EventSource"]) {
 assert.equal(script.includes(keyword),false,keyword);
}
const BUILD="capdiag-20261114-v1";
assert.ok(html.includes(BUILD));
function mount(seed){
 const out=[],nodes=new Map(),winEvents={},docEvents={};
 const parent={postMessage:(msg)=>out.push(msg)};
 const document={getElementById(id){if(!nodes.has(id))nodes.set(id,{textContent:""});
 return nodes.get(id);},addEventListener(type,fn){docEvents[type]=fn;}};
 const window={parent,addEventListener(type,fn){winEvents[type]=fn;}};
 const rand=Object.create(Math);rand.random=()=>seed;
 vm.runInNewContext(script,{window,document,Date,Math:rand,String,Object,Array},{timeout:1000});
 const send=data=>winEvents.message({source:parent,data});
 return {out,parent,document,winEvents,docEvents,get:id=>nodes.get(id)?.textContent,
  send,sendInit:data=>send({...data,id:out[0].id})};
}
function response(caps,context={private:"SECRET",theme:"dark"}){
 return {jsonrpc:"2.0",id:1,result:{protocolVersion:"2026-01-26",
  hostInfo:{name:"mock",version:"1"},hostCapabilities:caps,hostContext:context}};
}
const supported=mount(.12),other=mount(.91);
assert.notEqual(supported.get("instance"),other.get("instance"));
// The build label is static HTML content, not populated by JavaScript's mock DOM.
assert.ok(html.includes('<dd id="build">'+BUILD+'</dd>'));
supported.sendInit(response({experimental:{"openai/message":{}},message:{text:{}},serverTools:{}}));
assert.equal(supported.get("init"),"成功");
for(const id of ["extended","messageText","serverTools"])assert.equal(supported.get(id),"広告あり");
assert.ok(supported.get("context").includes("private"));
assert.ok(!supported.get("context").includes("SECRET"));
assert.equal(supported.out.length,2);
const absent=mount(.2);
absent.sendInit(response({}));
assert.equal(absent.get("extended"),"広告なし");
assert.equal(absent.get("messageText"),"広告なし");
assert.equal(absent.get("serverTools"),"広告なし");
const malformed=mount(.3);
malformed.sendInit(response([]));
assert.equal(malformed.get("init"),"エラー");
assert.equal(malformed.out.length,1);
const badMessage=mount(.4);
badMessage.sendInit(response({experimental:{"openai/message":true},serverTools:1}));
assert.equal(badMessage.get("init"),"エラー");
assert.equal(badMessage.get("extended"),undefined);
assert.equal(badMessage.get("serverTools"),undefined);
const error=mount(.5);
error.sendInit({jsonrpc:"2.0",id:1,error:{code:-1,message:"secret-private"}});
assert.equal(error.get("init"),"エラー");
assert.equal(error.out.length,1);
assert.ok(!JSON.stringify(error.out).includes("secret-private"));
assert.ok(!JSON.stringify([...error.out, error.get("init"), error.get("extended")]).includes("secret-private"));
const early=mount(.6);
early.send({jsonrpc:"2.0",id:22,method:"ui/resource-teardown"});
early.sendInit(response({experimental:{"openai/message":{}}}));
assert.equal(early.get("teardown"),"受信");
// These are static HTML defaults; no JavaScript write occurs after early teardown.
assert.equal(early.get("init"),undefined);
assert.equal(early.get("extended"),undefined);
assert.equal(early.out.length,2);
supported.winEvents.pageshow();
supported.docEvents.visibilitychange();
assert.equal(supported.get("pages"),"1");assert.equal(supported.get("visibility"),"1");
for(let i=0;i<10;i++){
 supported.sendInit(response({experimental:{"openai/message":{}}}));
 supported.winEvents.pageshow();
}
assert.equal(supported.out.length,2);
supported.send({jsonrpc:"2.0",id:77,method:"ui/resource-teardown"});
assert.equal(supported.out.length,3);
assert.equal(supported.out[2].id,77);
supported.sendInit(response({}));
assert.equal(supported.out.length,3);
for(const item of [supported,other,absent,malformed,badMessage,error,early]){
 assert.ok(item.out.every(x=>!["ui/message","tools/call","ui/update-model-context"].includes(x.method)));
}

// Cross-mount replay must not initialize another mounted iframe.
const replay=mount(.99);
replay.send({...response({experimental:{"openai/message":{}}}),id:supported.out[0].id});
assert.equal(replay.out.length,1);
assert.equal(replay.get("init"),undefined);
// Host-originated requests that collide with the pending ID are not responses.
replay.send({jsonrpc:"2.0",id:replay.out[0].id,method:"host-request",params:{}});
assert.equal(replay.out.length,1);
// Ignore messages from sources other than the parent.
replay.winEvents.message({source:{},data:{...response({}),id:replay.out[0].id}});
assert.equal(replay.out.length,1);
replay.sendInit(response({experimental:{"openai/message":{}}}));
assert.equal(replay.get("init"),"成功");

const badContainers=mount(.71);
badContainers.sendInit(response({experimental:[]}));
assert.equal(badContainers.get("init"),"エラー");
const missingVersion=mount(.72);
const versionReply=response({});
delete versionReply.result.hostInfo.version;
missingVersion.sendInit(versionReply);
assert.equal(missingVersion.get("init"),"エラー");

const badTeardown=mount(.73);
badTeardown.send({jsonrpc:"2.0",id:{bad:true},method:"ui/resource-teardown"});
assert.equal(badTeardown.out.length,1);
badTeardown.sendInit(response({}));
assert.equal(badTeardown.get("init"),"成功");
badTeardown.send({jsonrpc:"2.0",id:13,method:"ui/resource-teardown"});
assert.equal(badTeardown.out.length,3);
badTeardown.send({jsonrpc:"2.0",id:13,method:"ui/resource-teardown"});
assert.equal(badTeardown.out.length,3);
badTeardown.winEvents.pageshow();
badTeardown.docEvents.visibilitychange();
assert.equal(badTeardown.get("pages"),undefined);
assert.equal(badTeardown.get("visibility"),undefined);

// Verify an exact allowlisted RPC envelope, not only an absence of forbidden names.
for(const item of [supported,other,absent,malformed,badMessage,error,early,
                   replay,badContainers,missingVersion,badTeardown]){
 assert.equal(item.out[0].jsonrpc,"2.0");
 assert.equal(item.out[0].method,"ui/initialize");
 assert.ok(item.out[0].id.startsWith("capdiag-init-"));
 assert.equal(item.out[0].params.protocolVersion,"2026-01-26");
 let notifications=0,acks=0;
 for(const out of item.out.slice(1)){
   assert.equal(out.jsonrpc,"2.0");
   if(out.method==="ui/notifications/initialized"){
     assert.equal(out.id,undefined);
     notifications++;
   }else{
     assert.equal(out.method,undefined);
     assert.equal(typeof out.id==="string"||typeof out.id==="number",true);
     assert.equal(JSON.stringify(out.result),"{}");
     assert.deepEqual(Object.keys(out).sort(),["id","jsonrpc","result"]);
     acks++;
   }
 }
 assert.ok(notifications<=1);
 assert.ok(acks<=1);
}
console.log("PASS exact allowlisted outgoing JSON-RPC envelopes");
console.log("PASS versioned build and independent iframe markers");
console.log("PASS capability advertisement/absence/malformed/host error");
console.log("PASS lifecycle, late initialize, duplicate event, no message/draft/network");