(function(root){
 'use strict';
 async function request(input,options={}){
  const headers=new Headers(options.headers||{});
  if(!headers.has('Accept'))headers.set('Accept','application/json, application/x-ndjson');
  const target=new URL(input,root.location.href);
  if(target.origin===root.location.origin)headers.set('ngrok-skip-browser-warning','spk-app');
  return root.fetch(input,{...options,credentials:options.credentials||'same-origin',headers});
 }
 async function readJSON(response){
  if(!response.ok){const error=new Error(response.status===401?'Неверный ключ доступа':`Не удалось загрузить данные (${response.status})`);error.status=response.status;throw error;}
  if(!/application\/(?:[\w.-]+\+)?json\b/i.test(response.headers.get('content-type')||'')){
   const error=new Error('Сервер вернул страницу вместо данных. Обновите страницу и повторите попытку.');
   error.code='NON_JSON_RESPONSE';throw error;
  }
  return response.json();
 }
 root.SpkHTTP={request,readJSON,json:async(input,options)=>readJSON(await request(input,options))};
})(window);
