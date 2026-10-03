const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const code=source.slice(source.indexOf('function renderHistory()'),source.indexOf('async function loadWorkspace()'));
function check(kind) {
 const fields={'chat-history':{replaceChildren(...nodes){this.nodes=nodes}},'new-chat':{}};
 const context={$:id=>fields[id],current:()=>({kind}),selected:kind==='image'?'image':'model-a',working:false,workspaceReady:true,
   historyIndex:[{id:'chat-a',model:'model-a',title:'A'},{id:'chat-b',model:'model-b',title:'B'}],
   chats:new Map([['model-a','chat-a']]),records:new Map(),listing:{models:[{id:'model-a',name:'Model A'},{id:'model-b',name:'Model B'}]},
   Option:class {constructor(title,value){this.title=title;this.value=value}},document:{createElement:()=>({options:[],append(option){this.options.push(option)}})}};
 vm.createContext(context);vm.runInContext(code,context);context.renderHistory();
 const select=fields['chat-history'];assert.equal(select.hidden,false);assert.equal(select.disabled,false);
 assert.deepEqual(select.nodes.slice(1).map(g=>g.label),['Model A','Model B']);
 assert.deepEqual(select.nodes.slice(1).flatMap(g=>g.options.map(o=>o.value)),['chat-a','chat-b']);
 assert.equal(select.value,kind==='image'?'':'chat-a');assert.equal(fields['new-chat'].hidden,kind==='image');
}
check('chat');check('image');console.log('All stored chats remain accessible across models and image mode: PASS');
