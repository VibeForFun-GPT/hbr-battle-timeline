'use strict';
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const key = text => String(text).normalize('NFKC').replace(/[\s!！·・.。…-]/g,'').toLowerCase();
let doc = null, currentJob = null, jobs = [], aliases = {}, selectedIndex = -1, dirty = false, undo = [];
let runningJob = null, importedProfile = null, cropRect = [0,0,1,1], cropImage = null, cropAnchor = null;
let toastTimer = null, polling = false;
let sourceMode='live',captureActive=false,previewSource='video';
let catalogState=null,catalogTimer=null,catalogPolling=false,namesBusy=false;
let previewSession=0,previewSequence=0,previewPending=null,previewBusy=false,previewTimer=null;
let previewVideo=null,previewSelected=0,previewDisplayed=null,previewMetadata=null;
let startSearch=null,startResume=null,startOpening=false;
let activeGap=null;

async function api(path,body,allowStatusError=false){
  const response = await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  const data = await response.json();
  if(!response.ok || (data.error&&!allowStatusError)) throw Error(data.error || '本地服务暂时无法连接');
  return data;
}
function toast(text,error=false){$('toast').textContent=text;$('toast').classList.toggle('error',error);$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{$('toast').hidden=true;},error?7000:3500);}
function safe(fn){return async event=>{try{await fn(event);}catch(error){toast(error.message,true);}};}
function clock(seconds){const m=Math.floor(Math.max(0,seconds)/60);return String(m).padStart(2,'0')+':'+(Math.max(0,seconds)%60).toFixed(2).padStart(5,'0');}
function fileUrl(relative){return encodeURI('file/'+currentJob+'/'+relative);}
function markDirty(){dirty=true;$('unsaved').hidden=false;}
function remember(){if(doc){undo.push(structuredClone(doc));if(undo.length>35)undo.shift();}}
function people(){return new Map((doc?.team||[]).map(p=>[p.id,p]));}
function currentAliases(){const catalog={...(doc?.settings.aliases||{})};for(const row of doc?.rows||[])for(const a of row.actions){if(a.skill_full&&a.skill!=='—'&&catalog[a.skill_full]===undefined)catalog[a.skill_full]=a.skill;}return catalog;}
function reindex(){doc.rows.forEach((r,i)=>r.index=i+1);}
function skillSequence(sequence){const groups=[];for(const value of sequence){if(groups.length&&groups.at(-1)[0]===value)groups.at(-1)[1]++;else groups.push([value,1]);}return groups.map(([value,count])=>count>1?value+'×'+count:value).join(' + ');}

function renderCatalog(state){
  catalogState=state;
  const count=value=>Array.isArray(value)?value.length:Number(value)||0;
  const characters=count(state.character_count??state.characters),styles=count(state.style_count??state.styles);
  const percent=Math.round(Math.max(0,Math.min(1,Number(state.fraction)||0))*100);
  $('catalog-status').textContent=state.updating?`正在更新角色图库 ${percent}% · 已缓存 ${characters} 名角色、${styles} 张头像。`:state.ready?`已缓存 ${characters} 名角色 · ${styles} 张头像，可离线识别。`: '尚未下载角色图库。下载后可自动识别名字；现在也可手动命名。';
  if(!state.updating&&state.ready&&(state.error||state.message?.includes('未完成')))$('catalog-status').textContent+=' 更新未完成，缓存仍可用。';
  $('catalog-status').title=[state.updated_at?'更新于 '+state.updated_at:'',state.message||'',state.error||''].filter(Boolean).join('\n');
  $('update-catalog').disabled=!!state.updating;$('update-catalog').textContent=state.updating?'正在更新…':state.ready?'更新角色图库':'下载角色图库';
  $('identify-team').disabled=namesBusy||!!state.updating;
}
function scheduleCatalogPoll(){clearTimeout(catalogTimer);catalogTimer=catalogState?.updating?setTimeout(()=>refreshCatalog().catch(error=>{ $('catalog-status').textContent='图库状态暂时无法读取，请点击更新重试。';$('update-catalog').disabled=false;toast(error.message,true); }),1800):null;}
async function refreshCatalog(){if(catalogPolling)return;catalogPolling=true;try{renderCatalog(await api('api/catalog/status',undefined,true));scheduleCatalogPoll();}finally{catalogPolling=false;}}
function teamNameEvidence(person){
  const match=person.name_match||{},score=Number(match.raw_score??match.score);
  if(person.name_source==='hbr_quest_avatar'){
    const style=person.matched_style_id||match.matched_style_id||match.style_id;
    const title=Number.isFinite(score)?`Raw Similarity ${score.toFixed(4)}（相似度，不是成功概率）`:'';
    return `<small class="name-evidence" title="${escapeHtml(title)}">图库识别${style?' · Style '+escapeHtml(style):''}</small>`;
  }
  if(!person.name_is_placeholder)return `<small class="name-evidence">${person.name_source==='profile'?'队伍配置':person.name_source==='roster_ocr'?'配置画面 OCR':'手动命名'}</small>`;
  const candidates=(match.candidates||[]).slice(0,3);
  if(!candidates.length)return '<small class="name-evidence unresolved">名字未识别，可手动填写。</small>';
  return '<details class="name-candidates"><summary>名字待核对 · 查看候选</summary><ul>'+candidates.map(candidate=>{const value=Number(candidate.raw_score??candidate.score),style=candidate.matched_style_id||candidate.style_id;return `<li title="${escapeHtml(style?'Style '+style:'')}">${escapeHtml(candidate.name)}${Number.isFinite(value)?' <span>相似度 '+value.toFixed(2)+'</span>':''}</li>`;}).join('')+'</ul></details>';
}
function syncTeamNames(){
  const team=people(),named=new Set(doc.team.filter(p=>!p.name_is_placeholder).map(p=>Number(p.id)));
  for(const row of doc.rows){
    for(const action of row.actions){const person=team.get(action.character_id);if(person){action.character=person.alias;action.character_full=person.name;}}
    const oldNotes=row.notes||[];
    row.notes=oldNotes.filter(note=>{const match=/^角色 (\d+) 的(?:名称需要核对|名字未识别，请在队伍页核对)$/.exec(note);return !match||!named.has(Number(match[1]));});
    if(row.notes.length<oldNotes.length&&!row.notes.length&&!row.manual_edit)row.needs_review=false;
  }
}
function editedTeam(){
  return doc.team.map(person=>{
    const alias=document.querySelector(`[data-team-alias="${person.id}"]`).value.trim(),name=document.querySelector(`[data-team-name="${person.id}"]`).value.trim();
    if(alias===(person.name_is_placeholder?'':person.alias)&&name===(person.name_is_placeholder?'':person.name))return person;
    const placeholder=!alias&&!name;
    return {...person,alias:alias||name||'角色 '+person.id,name:name||alias||'角色 '+person.id,name_is_placeholder:placeholder,name_source:placeholder?'placeholder':'manual',name_confidence:placeholder?0:1};
  });
}

