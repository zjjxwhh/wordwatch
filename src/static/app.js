'use strict';
const $ = s => document.querySelector(s);
const iconPaths = {"plus":"<path d=\"M12 5v14M5 12h14\"/>","close":"<path d=\"m6 6 12 12M18 6 6 18\"/>","external":"<path d=\"M14 3h7v7M21 3l-12 12M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5\"/>","arrow":"<path d=\"M4 12h16m-6-6 6 6-6 6\"/>","refresh":"<path d=\"M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2\"/>","music":"<path d=\"M9 18V5l11-2v13M9 8l11-2\"/><ellipse cx=\"6\" cy=\"18\" rx=\"3\" ry=\"2\"/><ellipse cx=\"17\" cy=\"16\" rx=\"3\" ry=\"2\"/>","dot":"<circle cx=\"12\" cy=\"12\" r=\"4\" fill=\"currentColor\" stroke=\"none\"/>","monitors":"<rect x=\"3\" y=\"3\" width=\"18\" height=\"18\" rx=\"3\"/><path d=\"M3 9h18M9 9v12\"/>","messages":"<path d=\"M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2V11.5a9.5 9.5 0 0 1 19 0Z\"/><path d=\"M7 8h10M7 12h7\"/>","channels":"<path d=\"M12 18v-4M6 7l6 7 6-7\"/><circle cx=\"5\" cy=\"5\" r=\"2\"/><circle cx=\"19\" cy=\"5\" r=\"2\"/><circle cx=\"12\" cy=\"20\" r=\"2\"/>","templates":"<rect x=\"4\" y=\"3\" width=\"16\" height=\"18\" rx=\"2\"/><path d=\"M8 7h8M8 11h8M8 15h5\"/>","logo":"<path d=\"m12 2 10 10-10 10L2 12 12 2Z\"/><path d=\"m12 7 5 5-5 5-5-5 5-5Z\"/>"};
function icon(name){return `<svg class="ui-icon" aria-hidden="true" focusable="false" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${iconPaths[name]||''}</svg>`;}
const state = {page:'monitors', monitors:[], channels:[], templates:[], messages:[], paging:false, refreshBusy:false};
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names = {templates:['通知模板','自定义通知标题与正文，在监控项中选择使用','新建模板'],system:['系统设置','设置通知时区与代理，管理管理员账户和密码',''],monitors:['监控项','按群组和用户追踪消息，将通知送达你选择的渠道。','新建监控项'],messages:['消息记录','查看捕获的消息、推送结果，随时回到原始对话。',''],channels:['推送渠道','配置 Telegram Bot、Bark 与钉钉，在不同监控项中灵活复用','新建渠道'],settings:['Telegram 连接','配置 Telegram API、登录账号并查询群组','']};
const formValue = (form, name) => form.elements.namedItem(name);
let toastTimer;
function toast(message, error=false) { const el=$('#toast'); el.textContent=message; el.classList.toggle('error',error); el.hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>el.hidden=true,5000); }
async function api(path, method='GET', body) {
  const response=await fetch('/api'+path,{method,headers:body ? {'Content-Type':'application/json'} : {},body:body ? JSON.stringify(body) : undefined});
  if(response.status===401){window.location.replace('/login');throw new Error('登录已失效，请重新登录');}if(response.status===503){window.location.replace('/');throw new Error('请先初始化管理员账户');}if(!response.ok) { let data; try{data=await response.json();}catch{data={};} throw new Error(typeof data.detail==='string' ? data.detail : `请求失败 (${response.status})`); }
  return response.json();
}
const safe = fn => async event => { try{await fn(event);}catch(error){toast(error.message,true);} };
function empty(symbol,title,description) { return `<div class="empty"><div class="symbol">${icon(symbol)}</div><b>${title}</b><p>${description}</p></div>`; }
function channelName(id) { return state.channels.find(c=>c.id===id)?.name ?? '已删除渠道'; }
function renderMonitors() {
  $('#monitor-list').innerHTML=state.monitors.map(m=>`<article class="card"><div class="card-top"><div class="channel-heading"><h3>${escapeHTML(m.name)}</h3><span class="tag ${m.enabled?'on':'off'}">${m.enabled?icon('dot'):''}${m.enabled?'已启用':'已暂停'}</span></div><span class="chip channel-usage">已捕获 ${m.message_count} 条消息</span></div><div class="card-data">群组：${m.chat_id}<br>用户：${escapeHTML(m.users.join(', '))}</div><div class="chips">${m.channel_ids.map(id=>`<span class="chip">${escapeHTML(channelName(id))}</span>`).join('') || '<span class="chip">仅记录，不推送</span>'}<span class="chip">${escapeHTML(m.template_id?(state.templates.find(t=>t.id===m.template_id)?.name??'自定义'):(m.title_template||m.body_template?'自定义':'默认内容'))}</span></div><div class="card-actions"><button class="link-button" data-action="messages" data-id="${m.id}">查看消息 ${icon('external')}</button><button class="link-button" data-action="edit-monitor" data-id="${m.id}">编辑</button><button class="link-button delete" data-action="delete-monitor" data-id="${m.id}">删除</button></div></article>`).join('') || empty('monitors','从第一个监控项开始','设置群组和关注的用户，再选择通知送达的地方。');
  const filter=$('#message-filter'), selected=filter.value;
  filter.innerHTML='<option value="">全部监控项</option>'+state.monitors.map(m=>`<option value="${m.id}">${escapeHTML(m.name)}</option>`).join('');
  filter.value=selected;
}
function renderChannels() {
  $('#channel-list').innerHTML=state.channels.map(c=>`<article class="card"><div class="card-top"><div class="channel-heading"><h3>${escapeHTML(c.name)}</h3><span class="tag ${c.enabled?'on':'off'}">${c.enabled?icon('dot'):''}${c.enabled?'已启用':'已暂停'}</span></div><span class="chip channel-usage">${state.monitors.filter(m=>m.channel_ids.includes(c.id)).length} 个监控项使用</span></div><div class="card-data">通知渠道：${({telegram:'Telegram Bot',bark:'Bark',dingtalk:'钉钉群机器人'})[c.kind]}<br>${escapeHTML(c.kind==='telegram'?`接收 Chat ID：${c.config.chat_id}`:c.kind==='bark'?`服务地址：${c.config.server}`:'接收群组：Webhook 绑定的钉钉群')}</div><div class="chips"><span class="chip">${c.use_proxy?'使用系统代理':'直连'}</span><span class="chip">最多尝试 ${c.max_attempts} 次</span><span class="chip">${({telegram:'Bot Token 已保存',bark:'Device Key 已保存',dingtalk:'Webhook 已保存'})[c.kind]}</span>${c.kind==='bark'&&c.config.params?.sound?`<span class="chip">${icon('music')} ${escapeHTML(c.config.params.sound)}</span>`:''}</div><div class="card-actions"><button class="link-button" data-action="test-channel" data-id="${c.id}">测试推送 ${icon('external')}</button><button class="link-button" data-action="edit-channel" data-id="${c.id}">编辑</button><button class="link-button delete" data-action="delete-channel" data-id="${c.id}">删除</button></div></article>`).join('') || empty('channels','建立你的通知网络','添加多个 Bot、Bark 或钉钉渠道，在监控项中自由组合。');
}
function formatMessageTime(value){
  const date=new Date(value),pad=n=>String(n).padStart(2,'0');
  if(Number.isNaN(date.getTime()))return '';
  return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}
