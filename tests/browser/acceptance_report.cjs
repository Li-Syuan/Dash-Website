/* Visual verification of a reviewed, static local acceptance report. No server. */
'use strict';
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const crypto=require('node:crypto');
const assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const {pathToFileURL,fileURLToPath}=require('node:url');

const checkout=path.resolve(__dirname,'../..');
const outputRoot=path.join(checkout,'output');
const sections=['overview','findings','performance','reproduce','sources','limits'];
const statuses=['PASS','FAIL','SKIP','INVALID','INCOMPLETE'];
const expectedChecks=['desktop','mobile'].flatMap(viewport=>['document-and-legend','static-content-and-csp','document-width',
  'closed-screenshot-and-dom','details-keyboard'].map(name=>viewport+'-'+name)).concat(['no-external-resources-or-browser-errors','report-file-unchanged']);
const normalize=value=>process.platform==='win32'?path.resolve(value).toLowerCase():path.resolve(value);
const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
function inside(parent,target) {
  const relative=path.relative(parent,target);
  return Boolean(relative)&&relative!=='..'&&!relative.startsWith('..'+path.sep)&&!path.isAbsolute(relative);
}
function inspectChain(target) {
  let current=path.parse(target).root;
  for(const part of target.slice(current.length).split(path.sep).filter(Boolean)) {
    current=path.join(current,part);
    assert(!fs.lstatSync(current).isSymbolicLink(),'Observed symbolic link or junction in path');
  }
  assert.equal(normalize(fs.realpathSync(target)),normalize(target),'Resolved path differs from requested path');
}
function inspectWindowsReparse(paths) {
  if(process.platform!=='win32') return;
  // Literal paths are passed as data, never interpolated into shell source.
  const command=`$ErrorActionPreference='Stop'
try {
  foreach ($leaf in (ConvertFrom-Json -InputObject $env:QA_REPORT_INSPECT_PATHS)) {
    $item=Get-Item -LiteralPath $leaf -Force
    while ($null -ne $item) {
      if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { exit 21 }
      if ($item.PSIsContainer) { $item=$item.Parent } else { $item=$item.Directory }
    }
  }
  exit 0
} catch { exit 22 }`;
  const executable=path.join(process.env.SystemRoot||'C:\\Windows','System32/WindowsPowerShell/v1.0/powershell.exe');
  const result=spawnSync(executable,['-NoProfile','-NonInteractive','-Command',command],{
    windowsHide:true,encoding:'utf8',timeout:10000,maxBuffer:16384,
    env:{...process.env,QA_REPORT_INSPECT_PATHS:JSON.stringify(paths)},
  });
  assert.equal(result.status,0,result.status===21?'Observed Windows reparse point':'Windows path-attribute inspection did not complete');
}
function reportPath(argument) {
  assert(argument,'Usage: node tests/browser/acceptance_report.cjs output/<report>.html');
  const target=path.resolve(argument);
  assert(inside(outputRoot,target),'Report must be beneath this checkout/output');
  assert(/\.html?$/i.test(target),'Report must be an HTML file');
  if(process.platform==='win32') assert(!target.slice(path.parse(target).root.length).includes(':'),'Alternate data streams are not report files');
  inspectChain(outputRoot);inspectChain(target);inspectWindowsReparse([target]);
  assert(fs.statSync(target).isFile(),'Report must be a regular file');
  return target;
}
function cleanError(value) {
  let text=String(value);
  for(const [location,label] of [[checkout,'<checkout>'],[os.homedir(),'<user-home>']]) {
    for(const variant of [location,location.replaceAll('\\','/'),pathToFileURL(location).href]) text=text.split(variant).join(label);
  }
  return text.slice(0,6000);
}
function resourceLabel(url) {
  try {
    const parsed=new URL(url);
    return parsed.protocol==='file:'?'file:<local-report-or-resource>':parsed.protocol+'<resource>';
  } catch {return '<unrecognized-resource>';}
}

