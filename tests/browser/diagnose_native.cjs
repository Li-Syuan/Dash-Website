'use strict';
const fs=require('node:fs'), path=require('node:path'), http=require('node:http'), crypto=require('node:crypto');
const {spawn}=require('node:child_process'), readline=require('node:readline');
const {chromium}=require(process.env.QA_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'../..');
const out=path.join(root,'output/playwright','native-diagnostic-'+new Date().toISOString().replace(/[:.]/g,'-'));
fs.mkdirSync(out,{recursive:true});
const child=spawn(process.env.QA_PYTHON||'python',['-B',path.join(__dirname,'diagnose_native_server.py'),out],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe']});
child.stderr.pipe(fs.createWriteStream(path.join(out,'stderr.log')));
let resolveReady,rejectReady,sequence=0;const replies=new Map();
const ready=new Promise((resolve,reject)=>{resolveReady=resolve;rejectReady=reject;});
readline.createInterface({input:child.stdout}).on('line',line=>{const msg=JSON.parse(line);if(msg.event==='ready')resolveReady(msg);else if(msg.event==='reply'){replies.get(msg.sequence)?.(msg.result);replies.delete(msg.sequence);}});
child.on('error',rejectReady);child.on('exit',code=>{if(code)rejectReady(new Error('Diagnostic fixture exited '+code));});
function command(action,args={}){const id=++sequence;return new Promise(resolve=>{replies.set(id,resolve);child.stdin.write(JSON.stringify({action,sequence:id,...args})+'\n');});}
function nativeFetch(base,target){return new Promise(resolve=>{const record={client:'node http',path:target,received:0,chunks:[]};const digest=crypto.createHash('sha256'),start=Date.now();let done=false;
  function finish(error){if(done)return;done=true;Object.assign(record,{complete:!error,error:error?.code||error?.message,sha256:digest.digest('hex'),milliseconds:Date.now()-start});resolve(record);}
  const request=http.get(base+target,response=>{record.status=response.statusCode;record.headers=response.headers;response.on('data',part=>{record.received+=part.length;record.chunks.push(part.length);digest.update(part);});response.on('end',()=>finish());response.on('error',finish);});
  request.setTimeout(6000,()=>request.destroy(new Error('timeout')));request.on('error',finish);
});}
let browser, fixture;const results=[], browserFailures=[];
(async()=>{try{
  fixture=await ready;const base='http://127.0.0.1:'+fixture.port;
  browser=await chromium.launch({channel:process.env.QA_BROWSER_CHANNEL||'chrome',headless:true});
  const page=await browser.newPage();page.on('requestfailed',request=>browserFailures.push({path:new URL(request.url()).pathname,error:request.failure()?.errorText}));
  await page.goto(base+'/');
  for(const target of ['/small.js','/large.js','/table.js','/plotly.js','/plotly.js?chunk=16384']) {
    results.push(await command('python_fetch',{path:target}));
    results.push(await nativeFetch(base,target));
    results.push(await page.evaluate(async target=>{
      const record={client:'native chrome fetch',path:target,received:0,chunks:[]};const start=performance.now();
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),6000);const parts=[];
      try {const response=await fetch(target,{signal:controller.signal});record.status=response.status;record.headers=Object.fromEntries(response.headers);const reader=response.body.getReader();
        while(true){const part=await reader.read();if(part.done)break;record.received+=part.value.length;record.chunks.push(part.value.length);parts.push(part.value);}
        const bytes=new Uint8Array(record.received);let offset=0;for(const part of parts){bytes.set(part,offset);offset+=part.length;}
        const digest=await crypto.subtle.digest('SHA-256',bytes);record.sha256=Array.from(new Uint8Array(digest)).map(x=>x.toString(16).padStart(2,'0')).join('');record.complete=true;
      }catch(error){record.complete=false;record.error=error.name+': '+error.message;}finally{clearTimeout(timer);record.milliseconds=Math.round(performance.now()-start);}
      return record;
    },target));
    console.log(target,results.slice(-3).map(x=>x.client+':'+x.received+':'+x.complete).join(' '));
    fs.writeFileSync(path.join(out,'progress.json'),JSON.stringify({fixture,results,browserFailures},null,2));
  }
}catch(error){results.push({error:error.message});}
finally{if(browser)await browser.close();child.stdin.end(JSON.stringify({action:'shutdown'})+'\n');await new Promise(resolve=>child.once('exit',resolve));
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({fixture,results,browserFailures,serverStopped:true},null,2));console.log(path.relative(root,out));}
})();
