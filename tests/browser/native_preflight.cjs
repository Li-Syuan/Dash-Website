'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process'),readline=require('node:readline');
const {chromium}=require(process.env.QA_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'../..');
const out=path.join(root,'output/playwright','native-preflight-'+new Date().toISOString().replace(/[:.]/g,'-'));
fs.mkdirSync(out,{recursive:true});
const child=spawn(process.env.QA_PYTHON||'python',['-B',path.join(__dirname,'fixture_server.py'),path.join(out,'state')],
  {cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,QA_HTTP10:'0',QA_STATIC_BRIDGE:'0'}});
child.stderr.pipe(fs.createWriteStream(path.join(out,'server.log')));
let readyResolve,readyReject;const ready=new Promise((resolve,reject)=>{readyResolve=resolve;readyReject=reject;});
readline.createInterface({input:child.stdout}).on('line',line=>{try{const msg=JSON.parse(line);if(msg.event==='ready')readyResolve(msg);}catch{}});
child.on('error',readyReject);
const results=[],failures=[],pageErrors=[],consoleErrors=[];let browser,fixture,error,bars=0;
(async()=>{try{
  fixture=await ready;
  const browserSelection=process.env.QA_BROWSER_EXECUTABLE ? {executablePath:process.env.QA_BROWSER_EXECUTABLE} : {channel:process.env.QA_BROWSER_CHANNEL||'chrome'};
  browser=await chromium.launch({...browserSelection,headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:1080}}),page=await context.newPage();
  page.on('requestfailed',request=>failures.push({path:new URL(request.url()).pathname,error:request.failure()?.errorText}));
  page.on('pageerror',error=>pageErrors.push(error.message));
  page.on('console',message=>{if(message.type()==='error')consoleErrors.push(message.text());});
  // No request routes, response fulfillment, proxy, or browser cache override.
  const base='http://127.0.0.1:'+fixture.port;
  await page.goto(base+'/login');await page.waitForLoadState('networkidle');await page.locator('#username-box').fill('demo-admin');
  await page.locator('#password-box').fill('demo-only');await page.locator('#login-box').click();
  await page.waitForURL(base+'/');await page.waitForLoadState('networkidle');await page.goto(base+'/page3');
  await page.locator('#performance-chart .main-svg .barlayer .point path').first().waitFor({state:'visible',timeout:20000});
  bars=await page.locator('#performance-chart .main-svg .barlayer .point path').count();assert(bars>0);
  await page.screenshot({path:path.join(out,'native-plotly-render.png'),fullPage:true});
  fs.writeFileSync(path.join(out,'native-plotly-render.dom.txt'),await page.locator('body').innerText());
  for(let index=0;index<20;index++){
    const result=await page.evaluate(async({target,index})=>{
      const start=performance.now(),controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
      try{const response=await fetch(target+'?native_preflight='+index,{cache:'no-store',signal:controller.signal});
        const bytes=await response.arrayBuffer(),digest=await crypto.subtle.digest('SHA-256',bytes);
        return {iteration:index+1,status:response.status,headers:Object.fromEntries(response.headers),size:bytes.byteLength,
          sha256:Array.from(new Uint8Array(digest)).map(value=>value.toString(16).padStart(2,'0')).join(''),milliseconds:Math.round(performance.now()-start)};
      }finally{clearTimeout(timer);}
    },{target:fixture.plotly_asset.path,index});
    results.push(result);assert.equal(result.status,200);assert.equal(result.size,fixture.plotly_asset.size);assert.equal(result.sha256,fixture.plotly_asset.sha256);
    console.log('PASS native Plotly GET '+(index+1)+' bytes='+result.size);
  }
  assert.deepEqual(failures,[]);assert.deepEqual(pageErrors,[]);assert.deepEqual(consoleErrors,[]);
}catch(problem){error=problem.message;process.exitCode=1;}
finally{
  if(browser)await browser.close();
  const stopped=new Promise(resolve=>child.once('exit',resolve));child.stdin.end(JSON.stringify({action:'shutdown'})+'\n');await stopped;
  const state=path.join(out,'state');assert(path.resolve(state).startsWith(path.resolve(root,'output/playwright')+path.sep));
  fs.rmSync(state,{recursive:true,force:true});
  fs.writeFileSync(path.join(out,'source-hashes.json'),JSON.stringify(fixture?.source_hashes||{},null,2));
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({status:error?'failed':'passed',error,results,renderedSvgBars:bars,failures,pageErrors,consoleErrors,
    expectedAsset:fixture?.plotly_asset,versions:{node:process.version,python:fixture?.python,browser:browser?.version(),packages:fixture?.packages,
      browserChannel:process.env.QA_BROWSER_EXECUTABLE?null:process.env.QA_BROWSER_CHANNEL||'chrome',
      browserExecutable:process.env.QA_BROWSER_EXECUTABLE?path.basename(process.env.QA_BROWSER_EXECUTABLE):null},
    delivery:'native Chromium-family browser; no request interception; 20 uncached same-origin GETs',serverStopped:true,isolatedStateRemoved:true},null,2));
  console.log(path.relative(root,out));
}
})();
