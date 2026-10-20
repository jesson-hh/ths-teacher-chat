/* Inject only into the official top-level chat page owned by the desktop host.
   No extension API, fetch, HTTP bridge, account storage or website credentials. */
(() => {
  'use strict';
  if (window.top !== window || typeof window.chrome?.webview?.postMessage !== 'function' ||
      window.__THS_DESKTOP_TEACHER_RUNNING__) return;
  window.__THS_DESKTOP_TEACHER_RUNNING__ = true;
  const ROUTE_ID = 'VzPD.Y8fIGs_', ROOM = 'yuen-route:' + ROUTE_ID, TEACHER = 'dom-teacher:' + ROUTE_ID;
  const IMAGE_HOSTS = new Set(['fileoss.zx093.com', 'tgpub.zx093.com', 'ynimg.zx093.cn']);
  let busy = false, stopped = false, generation = 0, lastEmit = 0, roomNode = null, timer;
  let baselineAt = Date.now(), seen = new Set(), lastSignature = '';
  let vmScope = '', autoHistory = false, filterConfigured = false, projectedCount = 0;
  let viewMode='teacher',requestedView=window.__THS_CHAT_VIEW_MODE__==='all'?'all':'teacher';
  const viewCache=new Map();
  let restoringView=null;
  const exported = new Map(), digests = new Map();
  const history = {state:'idle', pages:0, remaining:0, pending:null, lastStatus:'', oldestTime:Infinity, oldestId:null,catchup:null};
  const latestSync={state:'idle',pending:null,lastRequest:Date.now(),syncedAt:null};
  let audit=null;
  function route() {
    try {
      const url = new URL(location.href);
      return url.origin === 'https://tougu.zx093.cn' && url.pathname === '/touguapp/webChatRoom/index.html' &&
        url.hash.split('?')[0] === '#/chatRoom' && new URLSearchParams(url.hash.split('?')[1]).get('teacherId') === ROUTE_ID &&
        !url.username && !url.password;
    } catch { return false; }
  }
  function publicRoom() {
    if (!route() || navigator.onLine === false || !document.querySelector('.chat-room-header-right .user-info .exit-btn')) return null;
    const room = document.querySelector('.chat-room-wrapper>.chat-room-container>.chat-room');
    return room && room.dataset.yuenHistory !== 'true' && room.querySelector(':scope>.chat-room-content') ? room : null;
  }
  function teacherParts(row) {
    const meta = row.querySelector(':scope>.content>.msg-c>.nick-name');
    if(!meta)return null;
    const student=!!meta.querySelector(':scope>.user,:scope>.me');
    if((viewMode==='teacher'&&student)||(!student&&(!meta.querySelector(':scope>img.vpg')||!meta.querySelector(':scope>span.t-no'))))return null;
    const body = row.querySelector(':scope>.content>.msg-c>.info-data');
    // Official renderer: vpg/t-no means userType 3|4; only userType 4 gets bg-blue.
    if (!body || (viewMode==='teacher'&&body.classList.contains('bg-blue')) || body.classList.contains('bg-vip') ||
        body.querySelector(':scope>.text>.vip-main-msg') || row.querySelector(':scope>.content>.msg-c>.vip-message')) return null;
    return {body, date: row.querySelector(':scope>.date-box>.date'),role:student?'student':body.classList.contains('bg-blue')?'assistant':'teacher',
      display_name:publicName(meta.querySelector(':scope>.name')?.textContent)};
  }
  function sanitize(value, limit=10000) {
    let text = value.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '').replace(/[\uD800-\uDFFF]/gu, '').trim().slice(0, limit);
    return /[\uD800-\uDBFF]$/.test(text) ? text.slice(0, -1) : text;
  }
  function ownText(node, allowQuoteRoot=false) {
    if (!node) return '';
    let text = '';
    function visit(current) {
      if (text.length >= 10000) return;
      if (current.nodeType === 3) { text += current.nodeValue || ''; return; }
      if (current.nodeType !== 1 || (current.matches('.reply-msg,.quote-msg,blockquote,script,style,iframe,object,embed') &&
          !(allowQuoteRoot && current===node))) return;
      if (current.tagName === 'BR') text += '\n';
      for (const child of current.childNodes) visit(child);
    }
    visit(node); return sanitize(text);
  }
  function imageUrl(value) {
    if (typeof value !== 'string' || !value.trim() || value.length > 2048) return null;
    try {
      const url = new URL(value, 'https://fileoss.zx093.com/');
      if (url.protocol === 'http:' && IMAGE_HOSTS.has(url.hostname)) url.protocol = 'https:';
      return url.protocol === 'https:' && IMAGE_HOSTS.has(url.hostname) && !url.port && !url.username &&
        !url.password && !url.search && !url.hash ? url.href : null;
    } catch { return null; }
  }
  function publicVm() {
    let vm = document.querySelector('.chat-room-wrapper')?.__vue__;
    for (let n=0; vm && !Array.isArray(vm.chatRoomMsgList) && n<8; n++) vm=vm.$parent;
    return vm && Array.isArray(vm.chatRoomMsgList) && vm.roomInfo?.id && vm.roomInfo.userId && !vm._loggingOut ? vm : null;
  }
  function scope(vm) { return vm ? String(vm.roomInfo.id)+'|'+String(vm.roomInfo.userId) : ''; }
  function stamp(value) {
    if (typeof value==='number') return value<1e11?value*1000:value;
    if (typeof value!=='string') return NaN;
    if (/^\d{10,13}$/.test(value)) return Number(value)*(value.length===10?1000:1);
    return Date.parse(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)?value.replace(' ','T')+'+08:00':value);
  }
  function plainHtml(value) {
    if (typeof value!=='string') return '';
    const template=document.createElement('template');
    template.innerHTML=value.slice(0,100000).replace(/<br\s*\/?\s*>/gi,'\n');
    template.content.querySelectorAll('script,style,iframe,object,embed,.reply-msg,.quote-msg,blockquote').forEach(node=>node.remove());
    return sanitize(template.content.textContent||'');
  }
  function publicName(value) {
    return typeof value==='string'?sanitize(value.replace(/[\r\n\t\u202A-\u202E\u2066-\u2069]/g,' '),80):'';
  }
  function sourceId(value) {
    if(typeof value==='number'&&!Number.isSafeInteger(value))return '';
    const text=typeof value==='string'||typeof value==='number'?String(value):'';
    return /^[\w.\-:/+=@]{1,256}$/.test(text)?text:'';
  }
  function publicMetadata(value) {
    const name=publicName(value.nickName),id=sourceId(value.id);
    return {...(name?{display_name:name}:{}),...(id?{source_id:id}:{})};
  }
  function nativeQuote(message,vm,replyTime) {
    // Only the public quote attached to an already permitted teacher reply;
    // never look up a member or search student/private-message lists.
    try {
      let quote=message.quoteContent;
      if(typeof quote==='string')quote=JSON.parse(quote.slice(0,100000));
      if(!quote||typeof quote!=='object'||Array.isArray(quote))return null;
      if(Number(quote.dr)===1||quote.isRevoked||Number(quote.replyFlag)===2)return null;
      if(quote.chatRoomId!=null&&String(quote.chatRoomId)!==String(vm.roomInfo.id))return null;
      if(quote.bizType!=null&&Number(quote.bizType)!==1)return null;
      const type=Number(quote.msgType);
      if(![0,1,2,3,5].includes(type))return null;
      if(Number(quote.feeStatus)===1&&Number(vm.roomInfo.contractState)!==3&&Number(vm.roomInfo.orderStatus)===1)return null;
      const time=stamp(quote.msgDate);
      if(Number.isFinite(time)&&(time<0||time>replyTime+120000))return null;
      let raw=quote.msgContent,text='',images=[];
      if([1,2,3].includes(type)&&typeof raw==='string'){try{raw=JSON.parse(raw.slice(0,100000));}catch{}}
      if(type===0||type===5)text=sanitize(plainHtml(raw),4000);
      else if(type===1){const url=imageUrl(typeof raw==='object'?raw?.imgUrl:raw);if(url)images=[url];}
      else text=sanitize(plainHtml(raw?.title),4000);
      return text||images.length?{text,images,published_at:Number.isSafeInteger(time)?new Date(time).toISOString():null,...publicMetadata(quote)}:null;
    } catch {return null;}
  }
  async function makeRecord(time,text,images,role='teacher',originalId='') {
    const published_at=new Date(time).toISOString();
    const author=role==='teacher'?TEACHER:'dom-'+role+':'+ROUTE_ID;
    const material=JSON.stringify([ROOM,author,published_at,text,images,...(originalId?[originalId]:[])]);
    let id=digests.get(material);
    if (!id) {
      const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(material));
      id='dom-'+[...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join('');
      if(digests.size>=5000)digests.delete(digests.keys().next().value);
      digests.set(material,id);
    }
    return {id,author_id:author,role,public:true,published_at,text,images};
  }
  function newAudit(total) {
    return {total,teacher:0,projected:0,duplicate:0,non_teacher:0,withdrawn:0,private_reply:0,
      room_mismatch:0,other_biz:0,locked:0,unsupported:0,bad_date:0,empty:0,first:null,last:null};
  }
  async function projectNative(message,vm,now,counts) {
    // Test role, public-list scope, withdrawal and access before touching body.
    // Read an attached public quote only after the parent teacher is permitted.
    const role=({1:'student',3:'teacher',4:'assistant'})[Number(message?.userType)];
    if(!role||(viewMode==='teacher'&&role!=='teacher')){counts.non_teacher++;return null;}
    if(role==='teacher')counts.teacher++;else counts.non_teacher++;
    if(Number(message.dr)===1||message.isRevoked){counts.withdrawn++;return null;}
    if(Number(message.replyFlag)===2){counts.private_reply++;return null;}
    if(String(message.chatRoomId)!==String(vm.roomInfo.id)){counts.room_mismatch++;return null;}
    if(message.bizType!==undefined&&Number(message.bizType)!==1){counts.other_biz++;return null;}
    const type=Number(message.msgType);
    if(![0,1,2,3,5].includes(type)){counts.unsupported++;return null;}
    if(Number(message.feeStatus)===1&&Number(vm.roomInfo.contractState)!==3&&Number(vm.roomInfo.orderStatus)===1){counts.locked++;return null;}
    const time=stamp(message.msgDate);
    if(!Number.isSafeInteger(time)||time<0||time>now+120000){counts.bad_date++;return null;}
    let raw=message.msgContent,text='',images=[];
    if ((type===1||type===2||type===3) && typeof raw==='string') {
      try { raw=JSON.parse(raw.slice(0,100000)); } catch {}
    }
    if (type===0||type===5) text=plainHtml(raw);
    else if (type===1) {
      const url=imageUrl(typeof raw==='object'?raw?.imgUrl:raw); if(url)images=[url];
    } else text=plainHtml(raw?.title);
    if(!text&&!images.length){counts.empty++;return null;}
    const originalId=sourceId(message.id);
    const record=await makeRecord(time,text,images,role,originalId);
    Object.assign(record,publicMetadata(message));
    const reply=type===0?nativeQuote(message,vm,time):null;
    if(reply)record.reply_context=reply;
    return record;
  }
  function historyStatus(vm) {
    const time=vm?stamp(vm.chatRoomMsgList[0]?.msgDate):NaN;
    if(Number.isSafeInteger(time)&&time<history.oldestTime){history.oldestTime=time;history.oldestId=vm.chatRoomMsgList[0]?.id;}
    const value={kind:'history_status',state:history.state,pages:history.pages,
      view_mode:viewMode,
      loaded:vm?Math.min(vm.chatRoomMsgList.length,50000):0,teachers:Math.min(audit?.teacher??projectedCount,5000),
      has_more:!!vm&&vm.hasMoreHistory!==false,oldest:Number.isFinite(history.oldestTime)?new Date(history.oldestTime).toISOString():null,
      latest_state:latestSync.state,latest_sync:latestSync.syncedAt,
      ...(audit?{audit}: {})};
    const signature=JSON.stringify(value);
    if(signature!==history.lastStatus){window.chrome.webview.postMessage(signature);history.lastStatus=signature;}
  }
  function resetHistory() {
    history.state='idle';history.pages=0;history.remaining=0;history.pending=null;history.lastStatus='';
    history.oldestTime=Infinity;history.oldestId=null;
    history.catchup=null;
    latestSync.state='idle';latestSync.pending=null;latestSync.lastRequest=Date.now();latestSync.syncedAt=null;
    autoHistory=false;filterConfigured=false;projectedCount=0;vmScope='';audit=null;exported.clear();digests.clear();
    restoringView=null;
  }
  function saveView(vm) {
    if(!vm||!autoHistory||scope(vm)!==vmScope)return;
    const first=vm.chatRoomMsgList[0],time=stamp(first?.msgDate);
    if(Number.isSafeInteger(time)&&time<history.oldestTime){history.oldestTime=time;history.oldestId=first.id;}
    viewCache.set(viewMode,{scope:vmScope,state:history.state==='loading'?'idle':history.state,
      pages:history.pages,oldestTime:history.oldestTime,oldestId:history.oldestId,
      hasMore:history.catchup?history.catchup.hasMore:vm.hasMoreHistory,
      lastTime:stamp(vm.chatRoomMsgList.at(-1)?.msgDate),syncedAt:latestSync.syncedAt,
      exported:new Map(exported)});
  }
  function requestHistory(action) {
    const vm=publicRoom()?publicVm():null;
    if(action==='cancel'){history.remaining=0;history.state='cancelled';historyStatus(vm);return;}
    if(action!=='older')return;
    if(!vm||typeof vm.getChatRecord!=='function'){history.state='unavailable';historyStatus(vm);return;}
    if(history.remaining||history.pending)return;
    history.remaining=20;history.state='loading';historyStatus(vm);
    scan().catch(()=>{});
  }
  function reconcileLatest(vm,now) {
    if(latestSync.pending){
      if(scope(vm)!==latestSync.pending.scope){resetHistory();return;}
      if(vm.isLoading){if(now-latestSync.pending.started>15000)latestSync.state='error';return;}
      const target=latestSync.pending.lastTime;
      const success=vm.chatRoomMsgList!==latestSync.pending.list;
      latestSync.pending=null;latestSync.state=success?'idle':'error';
      if(success){
        latestSync.syncedAt=new Date(now).toISOString();
        const firstTime=stamp(vm.chatRoomMsgList[0]?.msgDate);
        if(Number.isSafeInteger(target)&&Number.isSafeInteger(firstTime)&&firstTime>target){
          history.catchup={target,state:history.state,hasMore:vm.hasMoreHistory};
          history.remaining=20;history.state='loading';vm.hasMoreHistory=true;
        }
      }
      return;
    }
    if(now-latestSync.lastRequest<15000||vm.isLoading||history.pending||history.remaining||typeof vm.getChatRecord!=='function')return;
    latestSync.lastRequest=now;latestSync.state='loading';
    latestSync.pending={list:vm.chatRoomMsgList,scope:scope(vm),started:now,lastTime:stamp(vm.chatRoomMsgList.at(-1)?.msgDate)};
    vm.isLoading=true;
    // Re-read the site's own latest public page, even if an IM push was lost.
    // Retained host history is merged by ID; the reader does not jump pages.
    try{vm.getChatRecord(vm.roomInfo.id);}
    catch{vm.isLoading=false;latestSync.pending=null;latestSync.state='error';}
  }
  function historyStep(vm,now) {
    // The official method completes asynchronously. Refresh the archive cursor
    // before issuing the next request, rather than reuse the prior page cursor.
    const oldest=vm.chatRoomMsgList[0],oldestTime=stamp(oldest?.msgDate);
    if(Number.isSafeInteger(oldestTime)&&oldestTime<history.oldestTime){history.oldestTime=oldestTime;history.oldestId=oldest?.id;}
    if(history.pending) {
      if(scope(vm)!==history.pending.scope){resetHistory();return;}
      if(vm.isLoading) {
        if(now-history.pending.started>15000){history.remaining=0;history.state='error';historyStatus(vm);}
        return;
      }
      const first=vm.chatRoomMsgList[0],firstTime=stamp(first?.msgDate);
      const advanced=String(first?.id)!==history.pending.cursor&&Number.isSafeInteger(firstTime)&&firstTime<=history.pending.time;
      history.pending=null;
      if(!advanced && vm.hasMoreHistory!==false){history.remaining=0;history.state='error';historyStatus(vm);return;}
      if(advanced){history.pages++;history.remaining=Math.max(0,history.remaining-1);}
    }
    if(history.catchup){
      const firstTime=stamp(vm.chatRoomMsgList[0]?.msgDate);
      if(Number.isSafeInteger(firstTime)&&firstTime<=history.catchup.target){
        vm.hasMoreHistory=history.catchup.hasMore;
        history.state=history.catchup.state;history.remaining=0;history.catchup=null;
      }
    }
    if(vm.hasMoreHistory===false){history.remaining=0;history.state='exhausted';historyStatus(vm);return;}
    if(projectedCount>=5000||vm.chatRoomMsgList.length>=50000){history.remaining=0;history.state='limit';historyStatus(vm);return;}
    if(!history.remaining){if(history.state==='loading')history.state='idle';historyStatus(vm);return;}
    if(vm.isLoading||latestSync.pending||!vm.chatRoomMsgList.length)return;
    const cursor=history.catchup?vm.chatRoomMsgList[0]?.id:(history.oldestId??vm.chatRoomMsgList[0]?.id);
    if((typeof cursor!=='string'&&typeof cursor!=='number')||typeof vm.getChatRecord!=='function'){
      history.remaining=0;history.state='unavailable';historyStatus(vm);return;
    }
    history.pending={cursor:String(cursor),scope:scope(vm),started:now,time:history.catchup?stamp(vm.chatRoomMsgList[0]?.msgDate):(Number.isFinite(history.oldestTime)?history.oldestTime:stamp(vm.chatRoomMsgList[0]?.msgDate))};
    vm.isLoading=true;
    // The site's own read-only public history method, with its existing login
    // and permission checks. No token extraction or reconstructed HTTP request.
    try { vm.getChatRecord(vm.roomInfo.id,cursor); }
    catch {vm.isLoading=false;history.pending=null;history.remaining=0;history.state='error';}
    historyStatus(vm);
  }
  function setViewMode(mode) {
    if(mode!=='teacher'&&mode!=='all')return;
    requestedView=mode;
    // Let an in-flight official read finish before changing its filter, so a
    // delayed response cannot be tagged as belonging to the other view.
    scan().catch(()=>{});
  }
  window.__THS_TEACHER_SOURCE__=Object.freeze({requestHistory,setViewMode});
  async function project(row, now) {
    const parts = teacherParts(row);
    if (!parts || row.getClientRects().length === 0) return null;
    const date = parts.date?.textContent.trim() || '';
    if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(date)) return null;
    const time = Date.parse(date.replace(' ', 'T') + '+08:00');
    if (!Number.isSafeInteger(time) || time < 0 || time > now + 120000) return null;
    const body = parts.body;
    let text = ownText(body.querySelector(':scope>.text>.msg-content'));
    if (!text) text = ownText(body.querySelector(':scope>.link>.link-content'));
    if (!text) text = ownText(body.querySelector(':scope>.article>div>.title,:scope>.video>div>.title'));
    const image = body.querySelector(':scope>.image>.image-con img.el-image__inner');
    const url = imageUrl(image?.currentSrc || image?.getAttribute('src') || ''), images = url ? [url] : [];
    if (!text && !images.length) return null;
    const record=await makeRecord(time,text,images,parts.role);
    if(parts.display_name)record.display_name=parts.display_name;
    const quote=body.querySelector(':scope>.text>.reply-msg');
    if(quote&&!quote.querySelector('.vip-text,.vip-main-msg')) {
      const question=sanitize(ownText(quote.querySelector(':scope>.custom-msg.quote-msg'),true),4000);
      const quoteImage=quote.querySelector(':scope>.custom-msg img.el-image__inner');
      const quoteUrl=imageUrl(quoteImage?.currentSrc||quoteImage?.getAttribute('src')||'');
      const date=quote.querySelector(':scope>.custom-info>.reply-name')?.textContent.match(/\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/)?.[0];
      const quoteTime=stamp(date),validTime=Number.isSafeInteger(quoteTime)&&quoteTime>=0&&quoteTime<=time+120000;
      const replyLabel=quote.querySelector(':scope>.custom-info>.reply-name')?.textContent||'';
      const name=publicName(replyLabel.replace(/^@/,'').split(/[：:](?=\d{4}-)/)[0]);
      if(question||quoteUrl)record.reply_context={text:question,images:quoteUrl?[quoteUrl]:[],published_at:validTime?new Date(quoteTime).toISOString():null,...(name?{display_name:name}:{})};
    }
    return record;
  }
  function emit(messages, mode, ready) {
    // The host must validate WebMessageReceived.Source and its current URI before ingesting.
    window.chrome.webview.postMessage(JSON.stringify({identity_mode: 'dom_teacher', room_id: ROOM, teacher_id: TEACHER,
      source: 'yuen-native', observed_at: new Date().toISOString(), mode, view_mode:viewMode, source_ready: ready, messages}));
    lastEmit = Date.now();
  }
  async function scan() {
    if (busy || stopped) return;
    busy = true; const epoch = generation, now = Date.now();
    try {
      const room = publicRoom();
      if (!room) {
        roomNode = null; seen.clear(); lastSignature = ''; baselineAt = now;
        resetHistory();viewCache.clear();
        if (now - lastEmit >= 5000) emit([], 'snapshot', false);
        return;
      }
      const replaced = room !== roomNode;
      if (replaced) { roomNode = room; seen.clear(); baselineAt = now; }
      const vm=publicVm(), records=new Map();
      if(vm && vmScope!==scope(vm)) {
        if(vmScope)emit([],'snapshot',false);
        resetHistory();viewCache.clear();vmScope=scope(vm);baselineAt=now;
      }
      if(requestedView!==viewMode){
        if(vm?.isLoading)return;
        saveView(vm);
        const cached=viewCache.get(requestedView);
        generation++;resetHistory();viewMode=requestedView;vmScope=scope(vm);
        if(cached&&cached.scope===vmScope){
          Object.assign(history,{state:cached.state,pages:cached.pages,oldestTime:cached.oldestTime,oldestId:cached.oldestId});
          autoHistory=true;latestSync.syncedAt=cached.syncedAt;restoringView=cached;
          for(const [id,signature] of cached.exported)exported.set(id,signature);
        }
        roomNode=null;seen.clear();lastSignature='';baselineAt=now;
        return;
      }
      if(vm&&!filterConfigured){
        filterConfigured=true;
        const expected=viewMode==='all'?1:2;
        // Only the site's public all/teacher filter; never its private views.
        if(Number(vm.lookType)!==expected&&typeof vm.changeLookType==='function'){
          try{
            if(restoringView){
              latestSync.state='loading';latestSync.lastRequest=now;
              latestSync.pending={list:vm.chatRoomMsgList,scope:scope(vm),started:now,lastTime:restoringView.lastTime};
            }
            vm.changeLookType(expected);vm.isLoading=true;return;
          }catch{latestSync.pending=null;latestSync.state='error';}
        }
      }
      if(vm&&restoringView){
        if(vm.isLoading)return;
        vm.hasMoreHistory=restoringView.hasMore;
        restoringView=null;
      }
      const rows=vm?vm.chatRoomMsgList.slice(-50000):[...room.querySelectorAll(':scope>.chat-room-content .chat')].slice(-300);
      const counts=vm?newAudit(rows.length):null;
      for (const row of rows) {
        const record = vm?await projectNative(row,vm,now,counts):await project(row, now);
        if (epoch !== generation || stopped || !route() || (vm&&scope(vm)!==vmScope)) return;
        if(record){if(counts&&records.has(record.id))counts.duplicate++;records.set(record.id,record);}
      }
      // Recheck after asynchronous digests: logout/navigation may happen while hashing.
      if (epoch !== generation || publicRoom() !== room || stopped) return;
      const ordered = [...records.values()].sort((a, b) => a.published_at.localeCompare(b.published_at) || a.id.localeCompare(b.id));
      if(vm) {
        projectedCount=ordered.length;
        counts.projected=ordered.length;counts.first=ordered[0]?.published_at||null;counts.last=ordered.at(-1)?.published_at||null;audit=counts;
        const unseen=ordered.filter(record=>exported.get(record.id)!==JSON.stringify(record));
        let batch=[],bytes=0;
        for(const record of unseen){
          const size=new TextEncoder().encode(JSON.stringify(record)).length+1;
          if(batch.length>=100||bytes+size>220000){emit(batch,'snapshot',true);batch=[];bytes=0;}
          batch.push(record);bytes+=size;exported.set(record.id,JSON.stringify(record));
        }
        if(batch.length)emit(batch,'snapshot',true);
        if(exported.size>5000){exported.clear();for(const record of ordered.slice(-5000))exported.set(record.id,JSON.stringify(record));}
        if(!unseen.length&&now-lastEmit>=5000)emit([],'snapshot',true);
        if(!autoHistory&&vm.chatRoomMsgList.length&&!vm.isLoading){autoHistory=true;requestHistory('older');}
        reconcileLatest(vm,now);
        historyStep(vm,now);
        return;
      }
      const latest = []; let bytes = 0;
      for (const record of ordered.slice(-100).reverse()) {
        const size = new TextEncoder().encode(JSON.stringify(record)).length + 1;
        if (bytes + size > 220000) break;
        latest.unshift(record); bytes += size;
      }
      const signature = JSON.stringify(latest), fresh = replaced ? [] : latest.filter(record => !seen.has(record.id) &&
        Date.parse(record.published_at) >= baselineAt && Date.parse(record.published_at) >= now - 60000);
      seen = new Set(ordered.map(record => record.id));
      if (signature !== lastSignature || now - lastEmit >= 5000) {
        emit(fresh.length ? fresh : latest, fresh.length ? 'live' : 'snapshot', true); lastSignature = signature;
      }
    } finally { busy = false; }
  }
  function reset() { generation++; roomNode = null; seen.clear(); lastSignature = ''; lastEmit = 0; scan().catch(() => {}); }
  window.addEventListener('hashchange', () => {
    generation++; roomNode = null; seen.clear(); lastSignature = ''; lastEmit = 0;
    if (!route()) emit([], 'snapshot', false);
    else scan().catch(() => {});
  });
  window.addEventListener('online', reset);
  // Native WinForms heartbeat is independent of Chromium's hidden-tab timers.
  // scan() still validates the current route, login marker and public DOM.
  window.addEventListener('ths-desktop-source-tick', () => scan().catch(() => {}));
  window.addEventListener('offline', () => { generation++; roomNode = null; viewCache.clear();emit([], 'snapshot', false); });
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') reset(); });
  window.addEventListener('pagehide', () => { emit([], 'snapshot', false); stopped = true; generation++; clearInterval(timer); });
  window.addEventListener('pageshow', () => {
    if (stopped) { stopped = false; timer = setInterval(() => scan().catch(() => {}), 1000); }
    reset();
  });
  timer = setInterval(() => scan().catch(() => {}), 1000);
  scan().catch(() => {});
})();
