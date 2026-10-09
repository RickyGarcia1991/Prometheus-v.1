'use strict';

const $ = id => document.getElementById(id);
const REQUEST_TIMEOUT_MS = 30000;
const busyButtons = new Set();
const requestControllers = new Set();
const imageControls = [
  'imageWidth', 'imageHeight', 'brightness', 'contrast', 'saturation',
  'resizeImage', 'rotateImage', 'cropImage', 'resetImage', 'savePng', 'saveJpeg'
];
let token = null;
let catalog = null;
let closed = false;
let closing = false;
let initializing = false;
let initializationError = false;
let initializationVersion = 0;
let activeOnline = null;
let activeReview = null;
let lastResearch = null;
let lastReview = null;
let researchVersion = 0;
let reviewVersion = 0;
let regionVersion = 0;
let regionCount = 0;
let original = null;
let base = null;
let imageName = '';
let imageLoading = false;
let imageVersion = 0;
let imageEditVersion = 0;
const canvas = $('canvas');

function status(message) {
  if (!closed && !closing) $('status').textContent = message;
}

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function safeLink(url, text) {
  const parsed = new URL(url);
  if (parsed.protocol !== 'https:') throw Error('Unsupported link.');
  const link = node('a', text);
  link.href = parsed.href;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  return link;
}

// Derive disabled state centrally so old callbacks cannot revive controls.
function syncControls() {
  document.querySelectorAll('button, input, select').forEach(control => { control.disabled = closed || closing; });
  if (closed || closing) return;
  for (const id of ['closeApp', 'loadRegions', 'searchNasa', 'nasaQuery', 'nasaType', 'codeFile']) $(id).disabled = !token;
  for (const id of ['researchFilter', 'autoFilter', 'exportAuto']) $(id).disabled = !catalog;
  for (const id of ['loadRegions', 'searchNasa']) $(id).disabled = !token || !!activeOnline;
  $('regions').disabled = !token || !regionCount || activeOnline?.kind === 'regions';
  $('mapFreshness').disabled = !token || !$('regions').value || !!activeOnline;
  $('reviewCode').disabled = !token || !$('codeFile').files[0] || !!activeReview;
  $('exportResearch').disabled = !lastResearch;
  $('exportReview').disabled = !lastReview;
  for (const id of imageControls) $(id).disabled = !base || imageLoading;
  $('retryInit').disabled = initializing;
  $('retryInit').hidden = !initializationError;
  $('cancelOnline').hidden = !activeOnline;
  $('onlineStatus').hidden = !activeOnline;
  for (const id of busyButtons) $(id).disabled = true;
}

function action(id, callback, managed = false) {
  $(id).addEventListener('click', async () => {
    if (closed || closing || $(id).disabled) return;
    if (!managed) busyButtons.add(id);
    syncControls();
    try {
      await callback();
    } catch (error) {
      if (error.name !== 'AbortError') status(error.message);
    } finally {
      if (!managed) busyButtons.delete(id);
      syncControls();
    }
  });
}

async function requestJson(path, options = {}, controller = new AbortController()) {
  let timedOut = false;
  requestControllers.add(controller);
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(path, {...options, signal: controller.signal});
    const data = await response.json();
    if (!response.ok) throw Error(data?.error || 'The local request failed.');
    return data;
  } catch (error) {
    if (timedOut) throw Error('The browser stopped waiting after 30 seconds. Retry when the service is available.');
    if (error instanceof SyntaxError) throw Error('The local service returned an unreadable response. Retry initialization or restart the launcher.');
    throw error;
  } finally {
    clearTimeout(timer);
    requestControllers.delete(controller);
  }
}

function api(name, body, controller) {
  if (!token) throw Error('The local session is not ready. Retry initialization.');
  return requestJson('/api/' + name, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Prometheus-Token': token},
    body: JSON.stringify(body)
  }, controller);
}

function cancelOnline(message) {
  if (!activeOnline) return;
  const operation = activeOnline;
  activeOnline = null;
  operation.controller.abort();
  if (message) status(message);
  syncControls();
}

async function onlineLookup(kind, name, body, apply) {
  if (activeOnline) throw Error('Finish or cancel the current online lookup first.');
  const operation = {kind, controller: new AbortController()};
  activeOnline = operation;
  syncControls();
  try {
    const data = await api(name, body, operation.controller);
    if (!closed && !closing && activeOnline === operation) apply(data);
  } catch (error) {
    if (activeOnline === operation && error.name !== 'AbortError') status(error.message);
  } finally {
    if (activeOnline === operation) activeOnline = null;
    syncControls();
  }
}

