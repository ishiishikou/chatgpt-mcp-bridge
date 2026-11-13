// Completely offline lifecycle test. Never connects to MCP or ChatGPT.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "lifecycle-display-v1.html"), "utf8");
const match = html.match(/<script>([\s\S]*?)<\/script>/);
assert.ok(match, "lifecycle script exists");
const source = match[1];
for (const unsafe of ["ui/message", "tools/call", "setTimeout", "setInterval",
                      "fetch(", "XMLHttpRequest", "WebSocket", "EventSource"]) {
  assert.equal(source.includes(unsafe), false, "forbidden API: " + unsafe);
}

function mount() {
  const elements = new Map();
  const messageHandlers = new Map();
  const documentHandlers = new Map();
  const outbound = [];
  const document = {
    visibilityState: "visible",
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, {textContent:""});
      return elements.get(id);
    },
    addEventListener(name, callback) {documentHandlers.set(name, callback);}
  };
  const parent = {postMessage(message) {outbound.push(message);}};
  const window = {parent, addEventListener(name, callback) {messageHandlers.set(name, callback);}};
  vm.runInNewContext(source, {window, document, Date, Math, Boolean, String}, {timeout:1000});
  const value = id => elements.get(id)?.textContent;
  const emit = (name, data={}) => messageHandlers.get(name)?.(data);
  return {value, emit, document, documentHandlers, outbound, parent};
}

const instance = mount();
assert.match(instance.value("instance"), /^[0-9a-z]+-[0-9a-z]{1,8}$/);
assert.match(instance.value("mounted"), /^\d{4}-\d\d-\d\dT/);
assert.deepEqual(instance.outbound.map(m=>m.method), ["ui/initialize"]);
assert.equal(instance.outbound[0].id, 1);
assert.ok(instance.outbound[0].params.appCapabilities.availableDisplayModes.includes("inline"));

instance.emit("message", {source:instance.parent,data:{jsonrpc:"2.0",id:1,
  result:{hostCapabilities:{message:{text:true},serverTools:{}}}}});
assert.equal(instance.value("initialize"),"成功");
assert.equal(instance.value("capabilities"),"message.text=true / serverTools=true");
assert.deepEqual(instance.outbound.map(m=>m.method), ["ui/initialize","ui/notifications/initialized"]);

instance.document.visibilityState="hidden";
instance.documentHandlers.get("visibilitychange")();
assert.equal(instance.value("visibility"),"hidden");
assert.equal(instance.value("visibilityCount"),"1");
instance.emit("pageshow");
assert.equal(instance.value("pageCount"),"1");
instance.emit("message", {source:instance.parent,
  data:{jsonrpc:"2.0",id:99,method:"ui/resource-teardown"}});
assert.equal(instance.value("teardown"),"受信");
assert.deepEqual(instance.outbound.map(m=>m.method),
  ["ui/initialize","ui/notifications/initialized",undefined]);
assert.equal(instance.outbound[2].id,99);
assert.ok(instance.outbound[2].result);
assert.equal(instance.outbound.some(m=>m.method==="ui/message"||m.method==="tools/call"),false);

const denied = mount();
denied.emit("message",{source:denied.parent,
  data:{jsonrpc:"2.0",id:1,error:{message:"no"}}});
assert.equal(denied.value("initialize"),"エラー");
assert.deepEqual(denied.outbound.map(m=>m.method),["ui/initialize"]);

console.log("PASS display-only: no send/server-tool/network/timer APIs");
console.log("PASS initialize: host capability display and notification");
console.log("PASS lifecycle: visibility/page-show/teardown");
console.log("PASS initialize-error: fail without any side effects");
