const HiStrA={
 token(){return sessionStorage.getItem('histra-token')||''},
 setToken(value){sessionStorage.setItem('histra-token',value.trim())},
 headers(extra={}){const h={...extra};const t=this.token();if(t)h.Authorization=`Bearer ${t}`;return h},
 async api(url,options={}){options.headers=this.headers(options.headers||{});const response=await fetch(url,options);if(!response.ok){let message=`HTTP ${response.status}`;try{const value=await response.json();message=value.detail||JSON.stringify(value)}catch{message=await response.text()||message}throw new Error(message)}if(response.status===204)return null;const type=response.headers.get('content-type')||'';return type.includes('json')?response.json():response},
 bindToken(){const input=document.querySelector('#api-token');if(!input)return;input.value=this.token();input.addEventListener('change',()=>this.setToken(input.value));document.querySelector('#save-token')?.addEventListener('click',()=>{this.setToken(input.value);location.reload()})},
 fmtDate(value){if(!value)return '—';return new Date(value).toLocaleString()},
 fmtInt(value){return Number(value||0).toLocaleString()},
 status(value){return `<span class="badge ${String(value).toLowerCase()}">${this.escape(value)}</span>`},
 escape(value){return String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[ch]))},
 json(value){return JSON.stringify(value,null,2)},
 download(blob,name){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url)},
 setStatus(id,message,kind=''){const el=document.getElementById(id);if(!el)return;el.textContent=message;el.className=`status ${kind}`},
};document.addEventListener('DOMContentLoaded',()=>HiStrA.bindToken());