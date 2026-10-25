const vm=require('vm'),fs=require('fs'),assert=require('assert');
// ---- minimal DOM
class Node_{constructor(t){this.nodeName=t;this.childNodes=[];this.parentNode=null;this.attrs={};this.style={setProperty(k,v){this[k]=v}};this._cls=new Set();this.scrollTop=0;this.scrollHeight=100;this.clientHeight=50;this.offsetWidth=100;this.offsetHeight=100;this.value='';this.files=null;this.textContent_='';}
 get tagName(){return String(this.nodeName).toUpperCase()}
 get firstChild(){return this.childNodes[0]||null}
 appendChild(c){if(c.parentNode)c.parentNode.removeChild(c);c.parentNode=this;this.childNodes.push(c);return c}
 removeChild(c){const i=this.childNodes.indexOf(c);if(i>=0)this.childNodes.splice(i,1);c.parentNode=null;return c}
 remove(){if(this.parentNode)this.parentNode.removeChild(this)}
 insertBefore(c,ref){if(c.parentNode)c.parentNode.removeChild(c);c.parentNode=this;const i=ref?this.childNodes.indexOf(ref):-1;if(i<0)this.childNodes.push(c);else this.childNodes.splice(i,0,c);return c}
 setAttribute(k,v){this.attrs[k]=String(v)} getAttribute(k){return this.attrs[k]===undefined?null:this.attrs[k]}
 set className(v){this._cls=new Set(String(v).split(/\s+/).filter(Boolean))} get className(){return [...this._cls].join(' ')}
 get classList(){const s=this._cls;return{add:x=>s.add(x),remove:x=>s.delete(x),contains:x=>s.has(x),toggle:(x,f)=>{const on=f===undefined?!s.has(x):f;on?s.add(x):s.delete(x);return on}}}
 set textContent(v){this.childNodes=[];if(v!=='')this.appendChild(new Text_(String(v)))} get textContent(){return this.childNodes.map(c=>c.textContent).join('')}
 contains(n){while(n){if(n===this)return true;n=n.parentNode}return false}
 getBoundingClientRect(){return{left:5,bottom:5,top:0}} focus(){} click(){this.onclick&&this.onclick({target:this,currentTarget:this,clientX:1,clientY:1})} scrollIntoView(){}
 addEventListener(){} removeEventListener(){}
 _all(pred,out=[]){for(const c of this.childNodes){if(c instanceof Node_){if(pred(c))out.push(c);c._all(pred,out)}}return out}
 _match(sel){let m;if((m=sel.match(/^\.([\w-]+)$/)))return this._cls.has(m[1]);if((m=sel.match(/^\[([\w-]+)="(.*)"\]$/)))return this.attrs[m[1]]===m[2];if((m=sel.match(/^(\w+)\[([\w-]+)="(.*)"\]$/)))return this.nodeName===m[1]&&this.attrs[m[2]]===m[3];return this.nodeName===sel}
 querySelector(s){return this._all(n=>n._match(s))[0]||null} querySelectorAll(s){return this._all(n=>n._match(s))}
}
class Text_{constructor(t){this.t=t;this.parentNode=null}get textContent(){return this.t}}
const body=new Node_('body'),html=new Node_('html');const toasts=new Node_('div'),app=new Node_('div');body.appendChild(app);body.appendChild(toasts);
const metaCsrf=new Node_('meta');metaCsrf.attrs.name='csrf';metaCsrf.content='tok';
const doc={body,documentElement:html,hidden:false,createElement:t=>new Node_(t),createTextNode:t=>new Text_(t),getElementById:id=>id==='app'?app:id==='toasts'?toasts:null,
 querySelector:s=>s==='meta[name="csrf"]'?metaCsrf:body.querySelector(s),addEventListener(){},removeEventListener(){}};
