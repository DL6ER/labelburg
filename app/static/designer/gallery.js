// Templates: the saved labels and the ready-made ones in one gallery. The
// text typed at the top goes into their previews and six looks of its own;
// a label is filled in and printed without leaving, opened in the editor,
// used from anywhere by its link, tagged or removed.

import {
    $, $$, esc, icon, clone, debounce, toast, storeQuiet, S, C, L, sizeName, placed, fitsTape, allTags, hasTag, tagsOf,
    savedItem, demoItem, itemDesign, thumbUrl, drawThumb, preview, defaults, blanksOf, fieldType, blankChoices, valuesNow,
    printJob, entryName, saveLabel, loadSaved, postJSON, post, openLayer, closeLayer, templateOf, dateFormat, baseDesign, setWide, originOf, savedNow,
    sheetNote, untold, loadPrinted, keptSub, printKeptAgain, announceKept, keptLabel, IMAGE_GONE, blankRequest
} from './core.js';
import { perCopyOf } from './media.js';

let ctx = null;
// printed: the labels printed as the server lists them, null where it keeps none
const G = { cat: 'saved', fitsOnly: true, typed: '', typedCat: false, sel: null, printed: [] };

// When a saved label was saved, e.g. "today, 14:05", "Nov 3"
function savedWhen(mtime) {
    if (!mtime) return '';
    const d = new Date(mtime * 1000), now = new Date();
    if (d.toDateString() === now.toDateString()) return 'today, ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    return d.toLocaleDateString([], d.getFullYear() === now.getFullYear() ? { day: 'numeric', month: 'short' } : { day: 'numeric', month: 'short', year: 'numeric' });
}
export function savedSub(it) {
    const s = S.saved.find(x => x.name === it.id);
    const when = s ? savedWhen(s.mtime) : '';
    const what = placed(it.size) ? 'Made for ' + sizeName(it.size) : it.tags && it.tags.length ? it.tags.join(', ') : 'Saved label';
    return what + (when ? ', saved ' + when : '');
}

// --- The typed text ------------------------------------------------------------

const typedLines = () => G.typed.split('\n').map(t => t.trim()).filter(Boolean).map(t => t.replace(/\[([^\]\n]+)\]/g, (m, n) => templateOf(n)));
// The text typed goes into the blanks of a template, or into its lines if
// it has no blanks, as applyTyped() does
function applyTyped(item, design, values) {
    values = values || defaults({ design });
    const typed = typedLines();
    if (typed.length && ['demo', 'saved'].includes(item.kind)) {
        const blanks = blanksOf(design);
        if (blanks.length) {
            let k = 0;
            for (const b of blanks) if (fieldType({ design }, b) === 'text' && k < typed.length) values[b] = typed[k++];
        } else typed.forEach((t, i) => { if (design.text[i]) design.text[i].text = t; });
    }
    return { design, values };
}
// The six looks: the text typed as a label of its own
const LOOKS = [
    ['Big and simple', (lines, d) => { d.text = lines.map((t, i) => L(t, i ? 44 : 84, i === 0)); }],
    ['With date', (lines, d) => { d.text = lines.map((t, i) => L(t, i ? 40 : 70, i === 0)).concat([L(`{{datetime:${dateFormat()}}}`, 34)]); }],
    ['White on black', (lines, d) => {
        d.text = lines.length === 1 ? [L(` ${lines[0]} `, 76, true, 'center', { inverted: true })]
            : [L(` ${lines[0]} `, 44, true, 'center', { inverted: true })].concat(lines.slice(1).map(t => L(t, 64, true)));
    }],
    ['Framed', (lines, d) => {
        d.text = lines.map((t, i) => L(t, i ? 36 : 62, i === 0));
        Object.assign(d, { border_thickness: '5', border_roundness: '24', border_distance_x: '20', border_distance_y: '14', margin_top: '40', margin_bottom: '40', margin_left: '50', margin_right: '50' });
    }],
    ['With details', (lines, d) => {
        d.text = lines.map((t, i) => L(t, i ? 34 : 64, i === 0, 'left'));
        if (lines.length === 1) d.text.push(L(`{{datetime:${dateFormat()}}}`, 34, false, 'left'));
    }],
    ['With QR code', (lines, d) => {
        Object.assign(d, { print_type: 'qrcode_text', code_text: '', qrcode_size: '14', margin_left: '30', margin_right: '30' });
        setWide(d, true);
        d.text = lines.map((t, i) => L(t, i ? 60 : 100, i === 0, 'left'));
    }],
];
function lookItems() {
    const lines = typedLines();
    if (!lines.length) return [];
    return LOOKS.map(([name, make], i) => {
        const design = baseDesign(S.tape);
        make(lines, design);
        return { kind: 'look', id: 'look' + i, title: name, design, size: S.tape, sub: 'Your text' };
    });
}
// Ctrl Enter prints the text typed in the first look; one with blanks asks
// for them first
async function printFirstLook() {
    const look = lookItems()[0];
    if (!look) return;
    if (blanksOf(look.design).length) { showDetail(look, defaults({ design: look.design })); return; }
    const title = G.typed.split('\n').map(t => t.trim()).find(Boolean);
    const entry = await printJob({ title, design: look.design, values: {}, copies: 1, source: { kind: 'look', id: look.id } });
    if (!untold(entry)) toast(entry.state === 'printed' ? `${RESULT.printed} 1 label: ${entryName(entry)}` : `${RESULT[entry.state]}: ${entry.message}`, null, 6000);
}