function compactRows(){
  const rows=[];
  doc.rows.forEach((row,index)=>{
    if(row.kind==='extra' && row.turn!=null && rows.length && row.turn===rows.at(-1).turn && row.od_session===rows.at(-1).od_session){
      const prior=rows.at(-1), actions=new Map(prior.actions.map(a=>[a.character_id,a]));
      row.actions.forEach(a=>{if(a.skill==='—')return;if(actions.has(a.character_id)){const before=actions.get(a.character_id);before.skill_sequence.push(a.skill);before.skill=skillSequence(before.skill_sequence);before.skill_full+=' + '+a.skill_full;}else{prior.extra_actions.push(a);}});
      prior.needs_review ||= row.needs_review&&!row.reviewed;
      prior.reviewed = !!prior.reviewed && !!row.reviewed;
      prior.od_after = row.od_after;
      prior.sources.push(index);
    }else rows.push({...structuredClone(row),actions:row.actions.map(a=>({...structuredClone(a),skill_sequence:[a.skill]})),sourceIndex:index,sources:[index],extra_actions:[]});
  });
  return rows;
}

function currentCoverage(){
  if(!doc)return {gaps:[],missing_turn_count:0};
  if(!dirty&&doc.coverage)return doc.coverage;
  const gaps=[];
  for(let index=1;index<doc.rows.length;index++){
    const after=doc.rows[index-1],before=doc.rows[index];
    if(!Number.isInteger(after.turn)||!Number.isInteger(before.turn)||after.turn<1||before.turn>999||before.turn<=after.turn+1)continue;
    const start=Number.isFinite(after.confirm_time)?after.confirm_time:Number.isFinite(after.time)?after.time:null;
    const end=Number.isFinite(before.phase_start)?before.phase_start:Number.isFinite(before.time)?before.time:null;
    gaps.push({id:'gap-'+before.id,after_turn:after.turn,before_turn:before.turn,missing_turns:Array.from({length:before.turn-after.turn-1},(_,i)=>after.turn+i+1),after_row_id:after.id,before_row_id:before.id,start_time:start,end_time:end});
  }
  return {gaps,missing_turn_count:gaps.reduce((total,gap)=>total+gap.missing_turns.length,0)};
}
function gapLabel(gap){const first=gap.missing_turns[0],last=gap.missing_turns.at(-1);return (first===last?first:first+'–'+last)+' 回合未提取';}
function gapTime(gap){return Number.isFinite(gap.start_time)&&Number.isFinite(gap.end_time)?clock(gap.start_time)+'–'+clock(gap.end_time):'时间未记录';}
function gapButton(gap){return `<button class="gap-link" data-gap-id="${escapeHtml(gap.id)}">${escapeHtml(gapLabel(gap))} · ${gapTime(gap)} · 回看视频 ↗</button>`;}
function renderCoverage(){
  const coverage=currentCoverage();$('coverage-banner').hidden=!coverage.gaps.length;
  $('coverage-title').textContent=coverage.gaps.length+' 段回合缺少行动记录，共 '+coverage.missing_turn_count+' 个回合';
  $('coverage-gaps').innerHTML=coverage.gaps.map(gapButton).join('');
  return coverage;
}
function openGap(id){
  const gap=currentCoverage().gaps.find(item=>item.id===id);if(!gap)return;
  activeGap=gap;
  $('gap-title').textContent=gapLabel(gap);
  $('gap-range').textContent=gapTime(gap)+'。这段没有提取到可用的选招与行动确认；Skill 保留为未识别。可回看自动战斗、剪辑或画面遮挡的情况。';
  $('gap-start').disabled=!Number.isFinite(gap.start_time);$('gap-end').disabled=!Number.isFinite(gap.end_time);
  const video=$('gap-video');video.pause();
  video.onloadedmetadata=()=>{if(activeGap?.id===gap.id&&$('gap-dialog').open&&Number.isFinite(gap.start_time))video.currentTime=Math.min(gap.start_time,Math.max(0,video.duration-.05));};
  video.src=encodeURI('video/'+currentJob);
  $('gap-dialog').showModal();
}

function renderTable(){
  if(!doc)return;
  const coverage=renderCoverage(),gaps=new Map(coverage.gaps.map(gap=>[gap.before_row_id,gap]));
  let rows=$('compact-view').checked?compactRows():doc.rows.map((r,i)=>({...r,sourceIndex:i,sources:[i],extra_actions:[]}));
  if($('review-only').checked)rows=rows.filter(r=>r.needs_review&&!r.reviewed);
  $('od-header').textContent=$('od-view').value==='after'?'行动后 OD':'行动前 OD';
  $('axis-body').innerHTML=rows.map(row=>{
    const special=row.kind!=='normal';
    const actions=row.actions.map((a,i)=>`<td title="${escapeHtml(a.character_full)}">${escapeHtml(a.character)}</td><td class="skill" title="${escapeHtml(a.skill_full)}${row.extra_actions.length?'；追加换人：'+escapeHtml(row.extra_actions.map(a=>a.character+' '+a.skill).join('、')):''}">${escapeHtml(a.skill)}</td>`).join('');
    const od=row[$('od-view').value==='after'?'od_after':'od_before'];
    const review=row.reviewed?'<span class="review-ok">✓</span>':row.needs_review?'<span class="review-pill">待核对</span>':'<span style="color:#b0bccd">·</span>';
    const gap=gaps.get(row.id),gapRow=gap?`<tr class="coverage-gap-row"><td colspan="10">${gapButton(gap)}<span>角色与 Skill 未识别</span></td></tr>`:'';
    return gapRow+`<tr data-index="${row.sourceIndex}" class="${row.sources.includes(selectedIndex)?'selected':''}" title="点击核对${row.sources.length>1?'；本行合并了 '+row.sources.length+' 次确认':''}"><td class="round ${special?'special':''}">${escapeHtml(row.label)}</td>${actions}<td class="od">${od===null||od===undefined?'—':escapeHtml(Number(od).toString())}</td><td class="time">${clock(row.confirm_time)}</td><td class="review">${review}</td></tr>`;
  }).join('');
  $('count-normal').textContent=new Set(doc.rows.filter(r=>r.kind==='normal'&&Number.isInteger(r.turn)).map(r=>r.turn)).size;
  $('count-confirm').textContent=doc.rows.length;
  $('count-od').textContent=new Set(doc.rows.filter(r=>r.od_session!=null).map(r=>r.od_session)).size;
  $('count-review').textContent=doc.rows.filter(r=>r.needs_review&&!r.reviewed).length;
}

function renderJobs(){
  $('job-list').innerHTML=jobs.map(j=>`<button class="job-item ${j.id===currentJob?'active':''}" data-job="${escapeHtml(j.id)}"><strong>${escapeHtml(j.title)}</strong><span>${j.id==='sample'?'附件视频示例':escapeHtml(j.created_at?.slice(0,16).replace('T',' '))} · ${j.status==='done'?'已完成':j.status==='running'?'识别中':j.status==='capturing'?'捕获中':j.status==='error'?'需重试':'已停止'}</span></button>`).join('');
}