// ---- fake backend
const me={id:1,first_name:'Aman',last_name:'K',username:'aman',phone:'919876543210',bio:'hi',premium:false,has_photo:true};
const dlg=(id,type,title,extra)=>Object.assign({id,type,title,username:null,saved:false,unread:id%3,unread_mark:false,mentions:0,pinned:id===10,avatar:true,verified:false,deleted:false,blocked:false,contact:false,status:'recently',last:{text:'hello '+id,date:'2026-10-24T08:00:00+00:00',out:id%2===0,from:'Bob Smith',id:5}},extra||{});
const dialogs=[dlg(10,'private','Lisa Fan'),dlg(11,'bot','HelperBot'),dlg(-1001,'supergroup','Dev Group'),dlg(-1002,'channel','News'),dlg(12,'private','Deleted Account',{deleted:true}),dlg(13,'private','Bad Guy',{blocked:true}),dlg(1,'private','Saved Messages',{saved:true})];
const msgs=[{id:1,chat_id:10,date:'2026-10-23T08:00:00+00:00',out:false,from:'Lisa Fan',text:'hi https://x.com ok',kind:null},{id:2,chat_id:10,date:'2026-10-24T08:00:00+00:00',out:true,from:'Aman',text:'hello',kind:null,reply_to:1},
 {id:3,chat_id:10,date:'2026-10-24T08:01:00+00:00',out:false,from:'Lisa Fan',text:'',kind:'photo',file_name:'',size:1000},{id:4,chat_id:10,date:'2026-10-24T08:02:00+00:00',out:false,from:'x',text:'',kind:'sticker',emoji:'😀',animated:false},
 {id:5,chat_id:10,date:'2026-10-24T08:03:00+00:00',out:false,from:'x',text:'cap',kind:'file',file_name:'a.pdf',size:20480},{id:6,chat_id:10,date:'2026-10-24T08:04:00+00:00',out:false,from:'x',text:'',kind:'voice',duration:3},
 {id:7,chat_id:10,date:'2026-10-24T08:05:00+00:00',out:false,from:'x',text:'',kind:'video',size:1},{id:8,chat_id:10,date:'2026-10-24T08:05:00+00:00',out:false,from:'x',text:'',kind:'poll',poll:{question:'Q?',options:['a','b']}},
 {id:9,chat_id:10,date:'2026-10-24T08:06:00+00:00',out:false,from:'x',text:'',kind:'location',location:{lat:1,lon:2,title:null}},{id:10,chat_id:10,date:'2026-10-24T08:07:00+00:00',out:false,from:'x',text:'joined',kind:'service',service:true},
 {id:11,chat_id:10,date:'2026-10-24T08:08:00+00:00',out:false,from:'x',text:'',kind:'contact',contact:{name:'C',phone:'1'}},{id:12,chat_id:10,date:'2026-10-24T08:08:00+00:00',fwd:'Someone',out:false,from:'x',text:'fw',edited:'2026-10-24T08:09:00+00:00',kind:null}];
const calls=[];
const routes={'/admin/api/tg/me':()=>me,'/admin/api/tg/dialogs':()=>({items:dialogs,total:7,next:null,counts:{all:7,personal:2,bots:1,groups:1,channels:1,blocked:1,deleted:1}}),
 '/admin/api/tg/find':()=>({items:[{id:99,type:'private',title:'Global Guy',username:'gg'}]}),'/admin/api/tg/chat':()=>({id:10,type:'private',title:'Lisa Fan',username:'lf',bio:'bio',description:'',members:null,verified:false,deleted:false,contact:true,status:'recently',blocked:false,saved:false,muted:false,avatar:true}),
 '/admin/api/tg/history':()=>({messages:msgs,has_more:true}),'/admin/api/tg/ai':()=>({global:true,chat:true,info:{mode:'userbot',persona:'Lisa',lisa:{enabled:true,loaded:true,examples:16690,per_reply:3},providers:[{name:'primary',ready:true}]}}),
 '/admin/api/tg/privacy':()=>({items:[{key:'last_seen',label:'Last seen',value:'contacts',exceptions:2},{key:'phone',label:'Phone',value:'nobody',exceptions:0}],options:['everybody','contacts','nobody']}),
 '/admin/api/tg/blocked':()=>({items:[{id:13,name:'Bad Guy',username:null,deleted:false}],count:1}),'/admin/api/tg/sessions':()=>({items:[{hash:'1',current:true,device:'Bot',platform:'x',system:'',app:'Pyro',ip:'1.1.1.1',country:'IN',region:'',created:null,active:'2026-10-24T08:00:00+00:00'},{hash:'2',current:false,device:'Phone',platform:'ios',system:'17',app:'Telegram',ip:'2.2.2.2',country:'US',region:'',created:null,active:null}]}),
 '/admin/api/tg/ttl':()=>({days:180,options:[30,90,180,365]}),'/admin/api/tg/history_ttl':()=>({period:0,options:[0,86400,604800,2678400]}),'/admin/api/tg/notify':()=>({items:[{scope:'private',muted:false,previews:true},{scope:'groups',muted:true,previews:false}]}),
 '/admin/api/tg/contacts':()=>({items:[{id:20,name:'Cat',username:'cat',phone:'91',status:'online'}],count:1}),'/admin/api/tg/username_check':()=>({available:true}),
 '/admin/api/users':()=>({users:[{chat_id:10,user_id:10,name:'Zed',username:'z',chat_type:'private',count:4,last_text:'yo',last_ts:'2026-10-24T08:00:00+00:00',flags:[]}]}),'/admin/api/messages':()=>({messages:[{user_text:'u',bot_text:'b',ts:'2026-10-24T08:00:00+00:00',name:'Zed',flags:['love']}],next_before:null})};
const fakeFetch=(url,opts)=>{const path=url.split('?')[0];calls.push([opts&&opts.method||'GET',url,opts&&opts.body]);const f=routes[path];let data;
 if(f)data=f();else if((opts&&opts.method)==='POST'&&path.startsWith('/admin/api/tg/'))data=path.endsWith('/send')?{message:Object.assign({},msgs[1],{id:50,text:'sent'})}:path.endsWith('/edit')?{message:Object.assign({},msgs[1],{text:'ed'})}:{ok:true};else data={};
 return Promise.resolve({ok:true,status:200,json:()=>Promise.resolve(data)})};