action('cancelOnline', () => cancelOnline(
  'Online lookup cancelled in this page. The server may still finish its publisher request; no late result will be applied.'
), true);

function download(name, body, type = 'application/json') {
  if (closed || closing) return;
  const url = URL.createObjectURL(new Blob([body], {type}));
  const link = node('a');
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 3000);
}

function validateCards(entries) {
  if (!Array.isArray(entries)) throw Error('The source list has an unexpected format.');
  for (const entry of entries) {
    if (!entry || typeof entry.title !== 'string' || typeof entry.url !== 'string') throw Error('A source entry has an unexpected format.');
    safeLink(entry.url, '');
    if (entry.provenance_url) safeLink(entry.provenance_url, '');
    if (entry.resource_links != null && !Array.isArray(entry.resource_links)) throw Error('A source entry has an unexpected link list.');
    for (const link of entry.resource_links || []) {
      if (!link || typeof link.title !== 'string') throw Error('A source link has an unexpected format.');
      safeLink(link.url, '');
    }
  }
}

function renderCards(id, entries) {
  validateCards(entries);
  const target = $(id);
  target.replaceChildren();
  for (const entry of entries) {
    const card = node('article', undefined, 'card');
    card.append(node('div', entry.category || entry.type || 'SOURCE', 'tag'));
    const heading = node('h3');
    heading.append(safeLink(entry.url, entry.title));
    card.append(heading, node('p', entry.description));
    if (entry.access) card.append(node('p', entry.access, 'fine'));
    if (entry.region) card.append(node('p', 'Market / coverage: ' + entry.region, 'fine'));
    if (entry.source_date) card.append(node('p', 'Source date: ' + entry.source_date, 'fine'));
    if (entry.verification) card.append(node('p', entry.verification, 'fine'));
    if (entry.provenance_url) card.append(safeLink(entry.provenance_url, 'Source / directory ↗'));
    for (const link of entry.resource_links || []) {
      const paragraph = node('p');
      paragraph.append(safeLink(link.url, link.title + ' ↗'));
      card.append(paragraph);
    }
    target.append(card);
  }
  if (!entries.length) target.append(node('p', 'No matches. Try a broader term.'));
}

document.querySelectorAll('[data-tab]').forEach(button => {
  button.addEventListener('click', () => {
    if (closed || closing) return;
    document.querySelectorAll('.panel').forEach(panel => { panel.hidden = panel.id !== button.dataset.tab; });
    document.querySelectorAll('[data-tab]').forEach(tab => tab.setAttribute('aria-pressed', String(tab === button)));
  });
});

function unloadMap() {
  $('mapFrame').removeAttribute('src');
  $('mapFrame').hidden = true;
  $('mapPlaceholder').hidden = false;
  $('mapDate').textContent = 'Map unloaded.';
}

action('closeApp', async () => {
  closing = true;
  ++initializationVersion;
  ++imageVersion;
  ++imageEditVersion;
  ++researchVersion;
  ++regionVersion;
  ++reviewVersion;
  initializing = false;
  imageLoading = false;
  cancelOnline();
  activeReview = null;
  for (const controller of requestControllers) controller.abort();
  unloadMap();
  $('cancelOnline').hidden = true;
  $('onlineStatus').hidden = true;
  $('status').textContent = 'Closing workspace…';
  syncControls();
  try {
    await api('close', {});
    closed = true;
    $('status').textContent = 'Workspace closed. You can close this browser tab.';
  } catch (error) {
    closing = false;
    status('Could not confirm that the workspace closed. Retry Close workspace. ' + error.message);
  } finally {
    closing = false;
    syncControls();
  }
}, true);

action('loadMap', () => {
  const latitude = Number($('latitude').value);
  const longitude = Number($('longitude').value);
  const scale = Number($('mapScale').value);
  if (!$('latitude').value || !$('longitude').value || !Number.isFinite(latitude) ||
      !Number.isFinite(longitude) || latitude < -85 || latitude > 85 || longitude < -180 || longitude > 180) {
    throw Error('Enter latitude −85 to 85 and longitude −180 to 180.');
  }
  const box = [Math.max(-180, longitude - scale), Math.max(-85, latitude - scale / 2),
    Math.min(180, longitude + scale), Math.min(85, latitude + scale / 2)].join(',');
  $('mapFrame').src = 'https://www.openstreetmap.org/export/embed.html?' +
    new URLSearchParams({bbox: box, layer: 'mapnik', marker: latitude + ',' + longitude});
  $('mapFrame').hidden = false;
  $('mapPlaceholder').hidden = true;
  $('mapDate').textContent = 'Online view requested ' + new Date().toLocaleString() +
    '. This is not the date the map was surveyed. If tiles do not load, use OpenStreetMap directly.';
});
action('unloadMap', unloadMap);
action('placeSearch', () => {
  const query = $('placeQuery').value.trim();
  if (!query) throw Error('Enter a place to search.');
  window.open('https://www.openstreetmap.org/search?' + new URLSearchParams({query}), '_blank', 'noopener,noreferrer');
});

