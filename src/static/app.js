'use strict';
const $ = s => document.querySelector(s);
const state = {page:'monitors', monitors:[], channels:[], messages:[], paging:false, refreshBusy:false};
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names = {admin:['管理员账户','管理你的登录信息。','修改管理员账户和密码，维护工作空间访问权限。',''],monitors:['监控项','让重要发言，及时抵达。','按群组和用户追踪消息，将通知送达你选择的渠道。','＋ 新建监控项'],messages:['消息记录','每一条信号，都有迹可循。','查看捕获的消息、推送结果，随时回到原始对话。',''],channels:['推送渠道','一个渠道，连接多处关注。','配置 Telegram Bot 与 Bark，在不同监控项中灵活复用。','＋ 新建渠道'],settings:['Telegram 连接','连接你的 Telegram。','在这里完成账号登录、网络设置和群组查询。','']};
const formValue = (form, name) => form.elements.namedItem(name);
let toastTimer;
function toast(message, error=false) { const el=$('#toast'); el.textContent=message; el.classList.toggle('error',error); el.hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>el.hidden=true,5000); }
async function api(path, method='GET', body) {
  const response=await fetch('/api'+path,{method,headers:body ? {'Content-Type':'application/json'} : {},body:body ? JSON.stringify(body) : undefined});
  if(response.status===401){window.location.replace('/login');throw new Error('登录已失效，请重新登录');}if(response.status===503){window.location.replace('/');throw new Error('请先初始化管理员账户');}if(!response.ok) { let data; try{data=await response.json();}catch{data={};} throw new Error(typeof data.detail==='string' ? data.detail : `请求失败 (${response.status})`); }
  return response.json();
}
const safe = fn => async event => { try{await fn(event);}catch(error){toast(error.message,true);} };
function empty(symbol,title,description) { return `<div class="empty"><div class="symbol">${symbol}</div><b>${title}</b><p>${description}</p></div>`; }
function channelName(id) { return state.channels.find(c=>c.id===id)?.name ?? '已删除渠道'; }
function renderMonitors() {
  $('#monitor-count').textContent=`(${state.monitors.length})`;
  $('#monitor-list').innerHTML=state.monitors.map(m=>`<article class="card"><div class="card-top"><h3>${escapeHTML(m.name)}</h3><span class="tag ${m.enabled?'on':'off'}">${m.enabled?'● 已启用':'已暂停'}</span></div><div class="card-data">群组 <b>${m.chat_id}</b><br>用户 <b>${escapeHTML(m.users.join(', '))}</b><br>已捕获 <b>${m.message_count}</b> 条消息</div><div class="chips">${m.channel_ids.map(id=>`<span class="chip">${escapeHTML(channelName(id))}</span>`).join('') || '<span class="chip">仅记录，不推送</span>'}</div><div class="card-actions"><button class="link-button" data-action="messages" data-id="${m.id}">查看消息 ↗</button><button class="link-button" data-action="edit-monitor" data-id="${m.id}">编辑</button><button class="link-button delete" data-action="delete-monitor" data-id="${m.id}">删除</button></div></article>`).join('') || empty('▦','从第一个监控项开始','设置群组和关注的用户，再选择通知送达的地方。');
  const filter=$('#message-filter'), selected=filter.value;
  filter.innerHTML='<option value="">全部监控项</option>'+state.monitors.map(m=>`<option value="${m.id}">${escapeHTML(m.name)}</option>`).join('');
  filter.value=selected;
}
function renderChannels() {
  $('#channel-list').innerHTML=state.channels.map(c=>`<article class="card"><div class="card-top"><h3>${escapeHTML(c.name)}</h3><span class="tag ${c.enabled?'on':'off'}">${c.enabled?'● 已启用':'已暂停'}</span></div><div class="card-data"><b>${c.kind==='telegram'?'Telegram Bot':'Bark · POST /push'}</b><br>${escapeHTML(c.kind==='telegram'?`接收 Chat ID：${c.config.chat_id}`:c.config.server)}<br>被 <b>${state.monitors.filter(m=>m.channel_ids.includes(c.id)).length}</b> 个监控项使用</div><div class="chips"><span class="chip">${c.kind==='telegram'?'Bot Token 已保存':'Device Key 已保存'}</span>${c.kind==='bark'&&c.config.params?.sound?`<span class="chip">♫ ${escapeHTML(c.config.params.sound)}</span>`:''}</div><div class="card-actions"><button class="link-button" data-action="test-channel" data-id="${c.id}">测试推送 ↗</button><button class="link-button" data-action="edit-channel" data-id="${c.id}">编辑</button><button class="link-button delete" data-action="delete-channel" data-id="${c.id}">删除</button></div></article>`).join('') || empty('⇄','建立你的通知网络','添加多个 Bot 或 Bark 渠道，在监控项中自由组合。');
}
function renderMessages() {
  const statuses={sent:'已发送',pending:'等待推送',failed:'推送失败',cancelled:'已取消'};
  $('#message-list').innerHTML=state.messages.map(m=>`<article class="message"><div class="message-head"><b>${escapeHTML(m.monitor_name)} · ${escapeHTML(m.chat_title)}</b><span>${escapeHTML(new Date(m.sent_at).toLocaleString())}</span></div><p class="hint">${escapeHTML(m.sender_name)} ${m.username?'@'+escapeHTML(m.username):''} · 用户 ${m.sender_id ?? '未知'} · 消息 #${m.message_id}</p><div class="message-text">${escapeHTML(m.text)}</div><div class="message-foot">${m.deliveries.map(d=>`<span class="delivery ${d.status}" title="${escapeHTML(d.error || '')}">${escapeHTML(d.channel_name)} · ${statuses[d.status] || escapeHTML(d.status)}${d.attempts?` (${d.attempts})`:''}${d.status==='failed'&&d.channel_id?` <button data-action="retry" data-id="${d.id}">重试 ↻</button>`:''}</span>`).join('') || '<span class="muted">仅记录 / 捕获时无启用的渠道</span>'}${m.url?`<a href="${escapeHTML(m.url)}" target="_blank" rel="noopener noreferrer">打开原消息 ↗</a>`:''}</div></article>`).join('') || empty('☷','这里会留下新的消息','启用监控并登录 Telegram 后，匹配到的发言会显示在这里。');
}
async function refreshConfig() { [state.monitors,state.channels]=await Promise.all([api('/monitors'),api('/channels')]);renderMonitors();renderChannels(); }
async function refreshStatus() {
  const s=await api('/status');
  for(const name of ['monitors','messages','channels','failed']) $('#stat-'+name).textContent=s.counts[name];
  $('#connection').textContent=s.state;
  $('#login-state').textContent=s.state+(s.error?' · '+s.error:'');
  $('#last-refresh').textContent='最近更新 '+new Date().toLocaleTimeString();
}
async function loadMessages(append=false) {
  if(append && state.messageLoading)return;
  const requestId=(state.messageRequest??0)+1;
  state.messageRequest=requestId;state.messageLoading=true;state.paging=append;
  $('#load-more').disabled=true;
  const params=new URLSearchParams();
  if($('#message-filter').value)params.set('monitor_id',$('#message-filter').value);
  if(append&&state.messages.length)params.set('before',state.messages.at(-1).id);
  try{
    const records=await api('/messages?'+params);
    if(requestId!==state.messageRequest)return;
    state.messages=append?[...state.messages,...records]:records;
    $('#load-more').hidden=records.length<50;
    renderMessages();
  }finally{
    if(requestId===state.messageRequest){state.messageLoading=false;$('#load-more').disabled=false;}
  }
}
async function showPage(page) {
  state.page=page;
  document.querySelectorAll('.page').forEach(el=>el.hidden=el.id!=='page-'+page);
  document.querySelectorAll('.nav').forEach(el=>el.classList.toggle('active',el.dataset.page===page));
  const [label,title,description,action]=names[page];
  $('#breadcrumb').textContent=label;$('#page-title').textContent=title;$('#page-description').textContent=description;
  $('#primary-action').hidden=!action;$('#primary-action').textContent=action;
  if(page==='messages')await loadMessages();
  if(page==='settings')await loadSettings();if(page==='admin'&&!state.accountChanged){const f=$('#admin-form');f.querySelectorAll('input,button').forEach(el=>el.disabled=true);try{const info=await api('/admin');formValue(f,'username').value=info.username;}finally{f.querySelectorAll('input,button').forEach(el=>el.disabled=false);}}
}
function openMonitor(id) {
  const f=$('#monitor-form');f.reset();
  const m=state.monitors.find(m=>m.id===id);
  formValue(f,'id').value=id || '';formValue(f,'enabled').checked=m?.enabled??true;
  for(const key of ['name','chat_id'])formValue(f,key).value=m?.[key]??'';
  formValue(f,'users').value=m?.users.join('\n')??'';
  $('#monitor-dialog-title').textContent=id?'编辑监控项':'新建监控项';
  $('#channel-choices').innerHTML=state.channels.map(c=>`<label class="check"><input type="checkbox" name="channels" value="${c.id}" ${m?.channel_ids.includes(c.id)?'checked':''}>${escapeHTML(c.name)} <small>${c.kind}${c.enabled?'':' · 已暂停'}</small></label>`).join('') || '<p class="hint">还没有推送渠道，可稍后在“推送渠道”页面添加。</p>';
  $('#monitor-dialog').showModal();
}
const barkFields=['icon','sound','group','level','badge','volume','autoCopy','call','isArchive','copy'];
function switchChannelKind() { const kind=formValue($('#channel-form'),'kind').value;$('#telegram-fields').hidden=kind!=='telegram';$('#bark-fields').hidden=kind!=='bark';for(const el of $('#telegram-fields').querySelectorAll('input'))el.disabled=kind!=='telegram';for(const el of $('#bark-fields').querySelectorAll('input,select,textarea'))el.disabled=kind!=='bark'; }
function openChannel(id) {
  const f=$('#channel-form');f.reset();
  const channel=state.channels.find(c=>c.id===id), c=channel?.config??{};
  formValue(f,'id').value=id||'';formValue(f,'name').value=channel?.name??'';formValue(f,'kind').value=channel?.kind??'telegram';formValue(f,'enabled').checked=channel?.enabled??true;
  for(const key of ['token','chat_id','message_thread_id','server','device_key'])formValue(f,key).value=c[key]??(key==='server'?'https://api.day.app':'');
  formValue(f,'disable_notification').checked=!!c.disable_notification;
  const params={...(c.params??{})};
  for(const key of barkFields){const el=formValue(f,key);if(el.type==='checkbox')el.checked=params[key]===1||params[key]==='1';else el.value=params[key]??'';delete params[key];}
  formValue(f,'extra').value=Object.keys(params).length?JSON.stringify(params,null,2):'';
  $('#channel-dialog-title').textContent=id?'编辑推送渠道':'新建推送渠道';switchChannelKind();$('#channel-dialog').showModal();
}
function confirmDelete(message) {return new Promise(resolve=>{const d=$('#confirm-dialog');$('#confirm-text').textContent=message;const done=result=>{d.close();resolve(result);};$('#confirm-ok').onclick=()=>done(true);$('#confirm-cancel').onclick=()=>done(false);d.oncancel=()=>resolve(false);d.showModal();});}
async function loadSettings(){
  const f=$('#settings-form'),requestId=(state.settingsRequest??0)+1;
  state.settingsRequest=requestId;
  f.querySelectorAll('input,button').forEach(el=>el.disabled=true);
  try{
    const s=await api('/settings');
    if(requestId!==state.settingsRequest)return;
    for(const key of ['api_id','api_hash','proxy_host','proxy_port','proxy_username','proxy_password','http_proxy','max_attempts']){
      formValue(f,key).value=s[key]??'';
      if(s[key+'_configured'])formValue(f,key).placeholder='已保存，留空保留';
    }
    formValue(f,'clear_http_proxy').checked=false;
  }finally{
    if(requestId===state.settingsRequest)f.querySelectorAll('input,button').forEach(el=>el.disabled=false);
  }
}
document.querySelectorAll('.nav').forEach(el=>el.addEventListener('click',safe(()=>showPage(el.dataset.page))));
$('#primary-action').addEventListener('click',()=>state.page==='monitors'?openMonitor():openChannel());
document.querySelectorAll('.close-dialog').forEach(el=>el.addEventListener('click',()=>el.closest('dialog').close()));
formValue($('#channel-form'),'kind').addEventListener('change',switchChannelKind);
$('#monitor-form').addEventListener('submit',safe(async e=>{e.preventDefault();const f=e.target,id=formValue(f,'id').value;const users=formValue(f,'users').value.split(/[\s,，]+/).filter(Boolean).map(u=>/^\d+$/.test(u)?Number(u):u);await api('/monitors'+(id?'/'+id:''),id?'PUT':'POST',{name:formValue(f,'name').value,chat_id:Number(formValue(f,'chat_id').value),users,enabled:formValue(f,'enabled').checked,channel_ids:[...f.querySelectorAll('input[name="channels"]:checked')].map(el=>Number(el.value))});$('#monitor-dialog').close();await refreshConfig();await refreshStatus();toast('监控项已保存，即时生效');}));
$('#channel-form').addEventListener('submit',safe(async e=>{
  e.preventDefault();const f=e.target,id=formValue(f,'id').value,kind=formValue(f,'kind').value;let config;
  if(kind==='telegram'){config={token:formValue(f,'token').value,chat_id:formValue(f,'chat_id').value,disable_notification:formValue(f,'disable_notification').checked};if(formValue(f,'message_thread_id').value)config.message_thread_id=Number(formValue(f,'message_thread_id').value);}
  else {let params;try{params=JSON.parse(formValue(f,'extra').value||'{}');}catch{throw new Error('额外参数必须是有效的 JSON 对象');}if(!params||typeof params!=='object'||Array.isArray(params))throw new Error('额外参数必须是 JSON 对象');for(const key of barkFields){const el=formValue(f,key);if(el.type==='checkbox'){params[key]=el.checked?1:0;}else if(el.value!=='')params[key]=el.type==='number'?Number(el.value):el.value;}config={server:formValue(f,'server').value,device_key:formValue(f,'device_key').value,params};}
  await api('/channels'+(id?'/'+id:''),id?'PUT':'POST',{name:formValue(f,'name').value,kind,enabled:formValue(f,'enabled').checked,config});$('#channel-dialog').close();await refreshConfig();await refreshStatus();toast('推送渠道已保存');
}));
$('#settings-form').addEventListener('submit',safe(async e=>{e.preventDefault();const f=e.target;const body=Object.fromEntries(new FormData(f));body.api_id=body.api_id?Number(body.api_id):null;body.proxy_port=Number(body.proxy_port);body.max_attempts=Number(body.max_attempts);body.clear_http_proxy=formValue(f,'clear_http_proxy').checked;await api('/settings','PUT',body);await loadSettings();toast('连接配置已保存，正在重新连接');}));
$('#code-form').addEventListener('submit',safe(async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;try{await api('/telegram/code','POST',{phone:formValue(e.target,'phone').value});toast('验证码已发送，请查看 Telegram');}finally{button.disabled=false;}}));
$('#verify-form').addEventListener('submit',safe(async e=>{e.preventDefault();const result=await api('/telegram/verify','POST',{code:formValue(e.target,'code').value,password:formValue(e.target,'password').value});if(result.status==='password_required')toast('请填写两步验证密码，再次点击验证');else{e.target.reset();toast('Telegram 登录成功');await refreshStatus();}}));
$('#get-dialogs').addEventListener('click',safe(async e=>{e.target.disabled=true;try{const groups=await api('/telegram/dialogs');$('#dialogs').innerHTML=groups.map(g=>`<div class="dialog-row"><span>${escapeHTML(g.name)}</span><code>${g.id}</code></div>`).join('')||'没有找到已加入的群组';}finally{e.target.disabled=false;}}));
$('#message-filter').addEventListener('change',safe(()=>loadMessages()));$('#refresh-messages').addEventListener('click',safe(()=>loadMessages()));$('#load-more').addEventListener('click',safe(()=>loadMessages(true)));
document.addEventListener('click',safe(async e=>{const button=e.target.closest('[data-action]');if(!button)return;const id=Number(button.dataset.id),action=button.dataset.action;
  if(action==='edit-monitor')openMonitor(id);
  else if(action==='edit-channel')openChannel(id);
  else if(action==='messages'){$('#message-filter').value=String(id);await showPage('messages');}
  else if(action==='test-channel'){button.disabled=true;try{await api('/channels/'+id+'/test','POST');toast('测试通知已发送');}finally{button.disabled=false;}}
  else if(action==='retry'){await api('/deliveries/'+id+'/retry','POST');await loadMessages();toast('已重新加入推送队列');}
  else if(action==='delete-channel'&&await confirmDelete('删除此渠道后，所有监控项会解除对它的引用，待发送任务会取消。')){await api('/channels/'+id,'DELETE');await refreshConfig();await refreshStatus();toast('渠道已删除');}
  else if(action==='delete-monitor'&&await confirmDelete('删除此监控项？已捕获的消息会保留在“全部监控项”消息记录中。')){await api('/monitors/'+id,'DELETE');await refreshConfig();await refreshStatus();toast('监控项已删除');}
}));
async function initialize(){await refreshConfig();await refreshStatus();}
initialize().catch(error=>toast(error.message,true));
setInterval(async()=>{if(state.refreshBusy||document.hidden||state.accountChanged||state.accountUpdating)return;state.refreshBusy=true;try{await refreshStatus();if(state.page==='messages'&&!state.paging&&!state.messageLoading&&!document.querySelector('dialog[open]'))await loadMessages();if(state.page==='monitors'&&!document.querySelector('dialog[open]'))await refreshConfig();}catch{$('#connection').textContent='服务连接中断';}finally{state.refreshBusy=false;}},5000);

$('#admin-form').addEventListener('submit',safe(async e=>{
  e.preventDefault();
  const f=e.target,body=Object.fromEntries(new FormData(f));
  if(body.password!==body.confirm_password)throw new Error('两次新密码不一致');
  const button=f.querySelector('button');button.disabled=true;state.accountUpdating=true;
  try{
    await api('/admin','PUT',body);
    state.accountChanged=true;f.reset();f.hidden=true;$('#admin-success').hidden=false;
    toast('管理员账户信息已更新，请重新登录');
  }finally{button.disabled=false;state.accountUpdating=false;}
}));

$('#logout').addEventListener('click',safe(async()=>{state.accountUpdating=true;try{await api('/auth/logout','POST');window.location.replace('/login');}finally{state.accountUpdating=false;}}));