// Templates whose name, group, tags or blanks share words with the text typed
function textMatches() {
    const words = typedLines().join(' ').replace(/\{\{[^}]+\}\}/g, ' ').toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(w => w.length >= 3);
    if (!words.length) return [];
    const pool = [].concat(S.saved.map(savedItem), S.demoList.filter(d => d.language === S.lang).map(d => Object.assign(demoItem(d), { group: d.catName })));
    const scored = [];
    for (const it of pool) {
        const hay = (it.title + ' ' + (it.group || '') + ' ' + (it.tags || []).join(' ')).toLowerCase();
        let score = 0;
        for (const w of words) if (hay.includes(w) || hay.includes(w.slice(0, Math.max(4, w.length - 2)))) score++;
        if (score) scored.push([score + (fitsTape(it.size) ? .5 : 0) + (it.kind === 'saved' ? .3 : 0), it]);
    }
    return scored.sort((a, b) => b[0] - a[0]).slice(0, 24).map(x => x[1]);
}

// --- The gallery ------------------------------------------------------------------

export function mountGallery(context) { ctx = context; }

export function openTemplates(cat) {
    if (cat) { G.cat = cat; G.typedCat = false; }
    G.sel = null;
    openLayer(`<div class="dialog wide tpl" role="dialog" aria-label="Templates">
        <div class="dialog-h"><h2>Templates</h2>
            <textarea class="in tpl-typed" id="tplText" rows="1" placeholder="What should the label say? Type it to see it in the templates" aria-label="Text for the templates" spellcheck="false">${esc(G.typed)}</textarea>
            <div class="tpl-bar" id="tplBar"></div><button class="iconbtn" type="button" data-close aria-label="Close">${icon('x', '')}</button></div>
        <div class="tpl-body"><nav class="tpl-nav" id="tplNav" aria-label="Label groups"></nav><div class="tpl-grid" id="tplGrid"></div><aside class="tpl-detail" id="tplDetail" hidden></aside></div></div>`, 'center',
    () => { if (/^#(templates|fill)/.test(location.hash)) history.replaceState(null, '', '#edit'); });
    const typed = $('#tplText');
    const grow = () => { typed.rows = Math.min(4, Math.max(1, typed.value.split('\n').length)); };
    grow();
    const later = debounce(() => { drawGrid(); if (G.sel) showDetail(G.sel); }, 250);
    typed.addEventListener('input', () => { G.typed = typed.value; G.typedCat = !!typedLines().length; grow(); later(); });
    typed.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); if (!e.repeat) printFirstLook(); } });
    drawTemplates();
    loadSaved().then(changed => { if (changed) drawTemplates(); });
    loadKept();
}
async function loadKept() {
    try {
        const told = await loadPrinted();
        G.printed = told.enabled ? told.entries : null;
    } catch (e) { return; }
    if (G.cat === 'printed' && !G.printed) G.cat = 'saved';
    drawTemplates();
}