function invalidateRegion() {
  ++regionVersion;
  $('mapInfo').replaceChildren();
  if (activeOnline?.kind === 'freshness') cancelOnline('Region changed. Check the selected region to get its download link.');
  syncControls();
}
$('regions').addEventListener('change', invalidateRegion);
action('loadRegions', async () => {
  invalidateRegion();
  status('Getting regions from Geofabrik…');
  await onlineLookup('regions', 'regions', {}, data => {
    if (!Array.isArray(data.regions) || data.regions.some(region =>
      !region || typeof region.id !== 'string' || typeof region.name !== 'string')) {
      throw Error('The publisher returned an unexpected region list.');
    }
    const placeholder = node('option', 'Choose a region');
    placeholder.value = '';
    $('regions').replaceChildren(placeholder);
    for (const region of data.regions) {
      const option = node('option', region.name);
      option.value = region.id;
      $('regions').append(option);
    }
    $('regions').value = '';
    regionCount = data.regions.length;
    status('Region catalog retrieved ' + data.retrieved_utc + (data.cached ? ' · cached' : '') + '.');
  });
}, true);
action('mapFreshness', async () => {
  const region = $('regions').value;
  if (!region) throw Error('Load and choose a region first.');
  invalidateRegion();
  const version = regionVersion;
  status('Checking publisher metadata…');
  await onlineLookup('freshness', 'map_freshness', {region}, data => {
    if (version !== regionVersion || $('regions').value !== region) return;
    if (data.id !== region || typeof data.name !== 'string') throw Error('The publisher response did not match the selected region.');
    const bytes = Number(data.bytes);
    const size = data.bytes != null && Number.isFinite(bytes) && bytes >= 0 ? (bytes / 1024 / 1024).toFixed(1) + ' MiB' : 'not supplied';
    const link = safeLink(data.url, 'Download ' + data.name + ' OSM PBF from Geofabrik ↗');
    $('mapInfo').replaceChildren(
      node('h3', data.name),
      node('p', 'Publisher file modified: ' + (data.file_modified || 'not supplied') + '. Size: ' + size + '. Checked ' + data.checked_utc + (data.cached ? ' · cached' : '') + '.'),
      node('p', data.note, 'fine'), link
    );
    status('Map download metadata checked for ' + data.name + '. No download has started.');
  });
}, true);

