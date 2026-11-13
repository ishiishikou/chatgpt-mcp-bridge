// Offline MCP Apps host simulator: NEVER connects to ChatGPT or any network.
// Executes the real guarded-probe.html script, with a mocked host bridge.
// Every tools/call uses the real persistent Python SQLite store via subprocess.
const fs = require("fs"), path = require("path"), os = require("os");
const vm = require("vm"), assert = require("assert");
const {execFileSync} = require("child_process");
const root = __dirname;
const html = fs.readFileSync(path.join(root, "guarded-probe.html"), "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const localPython = path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const python = process.env.PYTHON || (fs.existsSync(localPython) ? localPython : (process.platform === "win32" ? "python" : "python3"));
const PYCODE = `
import json,sys
from pathlib import Path
from dispatch_store import initialize_database,claim_once,report_result,get_status
op=sys.argv[1]; db=Path(sys.argv[2]); event=sys.argv[3]
if op=='init': initialize_database(db); out={'created':True}
elif op=='claim': out=claim_once(event,enabled=True,path=db)
elif op=='result': out=report_result(event,sys.argv[4],sys.argv[5],path=db)
elif op=='status': out=get_status(event,path=db)
else: raise ValueError('bad operation')
print(json.dumps(out))
`;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function testScenario(name, scenario){
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "guarded-v2-offline-"));
  const db = path.join(temp, "ledger.sqlite3"), event = "test-only";
  const py=(op, ...args)=>JSON.parse(execFileSync(python,
    ["-c",PYCODE,op,db,event,...args],{cwd:root,encoding:"utf8",timeout:10000}));
  py("init");
  let attempts=0, notices=[], views=[];

  function createView({allowTools=true, teardownBeforeTimer=false}={}){
    const statusEl={textContent:""};
    const listeners = new Map();
    const host = {postMessage(request){
      if(request.method === "ui/notifications/initialized" || !request.method)return;
      const method=request.method, args=request.params?.arguments || {};
      let result, noResponse=false;
      if(method === "ui/initialize"){
        result={hostCapabilities:{message:{text:true}, ...(allowTools?{serverTools:{}}:{})}};
      } else if(method === "tools/call"){
        const value=request.params.name === "claim_guarded_probe"?
          py("claim"):request.params.name === "report_guarded_probe"?
          py("result",args.token,args.outcome):{error:"unexpected method"};
        result={content:[{type:"text",text:JSON.stringify(value)}]};
      } else if(method === "ui/message"){
        attempts++;
        if(scenario === "unknown") noResponse=true;
        else result={isError:false};
      } else {throw new Error("Unexpected host request: "+method);}
      if(!noResponse)queueMicrotask(()=>listeners.get("message")?.({
        source:host,data:{jsonrpc:"2.0",id:request.id,result}
      }));
    }};
    const window={parent:host,addEventListener:(type,cb)=>listeners.set(type,cb)};
    const document={getElementById:()=>statusEl};
    const customTimeout=(fn,ms)=>setTimeout(fn,ms===4000?50:ms===12000?350:ms);
    vm.runInNewContext(script,{
      window,document,setTimeout:customTimeout,clearTimeout,Promise,Map,Error,String
    },{timeout:1000});
    if(teardownBeforeTimer)setTimeout(()=>listeners.get("message")?.({
      source:host,data:{jsonrpc:"2.0",method:"ui/resource-teardown",id:123}
    }),20);
    views.push(statusEl);
    return statusEl;
  }

  try{
    if(scenario==="duplicate"){
      createView(); await delay(650);
      createView(); await delay(300);
      assert.equal(attempts,1);
      assert.equal(py("status").status,"host_accepted");
    } else if(scenario==="concurrent"){
      createView();createView();createView();
      await delay(900);
      assert.equal(attempts,1);
      assert.equal(py("status").status,"host_accepted");
    } else if(scenario==="unknown"){
      createView(); await delay(700);
      createView(); await delay(300);
      assert.equal(attempts,1);
      assert.equal(py("status").status,"unknown");
    } else if(scenario==="no-capability"){
      createView({allowTools:false}); await delay(100);
      assert.equal(attempts,0);
      assert.equal(py("status").status,"not_claimed");
    } else if(scenario==="teardown"){
      createView({teardownBeforeTimer:true}); await delay(150);
      assert.equal(attempts,0);
      assert.equal(py("status").status,"not_claimed");
    }
    console.log("PASS "+name, JSON.stringify({attempts,status:py("status").status,
       viewStatuses:views.map(x=>x.textContent.slice(0,140))}));
  } finally{
    fs.rmSync(temp,{recursive:true,force:true});
  }
}
(async()=>{
  for(const item of ["duplicate","concurrent","unknown","no-capability","teardown"]){
    await testScenario(item,item);
  }
})().catch(e=>{console.error(e);process.exitCode=1});
