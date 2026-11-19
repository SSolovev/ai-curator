let csrf="", currentContext=null, currentResult=null, currentGeneration=null, employeePage=1, employeeHasNext=false, currentRuntime="llama_cpp";
const generationStorageKey="curator_generation_id",generationPollIntervalMs=3000,generationPollNetworkRetries=10;
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const runtimeNames={llama_cpp:"llama.cpp (Docker)",ollama:"Ollama (Native)",external:"Внешняя LLM"};
const jiraSources={
  corporate:{url:"https://project.gpb.tech/jira",hint:"Используется рабочая Jira по адресу https://project.gpb.tech/jira.",tokenPlaceholder:"Оставьте пустым для PAT из secret-файла"},
  demo:{url:"http://mock-jira:9000",hint:"Демо Jira содержит только вымышленные данные. Демо-токен подставится автоматически.",tokenPlaceholder:"Для демо PAT не требуется"},
  custom:{url:"",hint:"Введите полный адрес своей Jira без /rest/api/... и укажите PAT.",tokenPlaceholder:"Введите PAT или оставьте пустым для secret-файла"}
};
async function api(path,options={}){options.headers={"Content-Type":"application/json","X-CSRF-Token":csrf,...options.headers};const r=await fetch(path,options);let data;try{data=await r.json()}catch{data={error:{message:"Сервис вернул неизвестный ответ."}}}if(!r.ok){const x=new Error(data.error?.message||"Ошибка запроса");x.code=data.error?.code;x.status=r.status;throw x}return data}
function notice(text,error=false){const n=$("#notice");n.textContent=text;n.className="notice"+(error?" error":"");n.scrollIntoView({behavior:"smooth",block:"center"})}
function hideNotice(){const n=$("#notice");n.textContent="";n.className="notice hidden"}
function generationResultNotice(text=""){const n=$("#generation-result-notice");n.textContent=text;n.className=text?"notice":"notice hidden"}
function show(id){$$('.panel:not(.diagnostics)').forEach(x=>x.classList.remove('active'));$(`#${id}`).classList.add('active');$$('.step').forEach(x=>x.classList.toggle('active',x.dataset.target===id))}
function esc(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function selectJiraSource(){const mode=$('#jira-source').value,source=jiraSources[mode],url=$('#jira-base-url'),token=$('#connect-form [name="token"]');url.value=source.url;url.readOnly=mode!=="custom";url.placeholder=mode==="custom"?"https://jira.company.local":"";token.value="";token.disabled=mode==="demo";token.placeholder=source.tokenPlaceholder;$('#jira-source-hint').textContent=source.hint;if(mode==="custom")url.focus()}
function renderModelDiagnostics(data,showDetails=true){currentRuntime=data.runtime;const external=data.runtime==='external';$('#model-runtime').textContent=data.runtime_label;$('#privacy').textContent=external?'Контекст обезличивается локально перед отправкой во внешнюю LLM':'Контекст обезличивается локально перед основной моделью';$('#model-context-title').textContent=external?'Что будет обезличено перед внешней моделью':'Что будет обезличено перед локальной моделью';const name=$('#include-name'),nameLabel=$('#include-name-label span');name.disabled=true;name.checked=false;nameLabel.textContent='Имя всегда скрывается обязательным PII-фильтром';const filterOk=Boolean(data.privacy_filter?.health?.ok),ok=Boolean(data.health?.ok)&&filterOk,status=$('#model-status');status.textContent=ok?`Готова · ${data.configured_model}`:`Недоступна · ${data.configured_model}`;status.className=`model-status ${ok?'ok':'error'}`;if(showDetails)$('#diagnostic-result').textContent=JSON.stringify(data,null,2)}
async function refreshModelDiagnostics(showDetails=true){try{const data=await api('/api/model/diagnostics');renderModelDiagnostics(data,showDetails);return data}catch(x){const status=$('#model-status');status.textContent='Диагностика недоступна';status.className='model-status error';if(showDetails)$('#diagnostic-result').textContent=x.message;return null}}
document.addEventListener("DOMContentLoaded",async()=>{csrf=(await fetch('/api/session').then(r=>r.json())).csrf_token;$$('.step').forEach(b=>b.onclick=()=>show(b.dataset.target));$('#jira-source').onchange=selectJiraSource;selectJiraSource();await refreshModelDiagnostics(false);const pending=sessionStorage.getItem(generationStorageKey);if(pending){show('preview');await restoreContext();void trackGeneration(pending)}});
$('#connect-form').onsubmit=async e=>{e.preventDefault();const mode=$('#jira-source').value,f=new FormData(e.target),body=Object.fromEntries(f);body.token=mode==='demo'?'demo-token':(body.token||'');for(const k of ['ca_bundle_path','proxy_url'])if(!body[k])body[k]=null;try{await api('/api/connect',{method:'POST',body:JSON.stringify(body)});e.target.token.value='';notice(mode==='demo'?'Демо Jira подключена. Все данные в ней вымышлены.':'Подключение к Jira проверено. Токен хранится только в памяти сессии.');show('people');await searchEmployees()}catch(x){notice(x.message,true)}};
async function searchEmployees(){try{const q=encodeURIComponent($('#employee-search').value),b=$('#billable').checked;const d=await api(`/api/employees?q=${q}&billable_only=${b}&page=${employeePage}&page_size=20`);employeeHasNext=d.has_next;$('#employees-page').textContent=`Страница ${d.page} · найдено ${d.total}`;$('#employees-prev').disabled=employeePage<=1;$('#employees-next').disabled=!employeeHasNext;$('#employees').innerHTML=d.items.map(x=>`<div class="employee-card"><div><b>${esc(x.summary||'Без названия')}</b><br><small>${esc(x.key)} · ${esc(x.status||'Статус не указан')}</small></div><button data-key="${esc(x.key)}">Выбрать</button></div>`).join('')||'<p>Доступные Employee-задачи не найдены.</p>';$$('[data-key]').forEach(b=>b.onclick=()=>loadEmployee(b.dataset.key))}catch(x){notice(x.message,true)}}
$('#search-button').onclick=()=>{employeePage=1;searchEmployees()};$('#employees-prev').onclick=()=>{if(employeePage>1){employeePage--;searchEmployees()}};$('#employees-next').onclick=()=>{if(employeeHasNext){employeePage++;searchEmployees()}};$('#load-key').onclick=()=>loadEmployee($('#employee-key').value);
async function loadEmployee(key){try{currentContext=await api(`/api/employees/${encodeURIComponent(key)}/load?include_display_name=false`,{method:'POST',body:'{}'});renderContext();show('preview');notice('Контекст загружен с использованием настроенного сопоставления полей.')}catch(x){notice(x.message,true)}}
async function restoreContext(){try{currentContext=await api('/api/context');renderContext()}catch(x){$('#context-summary').textContent=x.message;$('#records').innerHTML='<p>Серверный контекст недоступен. Если генерация ещё выполняется, дождитесь итогового статуса.</p>'}}
function record(item,kind){const text=item.text||item.comment||Object.entries(item.scores||{}).filter(x=>x[1]!=null).map(x=>`${x[0]}: ${x[1]}`).join(', ');return `<div class="record"><label><input class="record-check" type="checkbox" checked value="${esc(item.source_ref)}"><b>${esc(kind)} · ${esc(item.date||'дата не указана')} · ${esc(item.type||item.period||'')}</b></label><p>${esc(text||'Без текстового комментария')}</p><small>${esc(item.source_ref)}</small></div>`}
function renderContext(){const c=currentContext;c.employee.display_name=null;$('#context-summary').innerHTML=`<p><b>${esc(c.employee.jira_key)}</b> · ${esc(c.employee.role||'роль не сопоставлена')} · ${esc(c.employee.status||'статус не указан')}</p>${c.context_selection.warnings.map(w=>`<p class="warning">${esc(w)}</p>`).join('')}`;$('#records').innerHTML=[...c.mood_monitoring.map(x=>record(x,'Mood Monitoring')),...c.incomplete_mood_monitoring.map(x=>record(x,'Незавершённый MM')),...c.interactions.map(x=>record(x,'Взаимодействие')),...c.feedback.map(x=>record(x,'Feedback'))].join('')||'<p>Содержательных записей для анализа нет.</p>'}
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function waitForGeneration(generationId,onStatus){
  let networkFailures=0;
  while(currentGeneration===generationId){
    let job;
    try{
      job=await api(`/api/generate/${generationId}/status`);
      networkFailures=0;
      onStatus(job)
    }catch(x){
      if(x.message==='Failed to fetch'&&networkFailures++<generationPollNetworkRetries){await wait(generationPollIntervalMs);continue}
      throw x
    }
    if(job.status==='running'){await wait(generationPollIntervalMs);continue}
    if(job.status==='succeeded'){
      try{
        const completed=await api(`/api/generate/${generationId}/result`);
        return {result:completed.result,elapsedSeconds:job.elapsed_seconds}
      }catch(x){
        if(x.message==='Failed to fetch'&&networkFailures++<generationPollNetworkRetries){await wait(generationPollIntervalMs);continue}
        throw x
      }
    }
    const x=new Error(job.error?.message||(job.status==='cancelled'?'Генерация отменена.':'Формирование завершилось с ошибкой.'));
    x.code=job.error?.code;
    x.elapsedSeconds=job.elapsed_seconds;
    throw x
  }
  throw new Error('Генерация отменена.')
}
async function trackGeneration(generationId,initialElapsedSeconds=0){
  currentGeneration=generationId;
  sessionStorage.setItem(generationStorageKey,generationId);
  let generationUnavailable=false;
  const runtime=currentRuntime;
  let serverElapsedSeconds=Number(initialElapsedSeconds)||0,elapsedSyncedAt=Date.now();
  const syncElapsed=job=>{serverElapsedSeconds=Number(job.elapsed_seconds)||0;elapsedSyncedAt=Date.now()};
  const elapsedSeconds=()=>serverElapsedSeconds+(Date.now()-elapsedSyncedAt)/1000;
  const renderProgress=()=>{$('#notice').textContent=`${runtimeNames[runtime]} готовит сценарий… ${Math.floor(elapsedSeconds())} с`};
  $('#generate').disabled=true;
  $('#cancel').classList.remove('hidden');
  notice(`${runtimeNames[runtime]} готовит сценарий… ${Math.floor(elapsedSeconds())} с`);
  const ticker=setInterval(renderProgress,1000);
  try{
    const completed=await waitForGeneration(generationId,job=>{syncElapsed(job);renderProgress()});
    currentResult=completed.result;
    renderResult();
    show('result');
    hideNotice();
    generationResultNotice(`Сценарий готов за ${completed.elapsedSeconds.toFixed(1)} с. Проверьте вывод и раскройте источники.`)
  }catch(x){
    generationUnavailable=x.code==='GENERATION_NOT_FOUND';
    const duration=Number.isFinite(x.elapsedSeconds)?x.elapsedSeconds.toFixed(1):elapsedSeconds().toFixed(1);
    notice(`${x.message} Формирование завершилось с ошибкой за ${duration} с.`,true)
  }finally{
    clearInterval(ticker);
    $('#generate').disabled=false;
    $('#cancel').classList.add('hidden');
    if(currentGeneration===generationId)currentGeneration=null;
    if(generationUnavailable&&sessionStorage.getItem(generationStorageKey)===generationId)sessionStorage.removeItem(generationStorageKey)
  }
}
$('#generate').onclick=async()=>{
  const excluded=$$('.record-check:not(:checked)').map(x=>x.value),runtime=currentRuntime;
  generationResultNotice();
  $('#generate').disabled=true;
  $('#cancel').classList.remove('hidden');
  notice(`${runtimeNames[runtime]} готовит сценарий…`);
  try{
    const job=await api('/api/generate',{method:'POST',body:JSON.stringify({excluded_source_refs:excluded,include_display_name:$('#include-name').checked})});
    await trackGeneration(job.generation_id,job.elapsed_seconds)
  }catch(x){
    notice(`${x.message} Формирование не запущено.`,true)
  }finally{
    if(!currentGeneration){$('#generate').disabled=false;$('#cancel').classList.add('hidden')}
  }
};
$('#cancel').onclick=async()=>{if(!currentGeneration)return;try{const state=await api(`/api/generate/${currentGeneration}/cancel`,{method:'POST',body:'{}'});if(state.cancelled)notice('Отмена генерации…')}catch(x){notice(x.message,true)}};
function ev(items){return items?.length?`<details class="evidence"><summary>Источники (${items.length})</summary>${items.map(x=>`<div>${esc(x.date)} · ${esc(x.source_ref)}</div>`).join('')}</details>`:'<small>Источник не указан: гипотеза или недостаточно данных</small>'}
function evidenceText(items){return items?.length?`\n  Источники: ${items.map(x=>[x.date,x.source_ref].filter(Boolean).join(' · ')).join('; ')}`:'\n  Источник не указан: гипотеза или недостаточно данных'}
function statementsText(title,items){if(!items?.length)return'';return `\n### ${title}\n${items.map(x=>`- ${x.statement}${x.verify_question?`\n  Вопрос для проверки: ${x.verify_question}`:''}${evidenceText(x.evidence)}`).join('\n')}\n`}
function resultToEditableText(r){
  const h=r.history_summary,c=r.curator_cheat_sheet,lines=['# Сценарий встречи 1-to-1','','## Краткий анализ истории','',h.overall_dynamics||'Недостаточно данных для оценки динамики.'];
  lines.push(statementsText('Повторяющиеся темы',h.recurring_topics),statementsText('Положительные изменения',h.positive_changes),statementsText('Нерешённые вопросы',h.unresolved_questions),statementsText('Сигналы для проверки',h.signals_to_verify));
  lines.push('## Идеи для начала разговора','',...r.conversation_openers.map(x=>`- ${x.phrase}`),'','## Приоритеты встречи','');
  for(const x of r.meeting_priorities)lines.push(`### ${x.priority}. ${x.topic}`,x.why_now||'',evidenceText(x.evidence).trimStart(),'');
  lines.push('## Осторожные формулировки','');
  for(const x of r.sensitive_topics)lines.push(`### ${x.topic}`,`Не использовать: ${x.avoid}`,`Лучше сказать: ${x.use_instead}`,`Когда поднять тему: ${x.when_to_raise}`,'');
  lines.push('## Скрипт 30-минутной встречи','');
  for(const x of r.agenda){lines.push(`### ${x.start_minute}–${x.end_minute} мин. — ${x.goal}`);if(x.curator_lines?.length)lines.push('Реплики куратора:',...x.curator_lines.map(v=>`- ${v}`));if(x.key_questions?.length)lines.push('Вопросы:',...x.key_questions.map(q=>`- ${q.question}${q.follow_up?`\n  Уточнение: ${q.follow_up}`:''}`));if(x.listen_for?.length)lines.push('На что обратить внимание:',...x.listen_for.map(v=>`- ${v}`));lines.push('')}
  lines.push('## Памятка куратору','','### Три сигнала',...c.three_signals.map(x=>`- ${x}`),'','### Три желаемых исхода',...c.three_desired_outcomes.map(x=>`- ${x}`),'','### Проекты договорённостей',...c.suggested_agreements.map(x=>`- ${x.action} · владелец: ${x.owner} · срок: ${x.due}`),'','### Сообщение после встречи','',c.follow_up_message_template||'');
  if(r.data_quality.context_cut||r.data_quality.warnings.length){lines.push('','## Качество данных','');if(r.data_quality.context_cut)lines.push('- Часть истории не вошла в контекст модели из-за ограничений размера.');lines.push(...r.data_quality.warnings.map(x=>`- ${x}`))}
  return lines.join('\n').replace(/\n{3,}/g,'\n\n').trim()+'\n'
}
function renderResult(){
  const r=currentResult,h=r.history_summary,c=r.curator_cheat_sheet;
  $('#editable-result').value=resultToEditableText(r);
  $('#rendered-result').innerHTML=`
    <h3>Краткий анализ истории</h3><p>${esc(h.overall_dynamics)}</p>
    ${['recurring_topics','positive_changes','unresolved_questions','signals_to_verify'].map(k=>h[k].map(x=>`<article><b>${esc(x.statement)}</b><p>${esc(x.verify_question)}</p>${ev(x.evidence)}</article>`).join('')).join('')}
    <h3>Идеи для начала разговора</h3><ul>${r.conversation_openers.map(x=>`<li>${esc(x.phrase)}</li>`).join('')}</ul>
    <h3>Приоритеты встречи</h3>${r.meeting_priorities.map(x=>`<article><b>${x.priority}. ${esc(x.topic)}</b><p>${esc(x.why_now)}</p>${ev(x.evidence)}</article>`).join('')}
    <h3>Осторожные формулировки</h3>${r.sensitive_topics.map(x=>`<article><b>${esc(x.topic)}</b><p><s>${esc(x.avoid)}</s><br>${esc(x.use_instead)}</p><small>${esc(x.when_to_raise)}</small></article>`).join('')}
    <h3>Скрипт 30-минутной встречи</h3><table class="agenda"><thead><tr><th>Время</th><th>Цель</th><th>Реплики</th><th>Вопросы</th><th>Слушать</th></tr></thead><tbody>${r.agenda.map(x=>`<tr><td>${x.start_minute}–${x.end_minute}</td><td>${esc(x.goal)}</td><td>${x.curator_lines.map(esc).join('<br>')}</td><td>${x.key_questions.map(q=>esc(q.question)+(q.follow_up?`<br><small>↳ ${esc(q.follow_up)}</small>`:'')).join('<br>')}</td><td>${x.listen_for.map(esc).join('<br>')}</td></tr>`).join('')}</tbody></table>
    <h3>Памятка куратору</h3>
    <h4>Три сигнала</h4><ul>${c.three_signals.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>
    <h4>Три желаемых исхода</h4><ul>${c.three_desired_outcomes.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>
    <h4>Проекты договорённостей</h4><ul>${c.suggested_agreements.map(x=>`<li>${esc(x.action)} · владелец: ${esc(x.owner)} · срок: ${esc(x.due)}</li>`).join('')}</ul>
    <h4>Сообщение после встречи</h4><p>${esc(c.follow_up_message_template)}</p>
    ${(r.data_quality.context_cut||r.data_quality.warnings.length)?`<h4>Качество данных</h4>${r.data_quality.context_cut?'<p class="warning">Часть истории не вошла в контекст модели из-за ограничений размера.</p>':''}<ul>${r.data_quality.warnings.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}`
}
$('#copy').onclick=async()=>{const field=$('#editable-result');try{await navigator.clipboard.writeText(field.value);notice('Отредактированный текст скопирован.')}catch{field.focus();field.select();if(document.execCommand('copy'))notice('Отредактированный текст скопирован.');else notice('Не удалось скопировать текст. Выделите его и скопируйте вручную.',true)}};
function downloadBlob(blob,filename){const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=filename;a.click();URL.revokeObjectURL(a.href)}
$$('[data-export]').forEach(b=>b.onclick=async()=>{if(!currentResult)return;if(b.dataset.export==='markdown'){downloadBlob(new Blob([$('#editable-result').value],{type:'text/markdown;charset=utf-8'}),'one-on-one.md');notice('Отредактированный сценарий сохранён в Markdown.');return}const r=await fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({format:'json',result:currentResult})});downloadBlob(await r.blob(),'one-on-one.json')});
$('#diagnostics').onclick=()=>refreshModelDiagnostics();
$('#forget').onclick=async()=>{await api('/api/session',{method:'DELETE',body:'{}'});sessionStorage.removeItem(generationStorageKey);currentContext=currentResult=null;location.reload()};