function renderTeam(){
  if(!doc)return;
  const placeholders=doc.team.filter(p=>p.name_is_placeholder).length;
  $('team-placeholder-note').textContent=placeholders?`${placeholders} 个 Placeholder 待核对，可自动补全或手动命名；也可直接导出。`:'本队伍名字已填写，手动名字与队伍配置优先保留。';
  $('team-grid').innerHTML=doc.team.map((p,i)=>`<div class="team-card"><img src="${fileUrl(p.avatar)}" alt="角色 ${p.id} 的 Avatar"><span>Avatar ${p.id} · 开局${i<3?'前排':'后排'}</span>${teamNameEvidence(p)}<label>图表名称<input data-team-alias="${p.id}" aria-label="角色 ${p.id} 图表名称" value="${escapeHtml(p.name_is_placeholder?'':p.alias)}" placeholder="角色 ${p.id}，输入任意名字"></label><label>完整名称（可选）<input data-team-name="${p.id}" aria-label="角色 ${p.id} 完整名称" value="${escapeHtml(p.name_is_placeholder?'':p.name)}" placeholder="留空则沿用图表名称"></label></div>`).join('');
  const catalog=currentAliases();
  $('alias-list').innerHTML=Object.entries(catalog).map(([full,short])=>aliasItem(full,short)).join('');
  updateSkillOptions(catalog);
}
function aliasItem(full='',short=''){return `<div class="alias-item"><input class="alias-full" value="${escapeHtml(full)}" placeholder="完整 Skill 名称"><input class="alias-short" value="${escapeHtml(short)}" placeholder="简写"><button class="text-button remove-alias" aria-label="移除此简写">×</button></div>`;}
function updateSkillOptions(catalog){$('skill-options').innerHTML=Object.keys(catalog).map(k=>`<option value="${escapeHtml(k)}"></option>`).join('');}

function renderReference(){
  if(doc.reference){
    $('reference-image-box').innerHTML=`<img src="${fileUrl(doc.reference.image)}" alt="用户提供的行动轴附件">`;
    $('reference-notes').innerHTML='<p>已按视频实际画面识别。与附件的主要差异：</p><ul>'+doc.reference.notes.map(t=>'<li>'+escapeHtml(t)+'</li>').join('')+'</ul><p>左侧附件保留为对照，完整的视频确认记录见「行动轴」。</p>';
  }else{
    $('reference-image-box').textContent='当前视频没有附带参考图。';
    $('reference-notes').textContent='可逐行回看原视频。参考附件不参与自动识别。';
  }
}

function showTab(name){
  document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));
  for(const value of ['timeline','team','reference'])$('tab-'+value).hidden=value!==name;
}

async function loadJob(id){
  if(dirty&&doc)await saveDocument(false);
  const result=await api('api/job/'+encodeURIComponent(id));
  if(!result.document){if(result.job.status==='running'){runningJob=id;updateProgress(result.job);}toast(result.job.message);return;}
  currentJob=id;
  if(result.document){
    if($('gap-dialog').open)$('gap-dialog').close();
    doc=result.document;dirty=false;undo=[];selectedIndex=-1;
    $('unsaved').hidden=true;$('empty-state').hidden=true;$('document-view').hidden=false;$('inspector').hidden=true;
    $('document-title').value=doc.title;
    $('document-subtitle').textContent=doc.video.width+' × '+doc.video.height+' · '+clock(doc.video.duration)+' · '+doc.rows.length+' 次行动确认';
    $('capture-summary').hidden=!doc.capture;$('capture-summary').textContent=doc.capture?'实时捕获 · '+clock(doc.capture.duration)+(doc.capture.paused_seconds>.1?'；曾暂停取新画面 '+doc.capture.paused_seconds+' 秒，请核对期间是否有行动遗漏。':'；画面已保存，可逐行回看。'):'';
    if($('calibration-dialog').open)$('calibration-dialog').close();
    $('video-path').value=doc.video.path;$('start-time').value=clock(doc.settings.start);$('end-time').value=doc.settings.end<doc.video.duration-.1?clock(doc.settings.end):'';
    cropRect=doc.settings.layout?.crop||[0,0,1,1];
    $('source-video').src=encodeURI('video/'+id);
    renderTable();renderTeam();renderReference();showTab('timeline');
  }
  if(result.job.status==='running'){runningJob=id;updateProgress(result.job);}
  renderJobs();
}

function openInspector(index){
  if(!doc||index<0||index>=doc.rows.length)return;
  selectedIndex=index;const row=doc.rows[index];
  $('inspector').hidden=false;$('inspector-title').textContent='第 '+(index+1)+' 行 · '+row.label;
  $('frame-image').src=row.screenshot?fileUrl(row.screenshot):'';
  $('frame-time').textContent='Frame '+clock(row.time);
  $('edit-label').value=row.label;$('edit-turn').value=row.turn??'';$('edit-kind').value=row.kind;
  $('edit-time').value=row.confirm_time;$('edit-od-before').value=row.od_before??'';$('edit-od-after').value=row.od_after??'';
  $('edit-note').value=row.note||'';$('edit-reviewed').checked=!!row.reviewed;
  $('apply-skill-everywhere').checked=false;
  $('capture-frame').disabled=$('source-video').hidden;
  $('action-editors').innerHTML=row.actions.map((a,slot)=>`<div class="action-editor"><div class="action-editor-top"><span>前排位置 ${slot+1}</span><span>Confidence ${Math.round((a.confidence??0)*100)}%</span></div><div class="two-inputs"><label>角色<select data-action-role="${slot}">${doc.team.map(p=>`<option value="${p.id}" ${p.id===a.character_id?'selected':''}>${escapeHtml(p.alias)}</option>`).join('')}</select></label><label>Skill 简写<input data-action-short="${slot}" value="${escapeHtml(a.skill)}"></label></div><label>完整 Skill 名称<input data-action-full="${slot}" value="${escapeHtml(a.skill_full)}" list="skill-options"></label><small>原始 OCR：${escapeHtml(a.skill_raw||'（未选择 Skill）')}</small></div>`).join('');
  const notes=[...(row.notes||[])];
  if(row.od_trigger)notes.push('OD 发动 '+clock(row.od_trigger.time)+'；消耗档位 '+(row.od_level??'待核对')+'；'+row.bonus_text);
  if(row.od_action_count)notes.push('本次 OD 含 '+row.od_action_count+' 次行动确认（包括追加）。');
  $('review-notes').innerHTML=notes.map(escapeHtml).join('<br>');
  $('prev-row').disabled=index===0;$('next-row').disabled=index===doc.rows.length-1;
  if(!$('source-video').hidden)seekRow();
  renderTable();
}

function seekRow(){
  if(selectedIndex>=0)$('source-video').currentTime=Math.max(0,doc.rows[selectedIndex].confirm_time-2);
}
function evidenceMode(mode){const video=mode==='video',changed=$('source-video').hidden===video;$('source-video').hidden=!video;$('frame-image').hidden=video;$('show-video').classList.toggle('active',video);$('show-frame').classList.toggle('active',!video);$('capture-frame').disabled=!video;if(video&&changed)seekRow();if(!video)$('source-video').pause();}

