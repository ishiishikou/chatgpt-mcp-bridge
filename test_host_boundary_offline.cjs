// Offline contract tests for the *real* guarded widget.
// Never opens a browser, calls an MCP server, or changes the production ledger.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const htmlFile = fs.existsSync(path.join(__dirname, "guarded-probe.html"))
  ? path.join(__dirname, "guarded-probe.html")
  : path.join(__dirname, "..", "..", "guarded-probe.html");
const html = fs.readFileSync(htmlFile, "utf8");
const match = html.match(/<script>([\s\S]*?)<\/script>/);
assert.ok(match, "guarded script exists");
const source = match[1];

function newHostView() {
  const responses = new Map();
  const timers = new Map();
  const outgoing = [];
  let seq = 0;
  const host = {
    postMessage(payload) { outgoing.push(payload); }
  };
  const status = {textContent: ""};
  const listeners = new Map();
  const window = {
    parent: host,
    addEventListener(name, callback) { listeners.set(name, callback); }
  };
  const document = {getElementById(id) {
    assert.equal(id,"status"); return status;
  }};
  const setTimeout = (fn, delay) => {
    const key = ++seq;
    timers.set(key, {fn,delay});
    return key;
  };
  const clearTimeout = key => timers.delete(key);
  vm.runInNewContext(source, {
    window, document, setTimeout, clearTimeout, Promise, Map, String, Error,
  }, {timeout:1000});
  function event(data, sourceOverride = host) {
    listeners.get("message")({source:sourceOverride,data});
  }
  function reply(request, result, error) {
    event({jsonrpc:"2.0",id:request.id,...(error?{error}:{result})});
  }
  function toolReply(request, obj, isError = false) {
    reply(request, {isError,content:[{type:"text",text:JSON.stringify(obj)}]});
  }
  function tickOne(delay) {
    for(const [id,t] of timers) {
      if(t.delay === delay) {
        timers.delete(id);
        t.fn();
        return true;
      }
    }
    return false;
  }
  return {outgoing,status,timers,event,reply,toolReply,tickOne};
}
const turn = () => new Promise(resolve => setImmediate(resolve));
function countMessages(host, method) {
  return host.outgoing.filter(x=>x.method===method).length;
}

(async()=>{
  // 1: Unauthorized or unsatisfied host capability must NOT dispatch.
  const denied = newHostView();
  assert.equal(denied.outgoing[0].method,"ui/initialize");
  denied.reply(denied.outgoing[0], {hostCapabilities:{message:{text:true}}});
  await turn();
  assert.equal(countMessages(denied,"tools/call"),0);
  assert.equal(countMessages(denied,"ui/message"),0);
  console.log("PASS missing serverTools: no claim, no send");

  // 2: Spoofed message from another window cannot initialize the widget.
  const spoofed = newHostView();
  spoofed.event({jsonrpc:"2.0",id:1,result:{
    hostCapabilities:{message:{text:true},serverTools:{}}
  }}, {notTheParent:true});
  await turn();
  assert.equal(countMessages(spoofed,"ui/notifications/initialized"),0);
  assert.equal(countMessages(spoofed,"tools/call"),0);
  console.log("PASS unrelated window: ignored");

  // 3: Teardown before the delayed claim cancels all send-related timers.
  const closed = newHostView();
  closed.reply(closed.outgoing[0],{
    hostCapabilities:{message:{text:true},serverTools:{}}
  });
  await turn();
  assert.equal(countMessages(closed,"ui/notifications/initialized"),1);
  assert.ok([...closed.timers.values()].some(x=>x.delay===4000));
  closed.event({jsonrpc:"2.0",id:41,method:"ui/resource-teardown"});
  await turn();
  assert.equal(closed.tickOne(4000),false);
  assert.equal(countMessages(closed,"tools/call"),0);
  assert.equal(countMessages(closed,"ui/message"),0);
  console.log("PASS teardown: no claim, no send");

  // 4: Unsolicited host tool-result does NOT carry assistant response to the server.
  const fakeAssistant = newHostView();
  fakeAssistant.event({jsonrpc:"2.0",method:"ui/notifications/tool-result",
    params:{role:"assistant",content:[{type:"text",text:"private answer"}]}});
  await turn();
  assert.equal(countMessages(fakeAssistant,"tools/call"),0);
  assert.equal(countMessages(fakeAssistant,"ui/message"),0);
  console.log("PASS unsolicited tool-result: not treated as assistant callback");

  // 5: Even when host capability is valid, the persistent server refusal blocks send.
  const refused = newHostView();
  refused.reply(refused.outgoing[0],{
    hostCapabilities:{message:{text:true},serverTools:{}}
  });
  await turn();
  assert.equal(refused.tickOne(4000),true);
  await turn();
  assert.equal(countMessages(refused,"tools/call"),1);
  const claim = refused.outgoing.find(x=>x.method==="tools/call");
  assert.equal(claim.params.name,"claim_guarded_probe");
  refused.toolReply(claim,{granted:false,reason:"disabled"});
  await turn();
  assert.equal(countMessages(refused,"ui/message"),0);
  assert.match(refused.status.textContent,/送信しません/);
  console.log("PASS server refusal: no ui/message");

  // 6: A rejected/failed claim call fails closed as well.
  const errorHost = newHostView();
  errorHost.reply(errorHost.outgoing[0],{
    hostCapabilities:{message:{text:true},serverTools:{}}
  });
  await turn();
  errorHost.tickOne(4000);
  await turn();
  const badClaim = errorHost.outgoing.find(x=>x.method==="tools/call");
  errorHost.reply(badClaim,{}, {code:500,message:"permission denied"});
  await turn();
  assert.equal(countMessages(errorHost,"ui/message"),0);
  console.log("PASS failed claim call: no ui/message");
})().catch(e=>{console.error(e);process.exitCode=1;});