function renderMessages() {
  const statuses={sent:'已发送',pending:'等待推送',failed:'推送失败',cancelled:'已取消'};
  $('#message-list').innerHTML=state.messages.map(m=>`<article class="message"><div class="message-head"><b>${escapeHTML(m.monitor_name)} · ${escapeHTML(m.chat_title)}</b><span>${escapeHTML(formatMessageTime(m.sent_at))}</span></div><p class="hint">${escapeHTML(m.sender_name)} ${m.username?'@'+escapeHTML(m.username):''} · 用户 ${m.sender_id ?? '未知'} · 消息 #${m.message_id}</p><div class="message-text">${escapeHTML(m.text)}</div><div class="message-foot">${m.deliveries.map(d=>`<span class="delivery-item"><span class="delivery ${d.status}" title="${escapeHTML(d.error || '')}">${escapeHTML(d.channel_name)} · ${statuses[d.status] || escapeHTML(d.status)}${d.attempts?` (${d.attempts})`:''}</span>${d.status==='failed'&&d.channel_id?`<button class="delivery-retry" data-action="retry" data-id="${d.id}">重试 ${icon('refresh')}</button>`:''}</span>`).join('') || '<span class="muted">仅记录 / 捕获时无启用的渠道</span>'}${m.url?`<a href="${escapeHTML(m.url)}" target="_blank" rel="noopener noreferrer">打开原消息 ${icon('external')}</a>`:''}</div></article>`).join('') || empty('messages','这里会留下新的消息','启用监控并登录 Telegram 后，匹配到的发言会显示在这里。');
}
async function refreshConfig() { [state.monitors,state.channels,state.templates]=await Promise.all([api('/monitors'),api('/channels'),api('/templates')]);renderMonitors();renderChannels();renderTemplates(); }
async function refreshStatus() {
  const s=await api('/status');
  if(s.build){
    const date=s.build.date?new Date(s.build.date):null;
    const formatted=date&&!Number.isNaN(date.getTime())?new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(date):'未知';
    $('#build-date').textContent=s.build.local?'本地开发':'构建日期：'+formatted;
    $('#build-date').title=s.build.local?'本地运行，尚未构建镜像':'构建时间（Asia/Shanghai）';
    $('#build-commit').textContent='Commit：'+(s.build.commit||'未知');
  }
  for(const name of ['monitors','messages','channels','failed']) $('#stat-'+name).textContent=s.counts[name];
  $('#connection').textContent=s.state;
  $('#connection-card').dataset.state=s.error?'error':s.connected&&s.authorized?'active':'waiting';
  $('#login-state').textContent=s.state+(s.error?' · '+s.error:'');
  $('#login-state-card').dataset.state=$('#connection-card').dataset.state;
  updateTelegramFields(s);
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
function activatePage(page) {
  if(!Object.hasOwn(names,page))page='monitors';
  if(window.location.hash!=='#'+page)history.replaceState(null,'','#'+page);
  if(document.body.classList.contains('menu-open'))closeNavigation();
  state.page=page;
  document.querySelectorAll('.page').forEach(el=>el.hidden=el.id!=='page-'+page);
  document.querySelectorAll('.nav').forEach(el=>el.classList.toggle('active',el.dataset.page===page));
  const [label,description,action]=names[page];
  $('#page-title').textContent=label;$('#page-description').textContent=description;
  $('#primary-action').hidden=!action;$('#primary-action').innerHTML=action?icon('plus')+' '+escapeHTML(action):'';
  $('.page-heading').hidden=false;
}
async function showPage(page) {
  activatePage(page);
  page=state.page;
  if(page==='templates')await refreshConfig();
  if(page==='messages')await loadMessages();
  if(page==='settings')await loadSettings();if(page==='system')await Promise.all([loadSystemSettings(),loadAdminSettings()]);
}
function openMonitor(id) {
  const f=$('#monitor-form');f.reset();
  const m=state.monitors.find(m=>m.id===id);
  formValue(f,'id').value=id || '';formValue(f,'enabled').checked=m?.enabled??true;
  for(const key of ['name','chat_id','title_template','body_template'])formValue(f,key).value=m?.[key]??'';$('#template-preview').hidden=true;
  formValue(f,'users').value=m?.users.join('\n')??'';
  $('#monitor-dialog-title').textContent=id?'编辑监控项':'新建监控项';
  $('#channel-choices').innerHTML=state.channels.map(c=>`<label class="check"><input type="checkbox" name="channels" value="${c.id}" ${m?.channel_ids.includes(c.id)?'checked':''}>${escapeHTML(c.name)} <small>${c.kind}${c.enabled?'':' · 已暂停'}</small></label>`).join('') || '<p class="hint">还没有推送渠道，可稍后在“推送渠道”页面添加。</p>';
  configureMonitorTemplates(m);
  $('#monitor-dialog').showModal();
}
const barkFields=['icon','sound','group','level','badge','volume','autoCopy','call','isArchive','copy'];
function switchChannelKind() {const kind=formValue($('#channel-form'),'kind').value;for(const type of ['telegram','bark','dingtalk']){const fields=$('#'+type+'-fields');fields.hidden=kind!==type;for(const el of fields.querySelectorAll('input,select,textarea'))el.disabled=kind!==type;}}
function openChannel(id) {
  const f=$('#channel-form');f.reset();
  const channel=state.channels.find(c=>c.id===id), c=channel?.config??{};
  formValue(f,'id').value=id||'';formValue(f,'name').value=channel?.name??'';formValue(f,'kind').value=channel?.kind??'telegram';formValue(f,'enabled').checked=channel?.enabled??true;
  for(const key of ['token','chat_id','message_thread_id','server','device_key','webhook','secret'])formValue(f,key).value=c[key]??(key==='server'?'https://api.day.app':'');
  formValue(f,'use_proxy').checked=channel?.use_proxy??false;
  formValue(f,'max_attempts').value=channel?.max_attempts??10;
  formValue(f,'disable_notification').checked=!!c.disable_notification;
  formValue(f,'at_all').checked=!!c.at_all;
  const params={...(c.params??{})};
  for(const key of barkFields){const el=formValue(f,key);if(el.type==='checkbox')el.checked=params[key]===1||params[key]==='1';else el.value=params[key]??'';delete params[key];}
  formValue(f,'extra').value=Object.keys(params).length?JSON.stringify(params,null,2):'';
  $('#channel-dialog-title').textContent=id?'编辑推送渠道':'新建推送渠道';switchChannelKind();$('#channel-dialog').showModal();
}
function confirmDelete(message) {return new Promise(resolve=>{const d=$('#confirm-dialog');$('#confirm-text').textContent=message;const done=result=>{d.close();resolve(result);};$('#confirm-ok').onclick=()=>done(true);$('#confirm-cancel').onclick=()=>done(false);d.oncancel=()=>resolve(false);d.showModal();});}
async function loadSettings(){
  const f=$('#settings-form'),requestId=(state.settingsRequest??0)+1;
  state.settingsRequest=requestId;
  f.querySelectorAll('input,select,button').forEach(el=>el.disabled=true);
  try{
    const s=await api('/settings');
    if(requestId!==state.settingsRequest)return;
    for(const key of ['api_id','api_hash']){
      formValue(f,key).value=s[key]??'';
      if(s[key+'_configured'])formValue(f,key).placeholder='已保存，留空保留';
    }
    formValue(f,'use_proxy').checked=s.use_proxy;
    updateTelegramFields(s);
  }finally{
    if(requestId===state.settingsRequest)f.querySelectorAll('input,select,button').forEach(el=>el.disabled=false);
  }
}
document.querySelectorAll('.nav').forEach(el=>el.addEventListener('click',safe(()=>showPage(el.dataset.page))));
window.addEventListener('hashchange',safe(()=>showPage(window.location.hash.slice(1))));
$('#primary-action').addEventListener('click',()=>state.page==='monitors'?openMonitor():state.page==='templates'?openTemplate():openChannel());
document.querySelectorAll('.close-dialog').forEach(el=>el.addEventListener('click',()=>el.closest('dialog').close()));
formValue($('#channel-form'),'kind').addEventListener('change',switchChannelKind);
$('#monitor-form').addEventListener('submit',safe(async e=>{e.preventDefault();const f=e.target,id=formValue(f,'id').value;const users=formValue(f,'users').value.split(/[\s,，]+/).filter(Boolean).map(u=>/^\d+$/.test(u)?Number(u):u);await api('/monitors'+(id?'/'+id:''),id?'PUT':'POST',{name:formValue(f,'name').value,chat_id:Number(formValue(f,'chat_id').value),users,template_id:formValue(f,'template_id').value==='custom'?null:Number(formValue(f,'template_id').value),title_template:formValue(f,'title_template').value,body_template:formValue(f,'body_template').value,enabled:formValue(f,'enabled').checked,channel_ids:[...f.querySelectorAll('input[name="channels"]:checked')].map(el=>Number(el.value))});$('#monitor-dialog').close();await refreshConfig();await refreshStatus();toast('监控项已保存，即时生效');}));
$('#channel-form').addEventListener('submit',safe(async e=>{
  e.preventDefault();const f=e.target,id=formValue(f,'id').value,kind=formValue(f,'kind').value;let config;
  if(kind==='telegram'){config={token:formValue(f,'token').value,chat_id:formValue(f,'chat_id').value,disable_notification:formValue(f,'disable_notification').checked};if(formValue(f,'message_thread_id').value)config.message_thread_id=Number(formValue(f,'message_thread_id').value);}
  else if(kind==='dingtalk'){config={webhook:formValue(f,'webhook').value,secret:formValue(f,'secret').value,at_all:formValue(f,'at_all').checked};}
  else {let params;try{params=JSON.parse(formValue(f,'extra').value||'{}');}catch{throw new Error('额外参数必须是有效的 JSON 对象');}if(!params||typeof params!=='object'||Array.isArray(params))throw new Error('额外参数必须是 JSON 对象');for(const key of barkFields){const el=formValue(f,key);if(el.type==='checkbox'){params[key]=el.checked?1:0;}else if(el.value!=='')params[key]=el.type==='number'?Number(el.value):el.value;}config={server:formValue(f,'server').value,device_key:formValue(f,'device_key').value,params};}
  await api('/channels'+(id?'/'+id:''),id?'PUT':'POST',{name:formValue(f,'name').value,kind,enabled:formValue(f,'enabled').checked,use_proxy:formValue(f,'use_proxy').checked,max_attempts:Number(formValue(f,'max_attempts').value),config});$('#channel-dialog').close();await refreshConfig();await refreshStatus();toast('推送渠道已保存');
}));
$('#settings-form').addEventListener('submit',safe(async e=>{e.preventDefault();const f=e.target;const body=Object.fromEntries(new FormData(f));body.api_id=body.api_id?Number(body.api_id):null;body.use_proxy=formValue(f,'use_proxy').checked;await api('/settings','PUT',body);await loadSettings();toast('连接配置已保存，正在重新连接');}));
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
async function initialize(){activatePage(window.location.hash.slice(1)||'monitors');await refreshConfig();await refreshStatus();await showPage(state.page);}
initialize().catch(error=>toast(error.message,true));
setInterval(async()=>{if(state.refreshBusy||document.hidden||state.accountChanged||state.accountUpdating)return;state.refreshBusy=true;try{await refreshStatus();if(state.page==='messages'&&!state.paging&&!state.messageLoading&&!document.querySelector('dialog[open]'))await loadMessages();if(state.page==='monitors'&&!document.querySelector('dialog[open]'))await refreshConfig();}catch{$('#connection').textContent='服务连接中断';$('#connection-card').dataset.state='error';$('#login-state-card').dataset.state='error';$('#login-state').textContent='服务连接中断';}finally{state.refreshBusy=false;}},5000);

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

$('#preview-template').addEventListener('click',safe(async e=>{
  const button=e.target,f=$('#monitor-form');button.disabled=true;
  try{
    const result=await api('/templates/preview','POST',{title_template:formValue(f,'title_template').value,body_template:formValue(f,'body_template').value});
    $('#template-preview-title').textContent=result.title;
    $('#template-preview-body').textContent=result.body;
    $('#template-preview').hidden=false;
  }finally{button.disabled=false;}
}));
for(const key of ['title_template','body_template'])formValue($('#monitor-form'),key).addEventListener('input',()=>$('#template-preview').hidden=true);

function renderTemplates(){
  $('#template-list').innerHTML=state.templates.map(t=>`<article class="card template-card"><div class="card-top"><h3>${escapeHTML(t.name)}</h3><span class="chip">${state.monitors.filter(m=>m.template_id===t.id).length} 个监控项引用</span></div><dl class="template-content"><div class="template-section"><dt>通知标题</dt><dd class="template-title-text">${escapeHTML(t.title_template||'默认标题')}</dd></div><div class="template-section"><dt>通知正文</dt><dd class="template-body-frame"><pre class="template-snippet">${escapeHTML(t.body_template||'使用默认通知正文')}</pre></dd></div></dl><div class="card-actions"><button type="button" class="link-button" data-template-action="edit" data-id="${t.id}">编辑</button><button type="button" class="link-button delete" data-template-action="delete" data-id="${t.id}">删除</button></div></article>`).join('')||empty('templates','添加你的第一套模板','在监控项中引用，或导入后修改。');
}
function openTemplate(id){
  const f=$('#preset-form'),t=state.templates.find(t=>t.id===id);f.reset();
  formValue(f,'id').value=id||'';
  for(const key of ['name','title_template','body_template'])formValue(f,key).value=t?.[key]??'';
  $('#preset-dialog-title').textContent=id?'编辑通知模板':'新建通知模板';
  $('#preset-preview').hidden=true;$('#preset-dialog').showModal();
}
function configureMonitorTemplates(m){
  const f=$('#monitor-form'),select=formValue(f,'template_id');
  select.innerHTML='<option value="custom">自定义 / 默认内容</option>'+state.templates.map(t=>`<option value="${t.id}">${escapeHTML(t.name)}</option>`).join('');
  select.value=m?.template_id?String(m.template_id):'custom';
  if(!select.value)select.value='custom';
  $('#import-preset').innerHTML='<option value="">选择要导入的预设</option>'+state.templates.map(t=>`<option value="${t.id}">${escapeHTML(t.name)}</option>`).join('');
  state.monitorTemplateMode=select.value;
  state.customTemplateDraft={title_template:formValue(f,'title_template').value,body_template:formValue(f,'body_template').value};
  applyMonitorTemplateMode();
}
function applyMonitorTemplateMode(){
  const f=$('#monitor-form'),value=formValue(f,'template_id').value,custom=value==='custom';
  $('#template-import').hidden=!custom;
  const preset=state.templates.find(t=>String(t.id)===value);
  for(const key of ['title_template','body_template']){
    const el=formValue(f,key);el.readOnly=!custom;
    if(preset)el.value=preset[key];
  }
  $('#template-preview').hidden=true;
}
formValue($('#monitor-form'),'template_id').addEventListener('change',()=>{
  const f=$('#monitor-form'),next=formValue(f,'template_id').value;
  if(state.monitorTemplateMode==='custom'){
    state.customTemplateDraft={title_template:formValue(f,'title_template').value,body_template:formValue(f,'body_template').value};
  }
  if(next==='custom'){
    for(const key of ['title_template','body_template'])formValue(f,key).value=state.customTemplateDraft[key];
    $('#import-preset').value=state.monitorTemplateMode==='custom'?'':state.monitorTemplateMode;
  }
  state.monitorTemplateMode=next;applyMonitorTemplateMode();
});
$('#import-template').addEventListener('click',()=>{
  const preset=state.templates.find(t=>String(t.id)===$('#import-preset').value);
  if(!preset){toast('请先选择要导入的预设',true);return;}
  const f=$('#monitor-form');
  for(const key of ['title_template','body_template'])formValue(f,key).value=preset[key];
  $('#template-preview').hidden=true;toast('已导入为自定义副本，可直接修改');
});
$('#preset-form').addEventListener('submit',safe(async e=>{
  e.preventDefault();const f=e.target,id=formValue(f,'id').value;
  await api('/templates'+(id?'/'+id:''),id?'PUT':'POST',{name:formValue(f,'name').value,title_template:formValue(f,'title_template').value,body_template:formValue(f,'body_template').value});
  $('#preset-dialog').close();await refreshConfig();toast('通知模板已保存');
}));
$('#preview-preset').addEventListener('click',safe(async e=>{
  const button=e.target,f=$('#preset-form');button.disabled=true;
  try{
    const result=await api('/templates/preview','POST',{title_template:formValue(f,'title_template').value,body_template:formValue(f,'body_template').value});
    $('#preset-preview-title').textContent=result.title;$('#preset-preview-body').textContent=result.body;$('#preset-preview').hidden=false;
  }finally{button.disabled=false;}
}));
for(const key of ['title_template','body_template'])formValue($('#preset-form'),key).addEventListener('input',()=>$('#preset-preview').hidden=true);
document.addEventListener('click',safe(async e=>{
  const button=e.target.closest('[data-template-action]');if(!button)return;
  const id=Number(button.dataset.id);
  if(button.dataset.templateAction==='edit')openTemplate(id);
  else if(await confirmDelete('删除此预设？引用它的监控项将保留当前模板内容并转为自定义。')){
    await api('/templates/'+id,'DELETE');await refreshConfig();toast('预设已删除，监控项内容已保留');
  }
}));

async function loadSystemSettings(){
  const f=$('#system-settings-form');for(const form of [f,$('#proxy-settings-form')])form.querySelectorAll('input,select,button').forEach(el=>el.disabled=true);
  try{const settings=await api('/system-settings');formValue(f,'timezone').value=settings.timezone;
    const proxy=$('#proxy-settings-form');
    for(const key of ['proxy_type','proxy_host','proxy_port','proxy_username','proxy_password'])formValue(proxy,key).value=settings[key]??'';
    formValue(proxy,'proxy_password').placeholder='可选';
  }
  finally{for(const form of [f,$('#proxy-settings-form')])form.querySelectorAll('input,select,button').forEach(el=>el.disabled=false);}
}
$('#proxy-settings-form').addEventListener('submit',safe(async e=>{
  e.preventDefault();const f=e.target,button=f.querySelector('button');button.disabled=true;
  try{const body=Object.fromEntries(new FormData(f));body.proxy_port=Number(body.proxy_port);await api('/system-settings','PUT',body);await loadSystemSettings();toast('代理设置已保存');}
  finally{button.disabled=false;}
}));
$('#system-settings-form').addEventListener('submit',safe(async e=>{
  e.preventDefault();const f=e.target,button=f.querySelector('button');button.disabled=true;
  try{await api('/system-settings','PUT',{timezone:formValue(f,'timezone').value});toast('通知时区已保存');}
  finally{button.disabled=false;}
}));

async function loadAdminSettings(){
  if(state.accountChanged)return;
  const f=$('#admin-form');f.querySelectorAll('input,select,button').forEach(el=>el.disabled=true);
  try{const info=await api('/admin');formValue(f,'username').value=info.username;}
  finally{f.querySelectorAll('input,select,button').forEach(el=>el.disabled=false);}
}

const navigationBreakpoint=window.matchMedia('(max-width:1050px)');
function closeNavigation(restoreFocus=true){
  document.body.classList.remove('menu-open');
  $('#sidebar-backdrop').hidden=true;
  $('#menu-toggle').setAttribute('aria-expanded','false');
  if(restoreFocus&&navigationBreakpoint.matches)$('#menu-toggle').focus();
}
function openNavigation(){
  if(!navigationBreakpoint.matches)return;
  document.body.classList.add('menu-open');
  $('#sidebar-backdrop').hidden=false;
  $('#menu-toggle').setAttribute('aria-expanded','true');
  $('#sidebar-close').focus();
}
$('#menu-toggle').addEventListener('click',()=>document.body.classList.contains('menu-open')?closeNavigation():openNavigation());
$('#sidebar-close').addEventListener('click',()=>closeNavigation());
$('#sidebar-backdrop').addEventListener('click',()=>closeNavigation());
navigationBreakpoint.addEventListener('change',()=>closeNavigation(false));
document.addEventListener('keydown',event=>{
  if(!document.body.classList.contains('menu-open'))return;
  if(event.key==='Escape'){event.preventDefault();closeNavigation();}
  if(event.key==='Tab'){
    const controls=[...$('#main-sidebar').querySelectorAll('a[href],button:not([disabled])')].filter(el=>el.getClientRects().length);
    const first=controls[0],last=controls.at(-1);
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  }
});

function updateTelegramFields(info={}){
  if(info.api_hash_configured!==undefined)state.apiHashConfigured=info.api_hash_configured;
  if(typeof info.api_hash==='string')state.savedApiHash=info.api_hash;
  if(info.authorized!==undefined)state.telegramAuthorized=info.authorized;
  if(info.account_phone!==undefined)state.accountPhone=info.account_phone;
  const hash=formValue($('#settings-form'),'api_hash'),hashBadge=$('#api-hash-state');
  const hashChanged=!!hash.value&&hash.value!==(state.savedApiHash??'');
  hashBadge.textContent=hashChanged?'待保存':state.apiHashConfigured?(state.telegramAuthorized?'已保存 · 账号已登录':'已保存，留空保留'):'未填写';
  hashBadge.dataset.state=hashChanged?'pending':state.apiHashConfigured?'saved':'empty';
  const phone=formValue($('#code-form'),'phone'),phoneBadge=$('#phone-state');
  if(!phone.value&&state.accountPhone&&document.activeElement!==phone)phone.value=state.accountPhone;
  const matches=!!state.accountPhone&&phone.value.replace(/\D/g,'')===state.accountPhone.replace(/\D/g,'');
  phoneBadge.textContent=matches?(state.telegramAuthorized?'已验证 · 已登录':'已验证 · 当前未登录'):phone.value?'待验证':'未填写';
  phoneBadge.dataset.state=matches?'saved':phone.value?'pending':'empty';
}
formValue($('#settings-form'),'api_hash').addEventListener('input',()=>updateTelegramFields());
formValue($('#code-form'),'phone').addEventListener('input',()=>updateTelegramFields());

$('#toggle-api-hash').addEventListener('click',()=>{
  const input=formValue($('#settings-form'),'api_hash'),button=$('#toggle-api-hash');
  input.type=input.type==='text'?'password':'text';
  button.textContent=input.type==='text'?'隐藏':'显示';
  button.setAttribute('aria-label',button.textContent+' API Hash');
});