function applyRow(){
  if(selectedIndex<0)return;
  const values=[0,1,2].map(slot=>({id:Number(document.querySelector(`[data-action-role="${slot}"]`).value),short:document.querySelector(`[data-action-short="${slot}"]`).value,full:document.querySelector(`[data-action-full="${slot}"]`).value}));
  if(new Set(values.map(v=>v.id)).size!==3)throw Error('同一角色不能同时占据两个前排位置。');
  const time=Number($('edit-time').value);if(!Number.isFinite(time)||time<0||time>doc.video.duration)throw Error('确认时间超出了视频范围。');
  remember();const row=doc.rows[selectedIndex],team=people(),beforeActions=structuredClone(row.actions);
  row.label=$('edit-label').value;row.turn=$('edit-turn').value?Number($('edit-turn').value):null;row.kind=$('edit-kind').value;
  row.confirm_time=time;row.od_before=$('edit-od-before').value===''?null:Number($('edit-od-before').value);row.od_after=$('edit-od-after').value===''?null:Number($('edit-od-after').value);
  row.note=$('edit-note').value;row.reviewed=$('edit-reviewed').checked;row.needs_review=!row.reviewed;
  row.actions=values.map((value,i)=>({...row.actions[i],character_id:value.id,character:team.get(value.id).alias,character_full:team.get(value.id).name,skill:value.short,skill_full:value.full,slot:i+1}));
  let synchronized=0;
  if($('apply-skill-everywhere').checked){
    values.forEach((value,slot)=>{const previous=beforeActions[slot];if(previous.character_id!==value.id||(previous.skill===value.short&&previous.skill_full===value.full))return;
      for(const other of doc.rows){if(other===row)continue;for(const a of other.actions){if(a.character_id===value.id&&a.skill_full===previous.skill_full&&(a.skill_raw||'')===(previous.skill_raw||'')){a.skill=value.short;a.skill_full=value.full;a.manual_correction=true;other.manual_edit=true;other.reviewed=false;other.needs_review=true;synchronized++;}}}
    });
  }
  row.positions=[...values.map(v=>v.id),...(row.positions||[1,2,3,4,5,6]).filter(id=>!values.some(v=>v.id===id))];
  row.manual_edit=true;markDirty();renderTable();renderTeam();openInspector(selectedIndex);toast('已应用修改'+(synchronized?'，另同步 '+synchronized+' 处 Skill':'')+'；导出时会保存。');
}

async function saveDocument(message=true){
  if(!doc)return;
  doc.title=$('document-title').value;
  const result=await api('api/save/'+encodeURIComponent(currentJob),{document:doc});
  doc=result.document;dirty=false;$('unsaved').hidden=true;
  if(message)toast('行动轴已保存。');
}

function downloadJSON(data,name){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);}

function profile(){
  const mode=$('profile-mode').value;
  if(importedProfile&&mode==='imported')return {...importedProfile,name_mode:importedProfile.name_mode||(importedProfile.team?'profile':'auto'),layout:{...importedProfile.layout,crop:cropRect}};
  const p={layout:{...(doc?.settings.layout||{}),crop:cropRect},aliases:{},name_mode:mode==='ocr'?'ocr':mode==='placeholder'?'placeholder':'auto'};
  if(mode==='current'&&doc){p.team=doc.team.map(({name,alias,name_is_placeholder})=>({name,alias,name_is_placeholder}));p.aliases=currentAliases();p.name_mode='profile';}
  return p;
}

async function startAnalysis(){
  if(sourceMode==='live'){
    const hwnd=Number($('capture-window').value);if(!hwnd)throw Error('请先选择游戏窗口。');
    if(dirty)await saveDocument(false);
    $('analyze-button').disabled=true;
    try{const job=await api('api/live/start',{hwnd,fps:8,sample_rate:Number($('sample-rate').value),profile:profile()});runningJob=job.id;captureActive=true;updateProgress(job);jobs=await api('api/jobs');renderJobs();toast('捕获已就绪，请切回游戏窗口。结束时按 Ctrl + Alt + E。');}
    catch(error){$('analyze-button').disabled=false;throw error;}
    return;
  }
  const path=$('video-path').value.trim().replace(/^"|"$/g,'');if(!path)throw Error('请先选择战斗视频。');
  if(dirty)await saveDocument(false);
  $('analyze-button').disabled=true;
  try{const job=await api('api/analyze',{path,start:$('start-time').value,end:$('end-time').value,sample_rate:Number($('sample-rate').value),profile:profile()});runningJob=job.id;updateProgress(job);jobs=await api('api/jobs');renderJobs();toast('已开始分析，可以继续查看已有行动轴。');}
  catch(error){$('analyze-button').disabled=false;throw error;}
}

function updateProgress(job){captureActive=job.status==='capturing';$('progress-box').hidden=false;$('progress-fill').style.width=captureActive?'100%':Math.round(job.fraction*100)+'%';$('progress-fill').classList.toggle('capturing',captureActive);const duration=job.capture?.duration||0;$('progress-text').textContent=captureActive?clock(duration)+' · '+job.message:Math.round(job.fraction*100)+'% · '+job.message;$('cancel-button').hidden=job.status!=='running';$('end-live').hidden=!captureActive;$('end-live').disabled=job.capture?.state==='stopping';$('source-live').disabled=!!runningJob;$('source-recording').disabled=!!runningJob;$('capture-window').disabled=!!runningJob;}
async function poll(){
  if(polling||!runningJob)return;polling=true;
  try{
    const data=await api('api/job/'+runningJob);updateProgress(data.job);
    if(!['running','capturing'].includes(data.job.status)){
      const finished=runningJob;runningJob=null;$('analyze-button').disabled=false;
      jobs=await api('api/jobs');renderJobs();$('source-live').disabled=false;$('source-recording').disabled=false;$('capture-window').disabled=false;
      if(data.job.status==='done'){await loadJob(finished);toast('识别完成。请检查黄色标记，再导出行动轴。');}
      else toast(data.job.message,data.job.status==='error');
    }
  }catch(error){toast(error.message,true);}finally{polling=false;}
}

