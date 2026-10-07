const $=id=>document.getElementById(id);
const names={strength:'力量',agility:'敏捷',wisdom:'智慧',charisma:'魅力',luck:'幸运'};
const difficulty={weak:'杂鱼',normal:'普通',hard:'困难',extreme:'极难'};
let latest=null,waiting=false,messages=[],storageKey=null,pending=null;
function save(){if(storageKey){try{localStorage.setItem(storageKey,JSON.stringify(messages.slice(-160)));}catch{}}}
function paint(){const root=$('messages');const bottom=root.scrollHeight-root.scrollTop-root.clientHeight<100;root.replaceChildren();for(const entry of messages){const box=document.createElement('article');box.className='message '+entry.role;const name=document.createElement('p');name.className='name';name.textContent=entry.role==='user'?'你':'大肥鱼';const text=document.createElement('div');text.className='text';text.textContent=entry.text;box.append(name,text);root.append(box);}if(bottom)root.scrollTop=root.scrollHeight;}
function add(role,text,key=crypto.randomUUID()){if(messages.some(m=>m.key===key))return;messages.push({role,text,key});save();paint();}
function eventText(event){return `【${event.title}】\n${event.scene}\n\n敌人 Lv.${event.enemy_level} · ${difficulty[event.difficulty]}\n你打算怎么做？`+(event.clues?.length?'\n线索：'+event.clues.join('；'):'');}
function resultText(result){const label={success:'挑战成功',escaped:'逃跑成功',death:'失败，重新转生'}[result.outcome];return result.story+`\n\n${label}｜Lv.${result.before_level} → Lv.${result.level}｜+${result.gain}级\n当前称号：${result.title}`+(result.reward?`\n获得「${result.reward.name}」：${result.reward.nature}`:'');}
function update(data){latest=data;if(storageKey!==data.chat_id){storageKey=data.chat_id;try{const stored=JSON.parse(localStorage.getItem(storageKey)||'[]');messages=Array.isArray(stored)?stored.filter(m=>m&&typeof m.text==='string'&&['user','assistant','error'].includes(m.role)&&typeof m.key==='string'):[];}catch{messages=[];}paint();}
  if(!messages.length)add('assistant','哼哼，勇者，准备好了吗？\n发送“冒险”开始。遇到事件后直接说你的办法；想溜走就发“逃跑：你的方案”。\n也可以问“状态”或“背包”。','welcome');
  for(const attempt of [...data.history].reverse()){
    if(attempt.event.scene)add('assistant',eventText(attempt.event),'event:'+attempt.id);
    if(attempt.action){const key='action:'+attempt.id;if(!messages.some(m=>m.key===key)){const sent=messages.find(m=>m.role==='user'&&m.key.startsWith('sent:')&&(m.text===attempt.action||m.text.replace(/^逃跑[：:\s]*/, '')===attempt.action));if(sent){sent.key=key;save();}else add('user',attempt.action,key);}}
    if(attempt.result)add('assistant',resultText(attempt.result)+'\n今日剩余冒险：'+(attempt===data.history[0]?data.remaining+' / 3':'见“状态”'),'result:'+attempt.id);
  }
  const current=data.current;const processing=current&&['generating','evaluating'].includes(current.phase);
  $('busy').hidden=!(waiting||processing);$('message').disabled=waiting||!!processing;$('send').disabled=waiting||!!processing;
  if(current&&['generation_error','evaluation_error'].includes(current.phase))add('assistant','刚才的请求未完成，状态已保留。发送“重试”继续。','error:'+current.id+':'+current.phase);
  save();paint();
}
async function load(){try{const response=await fetch('/api/state');if(!response.ok)throw new Error('暂时无法读取进度，请刷新页面。');update(await response.json());}catch(error){add('error',error.message);}}
function route(input){if(/^(冒险|开始|开始冒险|继续冒险)$/.test(input))return ['start',{}];if(input==='挑战最终Boss'||input==='挑战最终boss'||input==='挑战最终 Boss')return ['start',{final_boss:true}];if(input==='重试')return ['retry',{}];if(/^(状态|属性|我的状态)$/.test(input))return ['local',`Lv.${latest.state.level} · ${latest.title}\n`+Object.entries(latest.state.attributes).map(([key,value])=>names[key]+' '+value).join(' / ')+`\n第 ${latest.state.reincarnation} 次转生｜今日剩余 ${latest.remaining} / 3 次`];if(/^(背包|物品|我的背包)$/.test(input))return ['local',latest.state.inventory.length?latest.state.inventory.map(item=>'「'+item.name+'」：'+item.nature).join('\n'):'背包空空的。先去冒险吧。'];if(/^(帮助|玩法)$/.test(input))return ['local','发送“冒险”开始，直接描述行动来挑战。\n“逃跑：方案”尝试逃走；“状态”“背包”查看成长与物品。\n每天3次；成功升1～3级，逃跑成功不升级，失败等级和背包清零。\n100级可发送“挑战最终Boss”。'];if(/^逃跑(?:[：:\s]|$)/.test(input))return ['act',{action:input.replace(/^逃跑[：:\s]*/, '').trim()||'我沿来路寻找安全路线逃跑。',flee:true}];if(!latest.current)return ['local','先发送“冒险”获取事件，再告诉我你的办法。'];return ['act',{action:input,flee:false}];}
async function send(){const input=$('message').value.trim();if(!input||waiting||!latest)return;const [command,body]=route(input);const key='sent:'+crypto.randomUUID();add('user',input,key);$('message').value='';$('message').style.height='auto';if(command==='local'){add('assistant',body);return;}
  waiting=true;update(latest);const signature=JSON.stringify({command,...body});if(!pending||pending.signature!==signature)pending={signature,id:crypto.randomUUID()};
  try{const response=await fetch('/api/'+command,{method:'POST',headers:{'Content-Type':'application/json','X-Playtest-Action':'play'},body:JSON.stringify({...body,request_id:pending.id})});const data=await response.json();if(!response.ok){pending=null;throw new Error(typeof data.detail==='string'?data.detail:'消息格式不正确。');}pending=null;waiting=false;update(data);}
  catch(error){add('error',error.message||'请求未完成，请刷新查看已保存的进度。');}
  finally{waiting=false;await load();$('message').focus();}
}
$('chat-form').addEventListener('submit',event=>{event.preventDefault();send();});
$('message').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();send();}});
$('message').addEventListener('input',()=>{$('message').style.height='auto';$('message').style.height=Math.min($('message').scrollHeight,150)+'px';});
load();setInterval(()=>{if(!waiting&&latest?.current&&['generating','evaluating'].includes(latest.current.phase))load();},3000);
