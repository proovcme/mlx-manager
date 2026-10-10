const managerBase = document.querySelector('meta[name="mlx-manager-base"]')?.content.replace(/\/+$/, "") || "";
const managerURL = path => managerBase + path;
const $ = id => document.getElementById(id);
const bytes = n => n == null ? '—' : (n / 2 ** 30).toFixed(1) + ' ГБ';
let listing = {models: [], runtimes: []}, selected = null, state = null, working = false;
const chats = new Map(), sessions = new Map(), records = new Map(), drafts = new Map();
let historyIndex = [], gallery = [], workspaceReady = false, saveQueue = Promise.resolve(), saveCount = 0;
let draftTimer = null, chatSaveTimer = null, controller = null, viewingImage = null, chatRequest = null;
const unsaved = new Map();
let trashIds = [];
let saveError = null;
let promptJob = null, promptPollBusy = false, appliedPromptJob = null;
const promptBusy = () => promptJob?.state === 'running';
let ragDatasets = [];
const draftFields = ['rag-dataset','prompt','system-prompt','max-tokens','temperature','size','steps','seed','cache','backend','prompt-model','series-count'];
function savingStatus() {
  $('save-status').classList.toggle('save-error',Boolean(unsaved.size));
  $('save-status').textContent = unsaved.size ? 'Не сохранено: '+(saveError || 'повторите сохранение') : saveCount ? 'Сохраняем…' : 'Сохранено на Mac';
  $('retry-save').hidden = !unsaved.size;
}
function persist(record) {
  const value = structuredClone(record.value); saveCount++; savingStatus();
  const task = saveQueue.catch(()=>{}).then(async()=>{
    const result = await api('/api/workspace','POST',{id:record.id,kind:record.kind,model:record.model,value,revision:record.revision || 0});
    record.revision=result.revision; unsaved.delete(record.id);
    if(record.kind==='chat') {
      const info={id:record.id,model:record.model,title:value.title,count:value.messages.length,revision:result.revision};
      historyIndex=historyIndex.filter(c=>c.id!==record.id); historyIndex.unshift(info); renderHistory();
    }
  });
  saveQueue=task;
  task.catch(error=>{saveError=error.message;unsaved.set(record.id,record); notice('Не удалось сохранить: '+error.message);}).finally(()=>{saveCount--;savingStatus()});
  return task;
}
function captureDraft() {
  if(!selected || !workspaceReady)return;
  const value={}; for(const id of draftFields)value[id]=$(id).value;
  let draft=drafts.get(selected);
  if(!draft){draft={id:'draft_'+selected,kind:'draft',model:selected,revision:0,value};drafts.set(selected,draft)}
  value['auto-enhance']=$('auto-enhance').checked;
  value['web-search']=$('web-search').checked;
  value['rag-enabled']=$('rag-enabled').checked;
  value.expansion=draft.value?.expansion;
  draft.value=value; return draft;
}
function saveDraft() {const draft=captureDraft(); if(draft)return persist(draft); return Promise.resolve();}
function newConversation(model=selected) {
  const record={id:crypto.randomUUID(),kind:'chat',model,revision:0,value:{title:'Новый чат',messages:[],status:'complete'}};
  records.set(record.id,record);chats.set(model,record.id);return record;
}
function currentRecord() {return records.get(chats.get(selected)) || newConversation();}
function renderHistory() {
  const image=current()?.kind==='image';
  $('chat-history').hidden=false; $('new-chat').hidden=image;
  const active=image ? null : chats.get(selected), entries=[...historyIndex];
  if(active && !entries.some(c=>c.id===active))entries.unshift({id:active,model:selected,title:records.get(active)?.value.title || 'Новый чат'});
  const groups=new Map();
  for(const entry of entries) {
    if(!groups.has(entry.model)) {const group=document.createElement('optgroup');group.label=listing.models.find(m=>m.id===entry.model)?.name || 'Сохранённая модель';groups.set(entry.model,group);}
    groups.get(entry.model).append(new Option(entry.title,entry.id));
  }
  const placeholder=new Option(image ? 'Чаты — все модели ('+historyIndex.length+')' : 'Выберите чат', '');placeholder.disabled=true;
  $('chat-history').replaceChildren(placeholder,...groups.values());
  $('chat-history').value=active || ''; $('chat-history').disabled=working || !workspaceReady || !entries.length;
  $('new-chat').disabled=working || !selected || !workspaceReady;
}
async function loadWorkspace() {
  const data=await api('/api/workspace'); historyIndex=data.chats;
  for(const entry of data.drafts)drafts.set(entry.model,{...entry,kind:'draft'});
  for(const chat of historyIndex)if(!chats.has(chat.model))chats.set(chat.model,chat.id);
  workspaceReady=true; savingStatus();
}
async function loadGallery() {gallery=(await api('/api/image/gallery')).images;}
function repeatImage(job) {
  if(!job.parameters){notice('У старого изображения параметры не были записаны. Повтор доступен для новых генераций.');return;}
  const p=job.parameters; $('prompt').value=p.prompt; $('size').value=`${p.width}x${p.height}`;
  if(!$('size').value)$('size').append(new Option(`${p.width} × ${p.height}`,`${p.width}x${p.height}`,true,true));
  for(const key of ['steps','seed','cache'])$(key).value=p[key]; $('auto-enhance').checked=false; $('series-count').value='1'; saveDraft(); $('prompt').focus(); notice('Параметры восстановлены. Нажмите «Создать изображение».');
}