function drawTemplates() {
    const nav = $('#tplNav');
    if (!nav) return;
    if (G.cat.startsWith('tag:') && !S.saved.some(s => hasTag(s, G.cat.slice(4)))) G.cat = 'saved';
    const demos = S.demoList.filter(d => d.language === S.lang);
    const counted = list => G.fitsOnly ? list.filter(d => fitsTape(d.label_size_id)).length : list.length;
    const on = id => !G.typedCat && G.cat === id;
    const btn = (id, name, n, cls = '') => `<button type="button" data-cat="${esc(id)}" class="${on(id) ? 'on' : ''}${esc(cls)}"><span>${esc(name)}</span><small>${esc(n)}</small></button>`;
    nav.innerHTML = (G.typedCat ? `<button type="button" class="on" data-cat="typed"><span>For your text</span></button>` : '')
        + btn('saved', 'Your labels', S.saved.length)
        + allTags().map(t => btn('tag:' + t, t, S.saved.filter(s => hasTag(s, t)).length, ' tag')).join('')
        + (G.printed ? btn('printed', 'Printed', G.printed.length) : '')
        + '<div class="nav-h">Ready-made</div>' + btn('all', 'All', counted(demos))
        + S.demos.map(c => btn(c.id, c.name, counted(demos.filter(d => d.cat === c.id)))).join('');
    $$('[data-cat]', nav).forEach(b => b.onclick = () => {
        if (b.dataset.cat === 'typed') return;
        G.cat = b.dataset.cat;
        G.typedCat = false;
        history.replaceState(null, '', '#templates/' + encodeURIComponent(G.cat));
        drawTemplates();
    });
    $('#tplBar').innerHTML = `<button type="button" class="pill${G.fitsOnly ? ' on' : ''}" id="tplFits" aria-pressed="${G.fitsOnly}">${icon('check')}Made for ${esc(sizeName(S.tape))}</button>
        <select class="in" id="tplLang" aria-label="Language of the ready-made labels"><option value="en"${S.lang === 'en' ? ' selected' : ''}>English</option><option value="de"${S.lang === 'de' ? ' selected' : ''}>Deutsch</option></select>`;
    $('#tplFits').onclick = () => { G.fitsOnly = !G.fitsOnly; drawTemplates(); };
    $('#tplLang').onchange = e => { S.lang = e.target.value; storeQuiet('designer_lang', S.lang); drawTemplates(); };
    drawGrid();
}

function gridItems() {
    if (G.typedCat) return lookItems().concat(textMatches());
    const demos = S.demoList.filter(d => d.language === S.lang);
    let items;
    if (G.cat === 'saved') items = S.saved.map(savedItem);
    else if (G.cat === 'printed') items = printedItems();
    else if (G.cat.startsWith('tag:')) items = S.saved.filter(s => hasTag(s, G.cat.slice(4))).map(savedItem);
    else {
        items = (G.cat === 'all' ? demos : demos.filter(d => d.cat === G.cat)).map(demoItem);
        if (G.fitsOnly) items = items.filter(i => fitsTape(i.size)).concat(items.filter(i => !fitsTape(i.size)));
    }
    return items.slice(0, 240);
}

// The labels printed, newest first, as Settings list them
const printedItems = () => (G.printed || []).map(h => ({ kind: 'printed', id: h.id, title: h.title, sub: keptSub(h), entry: h }));

// Saved labels and those with the text typed are drawn here, ready-made
// ones as typed and printed ones come from the server
const typedThumb = item => (design, values) => applyTyped(item, clone(design), values);
let shownItems = [];
function drawGrid() {
    const grid = $('#tplGrid');
    if (!grid) return;
    if ($('#tplNav [data-cat="typed"]') ? !G.typedCat : G.typedCat) { drawTemplates(); return; }
    const items = shownItems = gridItems(), typed = typedLines().length > 0;
    const shot = (it, i) => it.kind === 'printed' ? (it.entry.preview ? `<img loading="lazy" alt="" src="${esc(it.entry.preview)}">` : '')
        : it.kind === 'demo' && !typed ? `<img loading="lazy" alt="" src="${esc(thumbUrl(it))}">` : `<img alt="" data-thumb="${esc(i)}">`;
    grid.innerHTML = items.length ? items.map((it, i) => `<button type="button" class="thumb${G.sel && G.sel.kind === it.kind && G.sel.id === it.id ? ' sel' : ''}" data-i="${esc(i)}"><span class="bed">${shot(it, i)}</span>
        <b>${esc(it.title)}</b><small>${esc(it.kind === 'saved' ? savedSub(it) : placed(it.size) ? 'Made for ' + sizeName(it.size) : it.sub || '')}</small></button>`).join('')
        : `<p class="muted pad">${G.typedCat ? 'No template matches the text.' : G.cat === 'saved' ? 'Labels you save show up here.' : G.cat === 'printed' ? 'Labels you print show up here.' : 'No labels here.'}</p>`;
    grid.scrollTop = 0;
    $$('[data-thumb]', grid).forEach(img => { const it = items[+img.dataset.thumb]; drawThumb(img, it, typed ? typedThumb(it) : undefined); });
    $$('[data-i]', grid).forEach(b => b.onclick = () => showDetail(items[+b.dataset.i]));
}