function drawCrop(){
  const canvas=$('crop-canvas'),ctx=canvas.getContext('2d'),w=canvas.width,h=canvas.height;
  ctx.fillStyle='#182638';ctx.fillRect(0,0,w,h);if(!cropImage)return;
  ctx.drawImage(cropImage,0,0,w,h);
  const [x1,y1,x2,y2]=cropRect;
  ctx.fillStyle='rgba(15,27,43,.55)';ctx.fillRect(0,0,w,y1*h);ctx.fillRect(0,y2*h,w,h-y2*h);ctx.fillRect(0,y1*h,x1*w,(y2-y1)*h);ctx.fillRect(x2*w,y1*h,w-x2*w,(y2-y1)*h);
  ctx.strokeStyle='#85d8be';ctx.lineWidth=3;ctx.strokeRect(x1*w,y1*h,(x2-x1)*w,(y2-y1)*h);
  $('crop-caption').textContent='游戏画面范围：'+cropRect.map(x=>Math.round(x*100)+'%').join(' / ');
}
function previewPath(){return $('video-path').value.trim().replace(/^"|"$/g,'');}
function previewClock(seconds){
  const milliseconds=Math.round(Math.max(0,seconds)*1000),whole=Math.floor(milliseconds/1000);
  return String(Math.floor(whole/60)).padStart(2,'0')+':'+String(whole%60).padStart(2,'0')+'.'+String(milliseconds%1000).padStart(3,'0');
}
function timestampSeconds(value){
  if(!String(value).trim())return 0;
  const parts=String(value).trim().replaceAll('：',':').split(':');
  if(parts.length>3||parts.some(part=>!/^\d+(?:\.\d+)?$/.test(part.trim())))throw Error('时间请使用 MM:SS、HH:MM:SS 或秒数。');
  const time=parts.reduce((total,part)=>total*60+Number(part),0);
  if(!Number.isFinite(time))throw Error('请输入有效的视频时间。');
  return time;
}
function resetPreview(){
  cancelStartSearch();
  previewSession++;previewSequence++;clearTimeout(previewTimer);previewPending=null;
  previewVideo=null;previewDisplayed=null;previewMetadata=null;cropImage=null;cropAnchor=null;
}
function currentPreview(job){
  return $('calibration-dialog').open&&job.session===previewSession&&job.sequence===previewSequence&&job.mode===previewSource&&(job.mode==='live'||job.path===previewPath());
}
function clampPreview(time){
  if(!previewVideo)return 0;
  return Math.max(0,Math.min(previewVideo.max_time,Math.round(time*previewVideo.fps)/previewVideo.fps));
}
function renderPreviewControls(){
  $('apply-crop').disabled=previewDisplayed===null||!cropImage;
  if(previewSource!=='video')return;
  const available=!!previewVideo;
  $('preview-time').disabled=!available;
  if(available){
    $('preview-time').max=String(previewVideo.max_time);$('preview-time').step=String(1/previewVideo.fps);$('preview-time').value=String(previewSelected);
    $('preview-time').setAttribute('aria-valuetext',previewClock(previewSelected)+'，总时长 '+previewClock(previewVideo.duration));
  }
  $('preview-time-display').textContent=previewClock(previewSelected)+' / '+(available?previewClock(previewVideo.duration):'--:--');
  $('preview-prev-frame').disabled=!available||previewSelected<=0;
  $('preview-next-frame').disabled=!available||previewSelected>=previewVideo.max_time;
  $('preview-back-second').disabled=!available||previewSelected<=0;
  $('preview-forward-second').disabled=!available||previewSelected>=previewVideo.max_time;
}
function queuePreview(delay=0){
  const job={session:previewSession,sequence:++previewSequence,mode:previewSource,path:previewPath(),time:previewSelected,hwnd:Number($('capture-window').value)};
  previewDisplayed=null;previewPending=job;clearTimeout(previewTimer);
  $('preview-status').textContent=job.mode==='video'?'正在加载 '+previewClock(job.time)+' 的画面…':'正在截取游戏画面…';
  $('load-preview').disabled=job.mode==='live';renderPreviewControls();
  if(delay)previewTimer=setTimeout(drainPreview,delay);else void drainPreview();
}
async function drainPreview(){
  if(previewBusy||!previewPending)return;
  const job=previewPending;previewPending=null;previewBusy=true;
  try{
    if(job.mode==='live'&&!captureActive&&currentPreview(job))$('preview-instructions').textContent='请在 3 秒内切回游戏窗口。预览完成后回到本页框选完整游戏画面。';
    const data=job.mode==='live'?await api('api/live/preview',{hwnd:job.hwnd}):await api('api/preview',{path:job.path,time:job.time});
    if(!currentPreview(job))return;
    const image=new Image();
    await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(Error('预览画面加载失败，请重试。'));image.src=data.image;});
    if(!currentPreview(job))return;
    cropImage=image;previewDisplayed=job.mode==='video'?Number(data.time):0;
    if(job.mode==='video')previewSelected=previewDisplayed;
    drawCrop();renderPreviewControls();
    $('preview-instructions').textContent='拖动框选完整游戏画面，包含 TURN、OD 和底部 6 人头像。';
    $('preview-status').textContent=job.mode==='video'?'已显示 '+previewClock(previewDisplayed)+'；点击下方按钮，将此画面设为战斗开始并保存校准。':'游戏画面已载入，可拖动框选游戏区域。';
  }catch(error){if(currentPreview(job)){$('preview-status').textContent='预览失败：'+error.message;toast(error.message,true);}}
  finally{
    previewBusy=false;
    if(currentPreview(job))$('load-preview').disabled=false;
    if(previewPending){clearTimeout(previewTimer);previewTimer=setTimeout(drainPreview,80);}
  }
}
async function preview(){
  const session=previewSession;
  if(previewSource==='video'){
    const path=previewPath();if(!path)throw Error('请先选择视频。');
    if(!previewVideo||previewVideo.path!==path){
      $('preview-status').textContent='正在读取视频时长…';
      if(!previewMetadata||previewMetadata.session!==session||previewMetadata.path!==path)previewMetadata={session,path,promise:api('api/video-info',{path})};
      const request=previewMetadata;
      try{
        const info=await request.promise;
        if(session!==previewSession||!$('calibration-dialog').open||path!==previewPath())return;
        if(!(info.duration>0&&info.fps>0&&info.max_time>=0))throw Error('无法读取视频时长，请重新选择视频。');
        if(!previewVideo||previewVideo.path!==path){previewVideo={path,duration:info.duration,fps:info.fps,max_time:info.max_time};previewSelected=clampPreview(timestampSeconds($('start-time').value));renderPreviewControls();}
      }catch(error){if(session===previewSession){$('preview-status').textContent='视频信息读取失败，可点击重新载入预览重试。';throw error;}return;}
      finally{if(previewMetadata===request)previewMetadata=null;}
    }
  }else if(!Number($('capture-window').value)&&!captureActive)throw Error('请先选择游戏窗口。');
  if(session===previewSession)queuePreview();
}
function seekPreview(time,delay=0){if(previewVideo){if(startSearch)cancelStartSearch();previewSelected=clampPreview(time);queuePreview(delay);}}
function renderStartSearch(){
  const continuation=startResume&&startResume.path===previewPath()&&JSON.stringify(startResume.crop)===JSON.stringify(cropRect);
  $('auto-find-start').textContent=startSearch?'取消寻找起点':continuation?'继续寻找后续画面':'自动寻找战斗起点';
  $('preview-auto-start').textContent=startSearch?'取消寻找起点':continuation?'继续寻找后续画面':'自动寻找可用起点';
}
function cancelStartSearch(){
  const search=startSearch;startSearch=null;
  if(search){clearTimeout(search.timer);if(search.id)void api('api/find-start/'+search.id+'/cancel',{}).catch(()=>{});}
  renderStartSearch();
}
function currentStartSearch(search){
  return startSearch===search&&search.session===previewSession&&$('calibration-dialog').open&&previewSource==='video'&&search.path===previewPath()&&JSON.stringify(search.crop)===JSON.stringify(cropRect);
}
async function pollStartSearch(search){
  try{
    const state=await api('api/find-start/'+search.id);
    if(!currentStartSearch(search))return;
    $('start-search-status').textContent=state.message;$('preview-status').textContent=state.message;
    if(state.status==='running'){search.timer=setTimeout(()=>pollStartSearch(search),800);return;}
    startSearch=null;
    const result=state.result;
    if(state.status==='done'&&result?.found){
      startResume=null;renderStartSearch();
      const end=$('end-time').value.trim();let validEnd=false;
      try{validEnd=!end||timestampSeconds(end)>result.time;}catch{}
      if(validEnd)$('start-time').value=previewClock(result.time);
      $('start-search-status').textContent=validEnd?'已自动设为 '+previewClock(result.time)+'；可在预览中继续微调。':'找到 '+previewClock(result.time)+'，请先将结束时间调整到起点之后。';
      seekPreview(result.time);
      toast(validEnd?'已找到可用起点 '+previewClock(result.time)+'，请核对预览。':$('start-search-status').textContent,!validEnd);
    }else{
      startResume=Number.isFinite(result?.resume_from)?{path:search.path,crop:search.crop,time:result.resume_from}:null;
      renderStartSearch();$('preview-status').textContent=result?.message||state.message||'未找到可用起点，请调整游戏画面范围后重试。';
      toast($('preview-status').textContent,state.status==='error');
    }
  }catch(error){if(currentStartSearch(search)){startSearch=null;renderStartSearch();$('preview-status').textContent='自动寻找失败：'+error.message;toast(error.message,true);}}
}
async function beginStartSearch(){
  if(startSearch){cancelStartSearch();$('preview-status').textContent='已取消自动寻找，可拖动进度条手动选择。';return;}
  if(startOpening)return;
  startOpening=true;
  try{
    const opening=!$('calibration-dialog').open||previewSource!=='video'?openCalibration('video'):!previewVideo?preview():Promise.resolve();
    const session=previewSession,path=previewPath();
    await opening;
    if(session!==previewSession||!$('calibration-dialog').open||previewSource!=='video'||path!==previewPath()||startSearch)return;
  }finally{startOpening=false;}
  const continuation=startResume&&startResume.path===previewPath()&&JSON.stringify(startResume.crop)===JSON.stringify(cropRect);
  const search={session:previewSession,path:previewPath(),crop:[...cropRect],id:null,timer:null};
  startSearch=search;previewSequence++;previewPending=null;clearTimeout(previewTimer);previewDisplayed=null;
  renderPreviewControls();renderStartSearch();
  $('start-search-status').textContent='正在自动寻找可用选招画面…';$('preview-status').textContent='正在自动寻找可用选招画面，可随时取消或拖动进度条。';
  try{
    const state=await api('api/find-start',{path:search.path,search_from:continuation?startResume.time:0,profile:{layout:{crop:search.crop}}});
    search.id=state.id;
    if(!currentStartSearch(search)){void api('api/find-start/'+search.id+'/cancel',{}).catch(()=>{});return;}
    await pollStartSearch(search);
  }catch(error){if(currentStartSearch(search)){startSearch=null;renderStartSearch();$('preview-status').textContent='自动寻找失败：'+error.message;throw error;}}
}