let displayedImage = null;
let copiedReply=null,copyFeedbackTimer=null;
let chatActivity = null, progressBusy = false;
const duration = seconds => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2,'0')}`;
function renderActivity() {
  const image = current()?.kind === 'image', job = state?.job;
  const series = image && promptJob?.generate && promptJob.count > 1 ? promptJob : null;
  $('series-status').hidden = !series;
  if(series) {
    const remaining = Math.max(0, series.count - (series.completed || 0));
    const tail = series.state === 'running' ? series.stage === 'cancelling' ? 'Останавливаем серию…' : `Осталось ${remaining}` : ({done:'Серия завершена',cancelled:'Остальные отменены',failed:'Серия остановлена с ошибкой',interrupted:'Серия прервана. Можно запустить новую.'}[series.state] || series.state);
    $('series-status').textContent = `Готово ${series.completed || 0} из ${series.count} · ${tail}`;
  }
  if(image && promptJob?.state==='done' && !promptJob.generate && (!job || Date.parse(job.started_at)<promptJob.started_at*1000)) {$('activity').hidden=true;return;}
  if(image && promptBusy() && (promptJob.stage !== 'generating' || job?.id !== promptJob.image_job)) {
    $('activity').hidden=false;
    const labels={loading:'Загрузка модели для промпта…',thinking:'Продумывает сцену…',rewriting:'Пишет промпт…',unloading:'Выгрузка текстовой модели…',cancelling:'Остановка…',generating:`Подготовка изображения ${promptJob.index} из ${promptJob.count}…`};
    $('activity-stage').textContent=labels[promptJob.stage] || 'Подготовка…';
    $('activity-time').textContent=duration(Date.now()/1000-promptJob.started_at);
    $('activity-progress').removeAttribute('value');
    $('activity-detail').textContent=promptJob.model_name || 'Подготовка серии';return;
  }
  const chat = !image && chatActivity?.id === selected ? chatActivity : null;
  const loading = !image && state?.runtime?.model?.id === selected && state.runtime.state === 'starting';
  $('activity').classList.toggle('chat-activity',Boolean(chat || loading));
  $('activity').hidden = !((image && job) || chat || loading);
  if ($('activity').hidden) return;
  if(loading && !chat) {
    $('activity-stage').textContent='Загрузка модели…';
    $('activity-time').textContent='';
    $('activity-progress').removeAttribute('value');
    $('activity-detail').textContent='После загрузки можно отправить сообщение. Загрузку можно остановить кнопкой «Выгрузить».';
    return;
  }
  if (chat) {
    $('activity-stage').textContent = chat.phase;
    $('activity-time').textContent = duration((Date.now()-chat.started)/1000);
    $('activity-progress').removeAttribute('value');
    $('activity-detail').textContent = chat.phase.includes('Ищет') ? 'Ищем источники для последнего сообщения' : chat.phase.includes('выдержки') ? 'Источники найдены, готовим ответ' : 'Ответ поступает потоком';
    return;
  }
  const p = job.progress || {}, labels = {preparing:'Подготовка',loading_encoder:'Загрузка текстового энкодера',encoding:'Обработка промпта',loading_transformer:'Загрузка модели изображения',denoising:'Генерация',decoding:'Декодирование изображения',done:'Изображение готово',failed:'Ошибка генерации',cancelled:'Генерация отменена',detached:'Менеджер перезапущен',interrupted:'Задание прервано',cancelling:'Отмена генерации…'};
  const stage = job.state === 'running' ? job.cancel_reason ? 'cancelling' : p.stage || 'preparing' : job.state;
  $('activity-stage').textContent = (promptBusy() && promptJob.stage === 'generating' ? `Изображение ${promptJob.index} из ${promptJob.count} · ` : '') + (labels[stage] || stage);
  $('activity').dataset.stage=stage;
  const ended = job.finished_at ? Date.parse(job.finished_at) : Date.now();
  $('activity-time').textContent = duration(Math.max(0,(ended-Date.parse(job.started_at))/1000));
  const total = p.total || job.steps;
  if (stage === 'done') $('activity-progress').value = 100;
  else if (stage === 'denoising' && total) $('activity-progress').value = 100 * (p.step || 0) / total;
  else if (stage === 'decoding') $('activity-progress').value = 100;
  else $('activity-progress').removeAttribute('value');
  $('activity-detail').textContent = stage === 'denoising' ? total ? `Шаг ${p.step || 0} из ${total}${p.skipped != null ? ' · вычислено '+p.computed+' · из кеша '+p.skipped : ''}` : 'Ожидаем отметку первого шага' : stage === 'cancelling' ? 'Останавливаем процесс; если он не отвечает, завершение будет принудительным.' : stage === 'cancelled' ? 'Задание остановлено' : stage === 'interrupted' ? 'Процесс завершился без результата. Можно повторить генерацию.' : stage === 'failed' ? humanError(job.error) || `Код завершения: ${job.exit_code ?? '—'}. Можно повторить генерацию; подробности в логах.` : stage === 'done' ? imageTimingSummary(p) : 'Операция выполняется';
}
async function refreshProgress() {
  renderActivity();
  if (current()?.kind !== 'image' || !state || !(state.job?.state === 'running' || promptBusy() && ['generating','cancelling'].includes(promptJob.stage)) || progressBusy) return;
  progressBusy = true;
  try {
    let result;
    try { result = await api('/api/image/progress'); }
    catch (_) {
      const text=(await api('/api/logs?service=image')).text || '', matches=[...text.matchAll(/Denoising (\d+)\/(\d+)/g)], last=matches.at(-1);
      result={job:{...state.job,progress:{stage:text.includes('✓ Saved')?'done':text.includes('Decoding')?'decoding':text.includes('Generating')?'denoising':'preparing',step:last?Number(last[1]):0,total:last?Number(last[2]):null}}};
    }
    if (result.job && (promptBusy() || result.job.id === state.job?.id)) {state.job=result.job; renderActivity(); if (state.job.state==='done' && !gallery.some(j=>j.id===state.job.id)) {viewingImage=state.job.id;await loadGallery();renderConversation();}}
  } catch (_) {} finally {progressBusy=false;}
}
async function api(path, method = 'GET', body) {
  const response = await fetch(managerURL(path), {method, headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  let data;
  try {data=await response.json();}catch(_){throw Error('Сервис вернул некорректный ответ (HTTP '+response.status+'). Обновите страницу; если ошибка повторится, перезапустите Manager.');}
  if (!response.ok) throw Error(humanError(data.error || response.statusText));
  return data;
}
function humanError(text) {
  const errors={
    'Start a chat model first':'Сначала запустите выбранную модель.',
    'Selected model is not ready':'Модель ещё загружается. Дождитесь статуса «Готова».',
    'Selected model has active requests':'Модель ещё выполняет запрос. Дождитесь завершения ответа.',
    'Image generation is running':'Генерация ещё идёт. Дождитесь завершения или отмените её.',
    'No running image job':'Активной генерации нет.',
    'Manager restarted before workflow completed':'Менеджер перезапущен. Завершённые изображения сохранены; можно запустить новую серию.',
    'Manager restarted before prompt expansion completed':'Менеджер перезапущен. Завершённые изображения сохранены; можно запустить новую серию.',
    'Image decoding produced NaN or infinity; no image was saved. Try more denoising steps.':'Декодер вернул некорректные значения. Изображение не сохранено; попробуйте увеличить число шагов.',
    'Heavy-memory lock held by another process':'GPU занят другим заданием. Дождитесь его завершения.',
    'External heavy process detected; resolve it manually':'Обнаружен внешний процесс модели. Остановите его в приложении, где он был запущен.',
    'Memory pressure is unknown or too high for image generation':'Для генерации сейчас недостаточно свободной памяти. Выгрузите другие модели и повторите.',
    'Invalid chat messages or generation settings':'Проверьте параметры ответа. Слишком длинный разговор продолжите в новом чате.',
    'History changed in another tab. Reload before saving; your draft remains in this tab.':'История изменена в другой вкладке. Скопируйте несохранённый текст и обновите страницу.'
  };const tokens=typeof text==='string' && text.match(/Image prompt uses (\d+) tokens.*limit is (\d+)/);if(tokens)return `Промпт занимает ${tokens[1]} токенов вместе со служебным шаблоном. Предел: ${tokens[2]}. Сократите текст — он не был обрезан.`;return errors[text] || text;
}
function notice(text) { $('notice').textContent = text; }
function current() { return listing.models.find(m => m.id === selected); }
function messages() {return currentRecord().value.messages;}
function activeModel() {
  if (state?.mode === 'external_chat') return listing.models.find(m => m.external_chat)?.id;
  if (state?.mode === 'image') return 'qwen-image-21';
  if (state?.mode === 'model') return state.runtime?.model?.id;
  return null;
}
function ensureHistoryModel(record) {
  if(!listing.models.some(model=>model.id===record.model)) listing.models.push({id:record.model,name:record.value.model_name || 'Сохранённый чат',kind:'chat',available:false,missing:true,backends:[],weight_bytes:0});
}
function renderModels() {
  const list = $('models'); list.replaceChildren();
  const query = $('search').value.toLowerCase();
  for (const model of listing.models.filter(m => m.name.toLowerCase().includes(query))) {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'model' + (model.id === selected ? ' selected' : '');
    const name = document.createElement('span'); name.className = 'model-name'; name.textContent = model.name;
    const meta = document.createElement('span'); meta.className = 'model-meta';
    meta.textContent = `${model.kind === 'image' ? 'Изображения · MLX' : model.vision ? 'Чат · VLM' : 'Чат'} · ${bytes(model.weight_bytes)}${model.id === activeModel() ? ' · активна' : ''}${!model.available ? ' · нет движка' : ''}`;
    button.append(name, meta); button.disabled=working || promptBusy(); button.onclick = () => choose(model.id); list.append(button);
  }
  if (!list.children.length) { const text = document.createElement('p'); text.className = 'caption'; text.textContent = 'Модели не найдены'; list.append(text); }
}
async function choose(id) {
  if(selected && (working || promptBusy()) && selected!==id)return;
  if(selected && selected!==id) await saveDraft().catch(()=>{});
  clearTimeout(draftTimer);draftTimer=null; selected = id;
  try{localStorage.setItem('selected-model',id)}catch(_){} displayedImage = null; viewingImage=null;
  const active=chats.get(id);
  if(active && !records.has(active)) {
    try{const entry=await api('/api/workspace/entry?id='+encodeURIComponent(active)); records.set(active,{...entry,kind:'chat'});}
    catch(error){notice(error.message);return;}
    if(selected!==id)return;
  }
  const model = current();
  $('model-name').textContent = model?.name || 'Выберите модель';$('panel-model-name').textContent=model?.name || 'Выберите модель';
  $('backend').replaceChildren(new Option('Авто', 'auto'));
  for (const backend of model?.backends || []) if (backend !== 'mlx-image') $('backend').append(new Option(backend === 'omlx' ? 'oMLX' : 'MLX LM', backend));
  $('backend').hidden = model?.kind === 'image';
  $('image-options').hidden = model?.kind !== 'image';
  for(const id of ['image-settings-button','prompt-help-button'])$(id).hidden=model?.kind!=='image';
  document.body.classList.toggle('image-mode',model?.kind==='image');
  $('chat-options').hidden = model?.kind === 'image';
  $('web-options').hidden = model?.kind === 'image';
  $('rag-options-button').hidden = model?.kind === 'image';
  $('prompt-options').hidden = model?.kind !== 'image';
  renderPromptModels();
  $('send').textContent = model?.kind === 'image' ? 'Создать' : 'Отправить';
  $('prompt').placeholder = model?.kind === 'image' ? 'Опишите изображение…' : 'Напишите сообщение…';
  const value=drafts.get(id)?.value || {};
  const defaults={prompt:'','system-prompt':'','max-tokens':'2048',temperature:'0.7',size:'1152x768',steps:'20',seed:'2098',cache:'off',backend:'auto','series-count':'1','prompt-model':$('prompt-model').value};
  for(const key of Object.keys(defaults))$(key).value=value[key] ?? defaults[key];
  $('prompt-model').dataset.chosen=$('prompt-model').value;
  $('auto-enhance').checked=value['auto-enhance'] === true;
  $('web-search').checked=value['web-search'] === true;
  $('rag-enabled').checked=value['rag-enabled'] === true;
  restoreRagDataset(value['rag-dataset'] || '');
  showPromptPreview(value.expansion);
  $('catalog-toggle').setAttribute('aria-expanded','false');document.querySelector('.workspace>aside').classList.remove('models-visible');document.body.classList.add('sidebar-collapsed');$('sidebar-toggle').setAttribute('aria-expanded','false');
  resizePrompt(); renderHistory(); renderModels(); renderConversation(); $('conversation').scrollTop=$('conversation').scrollHeight; updateLatestButton(); updateControls(); renderActivity();
}
function renderConversation() {
  const area = $('conversation');
  const previousTop=area.scrollTop, follow=nearLatest(area);
  const expanded=new Set(Array.from(area.querySelectorAll('details[open][data-detail]'),d=>d.dataset.detail));
  area.replaceChildren($('prompt-preview'));
  if (current()?.kind === 'image') {
    const job=gallery.find(j=>j.id===viewingImage);
    if(job) {
      const image=document.createElement('img');image.className='image-result';image.alt='Созданное изображение';image.src=managerURL('/api/image/output?job='+encodeURIComponent(job.id));
      const tools=document.createElement('div');tools.className='image-tools';
      const back=document.createElement('button');back.textContent='← Галерея';back.onclick=()=>{viewingImage=null;renderConversation()};
      const repeat=document.createElement('button');repeat.textContent='Повторить параметры';repeat.disabled=!job.parameters;repeat.onclick=()=>repeatImage(job);
      const download=document.createElement('a');download.textContent='Скачать PNG';download.href=image.src;download.download=job.id+'.png';
      const enlarge=document.createElement('button');enlarge.textContent='Полный размер';enlarge.onclick=()=>{$('viewer-image').src=image.src;$('image-viewer').showModal()};
      const remove=document.createElement('button');remove.className='danger';remove.textContent='В корзину';remove.onclick=()=>confirmGalleryTrash([job.id]);
      const actions=document.createElement('button');actions.className='quiet icon-button';actions.textContent='⋯';actions.setAttribute('aria-label','Действия с изображением');actions.setAttribute('popovertarget','result-actions');
      const menu=document.createElement('div');menu.id='result-actions';menu.className='action-menu';menu.setAttribute('popover','auto');
      for(const button of [repeat,enlarge,remove]) {const run=button.onclick;button.onclick=()=>{menu.hidePopover();run()};menu.append(button);}
      tools.append(back,download,actions,menu);area.append(tools,image);image.onclick=enlarge.onclick;
      const detail=document.createElement('p');detail.className='caption';detail.textContent=job.parameters ? `${job.parameters.width} × ${job.parameters.height} · ${job.parameters.steps} шагов · seed ${job.parameters.seed} · ${job.parameters.cache}` : 'Параметры старой генерации не записаны';area.append(detail);
      if(job.parameters?.prompt_expansion){const original=document.createElement('p');original.className='caption';original.textContent='Исходный замысел: '+job.parameters.prompt_expansion.original_prompt;area.append(original)}
      if(job.parameters?.series){const series=document.createElement('p');series.className='caption';series.textContent=`Серия · ${job.parameters.series.index} из ${job.parameters.series.count}`;area.append(series)}
      if(job.progress){const timing=document.createElement('p');timing.className='caption';timing.textContent=imageTimingSummary(job.progress);area.append(timing)}
      if(job.parameters){const text=document.createElement('p');text.className='message-body';text.textContent=job.parameters.prompt;area.append(text)}
    } else if(gallery.length) {
      const title=document.createElement('p');title.className='caption';title.textContent=`Галерея · ${gallery.length} изображений`;area.append(title);
      const tools=document.createElement('div');tools.className='image-tools';const clear=document.createElement('button');clear.className='danger';clear.textContent='Очистить галерею';clear.onclick=()=>confirmGalleryTrash(gallery.map(j=>j.id));tools.append(clear);area.append(tools);
      const grid=document.createElement('div');grid.className='gallery';
      for(const item of gallery) {
        const card=document.createElement('button');card.className='image-card';card.type='button';card.setAttribute('aria-label','Открыть изображение '+item.id);
        const img=document.createElement('img');img.src=managerURL('/api/image/output?job='+encodeURIComponent(item.id));img.alt='';img.loading='lazy';
        const label=document.createElement('span');label.className='caption';label.textContent=new Date(item.saved_at*1000).toLocaleString('ru-RU');card.append(img,label);
        card.onclick=()=>{viewingImage=item.id;renderConversation()};grid.append(card);
      }
      area.append(grid);
    } else {const p=document.createElement('div');p.className='empty';p.textContent='Здесь появятся созданные изображения';area.append(p)}
    displayedImage=state?.job?.state==='done' ? state.job.id : null;
    fitImage();
    return;
  }
  const history = messages();
  if(currentRecord().value.status==='streaming' && !working){const warning=document.createElement('p');warning.className='caption';warning.textContent='Ответ был прерван. Полученный текст сохранён.';area.append(warning);}
  if (!history.length) {
    const empty = document.createElement('div'); empty.className = 'empty';
    const title = document.createElement('div'); title.className = 'empty-title'; title.textContent = current()?.name || 'Выберите модель';
    const hint = document.createElement('p'); hint.textContent = current() ? 'Запустите модель и начните разговор' : 'Поместите MLX-модель в ~/models или Hugging Face кеш и обновите каталог кнопкой ↻. Для чата нужен движок MLX LM или oMLX.'; empty.append(title, hint); area.append(empty); return;
  }
  for (const [index,message] of history.entries()) {
    const row = document.createElement('article'); row.className = 'message ' + message.role; row.dataset.messageIndex=String(index);
    const label = document.createElement('div'); label.className = 'speaker'; label.textContent = message.role === 'user' ? 'Вы' : current().name;
    const header=document.createElement('div');header.className='message-header';header.append(label);row.append(header);
    const thinking=message.content.match(/^\s*<think>([\s\S]*?)(?:<\/think>([\s\S]*)|$)/);
    const visibleReasoning=thinking ? thinking[1] : message.reasoning;
    if (visibleReasoning) { const details = document.createElement('details'), summary = document.createElement('summary'), text = document.createElement('pre'); details.dataset.detail=index+'-reasoning';details.open=expanded.has(details.dataset.detail);summary.textContent = 'Рассуждение'; text.textContent = visibleReasoning; details.append(summary, text); row.append(details); }
    const body = document.createElement('div'); body.className = 'message-body'; body.textContent = thinking ? thinking[2] || '' : message.content; row.append(body);
    for(const evidence of [message.rag, message.web_search].filter(e=>Array.isArray(e?.sources) && e.sources.length)) {
      const details=document.createElement('details');details.className='web-sources';details.dataset.detail=index+'-'+evidence.provider+'-sources';details.open=expanded.has(details.dataset.detail);const summary=document.createElement('summary');summary.textContent=(evidence.type==='rag' ? 'Документы' : 'Источники')+' · '+evidence.sources.length;details.append(summary);
      const query=document.createElement('p');query.className='caption';query.textContent='Запрос: '+(evidence.query || 'Поиск модели')+' · '+evidence.provider;details.append(query);
      for(const source of evidence.sources.slice(0,36)) {
        try {const url=new URL(source.url);if(!['http:','https:'].includes(url.protocol) || url.username || url.password)continue;}catch(_){continue;}
        const link=document.createElement('a');link.href=source.url;link.target='_blank';link.rel='noopener noreferrer';link.textContent='['+(evidence.type==='rag' ? 'D' : '')+source.id+'] '+source.title+(source.page ? ' · стр. '+source.page : '');
        const item=document.createElement('div');item.className='web-source';const excerpt=document.createElement('p');excerpt.textContent=source.snippet;item.append(link,excerpt);details.append(item);
      }
      row.append(details);
    }
    if(message.role==='assistant') {
      const copy=document.createElement('button'),key=currentRecord().id+':'+index;
      copy.type='button';copy.className='quiet copy-answer';copy.dataset.copyReply=key;copy.setAttribute('aria-label','Скопировать ответ');
      copy.textContent=copiedReply===key ? 'Скопировано' : 'Копировать';copy.disabled=!body.textContent.trim();
      copy.onclick=async()=>{
        try {await navigator.clipboard.writeText(body.textContent);copiedReply=key;copy.textContent='Скопировано';clearTimeout(copyFeedbackTimer);
          copyFeedbackTimer=setTimeout(()=>{if(copiedReply===key){copiedReply=null;for(const button of document.querySelectorAll('.copy-answer'))button.textContent='Копировать';}},2000);
        } catch(error) {notice('Не удалось скопировать ответ. Проверьте разрешение браузера на буфер обмена.');}
      };
      header.append(copy);
    }
    if(message.role==='assistant' && !body.textContent && working) {body.textContent=chatActivity?.phase || 'Готовит ответ…';body.classList.add('pending-answer');}
    area.append(row);
  }
  area.scrollTop=follow ? area.scrollHeight : previousTop;
  updateLatestButton();
}
function updateStreamingMessage(index,message) {
  const area=$('conversation'),follow=nearLatest(area);
  const row=area.querySelector(`[data-message-index="${index}"]`);
  if(!row){renderConversation();return;}
  const thinking=message.content.match(/^\s*<think>([\s\S]*?)(?:<\/think>([\s\S]*)|$)/);
  const content=thinking ? thinking[2] || '' : message.content;
  const reasoning=thinking ? thinking[1] : message.reasoning;
  const body=row.querySelector('.message-body');body.textContent=content || chatActivity?.phase || 'Готовит ответ…';body.classList.toggle('pending-answer',!content);
  const copy=row.querySelector('.copy-answer');if(copy)copy.disabled=!content.trim();
  if(reasoning){
    let details=row.querySelector('details:not(.web-sources)');
    if(!details){details=document.createElement('details');details.dataset.detail=index+'-reasoning';const summary=document.createElement('summary');summary.textContent='Рассуждение';details.append(summary,document.createElement('pre'));row.insertBefore(details,body);}
    details.querySelector('pre').textContent=reasoning;
  }
  if(follow)area.scrollTop=area.scrollHeight;updateLatestButton();
}
function nearLatest(area=$('conversation')) {return area.scrollHeight-area.clientHeight-area.scrollTop<64;}
function updateLatestButton() {$('latest-message').hidden=current()?.kind==='image' || nearLatest();}
$('conversation').addEventListener('scroll',updateLatestButton,{passive:true});
$('conversation').addEventListener('toggle',updateLatestButton,true);
window.addEventListener('resize',updateLatestButton);
$('latest-message').onclick=()=>{const area=$('conversation');area.scrollTop=area.scrollHeight;updateLatestButton();};
function resizePrompt() {const field=$('prompt');field.style.height='auto';field.style.height=Math.min(120,field.scrollHeight)+'px';}
$('prompt').addEventListener('input',resizePrompt);
$('chat-options').onclick=()=>{$('image-menu').hidePopover();$('chat-settings').showModal();};
$('history-toggle').onclick=()=>{$('history-dialog').showModal();};
$('diagnostics-button').onclick=()=>{$('workspace-menu').hidePopover();$('diagnostics-dialog').showModal();};
function updateControls() {
  const busy=working || promptBusy();
  $('scan').disabled=busy; $('backend').disabled=busy;
  for(const button of $('models').querySelectorAll('button'))button.disabled=busy;
  const model = current(), reserved = state?.reserved, runningImage = state?.job?.state === 'running';
  const active = selected === activeModel();
  const ready = !model?.missing && active && (state?.mode === 'external_chat' || state?.mode === 'image' || state?.runtime?.state === 'ready');
  $('delete-model').disabled = !model || model.missing || busy || reserved || state?.mode !== 'idle';
  $('delete-model').title = state?.mode !== 'idle' || reserved ? 'Сначала выгрузите модели и дождитесь окончания заданий' : 'Проверить путь и переместить модель в корзину';
  $('start').disabled = !model?.available || busy || reserved || runningImage || state?.mode === 'conflict' || state?.mode === 'external_image';
  $('stop').disabled = busy || reserved || runningImage || !activeModel();
  const autoImage=model?.kind==='image' && ($('auto-enhance').checked || Number($('series-count').value)>1) && model.available && !['conflict','external_image'].includes(state?.mode);
  $('send').disabled = !(ready || autoImage) || busy || reserved || runningImage || Boolean(state?.active_chat_requests);
  $('enhance').disabled=!model || busy || reserved || runningImage || !$('prompt-model').value || ['conflict','external_image'].includes(state?.mode);
  for(const id of ['auto-enhance','web-search','prompt-model','series-count','restore-idea','chat-options','rag-options-button','rag-enabled','rag-dataset','rag-connect','rag-url','rag-key','rag-transport'])$(id).disabled=busy;
  $('image-settings-button').disabled=busy || runningImage;
  $('prompt').disabled=busy && !chatActivity;
  $('send').textContent=model?.kind==='image' ? 'Создать' : chatActivity ? 'Ждём ответ…' : 'Отправить';
  $('send').hidden=Boolean(chatActivity);
  $('prompt').placeholder=chatActivity ? 'Можно написать следующее сообщение…' : model?.kind==='image' ? 'Опишите изображение…' : 'Напишите сообщение…';
  $('cancel-prompt').hidden=!promptBusy(); $('cancel-prompt').disabled=promptJob?.stage==='cancelling';
  $('cancel-prompt').textContent=promptJob?.stage==='generating' ? 'Остановить серию' : 'Остановить подготовку';
 $('cancel-chat').hidden=!controller; renderHistory(); $('cancel').hidden = !runningImage || promptBusy(); $('cancel').disabled = busy || Boolean(state?.job?.cancel_reason);
  const indicator=modelIndicator(model,state,selected,activeModel(),chatActivity?.id===selected,listing.models.find(m=>m.id===activeModel())?.name);
  $('model-name').textContent=indicator.name;
  $('model-status').textContent=indicator.status;
  $('model-status').dataset.tone=indicator.tone;
  $('model-name').title=indicator.name;
  $('compose-hint').textContent = reserved ? 'Другое задание занимает GPU; дождитесь его завершения' : model?.kind === 'image' ? runningImage ? 'Можно отменить текущее задание' : 'Изображение сохраняется на этом Mac' : working ? chatActivity?.phase || 'Выполняется действие…' : !ready ? 'Сначала запустите модель' : state?.active_chat_requests ? 'Модель отвечает в другом окне — дождитесь завершения' : 'Enter — отправить · Shift+Enter — новая строка';
  if(chatActivity)$('compose-hint').textContent='Модель отвечает · следующее сообщение можно отправить после ответа';
  updateContextStatus();
  renderActivity();
}
async function scan() {
  try { listing = await api('/api/catalog?refresh=1'); $('engines').textContent = listing.runtimes.map(r => r.name).join(' · ') || 'Движки не найдены'; $('roots').replaceChildren(...listing.roots.map(root => {const d=document.createElement('div'); d.textContent=root; return d})); if (!current() && records.has(chats.get(selected)))ensureHistoryModel(records.get(chats.get(selected))); if (!current()) await choose((promptBusy() ? 'qwen-image-21' : activeModel()) || listing.models.find(m=>m.id===localStorage.getItem('selected-model'))?.id || listing.models[0]?.id); else renderModels(); renderPromptModels(); }
  catch (error) { notice(error.message); }
}
let statusRefreshing=false;
async function refresh() {
  if(statusRefreshing)return;statusRefreshing=true;
  try { state = await api('/api/status'); promptJob=state.prompt_enhancement || promptJob; $('connection').textContent = state.reserved ? 'GPU занят другим заданием' : 'Локально · ' + (state.mode === 'idle' ? 'Память свободна' : state.mode === 'conflict' || state.mode === 'external_image' ? 'Внешний процесс' : 'На связи'); $('memory').textContent = `Свободно ${bytes(state.system.free_bytes)} · swap ${bytes(state.system.swap_used_bytes)} · memory pressure ${state.system.pressure_free_percent ?? '—'}%`; renderModels(); updateControls(); if (state.job?.state === 'done' && displayedImage !== state.job.id && current()?.kind === 'image') {viewingImage=state.job.id;await loadGallery();renderConversation();} if (state.runtime?.state === 'failed' && state.runtime?.error && selected === activeModel()) notice(state.runtime.error); }
  catch (error) { $('connection').textContent = 'Нет связи'; notice(error.message); }
  finally {statusRefreshing=false;}
}
async function action(path, body) {
  $('model-controls').open=false;working = true; updateControls(); notice(path === '/api/models/start' ? 'Запускается модель…' : path === '/api/mode' ? 'Выгружается модель…' : path === '/api/image/jobs' ? 'Запускается генерация…' : 'Выполняется действие…');
  try { await api(path, 'POST', body); notice(''); await refresh(); await refreshProgress(); }
  catch (error) { notice(error.message); }
  finally { working = false; updateControls(); }
}
$('catalog-toggle').onclick=()=>{const open=$('catalog-toggle').getAttribute('aria-expanded')!=='true';$('catalog-toggle').setAttribute('aria-expanded',String(open));document.querySelector('.workspace>aside').classList.toggle('models-visible',open);};
$('scan').onclick = scan; $('search').oninput = renderModels;
$('start').onclick = () => action('/api/models/start', {model: selected, backend: $('backend').value});
$('stop').onclick = () => action('/api/mode', {mode:'idle'});
$('cancel').onclick = () => action('/api/image/cancel', {});
async function startNewChat() {if(working)return; const old=currentRecord();if(old.value.messages.length)await persist(old).catch(()=>{}); const record=newConversation();sessions.delete(selected);await persist(record).catch(()=>{});renderHistory();renderConversation();updateControls();$('prompt').focus();notice('');}
$('new-chat').onclick=async()=>{await startNewChat();$('history-dialog').close();};
$('chat-history').onchange=async()=>{const id=$('chat-history').value;if(!id || working)return;try{if(!records.has(id)){const entry=await api('/api/workspace/entry?id='+encodeURIComponent(id));records.set(id,{...entry,kind:'chat'})}const record=records.get(id);ensureHistoryModel(record);chats.set(record.model,id);sessions.delete(record.model);await choose(record.model);$('history-dialog').close();if(record.value.status==='streaming')notice('Предыдущий ответ был прерван. Сохранён полученный текст.');else notice('');}catch(error){notice(error.message)}};
$('retry-save').onclick=async()=>{for(const record of [...unsaved.values()])await persist(record).catch(()=>{});};
$('cancel-chat').onclick=async()=>{
  const request_id=chatRequest;
  controller?.abort();
  if(request_id)try{await api('/api/chat/cancel','POST',{request_id});}catch(error){notice('Не удалось подтвердить остановку модели: '+error.message);}
};
for(const id of [...draftFields,'auto-enhance','web-search','rag-enabled'])$(id).addEventListener('input',()=>{clearTimeout(draftTimer);draftTimer=setTimeout(()=>{draftTimer=null;saveDraft().catch(()=>{})},500);updateControls()});
window.addEventListener('beforeunload',event=>{if(draftTimer || saveCount || unsaved.size || working){event.preventDefault();event.returnValue='';}});
$('compose').onsubmit = async event => {
  event.preventDefault(); const prompt = $('prompt').value.trim(), model = current();
  if (!prompt || !model || working) return;
  if(model.kind!=='image' && $('rag-enabled').checked && !$('rag-dataset').value){notice('Выберите набор LES в меню «Документы»');return;}
  if (model.kind === 'image' && ($('auto-enhance').checked || Number($('series-count').value)>1)) { await startPromptWorkflow(true); return; }
  if (model.kind === 'image') { const [width,height]=$('size').value.split('x').map(Number); await action('/api/image/jobs', {prompt,width,height,steps:Number($('steps').value),seed:Number($('seed').value),cache:$('cache').value,prompt_expansion:drafts.get(selected)?.value.expansion?.prompt===prompt ? {original_prompt:drafts.get(selected).value.expansion.original_prompt,model:drafts.get(selected).value.expansion.model} : undefined}); renderConversation(); return; }
  const id = selected, record=currentRecord(), history = record.value.messages; if(history.length>=98){notice('Достигнут лимит истории. Создайте новый чат; этот разговор сохранён.');return;} history.push({role:'user', content:prompt}); $('prompt').value=''; resizePrompt(); working=true; chatActivity={id,phase:'Отправляет сообщение…',started:Date.now()}; updateControls(); renderConversation(); $('conversation').scrollTop=$('conversation').scrollHeight; updateLatestButton(); $('prompt').focus(); notice('');
  try {
    record.value.model_name=model.name;
    if(record.value.title==='Новый чат')record.value.title=Array.from(prompt).slice(0,80).join('');
    await persist(record);await saveDraft();
    if (state.mode === 'external_chat' && state.external_chat_sessions && !sessions.has(id)) { const session = await api('/api/chat/sessions','POST',{pinned_state:''}); sessions.set(id, session.session_id); }
    const system = $('system-prompt').value.trim(); const original = history.map(m => ({role:m.role,content:m.content}));
    chatActivity={id,phase:$('web-search').checked ? 'Ищет в интернете…' : 'Обрабатывает промпт…',started:Date.now()}; updateControls();
    const assistant={role:'assistant',content:'',reasoning:''}; history.push(assistant);renderConversation();record.value.status='streaming';await persist(record);controller=new AbortController();updateControls();
    await streamReply({messages:system ? [{role:'system',content:system},...original] : original,max_tokens:Number($('max-tokens').value),temperature:Number($('temperature').value),session:sessions.get(id),stream:true,web_search:$('web-search').checked,rag:$('rag-enabled').checked ? [$('rag-dataset').value] : undefined}, delta=>{
      if(!chatSaveTimer)chatSaveTimer=setTimeout(()=>{chatSaveTimer=null;persist(record).catch(()=>{})},500);
      const reasoning=delta.reasoning_content || delta.reasoning;
      if(reasoning) assistant.reasoning+=reasoning;
      if(delta.content) assistant.content+=delta.content;
      const thinking=assistant.content.match(/^\s*<think>([\s\S]*?)(?:<\/think>([\s\S]*)|$)/);
      if((thinking && thinking[2] == null) || (reasoning && !delta.content)) chatActivity.phase='Думает…';
      else if(delta.content) chatActivity.phase='Отвечает…';
      if(selected===id) {
        if(thinking) {assistant.reasoning=thinking[1];updateStreamingMessage(history.length-1,assistant);}
        else updateStreamingMessage(history.length-1,assistant);
        updateControls();
      }
    }, meta=>{
      if(meta.type==='rag'){
        if(meta.phase==='ready'){assistant.rag=meta;persist(record).catch(()=>{});}
        chatActivity.phase=meta.detail || 'Работает с документами…';
        if(selected===id){renderConversation();updateControls();}
        return;
      }
      if(meta.type!=='web_search')return;
      if(meta.phase==='ready') {assistant.web_search=meta;chatActivity.phase='Читает найденные выдержки…';persist(record).catch(()=>{});}
      else chatActivity.phase='Ищет в интернете…';
      if(selected===id){renderConversation();updateControls();}
    });
    const thinking=assistant.content.match(/^\s*<think>([\s\S]*?)<\/think>([\s\S]*)$/);
    if(thinking) {assistant.reasoning=thinking[1]; assistant.content=thinking[2].trim();}
    if(!assistant.content && !assistant.reasoning) throw Error('Модель вернула пустой ответ');
    record.value.status='complete';
  } catch(error) {
    record.value.status='interrupted';
    const last=history.at(-1);
    if(error.name==='AbortError')error=new Error('Остановлено пользователем');
    if(last?.role==='assistant' && (last.content || last.reasoning)) notice('Ответ прерван: '+error.message);
    else {if(last?.role==='assistant')history.pop(); history.pop(); if(selected===id){const pending=$('prompt').value;$('prompt').value=prompt+(pending ? '\n\n'+pending : '');resizePrompt();} notice(error.message+' Сообщение возвращено в поле ввода.');}
  }
  finally {clearTimeout(chatSaveTimer);chatSaveTimer=null;controller=null;chatRequest=null;await persist(record).catch(()=>{});await saveDraft().catch(()=>{});chatActivity=null; working=false; if (selected===id) renderConversation(); await refresh(); updateControls(); }
};
function renderPromptModels() {
  const previous=$('prompt-model').dataset.chosen || $('prompt-model').value;
  const models=listing.models.filter(m=>m.kind!=='image' && m.available).sort((a,b)=>(a.weight_bytes || Infinity)-(b.weight_bytes || Infinity));
  $('prompt-model').replaceChildren(...models.map(m=>new Option(m.name,m.id)));
  if(models.some(m=>m.id===previous))$('prompt-model').value=previous;
  else $('prompt-model').value=models.find(m=>/qwen3-4b/i.test(m.name))?.id || models[0]?.id || '';
  $('prompt-model').dataset.chosen=$('prompt-model').value;
}
function showPromptPreview(value) {
  $('prompt-preview').hidden=!value;
  $('prompt-original').textContent=value ? 'Исходный замысел: '+value.original_prompt : '';
  $('prompt-result').textContent=value?.prompt || '';
}
async function startPromptWorkflow(generate) {
  const prompt=$('prompt').value.trim(); if(!prompt || working || promptBusy())return;
  const expand=!generate || $('auto-enhance').checked;
  if(expand && !$('prompt-model').value){notice('Выберите доступную текстовую модель.');return;}
  const [width,height]=$('size').value.split('x').map(Number);
  working=true;updateControls();notice('');
  try {
    await saveDraft();
    promptJob=await api(expand ? '/api/prompt/enhance' : '/api/image/series','POST',{
      prompt,model:$('prompt-model').value,generate,count:generate ? Number($('series-count').value) : 1,
      image:{width,height,steps:Number($('steps').value),seed:Number($('seed').value),cache:$('cache').value}});
    appliedPromptJob=null;showPromptPreview(promptJob);$('prompt-preview').open=true;
  }catch(error){notice(error.message)}finally{working=false;updateControls()}
}
async function refreshPrompt() {
  if(promptPollBusy)return;promptPollBusy=true;
  try {
    const result=await api('/api/prompt/status');promptJob=result.job;
    if(current()?.kind==='image' && promptJob) {
      if(promptBusy())showPromptPreview(promptJob);
      if(!promptBusy() && appliedPromptJob!==promptJob.id) {
        appliedPromptJob=promptJob.id;
        if(promptJob.state==='done' && promptJob.expand) {
          showPromptPreview(promptJob);
          // Apply to the draft only if it still contains the brief for this workflow.
          if($('prompt').value.trim()===promptJob.original_prompt) {
            if(!promptJob.generate){$('prompt').value=promptJob.prompt;$('auto-enhance').checked=false;}
            const draft=captureDraft();draft.value.expansion={original_prompt:promptJob.original_prompt,prompt:promptJob.prompt,model:promptJob.model_name};await persist(draft);
          }
        }
        if(promptJob.state==='failed' || promptJob.state==='interrupted')notice(humanError(promptJob.error || 'Подготовка остановлена. Можно повторить запуск.'));
        else if(promptJob.state==='cancelled')notice(`Остановлено. Готово изображений: ${promptJob.completed || 0}.`);
        else if(!promptJob.generate)notice('Промпт готов. Можно отредактировать его и создать изображение.');
        else notice(`Серия готова: ${promptJob.completed} из ${promptJob.count}.`);
        await refresh();await loadGallery();renderConversation();
      }
    }
    updateControls();
  }catch(error){if(promptBusy())notice('Не удалось обновить подготовку: '+error.message)}finally{promptPollBusy=false}
}
$('prompt-model').onchange=()=>{$('prompt-model').dataset.chosen=$('prompt-model').value};
$('enhance').onclick=()=>{$('image-settings').close();startPromptWorkflow(false)};
$('cancel-prompt').onclick=async()=>{try{promptJob=await api('/api/prompt/cancel','POST',{});updateControls()}catch(error){notice(error.message)}};
$('restore-idea').onclick=()=>{const value=drafts.get(selected)?.value.expansion;if(value){$('prompt').value=value.original_prompt;saveDraft().catch(()=>{});notice('Исходный замысел восстановлен.')}};
async function streamReply(body,onDelta,onMeta=()=>{}) {
  chatRequest=crypto.randomUUID();
  body={...body,request_id:chatRequest};
  const response=await fetch(managerURL('/api/chat'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:typeof controller!=='undefined'?controller?.signal:undefined});
  if(!response.ok) {const error=await response.json(); throw Error(error.error || response.statusText);}
  const reader=response.body.getReader(), decoder=new TextDecoder(); let buffer='', done=false;
  function frame(text) {
    const data=text.split('\n').filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n').trim();
    if(!data)return;
    if(data==='[DONE]'){done=true;return;}
    const chunk=JSON.parse(data); if(chunk.error)throw Error(typeof chunk.error==='string'?chunk.error:chunk.error.message || 'Ошибка модели');
    if(chunk.type==='web_search' || chunk.type==='rag'){onMeta(chunk);return;}
    const choice=chunk.choices?.[0]; if(choice?.delta)onDelta(choice.delta);
    if(choice?.finish_reason)done=true;
  }
  try {
    while(true) {
      const part=await reader.read(); buffer=(buffer+decoder.decode(part.value || new Uint8Array(),{stream:!part.done})).replace(/\r\n/g,'\n');
      let boundary; while((boundary=buffer.indexOf('\n\n'))>=0){frame(buffer.slice(0,boundary));buffer=buffer.slice(boundary+2);}
      if(part.done){if(buffer.trim())frame(buffer);break;}
    }
    if(!done)throw Error('Соединение закрыто до завершения ответа');
  } finally {await reader.cancel().catch(()=>{}); reader.releaseLock();}
}
$('prompt').onkeydown = event => {
  if(event.key!=='Enter' || event.isComposing || event.shiftKey || event.altKey)return;
  const textChat=current()?.kind!=='image';
  if(!textChat && !event.ctrlKey && !event.metaKey)return;
  event.preventDefault();
  if(!$('send').disabled)$('compose').requestSubmit();
};
$('load-logs').onclick = async () => {try {$('logs').textContent=(await api('/api/logs?service='+$('log-service').value)).text || 'Лог пуст';}catch(error){notice(error.message)}};
(async()=>{try{await loadWorkspace();await loadGallery();await scan();updateControls();await refresh();await refreshProgress();await refreshPrompt();}catch(error){notice('Не удалось загрузить историю: '+error.message);$('save-status').textContent='История недоступна';}})(); setInterval(refresh,8000); setInterval(refreshProgress,1000); setInterval(refreshPrompt,1000);
let deletionPlan = null;
$('delete-model').onclick = async () => {
  working = true; updateControls(); notice('');
  try {
    deletionPlan = await api('/api/models/delete-preview','POST',{model:selected});
    $('delete-target').textContent = deletionPlan.name + '\n' + deletionPlan.path + '\n' + bytes(deletionPlan.bytes);
    $('delete-snapshots').textContent = deletionPlan.all_snapshots ? 'Будут перемещены все snapshots и файлы кеша этой модели.' : 'Будет перемещена вся папка выбранной модели. Ссылки на старый путь перестанут работать до восстановления.';
    $('delete-confirm').value=''; $('delete-confirm').placeholder=deletionPlan.name; $('delete-confirm-button').disabled=true;
    $('delete-dialog').showModal();
  } catch(error) {notice(error.message)} finally {working=false; updateControls()}
};
$('delete-confirm').oninput=()=>{$('delete-confirm-button').disabled=$('delete-confirm').value!==deletionPlan?.name};
$('delete-close').onclick=()=>{$('delete-dialog').close(); deletionPlan=null};
$('delete-form').onsubmit=async event=>{
  event.preventDefault(); if(!deletionPlan || $('delete-confirm').value!==deletionPlan.name || working)return;
  working=true; updateControls(); $('delete-confirm-button').disabled=true; $('delete-close').disabled=true;
  try {const result=await api('/api/models/trash','POST',{token:deletionPlan.token,confirmation:$('delete-confirm').value}); $('delete-dialog').close(); deletionPlan=null; await scan(); notice('Модель в корзине: '+result.trash_path);}
  catch(error){$('delete-dialog').close(); notice(error.message)}
  finally {working=false; $('delete-close').disabled=false; await refresh()}
};

function imageTimingSummary(p) {
  const names={prompt_encoding:'Промпт',denoising:'Генерация',vae_decode:'Декодирование'};
  const parts=Object.entries(p.timings || {}).map(([key,value])=>`${names[key] || key}: ${duration(value)}`);
  const measured=Object.values(p.timings || {}).reduce((a,b)=>a+b,0);
  if(p.elapsed_seconds && measured)parts.push(`Загрузка и прочее: ${duration(Math.max(0,p.elapsed_seconds-measured))}`);
  if(p.computed!=null)parts.push(`Вычислено ${p.computed} · из кеша ${p.skipped || 0}`);
  if(p.skipped)parts.push('Пропуски не гарантируют сокращения общего времени');
  return parts.join(' · ') || 'Сохранено на Mac';
}
$('reading-mode').onclick=()=>{$('workspace-menu').hidePopover();const on=document.body.classList.toggle('reading-mode');$('reading-mode').setAttribute('aria-pressed',String(on));$('reading-mode').textContent=on ? 'Вернуть интерфейс' : 'Развернуть результат';fitImage();};
$('image-settings-button').onclick=()=>{$('image-menu').hidePopover();$('image-settings').showModal()};
$('prompt-help-button').onclick=()=>{$('image-menu').hidePopover();$('prompt-help').showModal()};
function confirmGalleryTrash(ids){trashIds=ids;$('gallery-trash-count').textContent=`Изображений: ${ids.length}`;$('gallery-trash').showModal();}
$('gallery-trash-confirm').onclick=async()=>{const button=$('gallery-trash-confirm');button.disabled=true;try{const result=await api('/api/image/trash','POST',{ids:trashIds});viewingImage=null;await loadGallery();renderConversation();$('gallery-trash').close();notice('Изображения перемещены в корзину: '+result.location);}catch(error){notice(error.message)}finally{button.disabled=false}};

$('sidebar-toggle').onclick=()=>{if(document.body.classList.contains('reading-mode'))$('reading-mode').click();const collapsed=document.body.classList.toggle('sidebar-collapsed');$('sidebar-toggle').setAttribute('aria-expanded',String(!collapsed));document.querySelector('aside').classList.toggle('models-visible',!collapsed);$('sidebar-toggle').textContent='Модели';fitImage();try{localStorage.setItem('sidebar-collapsed',String(collapsed))}catch(_){}};
try{if(localStorage.getItem('sidebar-collapsed')==='false'){$('sidebar-toggle').click()}}catch(_){}

function fitImage(){const image=$('conversation').querySelector('.image-result');if(image){const tools=$('conversation').querySelector('.image-tools');image.style.maxHeight=Math.max(160,$('conversation').clientHeight-(tools?.offsetHeight || 0)-45)+'px';}}
window.addEventListener('resize',fitImage);

$('sidebar-close').onclick=()=>{if(!document.body.classList.contains('sidebar-collapsed'))$('sidebar-toggle').click()};
document.addEventListener('pointerdown',event=>{if(!document.body.classList.contains('sidebar-collapsed') && !event.target.closest('#model-sidebar,#sidebar-toggle'))$('sidebar-close').click()});

function restoreRagDataset(id) {
  const options=[new Option('Выберите набор',''), ...ragDatasets.map(row=>new Option(row.name+' · '+row.documents+' док.',row.id))];
  if(id && !ragDatasets.some(row=>row.id===id))options.push(new Option('Выбранный набор · проверьте подключение',id));
  $('rag-dataset').replaceChildren(...options);$('rag-dataset').value=id;
}
function updateContextStatus() {
  const parts=[];
  if(current()?.kind!=='image') {
    if($('web-search').checked)parts.push('Интернет');
    if($('rag-enabled').checked)parts.push('LES · '+(ragDatasets.find(row=>row.id===$('rag-dataset').value)?.name || 'документы'));
  }
  $('context-status').hidden=!parts.length;$('context-status').textContent=parts.join(' + ');
}
$('rag-options-button').onclick=async()=>{
  $('image-menu').hidePopover();$('rag-dialog').showModal();
  try {const connection=await api('/api/rag/connection');$('rag-url').value=connection.url;$('rag-transport').value=connection.transport || 'api';$('rag-key').value='';$('rag-key').placeholder=connection.has_key ? 'Ключ сохранён · оставьте пустым' : 'Если требуется';$('rag-status').textContent=connection.configured ? 'Подключение сохранено. Нажмите «Подключить и проверить», чтобы получить наборы.' : 'Укажите адрес LES';}
  catch(error){$('rag-status').textContent=error.message;}
};
$('rag-connection-form').onsubmit=async event=>{
  event.preventDefault();const button=$('rag-connect');button.disabled=true;$('rag-status').textContent='Проверяем LES…';
  try {
    const body={url:$('rag-url').value,transport:$('rag-transport').value};if($('rag-key').value)body.api_key=$('rag-key').value;
    await api('/api/rag/connection','POST',body);$('rag-key').value='';
    const result=await api('/api/rag/datasets','POST',{});ragDatasets=result.datasets;
    const chosen=$('rag-dataset').value;restoreRagDataset(chosen);if(!chosen && ragDatasets.length===1)$('rag-dataset').value=ragDatasets[0].id;
    $('rag-enabled').disabled=!ragDatasets.length;$('rag-dataset').disabled=!ragDatasets.length;
    $('rag-status').textContent=ragDatasets.length ? 'Подключено · наборов: '+ragDatasets.length : 'Подключено, но пользовательских наборов пока нет';
    await saveDraft();updateContextStatus();
  } catch(error){$('rag-status').textContent=error.message;$('rag-enabled').disabled=true;$('rag-dataset').disabled=true;}
  finally {button.disabled=false;}
};
$('rag-enabled').onchange=updateContextStatus;$('rag-dataset').onchange=updateContextStatus;$('web-search').addEventListener('change',updateContextStatus);

function modelIndicator(model,state,selected,activeId,answering,activeName){
  const name=model?.name || state?.runtime?.model?.name || 'Выберите модель';
  if(!state)return {name,status:'Проверяем состояние…',tone:'idle'};
  if(state.reserved)return {name,status:'GPU занят другим заданием',tone:'busy'};
  const runtime=state.runtime;
  if(runtime?.model?.id===selected && runtime.state==='failed')return {name,status:'Ошибка запуска',tone:'error'};
  if(selected===activeId && activeId){
    if(state.mode==='image')return {name,status:state.job?.state==='running' ? 'Генерация изображения · MLX' : 'Режим изображений · MLX',tone:state.job?.state==='running' ? 'busy' : 'idle'};
    if(answering || state.active_chat_requests)return {name,status:'Отвечает · '+(runtime?.backend || 'oMLX'),tone:'busy'};
    if(runtime?.state==='starting')return {name,status:'Загружается…',tone:'busy'};
    if(state.mode==='external_chat' || runtime?.state==='ready')return {name,status:'Загружена · '+(runtime?.backend || 'oMLX'),tone:'ready'};
    return {name,status:'Остановлена',tone:'idle'};
  }
  if(activeId){
    const running=activeName || (state.mode==='image' ? 'Qwen Image' : runtime?.model?.name) || 'другая модель';
    return {name,status:'Выбрана, не загружена · сейчас работает: '+running,tone:'idle'};
  }
  if(['conflict','external_image'].includes(state.mode))return {name,status:'GPU занят внешним процессом',tone:'busy'};
  return {name,status:model ? 'Не загружена' : 'Модель не выбрана',tone:'idle'};
}
