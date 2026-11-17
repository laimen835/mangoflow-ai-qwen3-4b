// Isolated headless Edge profile: no access to the user's browser or accounts.
// Static bootstrap requests skip ngrok's interstitial; API requests must use
// the actual application's headers, reproducing a missing warning-cookie case.
const fs=require('node:fs/promises'),path=require('node:path'),{spawn}=require('node:child_process');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const base=process.argv[2];
if(!base||!/^https:\/\/[a-z0-9-]+\.ngrok-free\.(dev|app)$/.test(base))throw Error('Pass the verified ngrok URL');
const profile=path.join('D:/Codex-RAG-runtime/browser-checks','mobile-'+Date.now());
const edge='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
let browser,socket;const pending=new Map();let nextId=0;
async function send(method,params={}){
 const id=++nextId;
 const promise=new Promise((resolve,reject)=>pending.set(id,{resolve,reject}));
 socket.send(JSON.stringify({id,method,params}));return promise;
}
async function evaluate(expression){const value=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
 if(value.exceptionDetails)throw Error(value.exceptionDetails.text+': '+value.result?.description);
 return value.result.value;}
async function waitFor(expression){const deadline=Date.now()+30000;while(Date.now()<deadline){if(await evaluate(expression))return;await sleep(250);}throw Error('UI did not become ready: '+expression);}
(async()=>{
 await fs.mkdir(profile,{recursive:true});
 browser=spawn(edge,['--headless=new','--remote-debugging-port=0','--user-data-dir='+profile,
  '--no-first-run','--no-default-browser-check','--disable-extensions','about:blank'],{stdio:'ignore',windowsHide:true});
 let port;
 for(let i=0;i<60;i++){try{port=Number((await fs.readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0]);if(port)break;}catch{}await sleep(250);}
 if(!port)throw Error('Isolated browser did not start');
 const pages=await(await fetch('http://127.0.0.1:'+port+'/json/list')).json();
 const page=pages.find(p=>p.type==='page');if(!page)throw Error('No isolated page');
 socket=new WebSocket(page.webSocketDebuggerUrl);
 await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
 socket.addEventListener('message',event=>{
  const data=JSON.parse(event.data);
  if(data.id){const p=pending.get(data.id);if(p){pending.delete(data.id);data.error?p.reject(Error(data.error.message)):p.resolve(data.result);}return;}
  if(data.method==='Fetch.requestPaused'){
   const request=data.params;const u=new URL(request.request.url);
   const headers=Object.entries(request.request.headers).map(([name,value])=>({name,value:String(value)}));
   if(u.origin===base&&!u.pathname.startsWith('/api/')&&u.pathname!=='/ready')headers.push({name:'ngrok-skip-browser-warning',value:'static-test-bootstrap'});
   send('Fetch.continueRequest',{requestId:request.requestId,headers}).catch(console.error);
  }
 });
 await send('Page.enable');await send('Runtime.enable');await send('Network.enable');
 await send('Network.setCacheDisabled',{cacheDisabled:true});
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 await send('Emulation.setUserAgentOverride',{userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1'});
 await send('Fetch.enable',{patterns:['Document','Script','Stylesheet'].map(resourceType=>({urlPattern:base+'/*',resourceType,requestStage:'Request'}))});
 await send('Page.navigate',{url:base+'/widget/#teachers'});
 await waitFor("document.querySelectorAll('#teacher-list .teacher-card').length>0");
 const teachers=await evaluate("document.querySelectorAll('#teacher-list .teacher-card').length");
 await evaluate("document.querySelector('[data-view=\"schedule\"]').click()");
 await waitFor("document.querySelectorAll('#schedule-list .document-card').length>0");
 const schedules=await evaluate("document.querySelectorAll('#schedule-list .document-card').length");
 await evaluate("document.querySelector('[data-view=\"sources\"]').click()");
 await waitFor("document.querySelectorAll('#source-list .source-item').length>0");
 const sources=await evaluate("document.querySelectorAll('#source-list .source-item').length");
 const screenshot=await send('Page.captureScreenshot',{format:'png'});
 await fs.writeFile('logs/mobile-sources-fixed.png',Buffer.from(screenshot.data,'base64'));
 await evaluate(`(async()=>{const answer=await SpkHTTP.json('/api/v1/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:'Контакты колледжа'})});localStorage.setItem('rag_chat_session_id',answer.session_id);localStorage.setItem('spk_conversations_v2',JSON.stringify([{id:answer.session_id,title:'Проверка истории'}]));document.querySelector('[data-view="schedule"]').click();return true;})()`);
 await send('Page.reload',{ignoreCache:true});
 await waitFor("document.querySelectorAll('#chat-messages .message-row').length>=2&&document.querySelectorAll('#schedule-list .document-card').length>0");
 const refreshed=await evaluate("({view:location.hash,messages:document.querySelectorAll('#chat-messages .message-row').length,schedules:document.querySelectorAll('#schedule-list .document-card').length,toast:document.getElementById('toast').textContent})");
 if(/История недоступна|Не удалось загрузить историю/.test(refreshed.toast))throw Error('History failure after refresh');
 await send('Page.navigate',{url:base+'/widget/chat.html'});
 await waitFor("document.getElementById('messages')?.children.length>=2&&document.getElementById('send')?.disabled===false");
 await evaluate("document.getElementById('question').value='Контакты колледжа';document.getElementById('chat-form').requestSubmit()");
 await waitFor("document.getElementById('messages')?.children.length>=4&&document.getElementById('question')?.readOnly===false");
 const chat=await evaluate("({messages:document.getElementById('messages').children.length,answer:document.getElementById('messages').lastElementChild.textContent})");
 if(!/Контакты/.test(chat.answer)||/Не удалось получить ответ/.test(chat.answer))throw Error('Embedded chat stream did not finish');
 const result={mobile_viewport:'390x844',teachers,schedules,sources,refresh:refreshed,embedded_chat_messages:chat.messages};
 await fs.writeFile('logs/mobile-portal-check.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
 if(socket?.readyState===WebSocket.OPEN){send('Browser.close').catch(()=>{});await sleep(250);socket.close();}
 if(browser&&!browser.killed)browser.kill();
});
