'use strict';
(() => {
  const host = document.querySelector('.sidebar-bottom');
  if (!host) return;
  const button = document.createElement('button');
  button.className = 'nav-button muted'; button.id = 'open-support';
  button.textContent = 'ASMRTranslator 群助手'; host.prepend(button);
  const dialog = document.createElement('dialog');
  dialog.id = 'support-dialog'; dialog.className = 'desktop-config';
  dialog.setAttribute('aria-labelledby', 'support-title');
  dialog.innerHTML = `<div class="dialog-head"><h2 id="support-title">ASMRTranslator 群助手</h2><button type="button" class="icon-button" id="support-close" aria-label="关闭群助手">×</button></div>
    <div class="desktop-config-body">
    <p>技术答疑优先。答复经过独立 DeepSeek 安全审核后自动发送，无需人工逐条审批；未通过或审核不可用时拦截。问题记录保存在本机。</p>
    <p id="support-budget" role="status">每日固定额度 ¥5.00</p>
    <small>北京时间每天零点使用新额度。费用按高峰单价保守估算；费用未知的请求保留预留。此额度覆盖群助手和本页试答，不包含其他程序调用。</small>
    <p id="support-state" role="status"></p>
    <label for="support-root">ASMRTranslator 本机项目目录</label><input id="support-root" type="text" spellcheck="false">
    <button type="button" id="support-project">更新 README 和前后端代码知识</button><p id="support-knowledge"></p>
    <label for="support-group">目标 QQ 群</label><select id="support-group"><option value="">请先读取群列表</option></select>
    <button type="button" id="support-groups">读取群列表</button><button type="button" id="support-documents">更新所选群文档</button>
    <small>使用“模型与连接”中已有的 DeepSeek 和 OneBot 配置。更新群文档会下载并解析所选群的文档。</small>
    <div class="config-foot"><button type="button" id="support-start" class="primary-button">启动所选群自动回复</button><button type="button" id="support-stop">停止自动回复</button></div>
    <details><summary>试答（计入每日额度，仅在本页显示）</summary><label for="support-question">使用问题</label><textarea id="support-question" maxlength="6000" rows="3"></textarea><button type="button" id="support-preview">生成试答</button><p id="support-answer" style="white-space:pre-wrap"></p></details>
    <h3 id="support-bugs-title">问题汇总</h3><div id="support-bugs"></div>
    <details><summary>最近处理记录</summary><div id="support-recent"></div></details>
    <details><summary>最近安全审核</summary><div id="support-reviews"></div></details>
    <small id="support-database"></small>
    <p id="support-error" role="alert" style="white-space:pre-wrap"></p></div>`;
  document.body.append(dialog);
  const connectionHint = host.dataset.connectionHint;
  if (connectionHint) dialog.querySelectorAll('small')[1].textContent = connectionHint;
  const el = id => document.getElementById('support-'+id);
  let timer, busy = false, snapshot;
  async function api(path='', body, method='POST') {
    const response = await fetch('/api/support'+path, body === undefined ? {} : {
      method, headers:{'Content-Type':'application/json','X-ChatWeave-UI':'1'}, body:JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '群助手请求失败');
    return data;
  }
  function controls() {
    const running = snapshot?.running;
    for (const id of ['project','documents','groups','preview']) el(id).disabled = busy || Boolean(running && ['project','documents'].includes(id));
    el('start').disabled = busy || running || !el('group').value;
    // Stop remains available while a paid preview or another operation is in progress.
    el('stop').disabled = !running && !snapshot?.run?.active;
    el('group').disabled = busy || running;
    el('root').disabled = busy || running;
  }
  function render(data) {
    snapshot = data;
    const b = data.budget;
    el('budget').textContent = `每日固定 ¥5.00 · 已用约 ¥${b.spent_yuan.toFixed(4)} · 预留 ¥${b.reserved_yuan.toFixed(4)} · 剩余 ¥${b.remaining_yuan.toFixed(4)}`;
    const receiver = {connected:'已连接',connecting:'连接中',unconfigured:'未配置',auth_error:'认证失败',account_mismatch:'账号不匹配',disconnected:'已断开',stopped:'已停止'};
    el('state').textContent = `${data.running ? '运行中 · '+data.run.name : '已停止'} · 事件接收：${receiver[data.receiver.state] || '等待连接'}${b.unresolved ? ' · 有 '+b.unresolved+' 笔费用待核对' : ''}`;
    if (document.activeElement !== el('root') && !busy) el('root').value = data.knowledge.project_root;
    el('knowledge').textContent = `已索引 ${data.knowledge.sources} 份资料，${data.knowledge.chunks} 个片段。`;
    el('bugs-title').textContent = `问题汇总 · 今日新增 ${data.today_bugs} 条`;
    const fragment = document.createDocumentFragment();
    for (const bug of data.bugs) {
      const section = document.createElement('section'); section.style.marginBottom='16px';
      const title = document.createElement('strong'); title.textContent=`#${bug.id} ${bug.title}`;
      const detail = document.createElement('p'); detail.textContent=bug.details; detail.style.whiteSpace='pre-wrap';
      const source = document.createElement('small'); source.textContent=`${bug.day} · ${bug.sender} · ${bug.cid} · QQ消息 ${bug.mid}`;
      const select = document.createElement('select'); select.setAttribute('aria-label', `问题 ${bug.id} 状态`);
      for (const status of ['待确认','待补充','处理中','已解决','使用问题']) select.add(new Option(status,status,false,status===bug.status));
      select.onchange=()=>act(()=>api('/bugs/'+bug.id,{status:select.value},'PUT'));
      section.append(title,detail,source,select); fragment.append(section);
    }
    if (!data.bugs.length) fragment.append(document.createTextNode('尚无问题反馈。'));
    el('bugs').replaceChildren(fragment);
    el('recent').replaceChildren(...data.recent.map(row=>{
      const p=document.createElement('p');
      const states={REVIEW_BLOCKED:'审核拦截',BUDGET_BLOCKED:'额度不足',SUCCEEDED:'已发送',UNKNOWN:'结果待核对',PREVIEWED:'已试答',OWNER_CASUAL:'@群主闲聊，未回复',FOLLOWUP_CASUAL:'追问后闲聊，未回复',CASUAL_LIMIT:'闲聊次数已用完'};
      p.textContent=`[${states[row.state] || row.state}] ${row.sender}：${row.question.slice(0,160)}`; return p;
    }));
    el('reviews').replaceChildren(...(data.reviews || []).map(row=>{
      const p=document.createElement('p');
      const reasons={safe:'通过',code:'代码内容',internal_business:'内部业务信息',secret:'秘密或内部路径',injection:'注入或提示词泄露',unsupported:'未经确认的结论',uncertain:'无法确认',invalid_verdict:'审核结果格式无效',review_unavailable:'审核不可用',budget:'审核额度不足',invalid:'答复格式无效'};
      p.textContent=`${row.day} · ${row.state === 'ALLOWED' ? '允许' : '拦截'} · ${reasons[row.reason] || '无法确认'}`; return p;
    }));
    el('database').textContent=data.database ? `本地数据库：SQLite · ${data.database}` : '本地数据库：SQLite';
    if (data.error) el('error').textContent=data.error;
    controls();
  }
  async function reload() { render(await api()); }
  async function act(fn) {
    if (busy) return;
    busy=true; el('error').textContent=''; controls();
    try { await fn(); await reload(); }
    catch(error) { el('error').textContent=error.message; }
    finally { busy=false; controls(); }
  }
  button.onclick=async()=>{
    dialog.showModal();
    try { await reload(); } catch(error) { el('error').textContent=error.message; }
    clearInterval(timer); timer=setInterval(()=>{ if (!busy) reload().catch(error=>{el('error').textContent=error.message;}); },5000);
  };
  el('close').onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>clearInterval(timer));
  el('group').onchange=controls;
  el('project').onclick=()=>act(()=>api('/project',{project_root:el('root').value}));
  el('groups').onclick=()=>act(async()=>{
    const data=await api('/groups');
    const selected=el('group').value || snapshot?.run?.conversation_id;
    el('group').replaceChildren(new Option('选择 QQ 群',''), ...data.groups.map(g=>new Option(g.name,g.conversation_id,false,g.conversation_id===selected)));
  });
  el('documents').onclick=()=>act(async()=>{
    const data=await api('/documents',{conversation_id:el('group').value});
    if(data.errors.length) throw new Error(data.errors.join('\n')+'\n成功文档已更新，未读取的原群知识保留，请检查后重试。');
  });
  el('start').onclick=()=>act(()=>api('/start',{conversation_id:el('group').value}));
  el('stop').onclick=async()=>{
    try { await api('/stop',{}); await reload(); } catch(error) { el('error').textContent=error.message; }
  };
  el('preview').onclick=()=>act(async()=>{
    const data=await api('/preview',{question:el('question').value,conversation_id:el('group').value});
    el('answer').textContent=data.reply;
  });
})();