class FD{constructor(){this.d=[]}append(k,v){this.d.push([k,v])}}
const win={matchMedia:()=>({matches:false,addEventListener(){}}),innerWidth:1200,innerHeight:800,open(){},location:{}};
const ctx={window:win,document:doc,localStorage:{getItem:()=>null,setItem(){}},fetch:fakeFetch,FormData:FD,URLSearchParams,navigator:{clipboard:{writeText:()=>Promise.resolve()}},location:{},setTimeout,clearTimeout,setInterval:()=>0,console,Date,JSON,Object,Array,Math,parseInt,isNaN,String,Number,RegExp,Promise,Error};
win.window=win;vm.createContext(ctx);
const errors=[];process.on('unhandledRejection',e=>errors.push('unhandled: '+(e&&e.stack||e)));
const tick=()=>new Promise(r=>setImmediate(r)); const flush=async()=>{for(let i=0;i<8;i++)await tick()};
(async()=>{
 vm.runInContext(fs.readFileSync(require('path').join(__dirname,'..','..','web','app.js'),'utf8'),ctx);
 await flush();
 const list=app.querySelector('.list'); const rows=list.querySelectorAll('.row');
 assert(rows.length>=7,'dialog rows '+rows.length);
 const tabs=app.querySelectorAll('.tab'); assert.strictEqual(tabs.length,7);
 // switch tab
 tabs[5].onclick(); await flush(); assert(calls.some(c=>c[1].includes('filter=blocked')),'blocked filter requested');
 // search
 const s=app.querySelector('.search'); s.value='lis'; s.oninput&&s.oninput(); await new Promise(r=>setTimeout(r,350)); await flush();
 // open chat
 app.querySelector('.list').querySelectorAll('.row')[0].onclick(); await flush();
 const bubbles=app.querySelectorAll('.b'); assert(bubbles.length>=10,'bubbles '+bubbles.length);
 assert(app.querySelectorAll('.dsep').length>=2,'date separators');
 assert(app.querySelector('.chead').textContent.includes('AI on'),'ai chip');
 // click bubble => menu; click each menu item that is safe
 bubbles[1].onclick({target:bubbles[1],currentTarget:bubbles[1],clientX:3,clientY:3}); const menu=body.querySelector('.menu'); assert(menu,'menu shown');
 // reply
 [...menu.querySelectorAll('button')].find(b=>b.textContent==='Reply').onclick(); await flush();
 assert(app.querySelector('.cbar'),'reply bar');
 const ta=app.querySelector('.composer').querySelector('textarea'); ta.value='yo'; ta.onkeydown({key:'Enter',shiftKey:false,preventDefault(){}}); await flush();
 assert(calls.some(c=>c[0]==='POST'&&c[1].endsWith('/send')&&JSON.parse(c[2]).reply_to===2),'send with reply_to');
 assert(app.querySelectorAll('.b').length>=11,'sent message appended');
 // chat header menu
 const more=app.querySelector('.chead').querySelectorAll('button').pop(); more.onclick({currentTarget:more,clientX:1,clientY:1}); assert(body.querySelectorAll('.menu').length>=1,'chat menu');
 // settings sections
 for (const key of ['profile','notifications','privacy','chatset','contacts','bot','ailog']) {
   const before=errors.length; app.querySelectorAll('.topbar')[0].querySelector('button').onclick(); // opens drawer
   const drawer=app.querySelector('.drawer'); assert(drawer,'drawer');
   drawer.remove();
 }
 // open settings via drawer items
 app.querySelector('.topbar').querySelector('button').onclick(); const items=app.querySelector('.drawer').querySelectorAll('.ditem');
 items.find?0:0; const byText=t=>[...items].find(i=>i.textContent.includes(t));
 for (const label of ['My Profile','Contacts','Settings','AI bot','AI chat log']) {
   app.querySelector('.topbar').querySelector('button').onclick(); const its=app.querySelector('.drawer').querySelectorAll('.ditem');
   [...its].find(i=>i.textContent.includes(label)).onclick(); await flush();
   const sheet=body.querySelector('.sheetfull'); assert(sheet,'sheet for '+label);
   const nav=sheet.querySelectorAll('.snav .ditem'); for (const n of nav) { n.onclick(); await flush(); const inner=sheet.querySelector('.sinner'); assert(inner&&inner.textContent.length>5,'section rendered '+n.getAttribute('data-k')+': '+(sheet.querySelector('.sbody').textContent||'').slice(0,80)); }
   sheet.remove();
 }
 assert.deepStrictEqual(errors,[],errors.join('\n'));
 const toastErr=toasts.querySelectorAll('.toast.err').map(t=>t.textContent); assert.deepStrictEqual(toastErr,[],'error toasts: '+toastErr.join('|'));
 console.log('DOM smoke test OK;', calls.length,'api calls');
})().catch(e=>{console.error('FAIL',e&&e.stack||e);process.exit(1)});
