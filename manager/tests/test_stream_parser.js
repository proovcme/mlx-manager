const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../static/app.js'),'utf8');
const start = source.indexOf('async function streamReply(');
const end = source.indexOf("$('prompt').onkeydown", start);
async function run(wire, prefix="/mlx/", events=[]) {
  const bytes = new TextEncoder().encode(wire);
  const stream = new ReadableStream({start(controller){for(const byte of bytes)controller.enqueue(Uint8Array.of(byte));controller.close();}});
  const context={fetch:async(path,options)=>{
    assert.equal(path, prefix.replace(/\/+$/, '') + '/api/chat');
    assert.match(JSON.parse(options.body).request_id,/^[0-9a-f-]{36}$/);
    return new Response(stream);
  },document:{querySelector:()=>({content:prefix})},TextDecoder,Uint8Array,crypto:require('node:crypto').webcrypto};
  vm.createContext(context); vm.runInContext(source.slice(0, source.indexOf("const $")) + source.slice(start,end), context);
  const deltas=[];
  await context.streamReply({}, delta=>deltas.push(delta), meta=>events.push(meta));
  return deltas;
}
(async()=>{
  assert.deepEqual(await run('data: [DONE]\n\n', ''), []);
  const deltas=await run(': keepalive\r\n\r\ndata: {"choices":[{"delta":{"reasoning":"Думаю"}}]}\r\n\r\ndata: {"choices":[{"delta":{"content":"Ответ: 你好"}}]}\r\n\r\ndata: [DONE]\r\n\r\n');
  const events=[];await run('data: {"type":"web_search","phase":"searching"}\n\ndata: {"type":"web_search","phase":"ready","sources":[{"id":1,"title":"Источник"}]}\n\ndata: [DONE]\n\n', '/mlx/', events);
  assert.equal(events[0].phase, 'searching');assert.equal(events[1].sources[0].title, 'Источник');
  assert.equal(deltas[0].reasoning,'Думаю'); assert.equal(deltas[1].content,'Ответ: 你好');
  await assert.rejects(run('data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'),/до завершения/);
  await assert.rejects(run('data: {"error":"model failed"}\n\n'),/model failed/);
  const keyboard = source.slice(source.indexOf("$('prompt').onkeydown"),source.indexOf("$('load-logs').onclick"));
  let submitted=0,prevented=0,kind='chat';
  const fields={prompt:{},send:{disabled:false},compose:{requestSubmit:()=>submitted++}};
  const keys={$:id=>fields[id],current:()=>({kind})};vm.createContext(keys);vm.runInContext(keyboard,keys);
  const press=options=>fields.prompt.onkeydown({key:'Enter',preventDefault:()=>prevented++,...options});
  press({});assert.equal(submitted,1);
  press({shiftKey:true});press({isComposing:true});assert.equal(submitted,1);assert.equal(prevented,1);
  fields.send.disabled=true;press({});assert.equal(submitted,1);
  fields.send.disabled=false;kind='image';press({});assert.equal(submitted,1);
  press({ctrlKey:true});assert.equal(submitted,2);
  console.log('SSE parsing, web metadata, chat Enter/Shift+Enter/IME and busy guard: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