let report,artifactDir,browser,browserVersion,playwrightVersion,browserClosed=false;
const checks=[],requests=[],failedRequests=[],blockedRequests=[],pageErrors=[],consoleErrors=[];
const startedAt=new Date().toISOString();
async function check(name,operation) {
  try {const detail=await operation();checks.push({name,status:'PASS',detail});process.stdout.write('PASS '+name+'\n');}
  catch(error) {checks.push({name,status:'FAIL',error:cleanError(error.message)});process.stdout.write('FAIL '+name+': '+cleanError(error.message)+'\n');}
}
async function saveView(page,name) {
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:path.join(artifactDir,name+'.png'),fullPage:true,animations:'disabled'});
  fs.writeFileSync(path.join(artifactDir,name+'.dom.txt'),cleanErrorUnlimited(await page.locator('body').innerText()));
}
function cleanErrorUnlimited(value) {
  let text=String(value);
  for(const [location,label] of [[checkout,'<checkout>'],[os.homedir(),'<user-home>']]) {
    for(const variant of [location,location.replaceAll('\\','/'),pathToFileURL(location).href]) text=text.split(variant).join(label);
  }
  return text;
}
async function noOverflow(page) {
  const size=await page.evaluate(()=>({clientWidth:document.documentElement.clientWidth,
    scrollWidth:document.documentElement.scrollWidth,bodyScrollWidth:document.body.scrollWidth,viewport:window.innerWidth}));
  assert(size.scrollWidth<=size.clientWidth,'Document has horizontal overflow: '+JSON.stringify(size));
  return size;
}
async function inspectViewport(name,viewport) {
  const context=await browser.newContext({viewport,javaScriptEnabled:false,serviceWorkers:'block'});
  try {
    await context.route('**/*',async route=>{
      const request=route.request();let allowed=false;
      try {const url=new URL(request.url());allowed=url.protocol==='file:'&&normalize(fileURLToPath(url))===normalize(report);} catch {}
      if(!allowed) {blockedRequests.push({viewport:name,resource:resourceLabel(request.url()),type:request.resourceType()});await route.abort();}
      else await route.continue();
    });
    const page=await context.newPage();page.setDefaultTimeout(10000);page.setDefaultNavigationTimeout(15000);
    page.on('request',request=>requests.push({viewport:name,resource:resourceLabel(request.url()),type:request.resourceType()}));
    page.on('requestfailed',request=>failedRequests.push({viewport:name,resource:resourceLabel(request.url()),error:request.failure()?.errorText}));
    page.on('pageerror',error=>pageErrors.push({viewport:name,message:cleanError(error.message)}));
    page.on('console',message=>{if(message.type()==='error')consoleErrors.push({viewport:name,message:cleanError(message.text())});});
    await page.goto(pathToFileURL(report).href,{waitUntil:'load'});
    await check(name+'-document-and-legend',async()=>{
      assert.equal(await page.locator('html').getAttribute('lang'),'zh-Hant');
      assert((await page.title()).trim());assert.equal(await page.locator('h1').count(),1);assert(await page.locator('h1').isVisible());
      const meta=await page.locator('meta[name="viewport"]').getAttribute('content');assert(/width\s*=\s*device-width/i.test(meta||''));
      const legend=page.locator('#status-legend');assert.equal(await legend.count(),1);assert(await legend.isVisible());
      const text=await legend.innerText();for(const status of statuses)assert(new RegExp('\\b'+status+'\\b').test(text),'Legend missing '+status);
      const headings={};
      for(const id of sections) {
        const section=page.locator('#'+id);assert.equal(await section.count(),1,'Required section '+id);
        const heading=section.locator('h1,h2,h3').first();assert(await heading.isVisible(),'Visible heading required for '+id);
        headings[id]=(await heading.innerText()).trim();assert(headings[id]);
        assert((await section.innerText()).trim().length>headings[id].length,'Required section has no content: '+id);
      }
      const findings=await page.locator('#findings').innerText(),limits=await page.locator('#limits').innerText();
      assert(/FAIL|失敗/.test(findings),'Failure area must identify failures');
      assert(/未驗證|待驗證|尚未|INCOMPLETE|INVALID|UNRUN|限制|未完成/.test(findings+' '+limits),'Unverified scope must remain visible');
      assert(await page.locator('#reproduce pre,#reproduce code').count(),'Reproduction commands must be displayed as text');
      return {headings,statusLegend:statuses,historyCards:await page.locator('[data-evidence-role="history"]').count(),currentCards:await page.locator('[data-evidence-role="current"]').count()};
    });
    await check(name+'-static-content-and-csp',async()=>{
      const audit=await page.evaluate(()=>({
        csp:[...document.querySelectorAll('meta[http-equiv]')].filter(element=>element.httpEquiv.toLowerCase()==='content-security-policy').map(element=>element.content),
        executableElements:[...document.querySelectorAll('script,iframe,object,embed,form,button,input,select,textarea,[contenteditable]:not([contenteditable="false"])')].map(element=>element.tagName),
        eventHandlers:[...document.querySelectorAll('*')].flatMap(element=>[...element.attributes].filter(attribute=>/^on/i.test(attribute.name)).map(attribute=>element.tagName+'.'+attribute.name)),
        unsafeLinks:[...document.querySelectorAll('[href],[src],[action],[formaction]')].flatMap(element=>[...element.attributes].filter(attribute=>['href','src','action','formaction'].includes(attribute.name)&&(/^\s*(?:javascript|vbscript|data):/i.test(attribute.value)||/\.(?:exe|bat|cmd|ps1|sh|cjs|mjs|js)(?:[?#]|$)/i.test(attribute.value))).map(attribute=>element.tagName+'.'+attribute.name)),
        refresh:[...document.querySelectorAll('meta[http-equiv]')].some(element=>element.httpEquiv.toLowerCase()==='refresh'),
      }));
      assert.deepEqual(audit.executableElements,[]);assert.deepEqual(audit.eventHandlers,[]);assert.deepEqual(audit.unsafeLinks,[]);assert.equal(audit.refresh,false);
      assert.equal(audit.csp.length,1,'Exactly one CSP meta is required');
      const directives=new Map(audit.csp[0].split(';').map(value=>value.trim()).filter(Boolean).map(value=>{const [key,...values]=value.split(/\s+/);return [key.toLowerCase(),values.join(' ')];}));
      assert.equal(directives.get('default-src'),"'none'");
      for(const directive of ['script-src','connect-src','object-src','frame-src']) assert.equal(directives.get(directive)||directives.get('default-src'),"'none'",directive);
      assert.equal(directives.get('base-uri'),"'none'");assert.equal(directives.get('form-action'),"'none'");
      return {executableControls:0,eventHandlers:0,csp:audit.csp[0],reportScriptsEnabled:false};
    });
    await check(name+'-document-width',()=>noOverflow(page));
    await check(name+'-closed-screenshot-and-dom',async()=>{await saveView(page,name);return {screenshot:name+'.png',dom:name+'.dom.txt'};});
    await check(name+'-details-keyboard',async()=>{
      const details=page.locator('details');const count=await details.count();assert(count>0,'At least one native details disclosure is required');
      let tabCount=0,summaryFocused=false;
      while(tabCount<200&&!summaryFocused) {await page.keyboard.press('Tab');tabCount++;summaryFocused=await page.evaluate(()=>document.activeElement?.tagName==='SUMMARY');}
      assert(summaryFocused,'A disclosure summary must be reachable with Tab');
      for(let index=0;index<count;index++) {
        const detail=details.nth(index),summary=detail.locator(':scope > summary');assert.equal(await summary.count(),1);
        const ancestorIndexes=await detail.evaluate(element=>{
          const all=[...document.querySelectorAll('details')],ancestors=[];
          for(let parent=element.parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')ancestors.unshift(all.indexOf(parent));
          return ancestors;
        });
        const openedAncestors=[];
        for(const ancestorIndex of ancestorIndexes) {
          const ancestor=details.nth(ancestorIndex);
          if(!(await ancestor.evaluate(element=>element.open))){await ancestor.locator(':scope > summary').focus();await page.keyboard.press('Enter');openedAncestors.push(ancestorIndex);}
        }
        try {
          const initiallyOpen=await detail.evaluate(element=>element.open);await summary.focus();
          assert(await summary.evaluate(element=>element===document.activeElement));
          if(initiallyOpen)await page.keyboard.press('Space');
          assert.equal(await detail.evaluate(element=>element.open),false);
          await page.keyboard.press('Enter');assert.equal(await detail.evaluate(element=>element.open),true);
          await noOverflow(page);
          await page.keyboard.press('Space');assert.equal(await detail.evaluate(element=>element.open),false);
          if(initiallyOpen)await page.keyboard.press('Enter');
        } finally {
          for(const ancestorIndex of openedAncestors.reverse()){await details.nth(ancestorIndex).locator(':scope > summary').focus();await page.keyboard.press('Space');}
        }
      }
      const first=details.first(),summary=first.locator(':scope > summary'),initiallyOpen=await first.evaluate(element=>element.open);
      if(!initiallyOpen){await summary.focus();await page.keyboard.press('Enter');}
      await noOverflow(page);await saveView(page,name+'-details-expanded');
      if(!initiallyOpen){await summary.focus();await page.keyboard.press('Space');}
      return {disclosures:count,tabPressesToSummary:tabCount,keys:['Enter','Space'],allRestoredToOriginalState:true};
    });
  } finally {await context.close();}
}

(async()=>{
  let initialHash,inputBytes;
  try {
    assert.equal(process.argv.length,3,'Supply exactly one local report HTML path');
    report=reportPath(process.argv[2]);const original=fs.readFileSync(report);initialHash=digest(original);inputBytes=original.length;
    const output=path.join(outputRoot,'playwright');if(!fs.existsSync(output))fs.mkdirSync(output);inspectChain(output);inspectWindowsReparse([output]);
    artifactDir=fs.mkdtempSync(path.join(output,'acceptance-report-'+startedAt.replace(/[:.]/g,'-')+'-'));inspectChain(artifactDir);
    const moduleName=process.env.QA_PLAYWRIGHT_MODULE||'playwright';
    const {chromium}=require(moduleName);playwrightVersion=require(moduleName+'/package.json').version;
    browser=await chromium.launch({channel:process.env.QA_BROWSER_CHANNEL||'chrome',headless:true});browserVersion=browser.version();
    await inspectViewport('desktop',{width:1440,height:1000});
    await inspectViewport('mobile',{width:390,height:844});
    await check('no-external-resources-or-browser-errors',async()=>{
      assert.deepEqual(blockedRequests,[]);assert.deepEqual(failedRequests,[]);assert.deepEqual(pageErrors,[]);assert.deepEqual(consoleErrors,[]);
      return {observedRequests:requests.length,externalRequests:0,pageErrors:0,consoleErrors:0};
    });
    await check('report-file-unchanged',async()=>{reportPath(report);assert.equal(digest(fs.readFileSync(report)),initialHash);return {sha256:initialHash,bytes:inputBytes};});
  } catch(error) {checks.push({name:'harness',status:'FAIL',error:cleanError(error.message)});}
  finally {
    if(browser) {
      try {await browser.close();browserClosed=!browser.isConnected();assert(browserClosed);}
      catch(error){checks.push({name:'owned-browser-cleanup',status:'FAIL',error:cleanError(error.message)});}
    }
    const result={startedAt,completedAt:new Date().toISOString(),status:checks.some(check=>check.status==='FAIL')?'FAIL':'PASS',
      scope:'Static report rendering/accessibility checks only; this result does not certify the project or refresh underlying acceptance evidence.',
      report:report?{path:path.relative(checkout,report).replaceAll('\\','/'),bytes:inputBytes,sha256:initialHash}:null,
      runtime:{platform:process.platform,node:process.version,playwright:playwrightVersion,chrome:browserVersion},
      harnessSha256:digest(fs.readFileSync(__filename)),passed:checks.filter(check=>check.status==='PASS').length,failed:checks.filter(check=>check.status==='FAIL').length,
      unrun:expectedChecks.filter(name=>!checks.some(check=>check.name===name)),
      checks,requests,failedRequests,blockedRequests,pageErrors,consoleErrors,browserLaunched:Boolean(browser),ownedBrowserClosed:browserClosed,
      applicationServerStarted:false,reportScriptsEnabled:false};
    if(artifactDir) {
      fs.writeFileSync(path.join(artifactDir,'results.json'),JSON.stringify(result,null,2));
      const artifacts={};for(const name of fs.readdirSync(artifactDir)){const file=path.join(artifactDir,name);if(fs.statSync(file).isFile())artifacts[name]={bytes:fs.statSync(file).size,sha256:digest(fs.readFileSync(file))};}
      fs.writeFileSync(path.join(artifactDir,'artifact-hashes.json'),JSON.stringify(artifacts,null,2));
    }
    process.stdout.write(JSON.stringify({status:result.status,passed:result.passed,failed:result.failed,
      directory:artifactDir?path.relative(checkout,artifactDir).replaceAll('\\','/'):null,
      error:artifactDir?undefined:checks.find(check=>check.status==='FAIL')?.error})+'\n');
    process.exitCode=result.status==='PASS'?0:1;
  }
})();
