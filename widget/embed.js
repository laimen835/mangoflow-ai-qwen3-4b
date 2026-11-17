(function(){
'use strict';
if(document.getElementById('spk-chat-launcher'))return;
const url=new URL('chat.html?embed=1&v=8',document.currentScript.src);
const button=document.createElement('button');button.id='spk-chat-launcher';button.type='button';const logo=document.createElement('img');logo.src=new URL('mangoflow-logo.png',document.currentScript.src).href;logo.alt='';logo.width=32;logo.height=32;const caption=document.createElement('span');caption.textContent='MangoFlow AI';button.append(logo,caption);button.setAttribute('aria-expanded','false');button.setAttribute('aria-controls','spk-chat-window');
const frame=document.createElement('iframe');frame.id='spk-chat-window';frame.title='Диалог с MangoFlow AI';frame.src=url.href;frame.hidden=true;
const style=document.createElement('style');style.textContent=`
#spk-chat-launcher{display:flex;align-items:center;gap:10px;position:fixed;bottom:24px;right:24px;z-index:2147483001;min-height:56px;padding:14px 22px;border:0;border-radius:100px;background:#173d34;color:#fff;font:600 16px/1.4 Segoe UI,system-ui,sans-serif;cursor:pointer;box-shadow:0 8px 28px #173d3433}
#spk-chat-launcher img{object-fit:contain;background:#000;border-radius:9px;flex:none}#spk-chat-launcher:hover{background:#285546}#spk-chat-launcher:focus-visible{outline:3px solid #b47727;outline-offset:4px}
#spk-chat-window{position:fixed;bottom:96px;right:24px;width:min(420px,calc(100vw - 32px));height:min(640px,calc(100dvh - 120px));border:1px solid #cfdbd0;border-radius:22px;background:#fff;box-shadow:0 16px 64px #173d3433;z-index:2147483000;overflow:hidden;color-scheme:light}
#spk-chat-window[hidden]{display:none!important}
@media(max-width:540px){#spk-chat-launcher{bottom:max(16px,env(safe-area-inset-bottom));right:16px}#spk-chat-window{inset:0;width:100%;height:100dvh;border:0;border-radius:0}#spk-chat-launcher[aria-expanded=true]{display:none}}
`;
document.head.append(style);document.body.append(frame,button);
let opened=false,oldOverflow='',inertElements=[];
function modalMobile(){const mobile=opened&&matchMedia('(max-width:540px)').matches;if(mobile&&!inertElements.length){oldOverflow=document.body.style.overflow;document.body.style.overflow='hidden';for(const n of document.body.children){if(n===frame||n===button||n.tagName==='SCRIPT'||n.tagName==='STYLE')continue;inertElements.push([n,n.inert]);n.inert=true;}}else if(!mobile&&inertElements.length){document.body.style.overflow=oldOverflow;for(const [n,value]of inertElements)n.inert=value;inertElements=[];}}
function setOpen(value){opened=value;frame.hidden=!value;button.setAttribute('aria-expanded',String(value));caption.textContent=value?'Закрыть чат ×':'MangoFlow AI';modalMobile();if(value){frame.contentWindow.postMessage({type:'spk-chat-open',modal:matchMedia('(max-width:540px)').matches},url.origin);frame.focus();}else button.focus();}
button.onclick=()=>setOpen(!opened);
frame.onload=()=>{if(opened)frame.contentWindow.postMessage({type:'spk-chat-open',modal:matchMedia('(max-width:540px)').matches},url.origin);};
window.addEventListener('message',e=>{if(e.source===frame.contentWindow&&e.origin===url.origin&&e.data?.type==='spk-chat-close')setOpen(false);});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&opened)setOpen(false);});window.addEventListener('resize',()=>{modalMobile();if(opened)frame.contentWindow.postMessage({type:'spk-chat-layout',modal:matchMedia('(max-width:540px)').matches},url.origin);});
})();