function setSource(mode){sourceMode=mode;$('live-source').hidden=mode!=='live';$('recording-source').hidden=mode!=='video';$('live-hotkey').hidden=mode!=='live';$('source-live').classList.toggle('active',mode==='live');$('source-recording').classList.toggle('active',mode==='video');$('source-live').setAttribute('aria-pressed',String(mode==='live'));$('source-recording').setAttribute('aria-pressed',String(mode==='video'));$('analyze-button').innerHTML=(mode==='live'?'开始捕获':'生成行动轴')+' <span>→</span>';}
async function refreshWindows(){const previous=$('capture-window').value;const data=await api('api/live/windows');$('capture-window').innerHTML='<option value="">请选择游戏窗口</option>'+data.windows.map(w=>`<option value="${w.hwnd}">${escapeHtml(w.title)} · ${w.width}×${w.height}${w.minimized?'（已最小化）':''}</option>`).join('');if(data.windows.some(w=>String(w.hwnd)===previous))$('capture-window').value=previous;}
async function openCalibration(mode){
  if(mode==='video'&&!previewPath())throw Error('请先选择视频。');
  resetPreview();previewSource=mode;previewSelected=0;
  $('preview-video-controls').hidden=mode==='live';$('load-preview').disabled=false;
  $('calibration-title').textContent=mode==='live'?'画面校准':'选择开始画面与校准';
  $('load-preview').textContent=mode==='live'?'重新截取画面':'重新载入预览';
  $('apply-crop').textContent=mode==='live'?'应用校准':'从此画面开始并应用校准';
  $('preview-instructions').textContent=mode==='live'?'预览时请在 3 秒内切回游戏；取到画面后返回这里校准。':'拖动进度条选择起点；需要时在下方框选完整游戏画面。';
  $('preview-status').textContent='准备预览…';drawCrop();renderPreviewControls();
  $('calibration-dialog').showModal();await preview();
}

