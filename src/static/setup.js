'use strict';
const form=document.querySelector('#setup-form');
const message=document.querySelector('#setup-message');
async function checkInitialized(){
  const response=await fetch('/api/setup');
  if(response.ok&&(await response.json()).initialized) showSuccess('管理员账户已经初始化，请使用现有账户登录。');
}
function showSuccess(text){
  form.reset();form.hidden=true;
  document.querySelector('#setup-success .notice').textContent=text;
  document.querySelector('#setup-success').hidden=false;
}
form.addEventListener('submit',async event=>{
  event.preventDefault();message.hidden=true;
  const body=Object.fromEntries(new FormData(form));
  if(body.password!==body.confirm_password){message.textContent='两次密码不一致';message.hidden=false;return;}
  const button=form.querySelector('button');button.disabled=true;
  try{
    const response=await fetch('/api/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const result=await response.json();
    if(response.status===409){showSuccess('管理员账户已经初始化，请使用现有账户登录。');return;}
    if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'初始化失败，请检查输入');
    showSuccess('管理员账户已创建。进入管理页面后，使用刚设置的账户与密码登录。');
  }catch(error){message.textContent=error.message;message.hidden=false;}
  finally{button.disabled=false;}
});
checkInitialized().catch(()=>{message.textContent='无法连接服务，请刷新重试。';message.hidden=false;});
