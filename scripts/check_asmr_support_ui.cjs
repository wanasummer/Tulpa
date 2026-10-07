// Real browser with isolated API fixtures. No model calls or QQ sends.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const root = path.resolve(__dirname, '..');
const html = process.env.SUPPORT_STANDALONE ? fs.readFileSync(path.join(root,'web','support-home.html'),'utf8') : `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Support UI</title>
<link rel="stylesheet" href="/ui/app.css"><link rel="stylesheet" href="/ui/desktop.css">
<body><div class="sidebar-bottom"></div><script src="/ui/support.js"></script></body></html>`;
(async () => {
  const browser = await chromium.launch({channel:'msedge', headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1100,height:950}});
    const errors = [], requests = [];
    page.on('pageerror', error => errors.push(error.message));
    let running = false, failDocs = false;
    const state = () => ({budget:{limit_yuan:5,spent_yuan:0.12,reserved_yuan:0.03,remaining_yuan:4.85,unresolved:1},
      knowledge:{project_root:'C:/test/asmrTranstor',sources:3,chunks:12},
      receiver:{state:'connected'}, running, run:running?{name:'测试群',conversation_id:'1:group:2',active:1}:null,
      error:'', today_bugs:1, bugs:[{id:1,title:'字幕报错 <img src=x>',details:'请检查格式',day:'2026-10-05',sender:'群友',cid:'1:group:2',mid:'99',status:'待确认'}],
      recent:[{state:'REVIEW_BLOCKED',sender:'群友',question:'配音失败'}],
      reviews:[{day:'2026-10-05',state:'BLOCKED_MODEL',reason:'internal_business'}],database:'C:/test/data/asmr-support.sqlite3'});
    await page.route('http://127.0.0.1:39877/**', async route => {
      const request=route.request(), url=new URL(request.url());
      const reply=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
      if (url.pathname==='/') return route.fulfill({contentType:'text/html',body:html});
      if (url.pathname.startsWith('/ui/')) return route.fulfill({contentType:url.pathname.endsWith('.js')?'text/javascript':'text/css',body:fs.readFileSync(path.join(root,'web',url.pathname.slice(4)),'utf8')});
      if (url.pathname==='/api/support' && request.method()==='GET') return reply(state());
      if (url.pathname==='/api/support/groups') return reply({groups:[{name:'测试群',conversation_id:'1:group:2'}]});
      const body=request.postDataJSON(); requests.push({path:url.pathname,body,headers:request.headers()});
      if (url.pathname==='/api/support/start') {running=true;return reply(state());}
      if (url.pathname==='/api/support/stop') {running=false;return reply({stopped:true});}
      if (url.pathname==='/api/support/preview') return reply({reply:'在导出页面选择字幕文件。'});
      if (url.pathname==='/api/support/documents') return reply({errors:failDocs?['文档解析失败']:[]});
      return reply({updated:true});
    });
    await page.goto('http://127.0.0.1:39877/');
    await page.click('#open-support');
    await page.locator('#support-budget').filter({hasText:'剩余 ¥4.8500'}).waitFor();
    assert.match(await page.locator('#support-database').textContent(),/SQLite/);
    assert.match(await page.locator('#support-recent').textContent(),/审核拦截/);
    assert.match(await page.locator('#support-reviews').textContent(),/内部业务信息/);
    assert.equal(await page.locator('#support-dialog input[type=number]').count(),0);
    assert.equal(await page.locator('#support-bugs img').count(),0,'Untrusted bug text became HTML');
    await page.click('#support-groups');
    await page.selectOption('#support-group','1:group:2');
    await page.click('#support-project');
    await page.locator('#support-start:not([disabled])').waitFor();
    await page.click('#support-start');
    await page.locator('#support-state').filter({hasText:'运行中'}).waitFor();
    assert.equal(await page.locator('#support-project').isDisabled(),true);
    assert.equal(await page.locator('#support-documents').isDisabled(),true);
    await page.click('#support-stop');
    await page.locator('#support-state').filter({hasText:'已停止'}).waitFor();
    await page.locator('#support-dialog details').first().locator('summary').click();
    await page.fill('#support-question','如何导出字幕？');
    await page.click('#support-preview');
    await page.locator('#support-answer').filter({hasText:'在导出页面'}).waitFor();
    failDocs=true;
    await page.click('#support-documents');
    await page.locator('#support-error').filter({hasText:'原群知识保留'}).waitFor();
    await page.selectOption('#support-bugs select','处理中');
    await page.locator('#support-project:not([disabled])').waitFor();
    assert(requests.every(r => r.headers['x-chatweave-ui']==='1'));
    assert.deepEqual(requests.find(r=>r.path.endsWith('/start')).body,{conversation_id:'1:group:2'});
    assert(!requests.some(r=>'budget' in r.body || 'limit' in r.body));
    assert.equal(requests.filter(r=>r.path.endsWith('/start')).length,1,'Preview started another sender');
    const output=path.join(root,'.tmp');fs.mkdirSync(output,{recursive:true});
    await page.screenshot({path:path.join(output,'asmr-support-ui.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.getElementById('support-dialog').getBoundingClientRect().width<=innerWidth));
    await page.click('#support-close');
    assert.deepEqual(errors,[]);
    console.log('ASMR support UI acceptance passed: fixed budget, scoped group, start/stop, preview, bug status, XSS, mobile.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
