// Focused recovery tests for the production UI request identity protocol.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(process.env.MESH_UI_SCRIPT||path.join(__dirname,'../src/gis_mesh_workbench/webui/app.js'),'utf8');
const initial=source.slice(0,source.indexOf("$('form').addEventListener"));
const recovery=source.slice(source.indexOf('function setBusy('),source.indexOf('async function pollJob('));

function setup(){
  const elements=new Map(),storage=new Map();
  class Element{
    constructor(){this.children=[];this.value='';this.hidden=false;this.disabled=false;this.textContent='';this.className='';this.dataset={};this.attributes={};this.classes=new Set();this.classList={add:n=>this.classes.add(n),remove:n=>this.classes.delete(n),toggle:(n,on)=>on?this.classes.add(n):this.classes.delete(n)};}
    before(){} append(...items){this.children.push(...items);} replaceChildren(...items){this.children=items;}
    setAttribute(name,value){this.attributes[name]=String(value);} getAttribute(name){return this.attributes[name]??null;}
    removeAttribute(name){delete this.attributes[name];} addEventListener(){} querySelectorAll(){return [];}
  }
  const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const timers=[];
  const context=vm.createContext({
    document:{getElementById:get,createElement:()=>new Element(),createElementNS:()=>new Element(),querySelector:selector=>get(selector),querySelectorAll:()=>[],body:{dataset:{}}},
    location:{hash:'',pathname:'/'},history:{replaceState(){}},sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,String(value)),removeItem:key=>storage.delete(key)},
    URL:{revokeObjectURL(){}},Blob,atob,Uint8Array,URLSearchParams,crypto:{randomUUID:()=> 'stable-request-id-1234'},
    setTimeout:callback=>{timers.push(callback);return timers.length;},clearTimeout(){},console,fetch(){throw Error('mock API not configured');},coach:{update(){}}
  });
  vm.runInContext(initial+recovery,context);
  vm.runInContext('setBusy=value=>{busy=value};resetResult=()=>{}',context);
  return {context,get,storage,timers,run:code=>vm.runInContext(code,context)};
}

test('lost POST response recovers only the matching main operation/request identity',async()=>{
  const t=setup(),calls=[];let runPayload;
  t.run("preflightId='preflight-1';busy=false;pollJob=async id=>{jobId=id;}");
  t.context.mockApi=async(path,data)=>{
    calls.push(path);
    if(path==='/api/run'){runPayload=data;throw Error('response lost');}
    assert.equal(path,'/api/run/status?request_id=stable-request-id-1234');
    return {job_id:'main-job',operation:'extract',request_id:'stable-request-id-1234',status:'pass'};
  };
  t.run('api=mockApi');
  await t.run('startRun()');
  assert.equal(runPayload.preflight_id,'preflight-1');
  assert.equal(runPayload.request_id,'stable-request-id-1234');
  assert.deepEqual(calls,['/api/run','/api/run/status?request_id=stable-request-id-1234']);
  assert.equal(t.run('jobId'),'main-job');
  assert.equal(t.storage.has('mesh-cherry-run-request'),true);
});

test('recovery refuses an unrelated operation and clears the pending request',async()=>{
  const t=setup();let polled=false;
  t.run("runRequest={preflightId:'preflight-1',requestId:'stable-request-id-1234'};saveRunRequest();busy=true;pollJob=async()=>{polled=true;}");
  t.context.mockApi=async()=>({job_id:'comparison-job',operation:'comparison',request_id:'stable-request-id-1234',status:'pass'});t.run('api=mockApi');
  await t.run('recoverStart()');
  assert.equal(polled,false);
  assert.equal(t.run('busy'),false);
  assert.equal(t.storage.has('mesh-cherry-run-request'),false);
});

test('missing status entry replays the same idempotent start request',async()=>{
  const t=setup(),posts=[];
  t.run("runRequest={preflightId:'preflight-1',requestId:'stable-request-id-1234'};saveRunRequest();busy=true;pollJob=async id=>{jobId=id;}");
  t.context.mockApi=async(path,data)=>{
    if(path.startsWith('/api/run/status?'))return {job_id:null};
    if(path==='/api/run'){posts.push(data);return {job_id:'replayed-job',operation:'mesh_only',request_id:data.request_id,status:'running'};}
    throw Error('unexpected path '+path);
  };
  t.run('api=mockApi');
  await t.run('recoverStart()');
  assert.equal(JSON.stringify(posts),JSON.stringify([{preflight_id:'preflight-1',request_id:'stable-request-id-1234'}]));
  assert.equal(t.run('jobId'),'replayed-job');
});
