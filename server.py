import os
from mcp.server.apps import Apps
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

URI = "ui://chatgpt-event-probe/manual.html"
HTML = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ChatGPT MCP Apps送信検証</title>
<style>
:root{font-family:system-ui,sans-serif;color-scheme:light dark}
body{margin:0;padding:18px}
h2{font-size:17px;margin:0 0 12px}
p{font-size:13px;line-height:1.5}
button{padding:10px 14px;border-radius:9px;border:1px solid currentColor;background:transparent;color:inherit;font-size:13px}
button:disabled{opacity:.5}
#status{white-space:pre-wrap;font-size:12px;line-height:1.5;margin-top:12px}
</style>
</head>
<body>
<h2>公式MCP Apps送信テスト</h2>
<p>ボタンを押した場合のみ、固定の検証文をこの会話へ送信します。外部イベント・タイマーでの自動送信や会話の読み取りは行いません。</p>
<button disabled id="send">現在の会話へテストメッセージを送信</button>
<div id="status">ChatGPTとの接続を確認中…</div>
<script>
(function(){
 const parent=window.parent;
 const status=document.getElementById('status');
 const button=document.getElementById('send');
 const requests=new Map();let seq=1,ready=false;
 function sendRequest(method,params){
  return new Promise((resolve,reject)=>{
   const id=seq++;
   const timeout=setTimeout(()=>{requests.delete(id);reject(new Error('ホスト応答がタイムアウトしました'))},12000);
   requests.set(id,{resolve,reject,timeout});
   parent.postMessage({jsonrpc:'2.0',id,method,params},'*');
  });
 }
 window.addEventListener('message',(event)=>{
  if(event.source!==parent||event.data?.jsonrpc!=='2.0')return;
  const data=event.data;
  if(data.id!=null&&requests.has(data.id)){
    const item=requests.get(data.id);requests.delete(data.id);clearTimeout(item.timeout);
    if(data.error)item.reject(new Error(String(data.error.message||'Host error')));
    else item.resolve(data.result||{});
    return;
  }
  if(data.method==='ui/resource-teardown' && data.id!=null){
   parent.postMessage({jsonrpc:'2.0',id:data.id,result:{}},'*');
   ready=false;button.disabled=true;status.textContent='widget終了を受信しました';
  }
 });
 button.addEventListener('click',()=>{
  if(!ready)return;
  button.disabled=true;
  status.textContent='ui/messageの要求をホストへ送信中…';
  const text='公式MCP Apps ui/messageの手動送信テストです。「MCP Apps受信済み」とだけ返してください。';
  sendRequest('ui/message',{role:'user',content:[{type:'text',text}]})
   .then((result)=>{status.textContent=result.isError?'ホストが送信を拒否しました':'ホストが要求を受理しました。ChatGPT会話画面を確認してください';})
   .catch((e)=>{status.textContent='ホスト側で失敗: '+String(e.message).slice(0,220);})
   .finally(()=>{if(ready)button.disabled=false;});
 });
 sendRequest('ui/initialize',{
  appInfo:{name:'ChatGPT MCP Apps event probe',version:'0.1.0'},
  appCapabilities:{availableDisplayModes:['inline']},
  protocolVersion:'2026-01-26'
 }).then((result)=>{
  ready=true;
  parent.postMessage({jsonrpc:'2.0',method:'ui/notifications/initialized'},'*');
  button.disabled=false;
  status.textContent='ホスト接続済み。ボタン操作待ち。';
 }).catch((error)=>{status.textContent='ホスト初期化失敗: '+String(error.message).slice(0,220);});
})();
</script>
</body></html>"""

apps = Apps()

@apps.tool(resource_uri=URI, description="Show a private diagnostic widget. It can request one fixed innocuous follow-up message to the current ChatGPT conversation, only after the user presses its button. No autonomous chat injection or external data processing.")
def show_event_probe(ctx: Context) -> str:
    return "Manual-only ChatGPT MCP Apps widget. The user can click its button to request one fixed test follow-up. No automatic sending is implemented."

apps.add_html_resource(URI, HTML, title="ChatGPT manual message probe")
# LEGACY SAFETY SHUTDOWN (2026-11-13):
# Do not reactivate this resource or its automatic send timer.
# Old chat snapshots can contain a cached version of the previous HTML.
from pathlib import Path
AUTO_URI = "ui://chatgpt-event-probe/auto.html"
AUTO_HTML = Path(__file__).with_name("auto-probe.html").read_text(encoding="utf-8")

@apps.tool(
    resource_uri=AUTO_URI,
    description="RETIRED safety-only widget. The previous automatic-send experiment caused duplicates. It never sends a message. Do not use for further tests."
)
def show_auto_probe(ctx: Context) -> str:
    return "The legacy automatic-send experiment is DISABLED. This tool displays only a safety notice; no message is sent."

apps.add_html_resource(AUTO_URI, AUTO_HTML, title="Automatic message probe RETIRED")

# Fresh, independently named UI (never reuses cached legacy auto.html).
# Explicit environment opt-in is required to grant the first claim.
import json
from dispatch_store import claim_once, report_result, get_status
GUARDED_EVENT_ID = "probe-20261113-v2"
GUARDED_URI = "ui://chatgpt-event-probe/guarded-v2.html"
GUARDED_HTML = Path(__file__).with_name("guarded-probe.html").read_text(encoding="utf-8")

def claim_guarded_probe(ctx: Context) -> str:
    enabled = os.environ.get("MCP_AUTO_SEND_ENABLED") == "1"
    return json.dumps(claim_once(GUARDED_EVENT_ID, enabled=enabled))

def report_guarded_probe(ctx: Context, token: str, outcome: str) -> str:
    return json.dumps(report_result(GUARDED_EVENT_ID, token, outcome))

def guarded_probe_status(ctx: Context) -> str:
    return json.dumps(get_status(GUARDED_EVENT_ID))

@apps.tool(
    resource_uri=GUARDED_URI,
    description="Display the new guarded one-shot diagnostic UI. Automatic send is disabled unless the operator explicitly enables it on the server; do not invoke without an explicit future user request."
)
def show_guarded_probe(ctx: Context) -> str:
    return "Guarded one-shot UI; default DISABLED. Server-side persistent claim is required before any ui/message. Do not use until explicitly authorized."

apps.add_html_resource(GUARDED_URI, GUARDED_HTML, title="Guarded one-time message probe v2")

# A new, strictly display-only diagnostic with no message dispatch and no tools/call.
# Distinct URI prevents accidentally reactivating cached legacy HTML.
LIFECYCLE_URI = "ui://chatgpt-event-probe/lifecycle-display-v1.html"
LIFECYCLE_HTML = Path(__file__).with_name("lifecycle-display-v1.html").read_text(encoding="utf-8")

@apps.tool(
    resource_uri=LIFECYCLE_URI,
    description="Display a read-only MCP Apps lifecycle diagnostic widget. No automatic messages, no server-side tool calls, no external requests and no send timers."
)
def show_lifecycle_probe(ctx: Context) -> str:
    return "Display-only lifecycle probe; it does not send messages or call server helper tools."

apps.add_html_resource(LIFECYCLE_URI, LIFECYCLE_HTML, title="Lifecycle display-only probe v1")

mcp = MCPServer("ChatGPT event widget probe", version="0.3.0", extensions=[apps])
# App-only helpers have NO resourceUri: their calls must not reopen an iframe.
# Restrict visibility to the MCP App on the same server connection.
APP_ONLY = {"ui": {"visibility": ["app"]}}
mcp.add_tool(claim_guarded_probe, meta=APP_ONLY,
             description="Claim one persistent send permission (default disabled).")
mcp.add_tool(report_guarded_probe, meta=APP_ONLY,
             description="Record prior claim host-level result, without retrying.")
mcp.add_tool(guarded_probe_status, meta=APP_ONLY,
             description="Read fixed-event dispatch ledger status.")

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host=os.environ.get("BIND_HOST","127.0.0.1"), port=int(os.environ.get("PORT","3091")), streamable_http_path="/mcp", json_response=True, stateless_http=True)
