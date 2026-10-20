/* Synthetic offline WebView2 adapter verification. No browser, login, HTTP or real messages. */
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const assert = require('node:assert/strict'), {webcrypto} = require('node:crypto');
const code = fs.readFileSync(path.join(__dirname, 'desktop-source.js'), 'utf8');
const route = 'https://tougu.zx093.cn/touguapp/webChatRoom/index.html#/chatRoom?id=&teacherId=VzPD.Y8fIGs_';
const windowEvents = new Map(), documentEvents = new Map(), output = [];
let now = Date.now(), loggedIn = true, tick, bodyReads = 0, timerCount = 0;
class TestDate extends Date { constructor(value) { super(value === undefined ? now : value); } static now() { return now; } }
function row(role, text, time = now, image = '', options = {}) {
  const node = {nodeType: 1, tagName: 'DIV', matches() { return false; }, childNodes: [{nodeType: 3, nodeValue: text}]};
  const quote = {nodeType: 1, matches() { return true; }};
  Object.defineProperty(quote, 'childNodes', {get() { throw Error('quoted student content was read'); }});
  node.childNodes.push(quote);
  const meta = {querySelector(selector) {
    if (selector.includes('.user') && (role === 'student' || role === 'self')) return {};
    if (selector === ':scope>img.vpg' || selector === ':scope>span.t-no') return role === 'unknown' ? null : {};
    return null;
  }};
  const body = {classList: {contains(value) { return (role === 'assistant' && value === 'bg-blue') || (options.vip && value === 'bg-vip'); }},
    querySelector(selector) {
      if (selector === ':scope>.text>.msg-content') {
        if (role !== 'teacher' || options.vip) throw Error('non-teacher/locked body was read');
        bodyReads++; return node;
      }
      if (selector === ':scope>.image>.image-con img.el-image__inner' && image) return {currentSrc: image};
      if (selector === ':scope>.text>.reply-msg' && options.question) return {querySelector(selector) {
        if(selector === ':scope>.custom-msg.quote-msg')return {nodeType:1,tagName:'DIV',matches(){return true;},
          childNodes:[{nodeType:3,nodeValue:options.question}]};
        if(selector === ':scope>.custom-info>.reply-name')return {textContent:'@公开昵称：2026-01-01 08:00:00'};
        return null;
      }};
      return null;
    }};
  return {getClientRects() { return options.hidden ? [] : [{}]; }, querySelector(selector) {
    if (selector === ':scope>.content>.msg-c>.nick-name') return meta;
    if (selector === ':scope>.content>.msg-c>.info-data') {
      if (['student', 'self', 'unknown'].includes(role)) throw Error('student/unknown body container was read');
      return body;
    }
    if (selector === ':scope>.date-box>.date') return {textContent: new Date(time + 8 * 3600000).toISOString().slice(0, 19).replace('T', ' ')};
    return null;
  }};
}
const rows = [row('student', 'never-read'), row('self', 'never-read'), row('assistant', 'never-read'), row('unknown', 'never-read'),
  row('teacher', 'never-read', now, '', {vip: true}), row('teacher', 'never-read', now, '', {hidden: true}), row('teacher', '老师公开正文',now,'',{question:'与回答关联的学员提问'})];
const room = {dataset: {}, querySelector() { return {}; }, querySelectorAll(selector) {
  assert.equal(selector, ':scope>.chat-room-content .chat'); return rows;
}};
const document = {visibilityState: 'visible', querySelector(selector) {
  if (selector === '.chat-room-wrapper') return null;
  if (selector.includes('.exit-btn')) return loggedIn ? {} : null;
  assert.equal(selector, '.chat-room-wrapper>.chat-room-container>.chat-room'); return room;
}, addEventListener(type, handler) { documentEvents.set(type, handler); }};
Object.defineProperty(document, 'cookie', {get() { throw Error('cookie was read'); }});
const window = {addEventListener(type, handler) { windowEvents.set(type, handler); }, chrome: {webview: {
  postMessage(value) { assert.equal(typeof value, 'string'); output.push(JSON.parse(value)); }
}}};
window.top = window;
Object.defineProperty(window.chrome, 'runtime', {get() { throw Error('extension runtime was used'); }});
Object.defineProperty(window, 'localStorage', {get() { throw Error('source storage was read'); }});
const context = vm.createContext({document, window, navigator: {onLine: true}, location: {href: route}, Date: TestDate,
  URL, URLSearchParams, crypto: webcrypto, TextEncoder, Uint8Array,
  setInterval(handler) { tick = handler; timerCount++; return timerCount; }, clearInterval() {},
  fetch() { throw Error('adapter attempted a network request'); }});
