'use strict';
const form=document.querySelector('#login-form');
const message=document.querySelector('#login-message');
form.addEventListener('submit',async event=>{
  event.preventDefault();message.hidden=true;
  const button=form.querySelector('button');button.disabled=true;button.textContent='正在登录…';
  try{
    const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(form)))});
    const result=await response.json();
    if(response.status===409){window.location.replace('/');return;}
    if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'登录失败，请稍后重试');
    form.reset();window.location.replace('/');
  }catch(error){message.textContent=error.message;message.hidden=false;}
  finally{button.disabled=false;button.textContent='登录';}
});