function copyCanvas(source, width = source.width, height = source.height) {
  const result = document.createElement('canvas');
  result.width = width;
  result.height = height;
  return result;
}
function resetFilters() {
  for (const id of ['brightness', 'contrast', 'saturation']) $(id).value = 100;
}
function applyFilters() {
  if (!base || closed || closing) return;
  ++imageEditVersion;
  canvas.width = base.width;
  canvas.height = base.height;
  const context = canvas.getContext('2d');
  context.filter = `brightness(${$('brightness').value}%) contrast(${$('contrast').value}%) saturate(${$('saturation').value}%)`;
  context.drawImage(base, 0, 0);
  context.filter = 'none';
  $('imageWidth').value = base.width;
  $('imageHeight').value = base.height;
  $('imageStatus').textContent = `${imageName} · ${base.width} × ${base.height} current pixels · local browser memory · original unchanged`;
}
$('imageFile').addEventListener('change', async () => {
  if (closed || closing) return;
  const version = ++imageVersion;
  ++imageEditVersion;
  const file = $('imageFile').files[0];
  imageLoading = !!file;
  syncControls();
  if (!file) return;
  status('Loading ' + file.name + ' in this browser…');
  let bitmap = null;
  try {
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 12 * 1024 * 1024) {
      throw Error('Choose PNG, JPEG or WebP up to 12 MiB.');
    }
    bitmap = await createImageBitmap(file);
    if (version !== imageVersion || closed || closing) return;
    if (bitmap.width * bitmap.height > 24000000 || bitmap.width > 8192 || bitmap.height > 8192) {
      throw Error('Choose an image up to 24 megapixels with each side no larger than 8192.');
    }
    const next = copyCanvas(bitmap);
    next.getContext('2d').drawImage(bitmap, 0, 0);
    if (original) original.close();
    original = bitmap;
    bitmap = null;
    base = next;
    imageName = file.name;
    resetFilters();
    applyFilters();
    status('Image loaded locally: ' + imageName + '.');
  } catch (error) {
    if (version === imageVersion && !closed && !closing) {
      status('Could not load ' + file.name + ': ' + error.message + (base ? ' The preview still shows ' + imageName + '.' : ''));
    }
  } finally {
    if (bitmap) bitmap.close();
    if (version === imageVersion) imageLoading = false;
    syncControls();
  }
});
for (const id of ['brightness', 'contrast', 'saturation']) $(id).addEventListener('input', () => {
  if (!imageLoading) applyFilters();
});
action('resizeImage', () => {
  const width = Number($('imageWidth').value);
  const height = Number($('imageHeight').value);
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 ||
      width > 8192 || height > 8192 || width * height > 24000000) {
    throw Error('Use whole dimensions from 1–8192, up to 24 megapixels.');
  }
  const next = copyCanvas(base, width, height);
  next.getContext('2d').drawImage(base, 0, 0, width, height);
  base = next;
  applyFilters();
});
action('rotateImage', () => {
  const next = copyCanvas(base, base.height, base.width);
  const context = next.getContext('2d');
  context.translate(next.width, 0);
  context.rotate(Math.PI / 2);
  context.drawImage(base, 0, 0);
  base = next;
  applyFilters();
});
action('cropImage', () => {
  const side = Math.min(base.width, base.height);
  const next = copyCanvas(base, side, side);
  next.getContext('2d').drawImage(base, (base.width - side) / 2, (base.height - side) / 2, side, side, 0, 0, side, side);
  base = next;
  applyFilters();
});
action('resetImage', () => {
  base = copyCanvas(original);
  base.getContext('2d').drawImage(original, 0, 0);
  resetFilters();
  applyFilters();
});
async function saveImage(type) {
  if (!base) throw Error('Choose an image first.');
  const version = imageEditVersion;
  const exported = copyCanvas(canvas);
  const context = exported.getContext('2d');
  if (type === 'jpeg') {
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, exported.width, exported.height);
  }
  context.drawImage(canvas, 0, 0);
  const blob = await new Promise(resolve => exported.toBlob(resolve, 'image/' + type, .92));
  if (closed || closing) return;
  if (version !== imageEditVersion) {
    status('Image changed while preparing the export. Save again to export the current preview.');
    return;
  }
  if (!blob) throw Error('Export failed.');
  download('prometheus-image.' + (type === 'jpeg' ? 'jpg' : 'png'), blob, 'image/' + type);
  status('Export created for ' + imageName + '. Your original is unchanged.');
}
action('savePng', () => saveImage('png'));
action('saveJpeg', () => saveImage('jpeg'));

function invalidateResearch() {
  ++researchVersion;
  lastResearch = null;
  $('researchResults').replaceChildren();
  if (activeOnline?.kind === 'research') cancelOnline('Search changed. Run the new search to get citations.');
  syncControls();
}
$('nasaQuery').addEventListener('input', invalidateResearch);
$('nasaType').addEventListener('change', invalidateResearch);
action('searchNasa', async () => {
  invalidateResearch();
  const query = $('nasaQuery').value.trim();
  const type = $('nasaType').value;
  const version = researchVersion;
  if (!query) throw Error('Enter a NASA search.');
  status('Searching the selected NASA archive…');
  await onlineLookup('research', type === 'earthdata' ? 'earthdata' : 'nasa',
    type === 'earthdata' ? {query} : {query, type}, data => {
      if (version !== researchVersion || $('nasaQuery').value.trim() !== query || $('nasaType').value !== type) return;
      renderCards('researchResults', data.results);
      $('researchResults').prepend(node('p',
        `Search: “${query}” · ${data.source} · retrieved ${data.retrieved_utc}${data.cached ? ' · cached' : ''}. ${data.note}`, 'fine'));
      lastResearch = {...data, query, collection: type};
      status(data.results.length + ' results returned for “' + query + '”.');
    });
}, true);
action('exportResearch', () => download('prometheus-nasa-citations.json', JSON.stringify(lastResearch, null, 2)));