async function until(count) {
  for (let n = 0; n < 100 && output.length < count; n++) await new Promise(resolve => setTimeout(resolve, 5));
  assert(output.length >= count, 'expected an adapter heartbeat');
}
(async () => {
  vm.runInContext(code, context); await until(1);
  const initial = output[0];
  assert.equal(initial.identity_mode, 'dom_teacher'); assert.equal(initial.source_ready, true);
  assert.equal(initial.mode, 'snapshot'); assert.equal(initial.messages.length, 1);
  assert.equal(initial.messages[0].text, '老师公开正文'); assert.match(initial.messages[0].id, /^dom-[a-f0-9]{64}$/);
  assert.equal(initial.messages[0].reply_context.text,'与回答关联的学员提问');
  assert.equal(initial.messages[0].reply_context.display_name,'公开昵称','requested public quote nickname');
  assert(!JSON.stringify(output).includes('never-read'));
  now += 2000; rows.push(row('teacher', '老师第二条', now, 'https://fileoss.zx093.com/public.png')); tick();
  await until(2); assert.equal(output.at(-1).mode, 'live');
  assert.deepEqual(output.at(-1).messages[0].images, ['https://fileoss.zx093.com/public.png']);
  now += 2000; rows.push(row('teacher', '不转发带凭证图片', now, 'https://fileoss.zx093.com/public.png?token=never-forward')); tick();
  await until(3); assert.deepEqual(output.at(-1).messages[0].images, []);
  assert(!JSON.stringify(output).includes('never-forward'));
  loggedIn = false; now += 6000; tick(); await until(4);
  assert.equal(output.at(-1).source_ready, false); assert.deepEqual(output.at(-1).messages, []);
  loggedIn = true; tick(); await until(5); assert.equal(output.at(-1).mode, 'snapshot');
  context.navigator.onLine = false; windowEvents.get('offline')(); await until(6);
  assert.equal(output.at(-1).source_ready, false);
  context.navigator.onLine = true; windowEvents.get('online')(); await until(7);
  assert.equal(output.at(-1).mode, 'snapshot');
  context.location.href = 'https://tougu.zx093.cn/touguapp/webChatRoom/index.html#/other';
  windowEvents.get('hashchange')(); await until(8); assert.equal(output.at(-1).source_ready, false);
  const before = bodyReads; now += 6000; tick(); await until(9); assert.equal(bodyReads, before, 'off-route must never read bodies');
  context.location.href = route; windowEvents.get('hashchange')(); await until(10); assert.equal(output.at(-1).mode, 'snapshot');
  room.dataset.yuenHistory = 'true'; now += 6000; tick(); await until(11); assert.equal(output.at(-1).source_ready, false);
  delete room.dataset.yuenHistory; tick(); await until(12); assert.equal(output.at(-1).mode, 'snapshot');
  windowEvents.get('pagehide')(); await until(13); assert.equal(output.at(-1).source_ready, false);
  windowEvents.get('pageshow')(); await until(14); assert.equal(output.at(-1).mode, 'snapshot');
  const previousTimers = timerCount; vm.runInContext(code, context); assert.equal(timerCount, previousTimers, 'repeat injection must not duplicate the timer');
  vm.runInNewContext(code, {window: {top: {}, chrome: {webview: {postMessage() { throw Error('subframe emitted'); }}}}});
  if (process.argv.includes('--emit-fixture')) { initial.observed_at = new Date().toISOString(); process.stdout.write(JSON.stringify(initial)); }
  else process.stdout.write('PASS: desktop teacher adapter, attached public questions, no extension/network/credentials, standalone student/assistant/VIP isolation, logout/offline/navigation/history baselines and idempotent top-frame injection\n');
})().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