$('pick-video').addEventListener('click',safe(async()=>{const data=await api('api/pick-video',{});if(data.path){setSource('video');$('video-path').value=data.path;$('profile-mode').value='auto';cropRect=[0,0,1,1];cropImage=null;startResume=null;toast('视频已选择，正在自动寻找战斗起点。');await beginStartSearch();}}));
$('source-live').addEventListener('click',()=>setSource('live'));
$('source-recording').addEventListener('click',()=>setSource('video'));
$('refresh-windows').addEventListener('click',safe(refreshWindows));
$('capture-window').addEventListener('change',()=>{if(!captureActive){cropRect=[0,0,1,1];cropImage=null;}});
$('end-live').addEventListener('click',safe(async()=>{$('end-live').disabled=true;await api('api/live/stop',{});toast('已结束捕获，正在生成行动轴。');}));
$('live-calibrate').addEventListener('click',safe(()=>openCalibration('live')));
$('update-catalog').addEventListener('click',safe(async()=>{const state=await api('api/catalog/update',{},true);renderCatalog(state);scheduleCatalogPoll();toast(state.updating?'正在下载角色图库，可以继续查看行动轴。':state.ready?'角色图库已准备好。':'图库尚未下载完成。',!state.updating&&!state.ready);}));
$('identify-team').addEventListener('click',safe(async()=>{
  if(!doc||namesBusy)return;
  if(!catalogState?.ready){await refreshCatalog();if(!catalogState?.ready)throw Error('请先点击「下载角色图库」，完成后再识别队伍名字。');}
  namesBusy=true;$('identify-team').disabled=true;const targetJob=currentJob;
  try{
    const edited=editedTeam();
    if(edited.some((person,index)=>person!==doc.team[index])){remember();doc.team=edited;syncTeamNames();markDirty();renderTable();}
    if(dirty)await saveDocument(false);
    const result=await api('api/names/'+encodeURIComponent(targetJob),{});
    if(currentJob!==targetJob)return;
    remember();doc.team=result.team;syncTeamNames();markDirty();renderTable();renderTeam();if(selectedIndex>=0&&!$('inspector').hidden)openInspector(selectedIndex);
    if(result.catalog){renderCatalog(result.catalog);scheduleCatalogPoll();}
    toast(`队伍识别完成：${result.matched||0} 个图库匹配，${result.unresolved||0} 个名字待核对；已有名字保留。可撤销此次识别。`);
  }finally{namesBusy=false;$('identify-team').disabled=!!catalogState?.updating;}
}));
$('analyze-button').addEventListener('click',safe(startAnalysis));
$('cancel-button').addEventListener('click',safe(async()=>{if(runningJob)await api('api/cancel/'+runningJob,{});toast('正在停止，已保存的行动轴可以继续查看。');}));
$('save-button').addEventListener('click',safe(()=>saveDocument()));
$('document-title').addEventListener('change',()=>{remember();doc.title=$('document-title').value;markDirty();});
$('job-list').addEventListener('click',safe(async event=>{const button=event.target.closest('[data-job]');if(button)await loadJob(button.dataset.job);}));
document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>showTab(button.dataset.tab)));
for(const id of ['compact-view','review-only','od-view'])$(id).addEventListener('change',renderTable);
$('axis-body').addEventListener('click',event=>{const row=event.target.closest('[data-index]');if(row){openInspector(Number(row.dataset.index));$('inspector').scrollIntoView({behavior:'smooth',block:'nearest'});}});
$('axis-body').addEventListener('click',event=>{const button=event.target.closest('[data-gap-id]');if(button)openGap(button.dataset.gapId);});
$('coverage-gaps').addEventListener('click',event=>{const button=event.target.closest('[data-gap-id]');if(button)openGap(button.dataset.gapId);});
$('gap-start').addEventListener('click',()=>{if(activeGap)$('gap-video').currentTime=activeGap.start_time;});
$('gap-end').addEventListener('click',()=>{if(activeGap)$('gap-video').currentTime=activeGap.end_time;});
$('gap-dialog').addEventListener('close',()=>{activeGap=null;$('gap-video').pause();$('gap-video').onloadedmetadata=null;});
$('prev-row').addEventListener('click',()=>openInspector(selectedIndex-1));
$('next-row').addEventListener('click',()=>openInspector(selectedIndex+1));
$('close-inspector').addEventListener('click',()=>{$('inspector').hidden=true;$('source-video').pause();selectedIndex=-1;renderTable();});
$('show-frame').addEventListener('click',()=>evidenceMode('frame'));
$('show-video').addEventListener('click',()=>evidenceMode('video'));
$('seek-row').addEventListener('click',()=>{evidenceMode('video');seekRow();});
$('step-back').addEventListener('click',()=>{evidenceMode('video');$('source-video').currentTime=Math.max(0,$('source-video').currentTime-.5);});
$('step-forward').addEventListener('click',()=>{evidenceMode('video');$('source-video').currentTime=Math.min(doc.video.duration,$('source-video').currentTime+.5);});
$('apply-row').addEventListener('click',safe(applyRow));
$('action-editors').addEventListener('change',event=>{
  if(!event.target.matches('[data-action-full]'))return;
  const slot=event.target.dataset.actionFull, full=event.target.value, catalog={...aliases,...currentAliases()};
  const match=Object.keys(catalog).find(x=>key(x)===key(full));
  if(match)document.querySelector(`[data-action-short="${slot}"]`).value=catalog[match];
});
$('capture-frame').addEventListener('click',safe(async()=>{
  if(selectedIndex<0)return;
  const t=$('source-video').currentTime, data=await api('api/capture/'+currentJob,{time:t});
  remember();doc.rows[selectedIndex].screenshot=data.screenshot;doc.rows[selectedIndex].time=t;doc.rows[selectedIndex].time_text=clock(t);markDirty();openInspector(selectedIndex);evidenceMode('frame');toast('已替换本行 Screenshot，行动时间保留原值。');
}));
$('undo-button').addEventListener('click',()=>{if(!undo.length){toast('没有可撤销的修改。');return;}doc=undo.pop();markDirty();$('document-title').value=doc.title;renderTable();renderTeam();if(selectedIndex>=doc.rows.length)selectedIndex=doc.rows.length-1;if(selectedIndex>=0)openInspector(selectedIndex);toast('已撤销上一处修改。');});
$('add-row').addEventListener('click',safe(()=>{
  if(!doc)return;remember();const at=selectedIndex>=0?selectedIndex+1:doc.rows.length, prior=doc.rows[Math.max(0,at-1)];
  const time=Number.isFinite($('source-video').currentTime)&&$('source-video').currentTime>0?$('source-video').currentTime:prior.confirm_time;
  const actions=doc.team.slice(0,3).map((p,i)=>({slot:i+1,character_id:p.id,character:p.alias,character_full:p.name,skill:'A',skill_full:'普通攻擊',skill_raw:'',confidence:0,identity_confidence:0,skill_confidence:0}));
  const row={id:'manual-'+Date.now(),index:at+1,turn:prior.turn,bonus:null,kind:'normal',label:String(prior.turn),time,confirm_time:time,phase_start:time,time_text:clock(time),actions,positions:[1,2,3,4,5,6],od_before:null,od_after:null,od_session:null,screenshot:prior.screenshot,needs_review:true,reviewed:false,notes:['手动新增的行动，请核对时间与角色。'],manual_edit:true};
  doc.rows.splice(at,0,row);reindex();markDirty();renderTable();openInspector(at);toast('已新增一行，可调整回合与 Skill。');
}));
$('delete-row').addEventListener('click',()=>{if(selectedIndex<0)return;if(doc.rows.length===1){toast('至少需要保留一行。',true);return;}remember();doc.rows.splice(selectedIndex,1);reindex();markDirty();openInspector(Math.min(selectedIndex,doc.rows.length-1));toast('已删除；可用「撤销」恢复。');});
$('apply-team').addEventListener('click',safe(()=>{
  const edited=editedTeam();
  remember();doc.team=edited;syncTeamNames();
  markDirty();renderTable();renderTeam();toast('队伍名称已更新，所有换位记录同步更新。');
}));
$('reset-team').addEventListener('click',()=>{if(!doc)return;remember();doc.team=doc.team.map(p=>({...p,name:'角色 '+p.id,alias:'角色 '+p.id,name_source:'placeholder',name_is_placeholder:true,name_confidence:0}));const team=people();for(const row of doc.rows)for(const a of row.actions){a.character=team.get(a.character_id).alias;a.character_full=team.get(a.character_id).name;}markDirty();renderTable();renderTeam();toast('已恢复为 Placeholder，可重新命名或撤销。');});
$('add-alias').addEventListener('click',()=>{$('alias-list').insertAdjacentHTML('beforeend',aliasItem());$('alias-list').lastElementChild.querySelector('input').focus();});
$('alias-list').addEventListener('click',event=>{const button=event.target.closest('.remove-alias');if(button)button.closest('.alias-item').remove();});
$('apply-aliases').addEventListener('click',safe(()=>{
  const catalog={};document.querySelectorAll('.alias-item').forEach(item=>{const full=item.querySelector('.alias-full').value.trim(),short=item.querySelector('.alias-short').value.trim();if(full&&short)catalog[full]=short;});
  remember();doc.settings.aliases=catalog;
  doc.rows.forEach(r=>r.actions.forEach(a=>{const match=Object.keys(catalog).find(full=>key(full)===key(a.skill_full));if(match)a.skill=catalog[match];}));
  updateSkillOptions(catalog);markDirty();renderTable();toast('简写已应用，完整名称继续保留。');
}));
$('download-profile').addEventListener('click',()=>{if(!doc)return;downloadJSON({name:doc.title,name_mode:'profile',team:doc.team.map(({name,alias,name_is_placeholder})=>({name,alias,name_is_placeholder})),layout:doc.settings.layout,aliases:currentAliases()},'队伍与画面配置.json');});
$('import-profile').addEventListener('click',()=>$('profile-file').click());
$('profile-file').addEventListener('change',safe(async event=>{
  const file=event.target.files[0];if(!file)return;if(file.size>1000000)throw Error('队伍配置超过 1 MB，请确认选择的是配置 JSON。');
  let p=JSON.parse(await file.text());if(p.settings)p={team:p.team,layout:p.settings.layout,aliases:p.settings.aliases};
  if(p.team&&p.team.length!==6)throw Error('队伍配置需要 6 个角色。');
  importedProfile=p;if(p.layout?.crop)cropRect=p.layout.crop;
  let option=$('profile-mode').querySelector('[value=imported]');if(!option){option=document.createElement('option');option.value='imported';$('profile-mode').append(option);}option.textContent='导入：'+file.name;$('profile-mode').value='imported';toast('已导入配置，将用于下一次识别。');
}));
$('export-button').addEventListener('click',safe(async()=>{
  if(!doc)return;$('export-button').disabled=true;
  try{
    await saveDocument(false);
    const data=await api('api/export/'+currentJob,{compact:$('compact-view').checked,od:$('od-view').value});
    const names={png:'PNG 图表',xlsx:'Excel · 含完整名称',csv:'CSV 表格',json:'JSON 行动数据',html:'HTML 预览',zip:'ZIP · 全部核对资料'};
    $('download-links').innerHTML=Object.entries(data.files).map(([type,path])=>`<a href="${encodeURI(path)}?download=1" download>${names[type]}</a>`).join('');
    const count=doc.rows.filter(r=>r.needs_review&&!r.reviewed).length,gaps=currentCoverage().gaps.length;
    $('export-note').textContent=gaps?'当前有 '+gaps+' 段回合缺少行动记录，导出文件会明确保留缺口。':count?'当前有 '+count+' 行待核对，导出文件会保留标记。':'所有待核对行均已确认。';
    $('export-dialog').showModal();
  }finally{$('export-button').disabled=false;}
}));
$('calibrate-button').addEventListener('click',safe(()=>openCalibration('video')));
$('load-preview').addEventListener('click',safe(preview));
$('auto-find-start').addEventListener('click',safe(beginStartSearch));
$('preview-auto-start').addEventListener('click',safe(beginStartSearch));
$('preview-time').addEventListener('input',()=>seekPreview(Number($('preview-time').value),180));
$('preview-time').addEventListener('change',()=>seekPreview(Number($('preview-time').value)));
$('preview-prev-frame').addEventListener('click',()=>{if(previewVideo)seekPreview(previewSelected-1/previewVideo.fps);});
$('preview-next-frame').addEventListener('click',()=>{if(previewVideo)seekPreview(previewSelected+1/previewVideo.fps);});
$('preview-back-second').addEventListener('click',()=>seekPreview(previewSelected-1));
$('preview-forward-second').addEventListener('click',()=>seekPreview(previewSelected+1));
$('calibration-dialog').addEventListener('close',()=>{if(!$('calibration-dialog').open)resetPreview();});
$('video-path').addEventListener('input',()=>{if($('calibration-dialog').open)$('calibration-dialog').close();resetPreview();});
$('reset-crop').addEventListener('click',()=>{if(startSearch)cancelStartSearch();startResume=null;cropRect=[0,0,1,1];renderStartSearch();drawCrop();});
$('start-time').addEventListener('input',()=>{$('start-search-status').textContent='';});
$('apply-crop').addEventListener('click',safe(async()=>{
  const session=previewSession;
  if(previewDisplayed===null||!cropImage)throw Error('请等待当前预览画面载入完成。');
  if(cropRect[2]-cropRect[0]<.15||cropRect[3]-cropRect[1]<.15)throw Error('请框选完整游戏画面。');
  if(previewSource==='video'){
    if($('end-time').value.trim()&&timestampSeconds($('end-time').value)<=previewDisplayed)throw Error('选中的起点不早于结束时间，请先调整侧栏的结束时间。');
    $('start-time').value=previewClock(previewDisplayed);
    $('start-search-status').textContent='已使用所选画面 '+previewClock(previewDisplayed)+' 作为战斗起点。';
    toast('战斗开始已设为 '+previewClock(previewDisplayed)+'，画面校准已应用。');
  }else if(captureActive){
    $('apply-crop').disabled=true;
    try{await api('api/live/profile',{profile:profile()});}
    finally{if(session===previewSession)renderPreviewControls();}
    if(session!==previewSession||!$('calibration-dialog').open)return;
    toast('校准已应用到当前捕获，结束后使用新范围识别。');
  }
  else toast('校准已应用到下一次捕获。');
  $('calibration-dialog').close();
}));
$('crop-canvas').addEventListener('pointerdown',event=>{if(startSearch)cancelStartSearch();startResume=null;renderStartSearch();const rect=event.currentTarget.getBoundingClientRect();cropAnchor=[(event.clientX-rect.left)/rect.width,(event.clientY-rect.top)/rect.height];event.currentTarget.setPointerCapture(event.pointerId);});
$('crop-canvas').addEventListener('pointermove',event=>{if(!cropAnchor)return;const rect=event.currentTarget.getBoundingClientRect(),x=Math.min(1,Math.max(0,(event.clientX-rect.left)/rect.width)),y=Math.min(1,Math.max(0,(event.clientY-rect.top)/rect.height));cropRect=[Math.min(cropAnchor[0],x),Math.min(cropAnchor[1],y),Math.max(cropAnchor[0],x),Math.max(cropAnchor[1],y)];drawCrop();});
$('crop-canvas').addEventListener('pointerup',()=>{cropAnchor=null;});
$('help-button').addEventListener('click',()=>$('help-dialog').showModal());
document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>$(button.dataset.close).close()));
document.addEventListener('keydown',safe(async event=>{if((event.ctrlKey||event.metaKey)&&event.key==='s'&&doc){event.preventDefault();await saveDocument();}}));
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});

safe(async()=>{
  const init=await api('api/bootstrap');jobs=init.jobs;aliases=init.aliases;
  renderJobs();const recent=jobs.find(j=>j.status==='done');if(recent)await loadJob(recent.id);
  const running=jobs.find(j=>['running','capturing'].includes(j.status));if(running){runningJob=running.id;$('analyze-button').disabled=true;updateProgress(running);}
  if(init.live_supported){await refreshWindows();}else{setSource('video');$('source-live').disabled=true;$('source-live').title='实时捕获目前支持 Windows';}
  if(running?.capture?.window)$('capture-window').value=String(running.capture.window.hwnd);
  setInterval(poll,1100);
  refreshCatalog().catch(()=>{$('catalog-status').textContent='角色图库状态暂时无法读取，可以手动命名。';});
})();
