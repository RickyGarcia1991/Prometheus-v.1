// Native Chromium reader. The Python parent supplies every permitted remote response.
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {mkdir,readFile} from 'node:fs/promises';
import {createInterface} from 'node:readline';
import {join} from 'node:path';

const lines=createInterface({input:process.stdin,crlfDelay:Infinity});
const received=[],waiters=[];
lines.on('line',line=>{const waiter=waiters.shift();if(waiter)waiter(line);else received.push(line);});
const nextLine=()=>received.length?Promise.resolve(received.shift()):new Promise(resolve=>waiters.push(resolve));
const emit=value=>process.stdout.write('PROMETHEUS_BROWSER:'+JSON.stringify(value)+'\n');
const [chromePath,job]=process.argv.slice(2);
const request=JSON.parse(await nextLine());
const proxy=createServer((req,res)=>{res.writeHead(403);res.end();});
proxy.on('connect',(req,socket)=>{socket.end('HTTP/1.1 403 Forbidden\r\n\r\n');});
await new Promise(resolve=>proxy.listen(0,'127.0.0.1',resolve));
const profile=join(job,'profile');await mkdir(profile);
let chrome,ws,sequence=0,fetches=Promise.resolve();
const pending=new Map();
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let fatal=null;
function command(method,params={},sessionId){
 const id=++sequence;
 return new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>{pending.delete(id);reject(Error('Browser command timed out'));},20000);
  pending.set(id,{resolve,reject,timer});
  ws.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));
 });
}
try{
 chrome=spawn(chromePath,[
  '--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
  '--remote-debugging-port=0','--remote-debugging-address=127.0.0.1',
  '--user-data-dir='+profile,'--disable-background-networking',
  '--disable-quic','--disable-extensions','--disable-component-update',
  '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
  '--host-resolver-rules=MAP * ~NOTFOUND','--proxy-bypass-list=<-loopback>',
  '--proxy-server=http://127.0.0.1:'+proxy.address().port,'about:blank'
 ],{cwd:job,stdio:['ignore','ignore','inherit']});
 chrome.on('error',error=>{fatal=error;});
 let port,websocketPath;
 for(let i=0;i<250;i++){
  if(fatal)throw fatal;
  if(chrome.exitCode!==null)throw Error('Native Chrome could not start.');
  try{[port,websocketPath]=(await readFile(join(profile,'DevToolsActivePort'),'utf8')).trim().split('\n');break;}
  catch{await delay(100);}
 }
 if(!/^\d+$/.test(port||'')||!websocketPath?.startsWith('/devtools/browser/'))throw Error('Missing local browser endpoint.');
 ws=new WebSocket('ws://127.0.0.1:'+port+websocketPath);
 await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',()=>reject(Error('Local browser connection failed.')),{once:true});});
 ws.addEventListener('message',event=>{
  let value;try{value=JSON.parse(event.data);}catch{fatal=Error('Invalid browser response.');return;}
  if(value.id){
   const p=pending.get(value.id);if(!p)return;pending.delete(value.id);clearTimeout(p.timer);
   if(value.error)p.reject(Error('Browser command failed: '+value.error.message));else p.resolve(value.result||{});
  }else if(value.method==='Fetch.requestPaused'){
   fetches=fetches.then(async()=>{
    const r=value.params.request;
    emit({type:'fetch',url:r.url,method:r.method,resource_type:value.params.resourceType});
    const response=JSON.parse(await nextLine());
    await command(response.ok?'Fetch.fulfillRequest':'Fetch.failRequest',
      response.ok?{requestId:value.params.requestId,responseCode:200,body:response.body,responseHeaders:response.headers}
                 :{requestId:value.params.requestId,errorReason:'BlockedByClient'},value.sessionId);
   }).catch(error=>{fatal=error;});
  }
 });
 const {targetId}=await command('Target.createTarget',{url:'about:blank'});
 const {sessionId}=await command('Target.attachToTarget',{targetId,flatten:true});
 await command('Browser.setDownloadBehavior',{behavior:'deny'});
 await command('Network.enable',{},sessionId);
 await command('Network.setBypassServiceWorker',{bypass:true},sessionId);
 await command('Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'}]},sessionId);
 await command('Page.navigate',{url:request.url},sessionId);
 for(let i=0;i<50;i++){
  if(fatal)throw fatal;await delay(100);await fetches;
  const state=await command('Runtime.evaluate',{expression:'document.readyState',returnByValue:true},sessionId);
  if(state.result?.value==='complete'){await delay(350);await fetches;break;}
 }
 if(fatal)throw fatal;
 const result=await command('Runtime.evaluate',{expression:"({url:location.href,title:document.title,text:(document.body?.innerText||'').slice(0,16000),links:Array.from(document.querySelectorAll('a[href]')).slice(0,20).map(a=>({url:a.href,text:(a.innerText||'').slice(0,200)}))})",returnByValue:true},sessionId);
 if(result.exceptionDetails||!result.result?.value)throw Error('Page extraction failed.');
 emit({type:'result',result:result.result.value});
}finally{
 for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('Reader closed.'));}
 pending.clear();ws?.close();lines.close();
 if(chrome&&chrome.exitCode===null)chrome.kill('SIGTERM');
 proxy.close();
}
