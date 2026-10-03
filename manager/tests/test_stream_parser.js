const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js','utf8');
const start = source.indexOf('async function streamReply(');
const end = source.indexOf("$('prompt').onkeydown", start);
async function run(wire) {
  const bytes = new TextEncoder().encode(wire);
  const stream = new ReadableStream({start(controller){for(const byte of bytes)controller.enqueue(Uint8Array.of(byte));controller.close();}});
  const context={fetch:async(path,options)=>{
    assert.match(JSON.parse(options.body).request_id,/^[0-9a-f-]{36}$/);
    return new Response(stream);
  },TextDecoder,Uint8Array,crypto:require('node:crypto').webcrypto};
  vm.createContext(context); vm.runInContext(source.slice(start,end), context);
  const deltas=[];
  await context.streamReply({}, delta=>deltas.push(delta));
  return deltas;
}
(async()=>{
  const deltas=await run(': keepalive\r\n\r\ndata: {"choices":[{"delta":{"reasoning":"Думаю"}}]}\r\n\r\ndata: {"choices":[{"delta":{"content":"Ответ: 你好"}}]}\r\n\r\ndata: [DONE]\r\n\r\n');
  assert.equal(deltas[0].reasoning,'Думаю'); assert.equal(deltas[1].content,'Ответ: 你好');
  await assert.rejects(run('data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'),/до завершения/);
  await assert.rejects(run('data: {"error":"model failed"}\n\n'),/model failed/);
  console.log('SSE fragmentation, UTF-8, truncated stream and upstream error: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