// --- A label of the gallery ---------------------------------------------------------

const RESULT = { printed: 'Printed', failed: 'Not printed', unknown: 'Maybe not printed' };
function selectItem(item) {
    const box = $('#tplDetail');
    if (!box) return null;
    G.sel = item;
    $$('#tplGrid .thumb').forEach(b => b.classList.toggle('sel', shownItems[+b.dataset.i] === item));
    $('.tpl').classList.add('has-detail');
    box.hidden = false;
    return box;
}
const copiesHTML = n => `<span class="az"><button type="button" class="azb" data-cp="-1" aria-label="One copy less">${icon('minus', '')}</button><input class="anum num" id="tplCopies" value="${esc(n)}" inputmode="numeric" aria-label="Copies"><button type="button" class="azb" data-cp="1" aria-label="One copy more">${icon('plus', '')}</button></span>`;
const copiesNow = () => Math.min(100, Math.max(1, parseInt($('#tplCopies').value, 10) || 1));
const printedDone = (entry, kept) => { announceKept(entry, kept); loadKept(); };
// The question of printing again in the panel, Templates stay open
function askHere(same, next, choose) {
    const box = $('#tplAsk');
    if (!box) { choose(null); return; }
    box.innerHTML = `<b>Print again</b><span class="sp"></span><button class="btn" type="button" id="againNext">${esc(next)}</button>
        <button class="btn primary" type="button" id="againSame">${esc(same)}</button><button class="iconbtn" type="button" id="againNot" aria-label="Close">${icon('x', '')}</button>`;
    box.hidden = false;
    const answer = asBefore => { box.hidden = true; box.innerHTML = ''; choose(asBefore); };
    $('#againSame').onclick = () => answer(true);
    $('#againNext').onclick = () => answer(false);
    $('#againNot').onclick = () => answer(null);
    $('#againSame').focus();
}
// A picture of another program printed, as it was printed: printed again
function showPrinted(item) {
    const box = selectItem(item), h = item.entry;
    if (!box) return;
    box.innerHTML = `<div class="dialog-h"><h3>${esc(h.title)}</h3><span class="sp"></span><button class="iconbtn" type="button" data-unsel aria-label="Close">${icon('x', '')}</button></div>
        <div class="tpl-shot">${h.preview ? `<img alt="Preview" id="tplShot" src="${esc(h.preview)}">` : ''}</div>
        <p class="muted">${esc(item.sub)}</p>
        <div class="tpl-print">${copiesHTML(h.copies || 1)}<button class="btn primary" type="button" id="tplPrint"${h.again ? '' : ` disabled title="${esc(IMAGE_GONE)}"`}>${icon('refresh')}Print again</button></div>`;
    $('[data-unsel]', box).onclick = hideDetail;
    $$('[data-cp]', box).forEach(b => b.onclick = () => { const c = $('#tplCopies'); c.value = Math.min(100, Math.max(1, (parseInt(c.value, 10) || 1) + +b.dataset.cp)); });
    $('#tplPrint').onclick = () => printKeptAgain(h, printedDone, { copies: copiesNow() });
}
async function showDetail(item, values = null) {
    if (item.kind === 'printed' && !item.entry.open) { showPrinted(item); return; }
    const box = selectItem(item);
    if (!box) return;
    box.innerHTML = `<div class="dialog-h"><h3>${esc(item.title)}</h3><span class="sp"></span><button class="iconbtn" type="button" data-unsel aria-label="Close">${icon('x', '')}</button></div><p class="muted">Loading...</p>`;
    $('[data-unsel]', box).onclick = hideDetail;
    let design;
    try {
        // A label printed comes as it was printed, with the values of its blanks
        if (item.kind === 'printed' && !item.opened) item.opened = await keptLabel(item.entry);
        design = clone(item.kind === 'printed' ? item.opened.design : await itemDesign(item));
    } catch (e) { box.innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
    if (G.sel !== item) return;
    const starts = defaults({ design });
    const work = applyTyped(item, design, values || item.values || Object.assign({}, starts));
    const blanks = blanksOf(work.design), saved = item.kind === 'saved', before = item.kind === 'printed' ? item.entry : null;
    box.innerHTML = `<div class="dialog-h"><h3>${esc(item.title)}</h3><span class="sp"></span><button class="iconbtn" type="button" data-unsel aria-label="Close">${icon('x', '')}</button></div>
        <div class="tpl-shot"><img alt="Preview" id="tplShot"></div>
        ${placed(work.design.label_size) ? `<p class="mnote">${icon('warn')}<span>Made for ${esc(sizeName(work.design.label_size))}, prints on the ${esc(sizeName(S.tape))}</span></p>` : ''}
        ${blanks.length ? `<div class="tpl-fields" id="tplFields">${blanks.map((b, i) => blankField({ design: work.design }, b, work.values[b], i)).join('')}</div>` : ''}
        <div class="tpl-print">${copiesHTML(before ? before.copies || 1 : 1)}
            <button class="btn primary" type="button" id="tplPrint"${before && !before.again ? ` disabled title="${esc(IMAGE_GONE)}"` : ''}>${icon(before ? 'refresh' : 'printer')}${before ? 'Print again' : blanks.length ? 'Fill in and print' : 'Print'}</button></div>
        ${before ? '<div class="tpl-ask" id="tplAsk" role="group" aria-label="Print again" hidden></div>' : ''}
        <p class="mnote sheetnote" id="tplSheet" hidden></p>
        <div class="tpl-acts">
            <button class="btn soft" type="button" id="tplOpen">${icon('type')}Open in the editor</button>
            ${saved ? `<button class="btn ghost" type="button" id="tplLinks">${icon('qr')}Use from anywhere</button>
                <button class="btn ghost" type="button" id="tplTags">${icon('tag')}Tags</button>
                <button class="btn ghost danger" type="button" id="tplRemove">${icon('trash')}Remove</button>` : ''}
        </div>
        <div id="tplMore"></div>`;
    $('[data-unsel]', box).onclick = hideDetail;
    // Only the answer to the last preview asked is shown, earlier ones may come later
    let asked = 0;
    const shot = () => {
        const mine = ++asked;
        return perCopyOf(work.design).then(n => preview(work.design, work.values, copiesNow(), n)).then(p => shotDone(p, mine), () => { });
    };
    const shotDone = (p, mine) => {
        const img = $('#tplShot'), note = $('#tplSheet');
        if (!img || G.sel !== item || mine !== asked) return;
        img.src = p.url;
        note.innerHTML = sheetNote(p.sheet);
        note.hidden = !note.innerHTML;
    };
    shot();
    const later = debounce(shot, 250);
    // One print at a time until the server took it: a key held down or a
    // second click waits for that, the next name may follow while it waits
    let printing = false;
    const print = async () => {
        if (printing) return;
        printing = true;
        let taken = false;
        const free = () => { if (!taken) { taken = true; printing = false; } };
        try { await printOnce(free); } finally { free(); }
    };
    const printOnce = async accepted => {
        const copies = copiesNow();
        const now = valuesNow(work.values, starts, { design: work.design });
        // Printed again by the server with the values and copies set here
        if (before) {
            const opts = { copies, blanks: blankRequest(work.design, now).blanks, design: work.design, ask: askHere, accepted,
                title: entryName({ design: work.design, values: now, title: before.title }) };
            await printKeptAgain(before, printedDone, opts);
            return;
        }
        const first = $$('#tplFields [data-blank]').find(inp => fieldType({ design: work.design }, inp.dataset.blank) === 'text');
        const typed = first && first.value;
        const entry = await printJob({ title: item.title, design: work.design, values: now, copies, source: { kind: item.kind, id: item.id } }, null, undefined, () => {
            accepted();
            // Ready for the next one: the first text blank is emptied, unless typed into meanwhile
            if (saved && first && first.isConnected && first.value === typed) { first.value = ''; work.values[first.dataset.blank] = ''; first.focus(); later(); }
        });
        if (!untold(entry)) toast(entry.state === 'printed' ? `${RESULT.printed} ${copies === 1 ? '1 label' : copies + ' labels'}: ${entryName(entry)}` : `${RESULT[entry.state]}: ${entry.message}`, null, 6000);
    };
    $$('#tplFields [data-blank]').forEach(inp => {
        inp.addEventListener(inp.tagName === 'SELECT' ? 'change' : 'input', () => { work.values[inp.dataset.blank] = inp.value; later(); });
        inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); if (!e.repeat) print(); } });
    });
    $$('[data-cp]', box).forEach(b => b.onclick = () => { const c = $('#tplCopies'); c.value = Math.min(100, Math.max(1, (parseInt(c.value, 10) || 1) + +b.dataset.cp)); later(); });
    $('#tplCopies').addEventListener('input', later);
    $('#tplSheet').onclick = e => {
        const fill = e.target.closest('[data-fill]');
        if (!fill) return;
        $('#tplCopies').value = fill.dataset.fill;
        shot();
    };
    $('#tplPrint').onclick = print;
    $('#tplOpen').onclick = async () => {
        let open = item, design = work.design;
        if (saved) {
            try {
                const now = await savedNow(item.id);
                open = now.item;
                design = applyTyped(open, clone(now.design), clone(work.values)).design;
            } catch (e) { toast(e.message); return; }
        }
        closeLayer(true);
        // A label printed opens as the saved label it was printed from
        const origin = before ? item.opened.origin : originOf(open.kind, open.id, open.mtime);
        ctx.openEditor({ title: item.kind === 'look' ? typedLines()[0] || item.title : item.title, design, values: work.values, origin });
    };
    if (saved) {
        $('#tplLinks').onclick = () => linksPanel(item, work.design);
        $('#tplTags').onclick = () => tagsPanel(item);
        $('#tplRemove').onclick = () => removeLabel(item);
    }
    const first = $('#tplFields input');
    if (first && values) first.focus();
}
function hideDetail() {
    G.sel = null;
    const box = $('#tplDetail');
    if (box) { box.hidden = true; box.innerHTML = ''; }
    const tpl = $('.tpl');
    if (tpl) tpl.classList.remove('has-detail');
    $$('#tplGrid .thumb').forEach(b => b.classList.remove('sel'));
}

