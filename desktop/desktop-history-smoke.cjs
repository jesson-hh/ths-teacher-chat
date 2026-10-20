/* Offline public-list/history contract: no browser, authentication or network. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {webcrypto}=require('node:crypto');
const code=fs.readFileSync(require('node:path').join(__dirname,'desktop-source.js'),'utf8');
let now=Date.now(), tick, calls=0;
class Clock extends Date {constructor(value){super(value===undefined?now:value);}static now(){return now;}}
const output=[], events=new Map();
function message(id,role=3,extra={}) {
  return {id,chatRoomId:'public-room',userType:role,bizType:1,msgType:0,msgDate:now-100000-id*1000,
    msgContent:'teacher-'+id,...extra};
}
function denied(id,extra) {
  const item=message(id,3,extra);
  Object.defineProperty(item,'msgContent',{get(){throw Error('excluded body read');}});
  Object.defineProperty(item,'quoteContent',{get(){throw Error('quote read');}});
  return item;
}
function question(extra={}) {
  const quote={id:'question-42',nickName:'公开昵称',msgType:0,msgDate:now-400000,msgContent:'<b>学员的原始提问</b>',...extra};
  Object.defineProperty(quote,'userId',{get(){throw Error('account UID read');}});
  Object.defineProperty(quote,'quoteContent',{get(){throw Error('nested quote read');}});
  return quote;
}
function blockedQuestion(extra) {
  const quote=question(extra);
  Object.defineProperty(quote,'msgContent',{get(){throw Error('private/locked quote body read');}});
  return quote;
}
const site={roomInfo:{id:'public-room',userId:'local-account',orderStatus:1,contractState:1},
  lookType:1,isLoading:false,hasMoreHistory:true,chatRoomMsgList:[...Array.from({length:105},(_,index)=>message(105-index)),
    denied(991,{userType:1}),denied(992,{userType:4}),denied(993,{replyFlag:2}),
    denied(994,{chatRoomId:'other-room'}),denied(995,{dr:1}),denied(996,{bizType:2})],
  getChatRecord(room,cursor){
    assert.equal(room,'public-room');assert.equal(arguments.length,2);assert.equal(this.isLoading,true);
    assert.equal(String(cursor),String(this.chatRoomMsgList[0].id));
    calls++;const index=calls;
    setTimeout(()=>{this.chatRoomMsgList.unshift(message(105+index));this.isLoading=false;if(index===2)this.hasMoreHistory=false;},10);
  }};
site.chatRoomMsgList.find(item=>item.id===1).quoteContent=question();
site.chatRoomMsgList.find(item=>item.id===2).quoteContent=JSON.stringify({msgType:0,msgContent:'序列化提问',msgDate:now-400000});
site.chatRoomMsgList.find(item=>item.id===4).quoteContent=question({msgType:1,msgContent:{imgUrl:'https://fileoss.zx093.com/question.png'}});
site.chatRoomMsgList.find(item=>item.id===5).quoteContent=question({msgContent:'问'.repeat(4500)});
for(const [id,reason] of [[6,{feeStatus:1}],[7,{replyFlag:2}],[8,{chatRoomId:'other'}],[9,{dr:1}],[10,{bizType:2}]]) {
  site.chatRoomMsgList.find(item=>item.id===id).quoteContent=blockedQuestion(reason);
}
Object.defineProperty(site,'privateMsgList',{get(){throw Error('private list read');}});
Object.defineProperty(site,'centralToken',{get(){throw Error('credential read');}});
const room={dataset:{},querySelector(){return{};},querySelectorAll(){throw Error('virtual DOM fallback used despite native public list');}};
const document={visibilityState:'hidden',querySelector(selector){
  if(selector.includes('.exit-btn'))return{};
  if(selector==='.chat-room-wrapper')return {__vue__:site};
  if(selector==='.chat-room-wrapper>.chat-room-container>.chat-room')return room;
  throw Error('unexpected selector '+selector);
},createElement(tag){assert.equal(tag,'template');let html='';return{
  set innerHTML(value){html=value;},content:{querySelectorAll(){return[];},get textContent(){return html.replace(/<[^>]+>/g,'');}}
};},addEventListener(){}};
Object.defineProperty(document,'cookie',{get(){throw Error('cookie read');}});
const window={addEventListener(name,callback){events.set(name,callback);},chrome:{webview:{postMessage(raw){output.push(JSON.parse(raw));}}}};
window.top=window;Object.defineProperty(window,'localStorage',{get(){throw Error('storage read');}});
const context=vm.createContext({window,document,navigator:{onLine:true},location:{href:'https://tougu.zx093.cn/touguapp/webChatRoom/index.html#/chatRoom?id=&teacherId=VzPD.Y8fIGs_'},
  URL,URLSearchParams,Date:Clock,crypto:webcrypto,Uint8Array,TextEncoder,setInterval(callback){tick=callback;return 1;},clearInterval(){},
  fetch(){throw Error('HTTP reconstructed');}});
async function settle(){await new Promise(resolve=>setTimeout(resolve,100));}
(async()=>{
  vm.runInContext(code,context);await settle();
  for(let n=0;n<3;n++){now+=2000;tick();await settle();}
  const batches=output.filter(value=>Array.isArray(value.messages));
  const messages=new Map(batches.flatMap(value=>value.messages).map(value=>[value.id,value]));
  assert.equal(messages.size,107,'all loaded and earlier teachers, including virtualized rows');
  assert(batches.every(value=>value.messages.length<=100),'bounded batches');
  assert([...messages.values()].every(value=>value.role==='teacher'&&value.public===true));
  assert.equal(calls,2,'native public history cursor advances and exhaustion stops');
  assert.equal([...messages.values()].find(value=>value.text==='teacher-1').reply_context.text,'学员的原始提问');
  assert.equal([...messages.values()].find(value=>value.text==='teacher-1').reply_context.display_name,'公开昵称');
  assert.equal([...messages.values()].find(value=>value.text==='teacher-1').reply_context.source_id,'question-42');
  assert.equal([...messages.values()].find(value=>value.text==='teacher-2').reply_context.text,'序列化提问');
  assert.deepEqual([...messages.values()].find(value=>value.text==='teacher-4').reply_context.images,['https://fileoss.zx093.com/question.png']);
  assert.equal([...messages.values()].find(value=>value.text==='teacher-5').reply_context.text.length,4000,'bounded long question');
  for(const id of [6,7,8,9,10])assert(![...messages.values()].find(value=>value.text==='teacher-'+id).reply_context,'blocked quote omits context, retains teacher');
  assert.equal(output.filter(value=>value.kind==='history_status').at(-1).state,'exhausted');
  assert(!JSON.stringify(output).includes('local-account'),'account identity stays inside official page');
  assert(!JSON.stringify(output).includes('excluded'),'excluded bodies never exported');
  const count=messages.size;now+=6000;tick();await settle();
  assert.equal(new Set(output.flatMap(value=>value.messages||[]).map(value=>value.id)).size,count,'idempotent history');
  const originalId=[...messages.values()].find(value=>value.text==='teacher-3').id;
  site.chatRoomMsgList.find(item=>item.id===3).quoteContent=question({msgContent:'稍后补齐的问题'});
  now+=1000;tick();await settle();
  const updated=output.flatMap(value=>value.messages||[]).filter(value=>value.id===originalId).at(-1);
  assert.equal(updated.reply_context.text,'稍后补齐的问题','late quote updates the existing teacher identity');
  window.__THS_TEACHER_SOURCE__.requestHistory('cancel');
  assert.equal(output.at(-1).state,'cancelled');
  site.roomInfo.contractState=1;
  site.chatRoomMsgList.push(denied(997,{feeStatus:1}));now+=1000;tick();await settle();
  assert(!output.flatMap(value=>value.messages||[]).some(value=>value.text==='teacher-997'));
  const audit=output.filter(value=>value.kind==='history_status').at(-1).audit;
  assert.equal(audit.locked,1);assert.equal(audit.non_teacher,2);assert.equal(audit.private_reply,1);
  site.roomInfo.contractState=3;
  site.chatRoomMsgList=site.chatRoomMsgList.filter(value=>value.id<=107).sort((a,b)=>a.msgDate-b.msgDate);
  const missed=message(700,3,{msgDate:now-1000});let latestCalls=0;
  site.getChatRecord=function(room){
    assert.equal(room,'public-room');assert.equal(arguments.length,1);assert.equal(this.isLoading,true);
    latestCalls++;this.chatRoomMsgList=[...this.chatRoomMsgList.slice(-49),missed];this.isLoading=false;
  };
  now+=16000;tick();await settle();now+=2000;tick();await settle();
  assert.equal(latestCalls,1,'reconcile latest public page without IM push');
  assert(output.flatMap(value=>value.messages||[]).some(value=>value.text==='teacher-700'),'lost push was backfilled');
  assert(output.filter(value=>value.kind==='history_status').at(-1).latest_sync,'successful public record response recorded');
  const burst=Array.from({length:80},(_,index)=>message(1001+index,3,{msgDate:missed.msgDate+(index+1)*1000}));
  let overlapCalls=0;
  site.getChatRecord=function(room,cursor){
    assert.equal(room,'public-room');assert.equal(this.isLoading,true);
    if(arguments.length===1){this.chatRoomMsgList=burst.slice(-50);}
    else {assert.equal(cursor,burst[30].id,'catch-up uses recent gap cursor, not archive oldest');overlapCalls++;this.chatRoomMsgList.unshift(missed,...burst.slice(0,30));}
    this.isLoading=false;
  };
  now+=120000;tick();await settle();now+=2000;tick();await settle();now+=2000;tick();await settle();
  assert.equal(overlapCalls,1,'backfill beyond latest 50 until old watermark overlaps');
  const ids=new Set(output.flatMap(value=>value.messages||[]).map(value=>value.id));
  assert.equal(ids.size,188,'all older history and 81 recovered messages retained by consumer IDs');
  assert.equal(output.filter(value=>value.kind==='history_status').at(-1).state,'exhausted');
  const learner=message(2001,1,{nickName:'学员甲',msgContent:'公开学员发言',msgDate:now-2000});
  Object.defineProperty(learner,'userId',{get(){throw Error('learner account UID read');}});
  const helper=message(2002,4,{nickName:'助教乙',msgDate:now-1500});
  const switchTeacher=message(2003,3,{msgDate:now-1000});
  site.chatRoomMsgList=[switchTeacher];
  let switches=[];
  site.lookType=2;
  site.getChatRecord=function(room,cursor){
    assert.equal(room,'public-room');assert.equal(cursor,undefined);
    const filter=this.lookType;
    setTimeout(()=>{this.chatRoomMsgList=filter===1?[learner,helper,switchTeacher,denied(2004,{replyFlag:2})]:[switchTeacher];this.hasMoreHistory=false;this.isLoading=false;},10);
  };
  site.changeLookType=function(type){switches.push(type);this.lookType=type;this.chatRoomMsgList=[];this.hasMoreHistory=true;this.isLoading=false;this.getChatRecord('public-room');};
  site.isLoading=true;
  window.__THS_TEACHER_SOURCE__.setViewMode('all');await settle();
  assert(!output.some(value=>value.view_mode==='all'),'wait for previous in-flight source request');
  site.isLoading=false;
  for(let n=0;n<4;n++){now+=1000;tick();await settle();}
  const allBatch=output.filter(value=>value.view_mode==='all'&&value.messages?.length).at(-1);
  assert.equal(allBatch.messages.length,3,'all public teacher/student/assistant messages only');
  assert.deepEqual(allBatch.messages.map(value=>value.role),['student','assistant','teacher']);
  assert.equal(allBatch.messages[0].display_name,'学员甲');
  assert.equal(allBatch.messages[0].source_id,'2001');
  assert(!JSON.stringify(allBatch).includes('excluded'));
  site.hasMoreHistory=true; // More archive exists, but a cached switch must not auto-fetch it.
  const marker=output.length;
  window.__THS_TEACHER_SOURCE__.setViewMode('teacher');await settle();
  for(let n=0;n<4;n++){now+=1000;tick();await settle();}
  assert.deepEqual(switches,[1,2],'only official public all/teacher filters requested');
  assert(output.slice(marker).some(value=>value.view_mode==='teacher'&&value.messages?.length));
  assert(output.slice(marker).filter(value=>value.view_mode==='teacher').every(value=>(value.messages||[]).every(item=>item.role==='teacher')),'late all-chat rows never enter teacher view');
  window.__THS_TEACHER_SOURCE__.setViewMode('all');await settle();
  for(let n=0;n<4;n++){now+=1000;tick();await settle();}
  assert.equal(site.hasMoreHistory,true,'cached archive cursor/has-more state restored after latest-page refresh');
  assert.equal(output.filter(value=>value.kind==='history_status'&&value.view_mode==='all').at(-1).pages,0,'cached return does not start a fresh 20-page history run');
  assert.deepEqual(switches,[1,2,1]);
  process.stdout.write('PASS: native history, quote nickname/IDs, missed-push reconciliation, public all/teacher switching, in-flight response isolation, deduplication and private/credential isolation\n');
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
