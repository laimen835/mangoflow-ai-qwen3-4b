const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const calls=[];
let failure=null;
const window={location:new URL('https://demo.ngrok-free.dev/widget/'),fetch:async(url,options={})=>{
 calls.push({url,options});
 if(failure==='http')return new Response('Unavailable',{status:503});
 if(failure==='html'||!new Headers(options.headers).has('ngrok-skip-browser-warning'))
  return new Response('<html>ngrok Visit Site</html>',{headers:{'content-type':'text/html'}});
 if(String(url).endsWith('/stream'))return new Response('{"type":"delta","text":"Ответ"}\n',{headers:{'content-type':'application/x-ndjson'}});
 return Response.json({items:[{name:'Проверочный преподаватель'}]});
}};
vm.runInNewContext(fs.readFileSync('widget/http.js','utf8'),{window,Headers,URL,Error});
(async()=>{
 // This recreates the phone failure: HTTP 200 is an HTML warning, not JSON.
 const broken=await window.fetch('/api/v1/portal/teachers');
 await assert.rejects(()=>broken.json());
 const data=await window.SpkHTTP.json('/api/v1/portal/teachers?q=Иванов');
 assert.equal(data.items.length,1);
 assert.equal(calls.at(-1).options.credentials,'same-origin');
 assert.equal(calls.at(-1).options.headers.get('ngrok-skip-browser-warning'),'spk-app');
 assert.equal(calls.at(-1).url,'/api/v1/portal/teachers?q=Иванов');
 const controller=new AbortController();
 const stream=await window.SpkHTTP.request('/api/v1/chat/stream',{method:'POST',
  headers:{'Content-Type':'application/json'},body:'{"message":"Контакты"}',signal:controller.signal});
 assert.equal(stream.bodyUsed,false);
 assert.equal(calls.at(-1).options.signal,controller.signal);
 assert.equal(calls.at(-1).options.headers.get('Content-Type'),'application/json');
 assert.match(await stream.text(),/"type":"delta"/);
 failure='html';
 await assert.rejects(()=>window.SpkHTTP.json('/ready'),e=>e.code==='NON_JSON_RESPONSE');
 failure='http';
 await assert.rejects(()=>window.SpkHTTP.json('/ready'),e=>e.status===503);
 failure=null;
 await window.SpkHTTP.request('https://ncsa.ru/document.pdf');
 assert.equal(calls.at(-1).options.headers.has('ngrok-skip-browser-warning'),false);
 for(const [html,app]of [['index.html','app.js'],['chat.html','chat.js']]){
  const text=fs.readFileSync('widget/'+html,'utf8');
  const helper=text.indexOf('src="http.js?');
  const application=text.indexOf('src="'+app+'?');
  assert.ok(helper>=0&&application>helper);
 }
 console.log('HTTP client: HTML warning reproduced; JSON, streaming, errors, cookies and both entry points verified');
})().catch(error=>{console.error(error);process.exitCode=1;});