function blankField(item, name, value, i) {
    const kind = fieldType(item, name), id = 'tplF' + i;
    const head = `<label class="k" for="${esc(id)}">${esc(name)}</label>`;
    if (kind === 'choice') {
        const choices = blankChoices(item, name);
        return `<div>${head}<select class="in" id="${esc(id)}" data-blank="${esc(name)}">${choices.map(c => `<option${c === value ? ' selected' : ''}>${esc(c)}</option>`).join('')}</select></div>`;
    }
    return `<div>${head}<input class="in" id="${esc(id)}" type="${esc(kind)}" data-blank="${esc(name)}" value="${esc(value)}" placeholder="Fill in ${esc(name)}" autocomplete="off"></div>`;
}

// --- Links, tags and removing a saved label --------------------------------------------

function linksPanel(item, design) {
    const box = $('#tplMore');
    const blanks = blanksOf(design);
    const url = `${location.origin}${location.pathname}#fill/${encodeURIComponent(item.id)}`;
    const sample = !blanks.length ? '' : fieldType({ design }, blanks[0]) === 'choice' ? blankChoices({ design }, blanks[0])[0] || '' : blanks[0] === 'What' ? 'Chili' : 'Example';
    const example = blanks.length ? `${url}?${encodeURIComponent(blanks[0])}=${encodeURIComponent(sample)}` : url;
    const sticker = setWide(Object.assign(baseDesign(S.tape), {
        print_type: 'qrcode_text', code_text: url, qrcode_size: '12', margin_left: '30', margin_right: '30',
        text: [L('Scan to print a', 56, false, 'left'), L(item.title + ' label', 84, true, 'left')]
    }), true);
    box.innerHTML = `<div class="tpl-panel" id="tplLinksBox"><h4>Use from anywhere</h4>
        <label class="k" for="tplLink">Link${blanks.length ? `, <b>?${esc(blanks[0])}=...</b> fills in a blank` : ''}</label>
        <span class="row"><input class="in" id="tplLink" readonly value="${esc(example)}"><button class="btn soft sm" type="button" id="tplCopy">Copy</button></span>
        <p class="muted small">For bookmarks, NFC tags and Stream Deck buttons; on the phone "Add to Home screen" opens straight into this label.</p>
        <div class="tpl-shot small"><img alt="Sticker" id="tplSticker"></div>
        <button class="btn soft sm" type="button" id="tplPrintSticker">${icon('printer')}Print the QR sticker</button></div>`;
    preview(sticker, {}).then(p => { const img = $('#tplSticker'); if (img) img.src = p.url; }, () => { });
    $('#tplCopy').onclick = async () => {
        try { await navigator.clipboard.writeText($('#tplLink').value); toast('Link copied'); } catch (e) { $('#tplLink').select(); toast('Press Ctrl C to copy'); }
    };
    $('#tplPrintSticker').onclick = async () => {
        const entry = await printJob({ title: item.title + ' sticker', design: sticker, values: {}, copies: 1, source: { kind: 'sticker', id: item.id } });
        if (!untold(entry)) toast(entry.state === 'printed' ? 'Printed the sticker' : `${RESULT[entry.state]}: ${entry.message}`);
    };
}

