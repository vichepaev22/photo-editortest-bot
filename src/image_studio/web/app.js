'use strict';

(() => {
  const $ = id => document.getElementById(id);
  const icon = name => `<span class="ui-icon" aria-hidden="true">${({spark:'✦',plus:'+',photo:'▧',grid:'⊞',user:'○',arrow:'→',download:'↓',check:'✓',close:'×'})[name] || '·'}</span>`;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const configuredBase = window.OBRAZ_API_BASE ?? window.OBRAZ_CONFIG?.apiBase;
  const localHost = ['127.0.0.1','localhost','[::1]'].includes(location.hostname);
  let apiBase = '';
  if (typeof configuredBase === 'string' && configuredBase.trim()) {
    try { const parsed = new URL(configuredBase,location.href); if (parsed.protocol === 'https:' || localHost && parsed.protocol === 'http:') apiBase = parsed.origin; } catch {}
  } else if (localHost) apiBase = location.origin;
  const state = {preview:!apiBase,trial:!apiBase && window.OBRAZ_PREVIEW_TRIAL===true,connection:'loading',connecting:false,entering:false,pickerOpen:false,catalog: [], mode: 'mock', localDemo: false, token: '', me: null, tab: 'studio', preset: 'hair', documentMode:'keep', photos: [], uploading: new Set(), jobs: [], job: null, pending: null, submitting: false, polling: null, resultURLs: new Map(), sourceURLs: new Map(), liveURLs: new Set()};
  const bridge = window.Telegram?.WebApp?.initData ? window.Telegram.WebApp : null;
  const supports = version => Boolean(bridge?.isVersionAtLeast?.(version));
  const hints = {
    hair: ['Укажите длину, цвет и укладку.', 'Например: каре до плеч, мягкие волны и натуральный каштановый цвет', 'Портрет до и после изменения причёски'],
    clothes: ['Опишите одежду, цвет и материал.', 'Например: светлый льняной костюм и белая футболка; сохранить позу и лицо', 'Один человек в двух вариантах одежды'],
    glasses: ['Укажите форму, цвет и материал оправы.', 'Например: тонкая круглая оправа тёмного металла с прозрачными линзами', 'Один портрет без очков и с оправой'],
    background: ['Опишите место, свет и настроение кадра.', 'Например: заменить фон на светлую студию с тёплым дневным светом; сохранить человека', 'Один портрет с исходным и новым фоном'],
    enhance: ['Укажите, что улучшить без изменения внешности.', 'Например: убрать шум, мягко улучшить свет, сохранить черты лица и естественную кожу', 'Фото до и после улучшения света и чёткости'],
    merge: ['Загрузите два фото и опишите общий кадр.', 'Например: люди с обоих фото рядом у моря, общий дневной свет; сохранить лица', 'Два исходных портрета и один общий кадр'],
    document_original: ['4 фото 35×45 мм без ИИ. Нужен исходный снимок анфас на белом фоне; фон и одежду сохраняем.', 'Подготовить 4 одинаковых снимка 35×45 мм без ИИ; лицо и плечи по центру', 'Один PNG с четырьмя копиями для печати'],
    document: ['Фото на документы: 4 одинаковых снимка 35×45 мм. Выберите вариант одежды или подготовку исходника без ИИ.', 'Белый фон, светлая рубашка или деловой костюм; сохранить лицо. Фото на документы', 'Четыре одинаковых портрета для анкеты']
  };
  const presetIcon = id => `<span class="preset-emoji" aria-hidden="true">${({hair:"✂",clothes:"♧",glasses:"◉",background:"▧",enhance:"✦",merge:"⊞",document_original:"▤",document:"♧"})[id] || "·"}</span>`;
  const documentPreset = id => ['document','document_original'].includes(id);
  const hairExamples = {
    single:'Каре до плеч с мягкими волнами. Один образ; сохранить лицо, одежду и фон.',
    nine:'Одно изображение: сетка 3×3 из 9 разных причёсок — каре, боб, пикси, каскад, прямые длинные волосы, волны, кудри, чёлка, собранные волосы. В каждой ячейке человек с моего фото. Сохрани лицо, возраст, ракурс, одежду и фон; без текста.',
    twelve:'Одно изображение: сетка 3×4 из 12 разных причёсок — каре, боб, пикси, каскад, прямые длинные волосы, волны, кудри, чёлка, хвост, пучок, коса, короткая стрижка. В каждой ячейке человек с моего фото. Сохрани лицо, возраст, ракурс, одежду и фон; без текста.'
  };
  function renderHairExamples() {
    $('hairExamples').hidden=state.preset !== 'hair';
    $('hairExamples').querySelectorAll('button').forEach(button => {button.disabled=Boolean(state.pending || state.submitting);});
  }
  const documentDescriptions = {keep:'Белый фон, сохранить одежду и лицо. Четыре фото 35×45 мм',suit:'Белый фон, тёмный деловой костюм и светлая рубашка; сохранить лицо',shirt:'Белый фон, светлая рубашка; сохранить лицо',original:'Подготовить исходное фото: четыре снимка 35×45 мм без ИИ'};
  const selectedPresetId = () => state.preset === 'document' && state.documentMode === 'original' ? 'document_original' : currentPreset()?.id;
  function renderDocumentOptions() {
    const visible=state.preset === 'document'; $('documentOptions').hidden=!visible;
    if (!visible) return;
    $('documentMode').value=state.documentMode; $('documentMode').disabled=Boolean(state.pending || state.submitting);
    $('documentNote').textContent=state.documentMode === 'original' ? 'Без ИИ: фон и одежда сохранятся. Нужен снимок анфас на белом фоне, голова и плечи по центру. Проверьте требования документа.' : 'Белый фон и выбранная одежда. ИИ-результат не подходит для паспорта РФ. Готовый лист: 4 фото 35×45 мм, PNG, 300 DPI.';
  }

  const credits = number => state.trial ? `${number} ${number === 1 ? 'генерация' : number >= 2 && number <= 4 ? 'генерации' : 'генераций'}` : `${number} ${number === 1 ? 'попытка' : number >= 2 && number <= 4 ? 'попытки' : 'попыток'}`;
  const currentPreset = () => state.catalog.find(item => item.id === state.preset) || state.catalog[0];
  const activeStatuses = new Set(['queued', 'running']);
  const statusText = {queued:'Фото принято. Ожидаем своей очереди', running:'Создаём ваш образ', generated:'Образ готов', delivered:'Образ готов', failed:'Не удалось создать образ', review:'Задание требует проверки'};
  const errors = {insufficient_credits:'Недостаточно попыток. Посмотрите баланс в профиле.', invalid_image:'Не удалось прочитать фото. Выберите JPEG, PNG или WebP.', image_too_large:'Фото слишком большое. Максимум — 10 MB.', upload_limit:'Достигнут лимит загруженных фото. Удалите данные или дождитесь очистки.', consent_required:'Сначала подтвердите согласие на обработку фото.', active_job:'Дождитесь завершения текущего задания.', job_active:'Дождитесь завершения текущего задания.', not_found:'Файл недоступен: срок хранения истёк или он был удалён.', unauthorized:'Вход в студию завершился. Войдите снова.', demo_unavailable:'Тестовый вход здесь недоступен. Откройте студию из Telegram.', invalid_description:'Добавьте описание от 1 до 1500 символов.', interrupted_provider:'Задание прервалось и требует проверки поддержки.'};
  Object.assign(errors,{body_too_large:errors.image_too_large,photo_limit:errors.upload_limit,already_active:errors.active_job,unsupported_media:errors.invalid_image,invalid_prompt:errors.invalid_description,invalid_inputs:'Проверьте количество фото и описание, затем подтвердите ещё раз.',request_mismatch:'Это подтверждение уже использовано для другого образа. Обновите результаты.',confirmation_required:'Перед созданием подтвердите стоимость.'});
  Object.assign(errors,{trial_exhausted:'Все 3 бесплатные генерации использованы. Повторная выдача недоступна.',trial_not_granted:'Подтвердите согласие, чтобы получить 3 бесплатные реальные генерации.',invalid_trial_user:'Бесплатные реальные генерации доступны только после входа через Telegram.'});
  function notify(message, error = false) { $('notice').textContent = message; $('notice').classList.toggle('error', error); $('notice').hidden = false; }
  function storeToken(value) { state.token = value; try { value ? sessionStorage.setItem('obraz.session', value) : sessionStorage.removeItem('obraz.session'); } catch {} }
  function haptic(type = 'selectionChanged') { try { if(supports('6.1')) bridge?.HapticFeedback?.[type]?.(); } catch {} }
  function trackURL(blob) { const url = URL.createObjectURL(blob); state.liveURLs.add(url); return url; }
  function releaseURL(url) { if (url) { URL.revokeObjectURL(url); state.liveURLs.delete(url); } }
  function resetMedia() { for (const url of state.liveURLs) URL.revokeObjectURL(url); state.liveURLs.clear(); state.photos = []; state.resultURLs.clear(); state.sourceURLs.clear(); state.pickerOpen=false; document.querySelectorAll('[data-upload]').forEach(input => {input.value='';}); }
  class APIError extends Error { constructor(code, status) { super(errors[code] || 'Не удалось выполнить действие. Попробуйте позже или обратитесь в поддержку.'); this.code = code; this.status = status; } }
  async function api(path, options = {}) {
    if (state.preview) throw new APIError('demo_unavailable',403);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 25000);
    const headers = new Headers(options.headers || {});
    if (state.token) headers.set('Authorization', `Bearer ${state.token}`);
    if (options.json !== undefined) headers.set('Content-Type', 'application/json');
    try {
      const response = await fetch(`${apiBase}${path}`, {...options, headers, body:options.json !== undefined ? JSON.stringify(options.json) : options.body, cache:'no-store', signal:controller.signal});
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        if (response.status === 401) expireSession();
        throw new APIError(body.error || 'request_failed', response.status);
      }
      return options.blob ? response.blob() : response.json();
    } finally { clearTimeout(timer); }
  }
  function expireSession() {
    storeToken(''); state.me = null; clearTimeout(state.polling); state.job = null; state.jobs = []; resetMedia();
    $('confirmDialog').close(); $('deleteDialog').close(); showEntry(); renderPhotos(); renderMe(); renderResults(); updateAction();
    notify('Сессия завершилась. Войдите снова, чтобы продолжить.', true);
  }
  function showEntry() {
    $('entryPanel').hidden = false;
    if (state.preview) {
      $('entryHeading').textContent='Предпросмотр интерфейса';
      $('entryCopy').textContent='Можно выбрать образ, загрузить своё фото и проверить описание. Фото остаётся только в вашем браузере. Создание изображений и вход пока не подключены.';
      if(state.trial) $('entryCopy').textContent='В рабочей студии после входа через Telegram и согласия — всего 3 бесплатные реальные генерации. Объединение и каждый новый вариант используют по 1 генерации. ' + $('entryCopy').textContent;
      $('entryConsent').closest('label').hidden=true;
      $('enterButton').textContent='Попробовать интерфейс';$('enterButton').disabled=false;
      return;
    }
    if (state.connection === 'error' && !state.catalog.length) {
      $('entryHeading').textContent='Подключение к студии прервалось';
      $('entryCopy').textContent='Выберите фото сейчас — оно останется в этой вкладке. Для загрузки на сервер нужно подключение и ваше согласие.';
      $('entryConsent').closest('label').hidden=true;
      $('enterButton').textContent='Подключиться снова';$('enterButton').disabled=false;
      return;
    }
    $('entryConsent').closest('label').hidden=false;
    $('entryConsent').checked = false;
    const telegram = Boolean(bridge?.initData);
    $('entryHeading').textContent = state.me ? 'Разрешите обработку ваших фото' : telegram ? 'Добро пожаловать в Образ' : 'Посмотрите, как устроена студия';
    $('entryCopy').textContent = state.mode === 'mock' ? 'Это тестовый режим: результат — копия исходного фото, без ИИ-правки и передачи OpenAI. Фото и описания хранятся локально до 24 часов. Для людей на фото нужны права и их согласие.' : 'После подтверждения фото и описание передаются OpenAI для правки. Лицо может измениться. Локальные фото, описания и результаты хранятся до 24 часов. Для людей на фото нужны права и их согласие.';
    $('entryConsentText').textContent = state.mode === 'mock' ? 'Сервис для совершеннолетних. У меня есть права на фото и согласие всех изображённых людей. Согласен на локальную обработку фото и описания в тестовом режиме.' : 'Сервис для совершеннолетних. У меня есть права на фото и согласие всех изображённых людей. Согласен на обработку фото и описания и их передачу OpenAI.';
    if(state.trial) $('entryCopy').textContent='После входа через Telegram и согласия — всего 3 бесплатные реальные генерации OpenAI. Любая функция, включая объединение, использует 1 генерацию. Каждый новый вариант — ещё 1. ' + $('entryCopy').textContent;
    $('enterButton').textContent = state.me ? 'Разрешить и продолжить' : telegram ? 'Войти через Telegram' : state.localDemo ? 'Открыть тестовую студию' : 'Откройте студию из Telegram';
    $('enterButton').disabled = true;
    $('entryConsent').disabled = !state.me && !telegram && !state.localDemo;
  }
  async function enter() {
    if(state.preview) {$('entryPanel').hidden=true;showTab('studio');return;}
    if (state.connection === 'error' && !state.catalog.length) {await connect();return;}
    if (state.entering || !$('entryConsent').checked) return;
    state.entering=true;$('enterButton').disabled = true;$('enterButton').textContent='Подключаем…';updatePhotoStatus();
    try {
      if (!state.token) {
        const session = await api(bridge?.initData ? '/api/session' : '/api/demo-session', {method:'POST', json:bridge?.initData ? {init_data:bridge.initData} : {consent:true}});
        storeToken(session.token);
      }
      if (bridge?.initData || state.me) await api('/api/consent', {method:'POST',json:{accepted:true}});
      await loadMe(); state.connection='ready';$('entryPanel').hidden = Boolean(state.me?.consent); $('notice').hidden = true; updateAction();
    } catch (error) { notify(error instanceof APIError ? error.message : 'Студия недоступна. Проверьте соединение и попробуйте войти снова.', true); }
    finally { state.entering=false;$('enterButton').textContent=state.me ? 'Разрешить и продолжить' : bridge?.initData ? 'Войти через Telegram' : state.localDemo ? 'Открыть тестовую студию' : 'Откройте студию из Telegram';$('enterButton').disabled = !$('entryConsent').checked;updatePhotoStatus(); }
  }
  async function loadMe() { state.me = await api('/api/me'); state.trial=state.me.trial_access===true; renderMe(); if (!state.me.consent) showEntry(); else $('entryPanel').hidden = true; updateAction(); flushPhotos(); }
  function renderMe() {
    const available = state.me?.available;
    $('headerBalance').textContent = available ?? '—'; $('profileBalance').textContent = available ?? '—';
    $('profileName').textContent = state.me ? `Здравствуйте, ${state.me.user.first_name || 'это ваша студия'}.` : 'Войдите, чтобы увидеть свои попытки.';
    $('reservedBalance').textContent = state.me ? `В обработке: ${state.me.reserved}. Новое изменение — от 1 попытки.` : 'Баланс появится после входа.';
    $('deleteButton').disabled = !state.me && !state.preview;
    $('demoCreditsButton').hidden = !(state.me && (state.mode === 'mock' || state.trial && state.me.consent));
    $('providerInfo').textContent = state.mode === 'mock' ? 'Тестовый режим возвращает копию исходного фото без передачи OpenAI. Это проверка интерфейса, а не результат ИИ.' : 'Для создания образа ваши фото и описание передаются OpenAI. Лицо может измениться. Каждая новая правка требует подтверждения стоимости.';
    if(state.trial){
      $('reservedBalance').textContent=`Всего 3 бесплатные реальные генерации. В обработке: ${state.me?.reserved || 0}. Любая функция — 1 генерация.`;
      $('providerInfo').textContent='Фото и описание передаются OpenAI. Лицо может измениться. Каждый новый вариант использует ещё одну бесплатную генерацию; при ошибке резерв возвращается. Удаление фото, повторное согласие и вход не возобновляют квоту.';
      $('demoCreditsButton').textContent='Проверить бесплатные генерации';
      document.querySelector('.balance-card > span:not(.ui-icon)').textContent='Осталось бесплатных генераций';
    }
    if(state.preview){
      $('headerBalance').textContent='Локально';$('profileName').textContent='Предпросмотр без входа в аккаунт.';
      $('reservedBalance').textContent='Баланс доступен после подключения студии и входа.';
      $('providerInfo').textContent='Генерация не подключена. Фото и описание никуда не отправляются.';
      $('privacyInfo').textContent='Выбранные фото доступны только в этой вкладке браузера. Предпросмотр не сохраняет фото и описание на сервере.';
      $('deleteCopy').textContent='Уберите выбранные фото и описание из текущего предпросмотра.';
      $('deleteButton').textContent='Очистить предпросмотр';
      $('supportInfo').textContent='Для подключения и примеров обратитесь к владельцу студии.';
    }
  }
  function renderPresets() {
    $('presetGrid').innerHTML = state.catalog.map(item => `<button type="button" class="preset-card ${item.id === state.preset ? 'selected' : ''}" data-preset="${escape(item.id)}" aria-pressed="${item.id === state.preset}">${presetIcon(item.id)}<span class="preset-label">${escape(item.label.replace('Объединить два фото','Объединить').replace('Улучшение фото','Улучшение'))}</span><span class="preset-cost">${credits(item.credits)}</span>${item.id === state.preset ? '<span class="selection-mark" aria-hidden="true">✓</span>' : ''}</button>`).join('');
    $('looksGrid').innerHTML = state.catalog.map(item => `<button type="button" class="look-card" data-look="${escape(item.id)}"><div class="look-art-wrap"><span class="asset-slot-label"><strong>${escape(item.label)}</strong><span>${escape(hints[item.id]?.[2] || 'Пример этого изменения')}</span><small>Здесь будет пример владельца студии</small></span></div><div class="look-copy"><strong>${escape(item.label)}</strong><p>${escape(hints[item.id]?.[0] || 'Опишите свою идею в студии.')}</p><span>Попробовать · ${credits(item.credits)} ${icon('arrow')}</span></div></button>`).join('');
    $('description').placeholder = hints[state.preset]?.[1] || 'Опишите, что вы хотите изменить на фото';
    renderDocumentOptions(); updateAction();
  }
  function selectPreset(id) {
    if (state.uploading.size || state.pending) { notify('Завершите загрузку или отправку текущего задания перед сменой образа.'); return; }
    state.preset = id; if(id === 'document') {$('description').value=documentDescriptions[state.documentMode];} renderPresets(); renderPhotos(); flushPhotos(); haptic();
  }
  function renderPhotos() {
    const required = currentPreset()?.inputs || 1;
    $('photoSlots').classList.toggle('merge-slots', required === 2);
    $('photoSlots').innerHTML = Array.from({length:required}, (_, index) => {
      const photo = state.photos[index];
      return `<div class="photo-slot">${photo ? `<img src="${escape(photo.url)}" alt="Ваше исходное фото ${index + 1}"><div class="photo-overlay"><span>${state.uploading.has(index) ? 'Загружаем…' : photo.error ? 'Загрузка прервалась' : photo.id ? `Фото ${index + 1}` : 'Выбрано в этой вкладке'}</span>${photo.error ? `<button type="button" class="upload-retry" data-retry-upload="${index}">Повторить</button>` : ''}<button type="button" class="icon-button" data-remove="${index}" aria-label="Убрать фото ${index + 1}" ${state.pending || state.uploading.has(index) ? 'disabled' : ''}>${icon('close')}</button></div>` : `<button type="button" class="upload-label" data-pick-photo="${index}" aria-controls="photoPicker${index}" aria-describedby="photoStatus"><span class="upload-plus">${icon('plus')}</span><strong>${required === 2 ? `Добавить фото ${index + 1}` : 'Добавьте своё фото'}</strong><small>${required === 2 ? 'Два фото, один общий кадр' : 'Здесь начинается новый образ'}</small></button>`}</div>`;
    }).join('');
    $('photoCount').textContent = `${state.photos.slice(0,required).filter(Boolean).length} / ${required}`;
    updateAction();
  }
  function updatePhotoStatus() {
    const required=currentPreset()?.inputs || 1;
    const selected=state.photos.slice(0,required).filter(Boolean);
    const waiting=selected.some(photo => !photo.id);
    const failed=selected.find(photo => photo.error);
    let message='Фото можно заменить или убрать до создания. Загрузка не тратит попытки.';
    if (state.pending) message='Проверяем отправку задания. Фото можно изменить после получения ответа.';
    else if (state.uploading.size) message='Загружаем выбранное фото… Создание ещё не началось.';
    else if (state.pickerOpen) message='Выберите фото в системном окне. Если окно не открылось, нажмите «Добавить фото» ещё раз.';
    else if (state.preview) message='Фото остаётся только в этой вкладке. Генерация в предпросмотре не подключена.';
    else if (failed) message=failed.error;
    else if (state.connection==='loading' || state.entering) message=waiting ? 'Фото выбрано и остаётся в этой вкладке. Подключаем студию…' : 'Подключаем студию… Фото можно выбрать сейчас.';
    else if (state.connection==='error') message='Нет связи со студией. Выбранное фото остаётся в этой вкладке; подключитесь снова.';
    else if (!state.me?.consent) message=waiting ? 'Фото выбрано и остаётся в этой вкладке. Для загрузки подтвердите согласие выше.' : 'Можно выбрать фото сейчас. Для загрузки на сервер подтвердите согласие выше.';
    else if (waiting) message='Фото выбрано. Готовим загрузку…';
    else if (state.me.available < (currentPreset()?.credits || 1)) message=state.trial ? 'Бесплатные генерации закончились. Выбор и загрузка фото остаются доступны.' : 'Не хватает попыток для создания. Выбор и загрузка фото остаются доступны.';
    $('photoStatus').textContent=message;
    $('photoStatus').classList.toggle('error',Boolean(failed) || state.connection==='error');
    $('photoStatus').classList.toggle('loading',Boolean(state.uploading.size) || state.connection==='loading' || state.entering);
    $('reconnectButton').hidden=state.preview || state.connection!=='error';
  }
  function pickPhoto(index) {
    if (state.pending || state.uploading.size) {notify(state.pending ? 'Сначала проверим отправку текущего задания.' : 'Дождитесь завершения загрузки фото.');return;}
    const input=$(`photoPicker${index}`);
    if (!input) return;
    state.pickerOpen=true;updatePhotoStatus();
    // Keep the picker in this click's user gesture; authentication runs separately.
    try {
      if (typeof input.showPicker==='function') input.showPicker();
      else input.click();
    } catch {
      try {input.click();} catch {state.pickerOpen=false;notify('Не удалось открыть выбор фото. Попробуйте нажать «Добавить фото» ещё раз.',true);updatePhotoStatus();}
    }
  }
  async function upload(file, index) {
    state.pickerOpen=false;
    if (!file) {updatePhotoStatus();return;}
    if (state.pending || state.uploading.size) {notify('Завершите загрузку или отправку текущего задания перед выбором другого фото.');updatePhotoStatus();return;}
    if (file.size > 10_000_000 || file.size === 0) { notify('Выберите фото размером до 10 MB.', true); updatePhotoStatus();return; }
    if (!['image/jpeg','image/png','image/webp'].includes(file.type)) { notify('Поддерживаются JPEG, PNG и WebP.', true); updatePhotoStatus();return; }
    const previous=state.photos[index];
    if (previous && ![...state.sourceURLs.values()].includes(previous.url)) releaseURL(previous.url);
    state.photos[index]={id:state.preview ? crypto.randomUUID() : null,url:trackURL(file),file:state.preview ? null : file,error:null};
    $('notice').hidden=true;renderPhotos();
    await sendPhoto(index);
  }
  function flushPhotos() {
    if (state.preview || !state.token || !state.me?.consent || state.pending) return;
    state.photos.slice(0,currentPreset()?.inputs || 1).forEach((photo,index) => {if(photo?.file && !photo.error) sendPhoto(index);});
  }
  async function sendPhoto(index) {
    const selected=state.photos[index];
    if (!selected?.file || state.preview || !state.token || !state.me?.consent || state.pending || state.uploading.has(index)) return;
    state.uploading.add(index); renderPhotos();
    try {
      const photo = await api('/api/photos', {method:'POST',headers:{'Content-Type':'application/octet-stream'},body:selected.file});
      if(state.photos[index]===selected) {selected.id=photo.id;selected.file=null;selected.error=null;}
      $('notice').hidden = true;
    } catch (error) {
      if(state.photos[index]===selected) selected.error=error instanceof APIError ? error.message : 'Фото осталось в этой вкладке. Проверьте связь и нажмите «Повторить».';
      notify(error instanceof APIError ? error.message : 'Фото не загружено. Проверьте связь и нажмите «Повторить».', true);
    }
    finally { state.uploading.delete(index); renderPhotos(); }
  }
  function updateAction() {
    renderHairExamples();
    renderDocumentOptions();
    const preset = currentPreset(); const description = $('description').value.trim();
    const ready = Boolean((state.preview || state.me?.consent) && preset && !state.uploading.size && state.photos.slice(0,preset.inputs).filter(photo => photo?.id).length === preset.inputs && description && !state.submitting);
    const hasCredits = !state.me || (state.me.available >= (preset?.credits || 1));
    const running = state.jobs.some(job => activeStatuses.has(job.status) || job.status === 'review');
    $('reviewButton').disabled = state.pending ? state.submitting || !state.token : !ready || !hasCredits || running;
    $('createText').textContent = state.pending ? 'Проверить отправку задания' : state.preview ? 'Посмотреть подтверждение' : `Создать образ · ${credits(preset?.credits || 1)}`;
    $('createHint').textContent = state.pending ? 'Повтор отправки использует прежний ключ и не создаёт второй запрос' : running ? 'Сначала завершите текущее задание' : !state.me?.consent ? 'Войдите и разрешите обработку фото' : !hasCredits ? 'Недостаточно попыток — посмотрите профиль' : !ready ? 'Сначала добавьте фото и описание' : 'Стоимость подтвердите на следующем шаге';
    $('descriptionCount').textContent = `${$('description').value.length} / 1500`;
    $('description').disabled = Boolean(state.pending);
    if(state.preview) $('createHint').textContent=ready ? 'Предпросмотр: создание изображений не подключено' : 'Добавьте фото и описание — они останутся в браузере';
    else if(state.trial && state.me?.consent) $('createHint').textContent=!hasCredits ? 'Все 3 бесплатные генерации использованы' : running ? 'Сначала завершите текущее задание' : !ready ? 'Добавьте фото и описание' : 'Один результат — 1 из 3 бесплатных генераций';
    updatePhotoStatus();
    try {
      if (state.tab === 'studio' && !state.pending && ready && hasCredits && !running && !$('confirmDialog').open) {
        if(supports('6.0')) {const colors=getComputedStyle(document.documentElement);bridge?.MainButton?.setParams({text:state.preview ? 'Предпросмотр · подтверждение' : `Создать · ${credits(preset.credits)}`,color:colors.getPropertyValue('--accent').trim(),text_color:colors.getPropertyValue('--accent-ink').trim(),is_active:true}); bridge?.MainButton?.show();}
      } else if(supports('6.0')) bridge?.MainButton?.hide();
    } catch {}
  }
  function showTab(tab, focus = false) {
    state.tab = tab;
    ['studio','looks','results','profile'].forEach(name => { $(`${name}View`).hidden = name !== tab; });
    document.querySelectorAll('[data-tab]').forEach(button => { button.classList.toggle('active', button.dataset.tab === tab); if (button.dataset.tab === tab) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current'); });
    try { if(supports('6.1')) tab === 'studio' ? bridge?.BackButton?.hide() : bridge?.BackButton?.show(); } catch {}
    if (tab === 'results' && state.token) loadJobs().catch(error => notify(error instanceof APIError ? error.message : 'Не удалось обновить результаты. Нажмите «Обновить».', true));
    if (tab === 'profile' && state.token) loadMe().catch(error => notify(error instanceof APIError ? error.message : 'Не удалось обновить баланс.', true));
    updateAction(); if (focus) { window.scrollTo({top:0,behavior:'auto'}); const heading = $(`${tab}Heading`); heading.tabIndex = -1; heading.focus({preventScroll:true}); } haptic();
  }
  function openReview() {
    if ($('reviewButton').disabled) return;
    const preset = currentPreset();
    const payload = state.pending?.payload;
    const description = payload?.description || $('description').value.trim();
    $('confirmSummary').innerHTML = `<dt>Изменение</dt><dd>${escape(preset.label)}</dd><dt>Ваша идея</dt><dd>${escape(description)}</dd><dt>Стоимость</dt><dd>${credits(preset.credits)} · останется ${Math.max(0,(state.me?.available || 0) - preset.credits)}</dd>`;
    if(state.preview) $('confirmSummary').innerHTML = `<dt>Изменение</dt><dd>${escape(preset.label)}</dd><dt>Ваша идея</dt><dd>${escape(description)}</dd><dt>Стоимость после подключения</dt><dd>${credits(preset.credits)}</dd>`;
    $('confirmMode').textContent = state.mode === 'mock' ? 'Тестовый запуск вернёт копию исходного фото без ИИ-изменений и передачи OpenAI. Тестовые попытки спишутся после готовности.' : 'После подтверждения начнётся обработка фото и описания OpenAI. Лицо может измениться. Попытки спишутся после готовности результата.';
    if(state.trial) $('confirmMode').textContent='Всего 3 бесплатные реальные генерации OpenAI. Этот результат использует 1 генерацию, включая объединение фото. Каждый новый вариант — ещё 1; при ошибке резерв возвращается.';
    if(state.preset === 'document') $('confirmMode').textContent=(selectedPresetId() === 'document_original' ? 'Подготовка исходника без ИИ. Фон и одежда не меняются.' : 'Обработка ИИ с белым фоном. Результат не подходит для паспорта РФ.') + ' Один лист с четырьмя копиями использует 1 попытку.';
    $('submitError').hidden = !state.pending; if (state.pending) $('submitError').textContent = 'Ответ на прошлую отправку не получен. Повторим её с тем же ключом: второе задание не создастся.';
    $('confirmSubmit').textContent = state.pending ? 'Повторить с тем же ключом' : `Подтвердить · ${credits(preset.credits)}`;
    if(state.preview) {$('confirmMode').textContent='Это предпросмотр: фото и описание остаются в браузере. Создание изображений не подключено, попытки не списываются.';$('confirmSubmit').textContent='Понятно, вернуться в студию';}
    $('editDraft').disabled = Boolean(state.pending); $('confirmDialog').showModal(); updateAction();
  }
  async function submitJob() {
    if(state.preview) {$('confirmDialog').close();notify('Вы проверили подтверждение. Генерация не подключена; фото и описание остаются в браузере.');return;}
    if (state.submitting || !state.token) return;
    if (!state.pending) {
      const preset = currentPreset();
      state.pending = {payload:{request_key:crypto.randomUUID(),preset:selectedPresetId(),photos:state.photos.slice(0,preset.inputs).map(photo => photo.id),description:$('description').value.trim(),confirmed:true},source:state.photos[0].url};
    }
    const pending = state.pending;
    state.submitting = true; $('confirmSubmit').disabled = true; $('closeConfirm').disabled = true; $('editDraft').disabled = true; $('confirmSubmit').textContent = 'Отправляем…'; updateAction();
    try {
      const job = await api('/api/jobs', {method:'POST',json:pending.payload});
      state.sourceURLs.set(job.id,pending.source); state.pending = null;
      state.job = {...job,preset:pending.payload.preset,label:currentPreset().label,cost:currentPreset().credits};
      $('confirmDialog').close(); $('notice').hidden = true; $('resultDetail').hidden = true; showTab('results',true); renderJob(); pollJob(job.id);
      await loadMe();
    } catch (error) {
      if (error instanceof APIError && error.status >= 400 && error.status < 500 && error.status !== 408 && error.status !== 429) state.pending = null;
      $('submitError').hidden = false;
      $('submitError').textContent = state.pending ? 'Ответ не получен. Не запускайте новый вариант: повторите эту отправку с тем же ключом. Повтор не создаст второе задание.' : error.message;
      $('confirmSubmit').textContent = state.pending ? 'Повторить с тем же ключом' : 'Подтвердить и создать';
      $('editDraft').disabled = Boolean(state.pending);
    } finally { state.submitting = false; $('confirmSubmit').disabled = false; $('closeConfirm').disabled = false; updateAction(); renderPhotos(); }
  }
  async function loadJobs() {
    const data = await api('/api/jobs'); state.jobs = data.jobs; renderResults(); updateAction();
    if (!state.job) { const active = state.jobs.find(job => activeStatuses.has(job.status)); if (active) {state.job = active; renderJob(); pollJob(active.id);} }
  }
  function renderJob() {
    $('activeJob').hidden = !state.job || ['generated','delivered'].includes(state.job.status);
    if (!state.job) return;
    $('activeJob').innerHTML = `${activeStatuses.has(state.job.status) ? '<span class="spinner" aria-hidden="true"></span>' : icon('photo')}<div><strong>${escape(statusText[state.job.status] || 'Проверяем состояние задания')}</strong><p>${state.job.status === 'review' ? 'Автоматического повтора нет. Обратитесь в поддержку для проверки.' : state.job.status === 'failed' ? 'Повторный запрос не запущен. Обновите баланс и попробуйте новый вариант.' : 'Можно оставаться здесь или вернуться позже. Готовый файл появится в результатах.'}</p></div>`;
  }
  async function pollJob(id) {
    clearTimeout(state.polling);
    if (!state.token) return;
    try {
      state.job = await api(`/api/jobs/${encodeURIComponent(id)}`); renderJob();
      if (!activeStatuses.has(state.job.status)) {
        await loadJobs(); await loadMe();
        if (state.job.has_result) { $('resultDot').hidden = state.tab === 'results'; await openResult(state.job.id); }
        return;
      }
      state.polling = setTimeout(() => pollJob(id),1800);
    } catch (error) {
      if (!state.token) return;
      notify(error instanceof APIError ? error.message : 'Связь со студией потеряна. Проверяем только состояние задания; создание не повторяется.',true);
      state.polling = setTimeout(() => pollJob(id),5000);
    }
  }
  function renderResults() {
    if (!state.jobs.length) {
      $('resultsList').innerHTML = `<div class="empty-state">${icon('photo')}<h2>Здесь будет ваш новый образ</h2><p>${state.preview ? 'В предпросмотре результаты не создаются. Здесь появятся ваши фото после подключения генерации.' : state.me ? 'Добавьте фото и опишите идею. Каждый результат останется в вашей коллекции на 24 часа.' : 'Войдите в студию, чтобы увидеть свои результаты.'}</p><button type="button" class="primary-button" data-go-studio>Перейти в студию ${icon('arrow')}</button></div>`;
      return;
    }
    $('resultsList').innerHTML = state.jobs.map(job => {
      const date = new Date(typeof job.created === 'number' ? job.created * 1000 : job.created);
      const formatted = Number.isNaN(date.valueOf()) ? '' : date.toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
      return `<article class="result-row"><div><strong>${escape(job.label)}</strong><p>${escape(formatted)} · ${escape(statusText[job.status] || job.status)} · ${credits(job.cost)}</p></div>${job.has_result ? `<button type="button" class="secondary-button" data-result="${escape(job.id)}">Посмотреть ${icon('arrow')}</button>` : ''}</article>`;
    }).join('');
  }
  async function openResult(id) {
    $('resultDetail').hidden = false; $('resultDetail').innerHTML = '<div class="skeleton" role="status" aria-label="Загружаем готовое фото"></div>';
    try {
      let url = state.resultURLs.get(id);
      if (!url) { url = trackURL(await api(`/api/jobs/${encodeURIComponent(id)}/result`,{blob:true})); state.resultURLs.set(id,url); }
      const job = state.jobs.find(item => item.id === id) || state.job;
      const documents = documentPreset(job?.preset);
      const before = documents ? null : state.sourceURLs.get(id);
      $('resultDetail').innerHTML = `<section class="result-viewer"><div class="viewer-title"><h2>${escape(job?.label || 'Ваш образ')}</h2>${state.mode === 'mock' ? '<span class="mode-badge">Тестовая копия · без ИИ</span>' : ''}</div><div class="compare-frame${documents ? ' document-sheet' : ''}"><img src="${escape(url)}" alt="${state.mode === 'mock' ? 'Тестовая копия исходного фото, без ИИ-правки' : 'Готовый результат'}">${before ? `<img class="compare-before" src="${escape(before)}" alt="Исходное фото"><div class="compare-divider"></div><input class="compare-range" type="range" min="0" max="100" value="50" aria-label="Сравнение исходного фото и результата" aria-valuetext="50 процентов исходного фото">` : ''}</div><div class="compare-labels"><span>${before ? 'Исходное фото' : 'Готовый файл'}</span><span>${state.mode === 'mock' ? 'Тестовая копия' : before ? 'Новый образ' : ''}</span></div><div class="viewer-actions"><p>${documents ? (job?.preset === 'document' ? 'Портрет создан ИИ и не подходит для паспорта РФ. ' : 'Исходное фото подготовлено без ИИ; фон и одежда сохранены. ') + '4 фото 35×45 мм · PNG · 300 DPI. Печатайте 100%, без подгонки; проверьте размер после печати.' : state.mode === 'mock' ? 'В тестовом режиме фото не меняется. Здесь можно проверить сравнение и скачивание.' : before ? 'Двигайте разделитель, чтобы сравнить. Скачайте готовый JPEG в исходном качестве.' : 'Сравнение доступно для фото, загруженного в этой сессии. Готовый JPEG можно скачать.'}</p><button class="primary-button" type="button" data-download="${escape(id)}">${icon('download')}Скачать фото</button></div></section>`;
      $('resultDot').hidden = true;
    } catch (error) { $('resultDetail').innerHTML = `<div class="notice error" role="alert">${escape(error instanceof APIError ? error.message : 'Не удалось загрузить результат. Попробуйте открыть его снова.')}</div>`; }
  }
  function download(id) { const url = state.resultURLs.get(id); if (!url) return; const link = document.createElement('a'); link.href = url; link.download = `obraz-${id}.${documentPreset((state.jobs.find(item => item.id === id) || state.job)?.preset) ? 'png' : 'jpg'}`; document.body.append(link); link.click(); link.remove(); }
  async function deleteData() {
    if(state.preview){resetMedia();$('description').value='';$('deleteDialog').close();renderPhotos();showTab('studio');notify('Фото и описание убраны из предпросмотра.');return;}
    $('confirmDelete').disabled = true; $('cancelDelete').disabled = true;
    try {
      await api('/api/delete',{method:'POST',json:{confirmed:true}}); state.pending = null; expireSession(); $('description').value = ''; $('resultDetail').hidden = true; showTab('studio'); notify('Ваши локальные фото, описания и результаты удалены.');
    } catch(error) { $('deleteError').hidden = false; $('deleteError').textContent = error instanceof APIError ? error.message : 'Не удалось подтвердить удаление. Данные пока остаются в студии.'; }
    finally { $('confirmDelete').disabled = false; $('cancelDelete').disabled = false; }
  }
  function applyTheme() {
    if (!bridge?.initData) return;
    const root = document.documentElement;
    root.dataset.theme = bridge.colorScheme === 'dark' ? 'dark' : 'light';
    const theme = bridge.themeParams || {};
    for (const [variable,key] of [['--bg','bg_color'],['--surface','secondary_bg_color'],['--text','text_color'],['--muted','hint_color']]) {
      root.style.removeProperty(variable);
      if (/^#[\da-f]{6}$/i.test(theme[key] || '')) root.style.setProperty(variable,theme[key]);
    }
    document.querySelector('meta[name=theme-color]').content = theme.bg_color || '#f6f5f3';
    const safe = bridge.safeAreaInset || {};
    const content = bridge.contentSafeAreaInset || {};
    // Both insets describe protected viewport edges: reserve the larger exclusion once.
    root.style.setProperty('--safe-top', `${Math.max(0,safe.top || 0,content.top || 0)}px`);
    root.style.setProperty('--safe-bottom', `${Math.max(0,safe.bottom || 0,content.bottom || 0)}px`);
    updateAction();
  }
  function setupBridge() {
    if(!bridge) return;
    try { bridge.ready(); bridge.expand(); bridge.onEvent('themeChanged',applyTheme); if(supports('8.0')) {bridge.onEvent('safeAreaChanged',applyTheme); bridge.onEvent('contentSafeAreaChanged',applyTheme);} if(supports('6.0')) bridge.MainButton?.onClick(openReview); if(supports('6.1')) bridge.BackButton?.onClick(() => { if ($('confirmDialog').open) $('confirmDialog').close(); else showTab('studio',true); }); } catch {}
    applyTheme();
  }
  function bindEvents() {
    document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click',() => showTab(button.dataset.tab,true)));
    $('balanceButton').addEventListener('click',() => showTab('profile',true));
    document.querySelector('.wordmark').addEventListener('click',event => {event.preventDefault();showTab('studio',true);});
    $('allPresetsButton').addEventListener('click',() => showTab('looks',true));
    $('entryConsent').addEventListener('change',() => {$('enterButton').disabled = state.entering || !$('entryConsent').checked;}); $('enterButton').addEventListener('click',enter);
    $('reconnectButton').addEventListener('click',connect);
    $('presetGrid').addEventListener('click',event => {const button = event.target.closest('[data-preset]');if(button) selectPreset(button.dataset.preset);});
    $('looksGrid').addEventListener('click',event => {const button = event.target.closest('[data-look]');if(button) {selectPreset(button.dataset.look);showTab('studio',true);}});
    document.querySelectorAll('[data-upload]').forEach(input => {
      input.addEventListener('change',() => {const file=input.files[0];input.value='';upload(file,Number(input.dataset.upload));});
      input.addEventListener('cancel',() => {state.pickerOpen=false;updatePhotoStatus();notify('Выбор фото отменён. Нажмите «Добавить фото», когда будете готовы.');});
    });
    $('photoSlots').addEventListener('click',event => {
      const picker=event.target.closest('[data-pick-photo]');if(picker) {pickPhoto(Number(picker.dataset.pickPhoto));return;}
      const retry=event.target.closest('[data-retry-upload]');if(retry) {sendPhoto(Number(retry.dataset.retryUpload));return;}
      const button=event.target.closest('[data-remove]');if (!button || state.pending || state.uploading.size) return;
      const index=Number(button.dataset.remove);const photo=state.photos[index];if(photo && ![...state.sourceURLs.values()].includes(photo.url)) releaseURL(photo.url);state.photos[index]=null;renderPhotos();
    });
    $('documentMode').addEventListener('change',() => {if(state.pending || state.submitting) return; state.documentMode=$('documentMode').value; $('description').value=documentDescriptions[state.documentMode]; updateAction();});
    $('hairExamples').addEventListener('click',event => {
      const button=event.target.closest('[data-hair-example]');
      if(!button || state.preset !== 'hair' || state.pending || state.submitting) return;
      const example=hairExamples[button.dataset.hairExample]; if(!example) return;
      $('description').value=example; updateAction(); $('description').focus();
    });
    $('description').addEventListener('input',updateAction); $('reviewButton').addEventListener('click',openReview); $('confirmSubmit').addEventListener('click',submitJob);
    for (const id of ['closeConfirm','editDraft']) $(id).addEventListener('click',() => {if(!state.submitting) {$('confirmDialog').close();updateAction();}});
    $('confirmDialog').addEventListener('cancel',event => {if(state.submitting) event.preventDefault();}); $('confirmDialog').addEventListener('close',updateAction);
    $('refreshResults').addEventListener('click',async () => { if(!state.token) {showTab('studio',true);return;} $('refreshResults').disabled=true; try {await loadJobs();if(state.job) {renderJob();if(activeStatuses.has(state.job.status)) pollJob(state.job.id);}} catch(error){notify(error instanceof APIError ? error.message : 'Не удалось обновить результаты.',true);}finally{$('refreshResults').disabled=false;} });
    $('resultsList').addEventListener('click',event => {const result=event.target.closest('[data-result]');if(result)openResult(result.dataset.result);if(event.target.closest('[data-go-studio]'))showTab('studio',true);});
    $('resultDetail').addEventListener('input',event => {if(!event.target.matches('.compare-range'))return;event.target.closest('.compare-frame').style.setProperty('--compare',`${event.target.value}%`);event.target.setAttribute('aria-valuetext',`${event.target.value} процентов исходного фото`);});
    $('resultDetail').addEventListener('click',event => {const button=event.target.closest('[data-download]');if(button)download(button.dataset.download);});
    $('deleteButton').addEventListener('click',() => {$('deleteError').hidden=true;$('deleteDialog').showModal();}); $('cancelDelete').addEventListener('click',() => $('deleteDialog').close()); $('confirmDelete').addEventListener('click',deleteData);
    $('deleteDialog').addEventListener('cancel',event => {if($('confirmDelete').disabled)event.preventDefault();});
    $('demoCreditsButton').addEventListener('click',async () => { $('demoCreditsButton').disabled=true;try{const result=await api('/api/demo-credits',{method:'POST'});await loadMe();notify(state.trial ? `Всего 3 бесплатные реальные генерации. Доступно: ${state.me.available}. Повторная выдача не предусмотрена.` : result.granted ? 'Тестовые попытки добавлены.' : 'Тестовые попытки уже выдавались. Повторная выдача недоступна.');}catch(error){notify(error instanceof APIError ? error.message : 'Не удалось проверить тестовые попытки.',true);}finally{$('demoCreditsButton').disabled=false;} });
    window.addEventListener('pagehide',event => {
      clearTimeout(state.polling);
      if(!event.persisted) resetMedia();
    });
    window.addEventListener('pageshow',async event => {
      if(!event.persisted) return;
      renderPhotos();updateAction();
      if(state.preview || !state.token) return;
      try {
        await loadMe();
        await loadJobs();
        if(state.job) {
          state.job=state.jobs.find(job=>job.id===state.job.id) || state.job;
          renderJob();
          if(activeStatuses.has(state.job.status)) pollJob(state.job.id);
        }
      } catch(error) {
        if(state.token) notify(error instanceof APIError ? error.message : 'Не удалось восстановить соединение. Обновите результаты перед новым созданием.',true);
      }
    });
  }
  async function init() {
    bindEvents(); setupBridge(); renderPhotos(); renderResults();
    if(state.preview){
      state.connection='ready';
      state.catalog=[{id:'hair',label:'Причёска',inputs:1,credits:1},{id:'clothes',label:'Одежда',inputs:1,credits:1},{id:'glasses',label:'Очки',inputs:1,credits:1},{id:'background',label:'Фон',inputs:1,credits:1},{id:'enhance',label:'Улучшение фото',inputs:1,credits:1},{id:'merge',label:'Объединить два фото',inputs:2,credits:state.trial?1:2},{id:'document',label:'Фото на документы',inputs:1,credits:1}];
      $('modeBadge').textContent='Предпросмотр';renderPresets();renderPhotos();renderMe();showEntry();
      document.querySelector('.privacy-note').innerHTML='<span class="privacy-dot"></span>Фото остаются только в вашей вкладке браузера.';
      $('resultsView').querySelector('.fine-print').textContent='Предпросмотр интерфейса: генерация и хранение результатов пока не подключены.';
      $('deleteHeading').textContent='Очистить предпросмотр?';$('deleteDialog').querySelector('.dialog-content>p').textContent='Выбранные фото и описание будут убраны из текущей вкладки.';$('confirmDelete').textContent='Да, очистить предпросмотр';
      return;
    }
    await connect();
  }
  async function connect() {
    if (state.preview || state.entering || state.connecting) return;
    state.connecting=true;state.connection='loading';updateAction();
    $('reconnectButton').disabled=true;
    try {
      const catalog=await api('/api/catalog'); state.catalog=catalog.presets;state.mode=catalog.mode;state.localDemo=catalog.local_demo;state.trial=catalog.trial_access===true;
      if (!state.catalog.some(item=>item.id===state.preset)) state.preset=state.catalog[0]?.id;
      $('modeBadge').textContent=state.trial?'Бесплатный доступ':state.mode==='mock'?'Тестовый режим':'Личная студия';
      $('supportInfo').textContent=catalog.support_contact || 'Откройте раздел поддержки в боте.';
      renderPresets();renderPhotos();renderMe();
      if(bridge?.initData) {const session=await api('/api/session',{method:'POST',json:{init_data:bridge.initData}});storeToken(session.token);}
      else {try{storeToken(sessionStorage.getItem('obraz.session') || '');}catch{}}
      if(state.token) {await loadMe();await loadJobs();}else showEntry();
      state.connection='ready';
    } catch(error) {state.connection='error';showEntry();notify(error instanceof APIError ? error.message : 'Не удалось подключиться к студии. Проверьте связь и нажмите «Подключиться снова».',true);}
    finally {state.connecting=false;$('reconnectButton').disabled=false;updateAction();}
  }
  init();
})();
