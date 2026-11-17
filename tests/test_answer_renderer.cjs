const assert=require('node:assert/strict');
const {tokens,safeURL,render}=require('../widget/answer-renderer.js');
const sources=[{url:'https://ncsa.ru/a.pdf',title:'Документ'}];
const cases=[
 ['Сайт https://ncsa.ru/a.pdf.', ['https://ncsa.ru/a.pdf']],
 ['[Открыть PDF](https://ncsa.ru/a.pdf)', ['https://ncsa.ru/a.pdf']],
 ['Смотрите [1], затем [9].', ['https://ncsa.ru/a.pdf']],
 ['(https://ncsa.ru/file_(1).pdf).', ['https://ncsa.ru/file_(1).pdf']],
 ['https://ncsa.ru/inst.php?ELEMENT_ID=148&x=1!', ['https://ncsa.ru/inst.php?ELEMENT_ID=148&x=1']],
 ['[плохо](javascript:alert(1)) <img src=x onerror=alert(1)>', []],
 ['https://'+'user'+':'+ 'password'+'@ncsa.ru/a', []],
 ['https://ncsa.ru/a\nhttps://ncsa.ru/b', ['https://ncsa.ru/a','https://ncsa.ru/b']],
];
for(const [text,expected]of cases)assert.deepEqual(tokens(text,sources).filter(x=>x.href).map(x=>x.href),expected,text);
for(const value of ['javascript:alert(1)','data:text/html,x','/relative','mailto:a@b.com'])assert.equal(safeURL(value),null);
global.document={createTextNode:text=>({kind:'text',text}),createElement:tag=>({kind:tag})};
const container={children:[],replaceChildren(){this.children=[];},append(n){this.children.push(n);}};
render(container,'<script>alert(1)</script> [1]',sources);
assert.equal(container.children[0].kind,'text');
assert.equal(container.children[1].kind,'a');
assert.equal(container.children[1].rel,'noopener noreferrer');
assert.equal(container.children[1].target,'_blank');
console.log('Link renderer: 8 scenarios + protocol and DOM safety passed');