function tagsPanel(item) {
    const box = $('#tplMore'), saved = S.saved.find(x => x.name === item.id), tags = saved ? saved.tags : [];
    const known = allTags();
    box.innerHTML = `<form class="tpl-panel form" id="tplTagsForm" novalidate><h4>Tags</h4>
        <input class="in" id="tplTagsIn" value="${esc(tags.join(', '))}" placeholder="e.g. Kitchen, Freezer" autocomplete="off" aria-label="Tags">
        ${known.length ? `<div class="pills">${known.map(t => `<button type="button" class="pill${hasTag({ tags }, t) ? ' on' : ''}" data-tag="${esc(t)}">${esc(t)}</button>`).join('')}</div>` : ''}
        <div class="acts"><button class="btn primary sm" type="submit">Save the tags</button></div></form>`;
    const input = $('#tplTagsIn');
    $$('[data-tag]', box).forEach(b => b.onclick = () => {
        const t = tagsOf(input.value), had = hasTag({ tags: t }, b.dataset.tag);
        input.value = (had ? t.filter(x => x.toLowerCase() !== b.dataset.tag.toLowerCase()) : t.concat(b.dataset.tag)).join(', ');
        b.classList.toggle('on', !had);
    });
    input.focus();
    $('#tplTagsForm').onsubmit = async e => {
        e.preventDefault();
        let r;
        try { r = await postJSON('/repository/tags', { name: item.id, tags: tagsOf(input.value) }); } catch (err) { toast(err.message); return; }
        const data = await r.json().catch(() => ({}));
        if (!r.ok || !data.success) { toast(data.message || 'The tags could not be saved'); return; }
        await loadSaved();
        const s = S.saved.find(x => x.name === item.id);
        toast(`Tags of "${item.title}" saved`);
        drawTemplates();
        if (s) showDetail(Object.assign(savedItem(s), { design: item.design }));
    };
}

