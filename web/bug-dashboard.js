const $ = id => document.getElementById(id);
function element(tag, text, cls) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node; }
function groupName(cid) { return 'QQ群 ' + cid.split(':').pop(); }
function bars(id, rows, label) {
  const root = $(id); root.replaceChildren();
  if (!rows.length) { root.append(element('p', '暂无记录', 'empty')); return; }
  const max = Math.max(1, ...rows.map(r => r.count));
  for (const row of rows) {
    const box = element('div'); const text = element('div', undefined, 'bar-label');
    text.append(element('span', label(row)), element('b', row.count));
    const track = element('div', undefined, 'track'); const fill = element('div', undefined, 'fill');
    fill.style.width = (row.count / max * 100) + '%'; track.append(fill); box.append(text, track); root.append(box);
  }
}
function render(data) {
  for (const id of ['total','open','resolved']) $(id).textContent = data[id];
  $('groups-count').textContent = data.groups.length;
  bars('statuses', data.statuses, r => r.status); bars('groups', data.groups, r => groupName(r.cid)); bars('days', data.days, r => r.day);
  const root = $('bugs'); const expanded = new Set([...root.querySelectorAll('details[open]')].map(n => n.dataset.id)); root.replaceChildren();
  $('shown').textContent = data.shown < data.total ? `最近 ${data.shown} / 共 ${data.total} 条` : `共 ${data.total} 条`;
  if (!data.bugs.length) root.append(element('p', '还没有群反馈记录。', 'empty'));
  for (const bug of data.bugs) {
    const item = element('details'); item.dataset.id = String(bug.id); item.open = expanded.has(String(bug.id));
    const summary = element('summary'); const title = element('div', bug.title, 'issue-title');
    title.append(element('div', `${groupName(bug.cid)} · ${bug.day} · ${bug.sender || '群友'}`, 'issue-meta'));
    summary.append(element('span', '#' + bug.id, 'issue-id'), title, element('span', bug.status, 'badge' + (bug.status === '已解决' ? ' resolved' : '')));
    const body = element('div', undefined, 'detail-body');
    body.append(element('h3', '汇总详情'), element('p', bug.details || '暂无详情'));
    if (bug.question) body.append(element('h3', '群友原始反馈'), element('p', bug.question));
    item.append(summary, body);
    const row = element('div', undefined, 'issue-row');
    const remove = element('button', '删除', 'delete-button'); remove.type = 'button';
    remove.setAttribute('aria-label', `删除反馈 #${bug.id}`);
    remove.addEventListener('click', () => {
      pendingDelete = bug.id; $('delete-title').textContent = `#${bug.id} · ${bug.title}`;
      $('delete-error').hidden = true; $('delete-dialog').showModal();
    });
    row.append(item, remove); root.append(row);
  }
  $('sync').textContent = '已更新 · ' + new Date(data.updated_at).toLocaleTimeString('zh-CN', {hour12:false});
}
let loading = false;
let pendingDelete = null;
let deleting = false;
$('delete-cancel').addEventListener('click', () => $('delete-dialog').close());
$('delete-dialog').addEventListener('cancel', event => { if (deleting) event.preventDefault(); });
$('delete-confirm').addEventListener('click', async () => {
  if (deleting || pendingDelete === null) return;
  deleting = true; $('delete-confirm').disabled = true; $('delete-cancel').disabled = true;
  $('delete-confirm').textContent = '正在删除…';
  try {
    const response = await fetch(`/api/bugs/${pendingDelete}`, {method:'DELETE', headers:{'X-Dashboard-Action':'delete'}});
    if (!response.ok) {
      const result = await response.json(); throw new Error(result.detail || '删除失败，请稍后重试。');
    }
    $('delete-dialog').close(); pendingDelete = null;
    await refresh();
  } catch (error) {
    $('delete-error').textContent = error.message; $('delete-error').hidden = false;
  } finally {
    deleting = false; $('delete-confirm').disabled = false; $('delete-cancel').disabled = false;
    $('delete-confirm').textContent = '确认删除';
  }
});
async function refresh() {
  if (loading) return; loading = true; $('refresh').disabled = true;
  try {
    const response = await fetch('/api/bugs', {cache:'no-store'}); if (!response.ok) throw new Error('读取失败');
    render(await response.json()); $('error').hidden = true;
  } catch {
    $('error').hidden = false; $('error').textContent = '数据暂时无法更新，稍后会自动重试。上次读取的内容仍保留。'; $('sync').textContent = '更新失败';
  } finally { loading = false; $('refresh').disabled = false; }
}
$('refresh').addEventListener('click', refresh);
refresh(); setInterval(refresh, 30000);
