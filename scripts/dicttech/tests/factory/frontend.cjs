#!/usr/bin/env node
'use strict';
// DOM-only source-postimage checks. Every browser request is blocked.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../../..');
const patch = fs.readFileSync(path.join(root, 'patches/feeds/app_airoha/luci-app-airoha-factory/120-dicttech-readonly-split-dsd.patch'), 'utf8');
const target = 'luci-app-airoha-factory/htdocs/luci-static/resources/view/airoha_factory/factory.js';
const lines = patch.split('\n'); const start = lines.indexOf('+++ b/' + target) + 1;
assert(start > 0); const m = lines[start].match(/^@@ -1,\d+ \+1,(\d+) @@$/); assert(m);
const result = [];
for (const line of lines.slice(start + 1)) {
  if (line.startsWith('diff --git ')) break;
  assert(!line.startsWith('@@'));
  if (line.startsWith(' ') || line.startsWith('+')) result.push(line.slice(1));
}
assert.equal(result.length, +m[1]); const source = result.join('\n') + '\n';
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
(async () => {
 const browser = await chromium.launch({headless:true, executablePath:process.env.BROWSER_EXECUTABLE, args:['--no-sandbox']});
 try {
  const context = await browser.newContext(); let requests = 0;
  await context.route('**/*', route => {requests++; return route.abort();});
  const page = await context.newPage(); await page.setContent('<body style="background:white"></body>');
  const evidence = await page.evaluate(source => {
   String.prototype.format = function(...args) {let i=0; return this.replace(/%s/g, () => String(args[i++]));};
   function E(tag,attrs,children) {
    if (Array.isArray(tag)) {const fragment=document.createDocumentFragment();tag.forEach(node=>fragment.append(node));return fragment;}
    if (typeof attrs !== 'object' || Array.isArray(attrs) || attrs instanceof Node || attrs === null) {children=attrs;attrs={};}
    const el=document.createElement(tag);
    for (const [key,value] of Object.entries(attrs || {})) {
     if (value == null) continue;
     if (typeof value === 'function') el.addEventListener(key,value); else el.setAttribute(key,String(value));
    }
    const add = child => {if(Array.isArray(child))child.forEach(add);else if(child!=null)el.append(child instanceof Node?child:document.createTextNode(String(child)));};
    add(children);return el;
   }
   let rpcCalls=0;const ui={createHandlerFn:()=>()=>{},addNotification:()=>{}};
   const view=new Function('rpc','ui','view','E','_',source)({declare:()=>()=>{rpcCalls++;throw Error('RPC forbidden');}},ui,{extend:x=>x},E,x=>x);
   let superCalls=0;
   view.super=function(key,args) {
    if(key!=='addFooter'||!Array.isArray(args)||args.length)throw Error('invalid framework super delegation');
    superCalls++;return E('div', {'class':'base-footer'}, E('button',{},'BASE-SAVE'));
   };
   const raw={layout:'raw-dsd',writable:false,readable:true,mtd:'/dev/mtd2',
    fields:{serial_number:'SYNTHETIC-123',wan_mac:'02:11:22:33:44:50',lan_mac:'02:11:22:33:44:a9'},
    metadata:{model:'Synthetic XR1710G',bridge_mac:'02:00:00:00:99:00',description:'DictTech AP synthetic'}};
   const dom=view.render(raw);document.body.append(dom);
   const pairs=Object.fromEntries([...dom.querySelectorAll('dl')].flatMap(dl=>[...dl.querySelectorAll('dt')].map(dt=>[dt.textContent,dt.nextElementSibling.textContent])));
   const answer={factoryWAN:pairs['Factory WAN MAC'],factoryLAN:pairs['Factory LAN MAC'],serial:pairs['Factory serial number'],
    bridge:pairs['Current configured bridge MAC'],inputs:dom.querySelectorAll('input').length,
    misleadingUpgrade:dom.textContent.includes('Requires latest U-Boot'),guessedEnv:dom.textContent.includes('U-Boot ethaddr'),
    saveButtons:[...dom.querySelectorAll('button')].filter(b=>b.textContent.includes('Save')).length};
   answer.rawFooterEmpty=view.addFooter().childNodes.length===0;
   view.handleSave();answer.rpcCallsAfterRawSave=rpcCalls;
   const missing=view.render({error:'factory unavailable',metadata:raw.metadata});
   answer.missingMetadata=missing.querySelector('.fac-metadata').textContent.includes('DictTech AP synthetic');
   answer.missingWarning=!!missing.querySelector('.alert-message.warning');
   answer.errorFooterEmpty=view.addFooter().childNodes.length===0;
   for(const layout of ['legacy','dsd']) {
    view.render({...raw,layout,writable:false,env:{}});
    if(!view.addFooter().querySelector('button'))throw Error('supported layout lost standard footer');
   }
   answer.supportedFooterDelegations=superCalls;
   return answer;
  },source);
  assert.deepEqual(evidence,{factoryWAN:'02:11:22:33:44:50',factoryLAN:'02:11:22:33:44:a9',serial:'SYNTHETIC-123',bridge:'02:00:00:00:99:00',inputs:0,misleadingUpgrade:false,guessedEnv:false,saveButtons:0,rpcCallsAfterRawSave:0,missingMetadata:true,missingWarning:true,rawFooterEmpty:true,errorFooterEmpty:true,supportedFooterDelegations:2});
  assert.equal(requests,0);console.log(JSON.stringify({passed:true,scope:'network-blocked DOM',...evidence}));
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