async function removeLabel(item) {
    const tags = (S.saved.find(x => x.name === item.id) || item).tags;
    const f = new FormData();
    f.append('name', item.id);
    let kept, r;
    try {
        kept = clone(await itemDesign(item));
        r = await post('/repository/delete', f);
    } catch (e) { toast(e.message); return; }
    if (!r.ok) { toast('The label could not be removed'); return; }
    await loadSaved();
    hideDetail();
    drawTemplates();
    const undo = async () => {
        try { await saveLabel(item.title, kept, item.id, tags); } catch (e) { toast(e.message); }
        drawTemplates();
    };
    toast(`Removed "${item.title}"`, { label: 'Undo', run: undo }, 8000);
}

// #fill/<name>?Blank=value: the saved label chosen in the gallery, its
// blanks filled in from the address
export async function openFill(name, params) {
    const s = S.saved.find(x => x.name === name);
    if (!s) { toast('This label is not saved on this server'); openTemplates('saved'); return; }
    const item = savedItem(s);
    let design;
    try { design = await itemDesign(item); } catch (e) { toast(e.message); return; }
    const values = defaults({ design });
    for (const [k, v] of params.entries()) {
        if (!blanksOf(design).includes(k)) continue;
        if (fieldType({ design }, k) === 'choice' && !blankChoices({ design }, k).includes(v)) { toast(`"${v}" is none of the choices for ${k}.`); continue; }
        values[k] = v;
    }
    G.typed = '';
    openTemplates('saved');
    showDetail(item, values);
}