function invalidateReview() {
  ++reviewVersion;
  lastReview = null;
  $('reviewResults').replaceChildren();
  if (activeReview) activeReview.controller.abort();
  activeReview = null;
  syncControls();
}
$('codeFile').addEventListener('change', invalidateReview);
action('reviewCode', async () => {
  invalidateReview();
  const file = $('codeFile').files[0];
  if (!file) throw Error('Select a source file.');
  if (file.size > 800000) throw Error('Choose a source file under 800 KB / 200,000 characters.');
  const operation = {version: reviewVersion, controller: new AbortController()};
  activeReview = operation;
  const isCurrent = () => !closed && !closing && activeReview === operation &&
    operation.version === reviewVersion && $('codeFile').files[0] === file;
  syncControls();
  status('Checking ' + file.name + ' locally…');
  try {
    const source = await file.text();
    if (!isCurrent()) return;
    const data = await api('review', {name: file.name, source}, operation.controller);
    if (!isCurrent()) return;
    if (data.file !== file.name || !Array.isArray(data.findings)) throw Error('The check returned an unexpected report.');
    const target = $('reviewResults');
    target.replaceChildren(node('h3', data.file), node('p', 'Checked ' + data.checked_utc, 'fine'), node('p', data.scope, 'callout'));
    if (data.truncated) target.append(node('p', 'Report limit reached: additional findings may be omitted.', 'callout'));
    for (const item of data.findings) {
      const card = node('article', undefined, 'card');
      card.append(node('h3', `Line ${item.line} · ${item.rule}`), node('p', item.message));
      target.append(card);
    }
    if (!data.findings.length) target.append(node('p', 'No patterns were flagged by these limited checks. This does not establish that the file is secure.'));
    lastReview = data;
    status('Local code triage complete for ' + file.name + '. Source was not saved.');
  } catch (error) {
    if (isCurrent() && error.name !== 'AbortError') status(error.message);
  } finally {
    if (activeReview === operation) activeReview = null;
    syncControls();
  }
}, true);
action('exportReview', () => download('prometheus-code-triage.json', JSON.stringify(lastReview, null, 2)));

function filter(group, query) {
  const terms = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
  return catalog[group].filter(entry => terms.every(term => JSON.stringify(entry).toLowerCase().includes(term)));
}
$('researchFilter').addEventListener('input', () => {
  if (catalog && !closed && !closing) renderCards('researchLinks', filter('research', $('researchFilter').value));
});
$('autoFilter').addEventListener('input', () => {
  if (!catalog || closed || closing) return;
  const entries = filter('automotive', $('autoFilter').value);
  renderCards('autoLinks', entries);
  $('autoCount').textContent = `${entries.length} of ${catalog.automotive.length} manufacturer/group portals · exact coverage depends on market and model year`;
});
action('exportAuto', () => download('prometheus-automotive-directory.json', JSON.stringify({
  generated_utc: new Date().toISOString(), coverage: catalog.coverage,
  automotive: catalog.automotive, registries: catalog.registries
}, null, 2)));

async function initialize() {
  const version = ++initializationVersion;
  initializing = true;
  initializationError = false;
  syncControls();
  status('Starting the local workspace…');
  const results = await Promise.allSettled([requestJson('/api/bootstrap'), requestJson('/catalog.json')]);
  if (version !== initializationVersion || closed || closing) return;
  const errors = [];
  const [sessionResult, catalogResult] = results;
  if (sessionResult.status === 'fulfilled' && typeof sessionResult.value?.token === 'string' && sessionResult.value.token) {
    token = sessionResult.value.token;
  } else {
    errors.push('The local session could not initialize.');
  }
  if (catalogResult.status === 'fulfilled') {
    try {
      const data = catalogResult.value;
      for (const group of ['maps', 'research', 'automotive', 'registries', 'connections']) validateCards(data?.[group]);
      catalog = data;
      renderCards('mapLinks', data.maps);
      renderCards('researchLinks', filter('research', $('researchFilter').value));
      renderCards('autoLinks', filter('automotive', $('autoFilter').value));
      renderCards('registryLinks', data.registries);
      renderCards('connectionLinks', data.connections);
      $('autoCount').textContent = `${data.automotive.length} manufacturer/group portals · ${data.coverage}`;
    } catch (error) {
      errors.push('The source directory has an unexpected format.');
    }
  } else {
    errors.push('The source directory could not load.');
  }
  initializing = false;
  initializationError = errors.length > 0;
  syncControls();
  status(errors.length ? errors.join(' ') + ' Use Retry initialization, or restart the launcher.' :
    'Ready. Online tools connect only when you choose them.');
}
action('retryInit', initialize, true);
syncControls();
void initialize();
