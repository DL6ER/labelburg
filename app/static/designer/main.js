// The label designer: the top bar, the printer chip, search, templates,
// saving and the views. The editor itself is in editor.js, the text field
// in field.js, what they share in core.js.

import {
    $, $$, esc, icon, S, C, API, toast, own, load, storeQuiet, refreshPrinters, tapeState, printerState, printerLabel, printerName, isDemo, sizeShort,
    sizeName, rollOf, asksLength, unsizedRoll, ASK_LENGTH, tapeKnown, tapeLeft, loadDemos, loadSaved, demoItem, savedItem, itemDesign, thumbUrl, drawThumb, detectTape, fitsTape, placed,
    allTags, tagsOf, hasTag, openLayer, closeLayer, layerOpen, entryName, savedAs, thePrinter, L, isEmptyDesign, originOf, carryOverLabelSettings,
    loadLabelSettings, savedNow, forgetGoneKeys, printAgain, printedSub, followPrinter, resolvePending, maxCopies, onTape
} from './core.js';
import { calibrationHTML, bindCalibration } from './calibration.js';
import { renderEditor, openEditor, stepHistory, printNow, saveNow, printerChanged, drawPrinter, hooks, editorUI, newEdit, setEdit, loadEdit, copiesChanged } from './editor.js';
import { closePop } from './field.js';
import { mountGallery, openTemplates, openFill, savedSub } from './gallery.js';
import { renderSettings, redrawSettings, redrawLabelSettings } from './settings.js';
import { mountPhone, phone } from './phone.js';
import { mountQueue, followQueue } from './queue.js';

// Views other parts of the page register, e.g. the settings
export const views = {};
views.settings = el => renderSettings(el, { chip: renderChip, refresh, theme: setTheme });

// --- Routing ---------------------------------------------------------------------

const viewOf = () => (location.hash.replace(/^#/, '').split(/[/?]/)[0]) || 'edit';
function route() {
    closeLayer(true);
    closePop();
    const view = viewOf();
    $$('#tabs a').forEach(a => a.classList.toggle('on', a.dataset.tab === (view === 'settings' ? 'settings' : 'edit')));
    // On the phone the gear is the way to the settings and back
    $('#gear').classList.toggle('on', view === 'settings');
    $('#gear').href = view === 'settings' ? '#edit' : '#settings';
    $('#gear').title = view === 'settings' ? 'Back to the editor' : 'Settings';
    document.body.dataset.view = view === 'settings' ? 'settings' : 'edit';
    if (view === 'settings') {
        $('#view').innerHTML = '';
        views.settings($('#view'));
        return;
    }
    renderEditor();
    // #templates/<group> and #fill/<name>?Blank=value
    const [path, query] = location.hash.replace(/^#/, '').split('?');
    const arg = path.split('/').slice(1).join('/');
    let decoded = arg;
    try { decoded = decodeURIComponent(arg); } catch (e) { }
    if (view === 'templates') openTemplates(decoded || undefined);
    else if (view === 'fill' && decoded) openFill(decoded, new URLSearchParams(query || ''));
}

// --- The printer chip ------------------------------------------------------------------

function renderChip() {
    const st = printerState(S.printer);
    const chip = $('#printerChip');
    const short = asksLength() ? unsizedRoll(S.printer) : sizeShort(S.tape);
    chip.innerHTML = `<span class="d ${esc(st.cls)}"></span><b>${esc(S.printer ? printerLabel(S.printer) : 'No printer')}</b>${S.printer ? `<small>${esc(short)}</small>` : ''}`;
    const tape = asksLength() ? `${unsizedRoll(S.printer)}, ${ASK_LENGTH}`
        : [sizeName(S.tape), rollOf(S.printer)].filter(Boolean).join(', ') + (tapeKnown() ? '' : ', the printer does not say');
    chip.title = `${S.printer ? printerLabel(S.printer) + ': ' : ''}${st.text}, ${tape}`;
}

// The printer chosen in this browser that is not there: said once, until it
// is back
let goneSaid = null, searchAgain = null;
async function refresh(look = false) {
    const before = tapeState();
    await refreshPrinters(look);
    // While the server looks for printers on Bluetooth, asked again soon: one
    // it finds shows without waiting for the next 20 s
    clearTimeout(searchAgain);
    searchAgain = S.searching ? setTimeout(() => refresh(), 3000) : null;
    const chosen = load(own('designer_printer'), null);
    const there = S.printers.some(p => p.path === chosen);
    if (chosen && !there && S.printer && goneSaid !== chosen) {
        goneSaid = chosen;
        toast(`The printer ${chosen.replace('file://', '')} is not connected, labels print on ${thePrinter(S.printer)} (${S.printer.path.replace('file://', '')})`);
    }
    if (there) goneSaid = null;
    shown(before);
}
// The status of the printers shown where it is, the label placed anew on
// another tape
function shown(before) {
    renderChip();
    drawPrinter();
    if ($('.pp')) drawPrinters();
    if (tapeState() !== before) printerChanged();
    redrawSettings();
    followQueue();
}
// Every 20 s from a tab that is shown: one left in the background keeps no
// printer busy, e.g. a B1 its Bluetooth connection. Shown again it asks at once.
function poll() {
    if (!document.hidden) refresh();
}
// The printer chosen every 2 s where that costs nothing, e.g. one on USB:
// another roll or a lost connection shows at once
async function follow() {
    const before = tapeState();
    if (await followPrinter()) shown(before);
}

function drawPrinters() {
    const box = $('.pp');
    if (!box) return;
    const list = S.printers.map((p, k) => {
        const st = printerState(p);
        const on = S.printer === p;
        // A printer that cannot be reached says only that, its tape is unknown
        const gone = p.status_type === 'Error';
        const t = p.tape || {};
        const asks = !gone && asksLength(p);
        const tape = gone || !t.id ? '' : isDemo(p) ? sizeName(t.id) : t.known ? `${sizeName(t.id)} ${t.manual ? 'set by hand' : 'in the printer'}`
            : asks ? `${unsizedRoll(p)}, ${ASK_LENGTH}` : (t.candidates || []).length ? `measures labels ${t.unknown}` : t.unknown ? `reports ${t.unknown} tape` : 'does not say which tape it has';
        return `<button class="prow${on ? ' on' : ''}" type="button" data-p="${esc(k)}"${gone ? ` title="${esc(st.text)}"` : ''}><span class="d ${esc(st.cls)}"></span>
            <span class="grow"><b>${esc(printerLabel(p))}</b><small>${esc([p.name ? printerName(p) : '', gone ? 'Cannot be reached' : st.text, tape, gone || asks ? '' : rollOf(p), tapeLeft(p), p.battery != null ? `Battery ${p.battery}%` : ''].filter(Boolean).join(', '))}</small>
                ${p.weak_connection ? '<small class="warn">The Wi-Fi connection seems weak, try moving the printer closer to the Wi-Fi router.</small>' : ''}
                ${p.battery_low ? '<small class="warn">The battery is low, charge the printer.</small>' : ''}</span>
            ${on ? icon('check') : ''}</button>`;
    }).join('') || '<p class="muted">No printer answered.</p>';
    box.innerHTML = `<h4>Printers</h4><div class="plist">${list}</div>${S.printerHints.map(h => `<p class="muted small">${esc(h)}</p>`).join('')}
        ${calibrationHTML(S.printer)}
        <div class="pp-acts"><button class="btn ghost" type="button" id="look">${icon('search')}Look for printers</button>
        <a class="btn ghost" href="#settings/printers" id="toSettings">Printer settings ${icon('arrow')}</a></div>`;
    $$('[data-p]', box).forEach(b => b.onclick = () => {
        const before = tapeState();
        S.printer = S.printers[+b.dataset.p];
        storeQuiet(own('designer_printer'), S.printer.path);
        detectTape();
        renderChip();
        drawPrinters();
        followQueue();
        if (tapeState() !== before) printerChanged();
    });
    $('#look').onclick = async () => { $('#look').disabled = true; await refresh(true); };
    $('#toSettings').onclick = () => closeLayer(true);
    if (S.printer) bindCalibration(box, S.printer, () => shown(tapeState()));
}
function openPrinters() {
    if (phone()) openLayer('<div class="pp pop-card" role="dialog" aria-label="Printers"></div>', 'sheet');
    else {
        const r = $('#printerChip').getBoundingClientRect();
        openLayer(`<div class="pp pop-card" role="dialog" aria-label="Printers" style="top:${esc(r.bottom + 8)}px;right:${esc(Math.max(8, innerWidth - r.right))}px"></div>`, 'clear');
    }
    drawPrinters();
}

// --- Search -----------------------------------------------------------------------------

// Run from the editor: opened first where another view is shown
function inEditor(fn) {
    if (viewOf() === 'edit' && $('#stage')) { fn(); return; }
    addEventListener('hashchange', () => fn(), { once: true });
    location.hash = 'edit';
}
function themeTo(mode) {
    setTheme(mode);
    if (viewOf() === 'settings') route();
}
// Typed after ">" in the search
const COMMANDS = [
    { name: 'New label', icon: 'plus', run: () => inEditor(() => $('#newLabel').click()) },
    { name: 'Templates', icon: 'grid', run: () => inEditor(() => openTemplates()) },
    { name: 'Go to the editor', icon: 'type', run: () => { location.hash = 'edit'; } },
    { name: 'Printer and settings', icon: 'printer', run: () => { location.hash = 'settings/printers'; } },
    { name: 'Printed labels', icon: 'clock', run: () => { location.hash = 'settings/printed'; } },
    { name: 'Dark mode', icon: 'moon', run: () => themeTo('dark') },
    { name: 'Light mode', icon: 'sun', run: () => themeTo('light') },
    { name: 'Follow the system for dark mode', icon: 'contrast', run: () => themeTo(null) },
];

function openSearch() {
    openLayer(`<div class="palette" role="dialog" aria-label="Search">
        <div class="q-in">${icon('search')}<input id="palQ" placeholder="Search labels, or type > for commands" autocomplete="off" enterkeyhint="go"><span class="kbd">Esc</span></div>
        <div class="res-list" id="palRes"></div>
        <div class="pal-foot"><span class="keys"><span class="kbd">Up</span> <span class="kbd">Down</span> choose</span><span class="keys"><span class="kbd">Enter</span> <span id="palEnter">open</span></span><span>Type <b>&gt;</b> for commands</span></div></div>`, 'top');
    const q = $('#palQ');
    let results = [], sel = 0;
    const draw = () => {
        const text = q.value.trim().toLowerCase();
        const m = s => !text || s.toLowerCase().includes(text);
        const groups = text.startsWith('>')
            ? [['Commands', COMMANDS.filter(c => c.name.toLowerCase().includes(text.slice(1).trim())).map(c => ({ kind: 'command', title: c.name, command: c }))]]
            : [
                ['Saved labels', S.saved.filter(x => m(x.title + ' ' + x.tags.join(' '))).slice(0, 6).map(savedItem)],
                ['Printed before', (() => { const seen = new Set(); return S.history.filter(h => { const n = entryName(h); if (h.state === 'printing' || seen.has(n) || !m([n, h.title, ...Object.values(h.values || {})].join(' '))) return false; seen.add(n); return true; }).slice(0, text ? 4 : 3).map(h => ({ kind: 'printed', id: h.id, title: entryName(h), sub: printedSub(h), entry: h, size: h.design.label_size })); })()],
                ['Templates', text ? S.demoList.filter(d => d.language === S.lang && m(d.title + ' ' + d.catName)).sort((a, b) => fitsTape(b.label_size_id) - fitsTape(a.label_size_id)).slice(0, 6).map(demoItem) : []],
            ];
        results = [];
        let html = '';
        for (const [name, list] of groups) {
            if (!list.length) continue;
            html += `<div class="grp-h">${esc(name)}</div>`;
            for (const it of list) {
                const i = results.push(it) - 1;
                if (it.kind === 'command') {
                    html += `<button class="res cmd" type="button" data-r="${esc(i)}"><span class="ico">${icon(it.command.icon, '')}</span><span class="grow"><b>${esc(it.title)}</b></span><span class="act">run</span></button>`;
                    continue;
                }
                const sub = it.kind === 'saved' ? savedSub(it) : (it.sub || '') + (placed(it.size) ? ', made for ' + sizeName(it.size) : '');
                html += `<button class="res" type="button" data-r="${esc(i)}"><span class="ico">${it.kind === 'printed' ? icon('refresh') : it.kind === 'saved' ? `<img alt="" data-thumb="${esc(i)}">` : `<img loading="lazy" alt="" src="${esc(thumbUrl(it))}">`}</span><span class="grow"><b>${esc(it.title)}</b><small>${esc(sub)}</small></span><span class="act">${it.kind === 'printed' ? 'print again' : 'open'}</span></button>`;
            }
        }
        $('#palRes').innerHTML = html || '<p class="muted pad">Nothing found. Try another word, or &gt; for commands.</p>';
        $$('#palRes [data-thumb]').forEach(img => drawThumb(img, results[+img.dataset.thumb]));
        sel = Math.min(sel, Math.max(0, results.length - 1));
        mark();
        $$('[data-r]').forEach(b => b.onclick = () => choose(+b.dataset.r));
    };
    const mark = () => {
        $$('[data-r]').forEach(b => {
            b.classList.toggle('on', +b.dataset.r === sel);
            if (+b.dataset.r === sel) b.scrollIntoView({ block: 'nearest' });
        });
        // What Enter does with the row chosen
        const it = results[sel];
        $('#palEnter').textContent = it && it.kind === 'printed' ? 'print again' : it && it.kind === 'command' ? 'run' : 'open';
    };
    const choose = i => {
        const it = results[i];
        if (!it) return;
        closeLayer(true);
        if (it.kind === 'command') it.command.run();
        // Printed before: printed again as it was
        else if (it.kind === 'printed') printAgain(it.entry);
        else openItem(it);
    };
    q.addEventListener('input', () => { sel = 0; draw(); });
    q.addEventListener('keydown', e => {
        if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(results.length - 1, sel + 1); mark(); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); mark(); }
        else if (e.key === 'Enter') { e.preventDefault(); choose(sel); }
    });
    draw();
    q.focus();
    loadSaved().then(changed => { if (changed && q.isConnected) draw(); });
}

// A saved label or a template, opened in the editor
async function openItem(it) {
    try {
        let design;
        if (it.kind === 'saved') ({ item: it, design } = await savedNow(it.id));
        else design = await itemDesign(it);
        openEditor({ title: it.title, design, origin: originOf(it.kind, it.id, it.mtime) },
            () => toast(`Opened "${it.title}"${placed(design.label_size) ? `, made for ${sizeName(design.label_size)}` : ''}`));
    } catch (e) { toast(e.message); }
}

// --- Saving ---------------------------------------------------------------------------------

function tagsField(tags) {
    const known = allTags();
    return `<label><span class="k">Tags</span><input class="in" id="saveTags" value="${esc(tags.join(', '))}" placeholder="e.g. Kitchen, Freezer" autocomplete="off"></label>
        ${known.length ? `<div class="pills" id="tagChips">${known.map(t => `<button type="button" class="pill${tags.some(x => x.toLowerCase() === t.toLowerCase()) ? ' on' : ''}" data-tag="${esc(t)}">${esc(t)}</button>`).join('')}</div>` : ''}`;
}
// Asks for the name and the tags: a
// saved label of the same name is only replaced when asked again
function saveDialog(title, save, design, tags = []) {
    openLayer(`<div class="dialog narrow" role="dialog" aria-label="Save to your labels">
        <div class="dialog-h"><div><h2>Save to your labels</h2><p class="muted">It is saved ${esc(savedAs(design))} and found in Templates and the search.</p></div><span class="sp"></span>
            <button class="iconbtn" type="button" data-close aria-label="Close">${icon('x', '')}</button></div>
        <form class="form" id="saveForm" novalidate>
            <label><span class="k">Name</span><input class="in" id="saveName" value="${esc(title)}" autocomplete="off"></label>
            ${tagsField(tags)}
            <p class="err" id="saveErr" hidden></p>
            <div class="acts"><button class="btn primary" type="submit" id="saveGo">Save</button></div>
        </form></div>`, 'center');
    const name = $('#saveName'), go = $('#saveGo'), input = $('#saveTags');
    $$('#tagChips [data-tag]').forEach(b => b.onclick = () => {
        const t = tagsOf(input.value), had = t.some(x => x.toLowerCase() === b.dataset.tag.toLowerCase());
        input.value = (had ? t.filter(x => x.toLowerCase() !== b.dataset.tag.toLowerCase()) : t.concat(b.dataset.tag)).join(', ');
        b.classList.toggle('on', !had);
    });
    let replacing = null;
    const say = text => { $('#saveErr').textContent = text; $('#saveErr').hidden = !text; };
    name.addEventListener('input', () => { replacing = null; say(''); go.textContent = 'Save'; });
    name.select();
    name.focus();
    $('#saveForm').onsubmit = async e => {
        e.preventDefault();
        for (let tries = 0; tries < 2; tries++) {
            const t = name.value.trim();
            if (!t) return say('Give the label a name.');
            const taken = S.saved.find(x => x.title.toLowerCase() === t.toLowerCase());
            if (taken && replacing !== taken.name) {
                replacing = taken.name;
                go.textContent = 'Replace it';
                // The label it replaces keeps its tags, those typed here are
                // added; given another name instead, its tags go again
                const typed = tagsOf(input.value);
                const show = tags => {
                    input.value = tags.join(', ');
                    $$('#tagChips [data-tag]').forEach(b => b.classList.toggle('on', hasTag({ tags }, b.dataset.tag)));
                };
                show(taken.tags.concat(typed.filter(x => !hasTag(taken, x))));
                name.addEventListener('input', () => show(tagsOf(input.value).filter(x => hasTag({ tags: typed }, x) || !hasTag(taken, x))), { once: true });
                return say(`"${taken.title}" is one of your saved labels already. Replace it, or give this label another name.`);
            }
            go.disabled = true;
            try {
                await save(t, taken ? taken.name : null, tagsOf(input.value));
                if (name.isConnected) closeLayer(true);
                return;
            } catch (err) {
                if (!err.taken || tries) return say(err.message);
            } finally {
                go.disabled = false;
            }
        }
    };
}

// --- Theme, keys and start ----------------------------------------------------------------------

function setTheme(mode) {
    if (mode) document.documentElement.setAttribute('data-theme', mode);
    else document.documentElement.removeAttribute('data-theme');
    try { mode ? localStorage.setItem('designer_theme', mode) : localStorage.removeItem('designer_theme'); } catch (e) { }
}
export { setTheme };

// Menus and popovers close on a press elsewhere: Esc presses there for them
function closeMenus() {
    if (!$('.line-menu, .media-menu, .color-menu, .chip-pop')) return false;
    document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    return true;
}

document.addEventListener('keydown', e => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === 'k') { e.preventDefault(); openSearch(); return; }
    if (e.key === 'Escape' && layerOpen()) { closeLayer(); return; }
    if (e.key === 'Escape' && !e.defaultPrevented) {
        if (closeMenus()) return;
        const adv = $('#adv');
        if (adv && !adv.hidden) { $('[data-close-adv]', adv).click(); return; }
    }
    if (viewOf() !== 'edit' || layerOpen()) return;
    // Held down, the key repeats: one print for one press
    if (mod && e.key === 'Enter') { e.preventDefault(); if (!e.repeat) printNow(); return; }
    // Undo and redo of the label, also while typing in its field. Other
    // fields, e.g. of a chip, undo their own typing.
    const own = !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if (mod && own && e.key.toLowerCase() === 'z') { e.preventDefault(); stepHistory(e.shiftKey ? 1 : -1); return; }
    if (mod && own && e.key.toLowerCase() === 'y') { e.preventDefault(); stepHistory(1); }
});

function bindTop() {
    $('#openSearch').onclick = openSearch;
    $('#printerChip').onclick = openPrinters;
    $('#print').onclick = () => printNow();
    $('#save').onclick = () => saveNow(saveDialog);
    hooks.save = then => saveNow(saveDialog, then);
    $$('.printbtn [data-step]').forEach(b => b.onclick = () => {
        const inp = $('#copies');
        inp.value = Math.min(S.edit ? maxCopies(onTape(S.edit.design).label_size) : 100, Math.max(1, (parseInt(inp.value, 10) || 1) + +b.dataset.step));
        if (S.edit) { S.edit.copies = +inp.value; delete S.edit.filledFrom; }
        copiesChanged();
    });
    $('#copies').addEventListener('input', () => {
        if (S.edit) { S.edit.copies = parseInt($('#copies').value, 10) || 1; delete S.edit.filledFrom; }
        copiesChanged();
    });
    hooks.templates = () => openTemplates();
    mountGallery({ openEditor });
    addEventListener('hashchange', route);
}

// The label designer works without the internet: a link to a page there,
// also one opened with the middle button, asks before it opens it
const elsewhere = a => /^https?:$/.test(a.protocol) && a.origin !== location.origin;
['click', 'auxclick'].forEach(type => document.addEventListener(type, e => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a || !elsewhere(a) || (type === 'auxclick' && e.button !== 1)) return;
    e.preventDefault();
    askToLeave(a.href);
}, true));
function askToLeave(url) {
    const host = new URL(url).host;
    openLayer(`<div class="dialog narrow ask" role="alertdialog" aria-label="Open ${esc(host)}?">
        <div class="okc warn">${icon('globe', '')}</div><h2>Open ${esc(host)}?</h2>
        <p class="muted">The link opens a page on the internet in a new tab. The label designer itself works without the internet.</p>
        <div class="acts"><button class="btn" type="button" data-close>Cancel</button><button class="btn primary" type="button" id="leaveGo">${icon('out')}Open ${esc(host)}</button></div></div>`, 'center');
    $('#leaveGo').onclick = () => { closeLayer(true); window.open(url, '_blank', 'noopener'); };
    $('#leaveGo').focus();
}

// Content shared with the installed app from the share menu of a phone
// arrives as ?shared=<token>: a file becomes
// the image of a new label, a link its QR code, a text its lines
async function receiveShared() {
    const params = new URLSearchParams(location.search);
    const token = params.get('shared');
    if (!token) return;
    // Reloading the page must not fetch it again
    params.delete('shared');
    history.replaceState(null, '', location.pathname + (params.toString() ? '?' + params : '') + '#edit');
    let item;
    try {
        const r = await fetch(`${C.base}/share?token=${encodeURIComponent(token)}`);
        item = await r.json();
        if (!r.ok) throw new Error(item.message);
    } catch (e) {
        toast(e.message || 'What was shared could not be loaded, share it again.');
        return;
    }
    const edit = newEdit(), d = edit.design;
    if (item.data) {
        d.print_type = 'image';
        d.image = { data: item.data, mime: item.type, name: item.name };
        edit.title = item.name.replace(/\.[^.]+$/, '') || edit.title;
    } else if (item.url) {
        d.print_type = 'qrcode_text';
        d.code_text = item.url;
    } else {
        const lines = item.text.split(/\r?\n/).filter(t => t.trim()).map((t, i) => L(t, i ? 44 : 70, i === 0));
        if (lines.length) d.text = lines;
    }
    const before = S.edit || loadEdit();
    setEdit(edit);
    if (before && !isEmptyDesign(before.design)) toast('Started a new label with what was shared', { label: 'Undo', run: () => { setEdit(before); renderEditor(); } });
}

(async function start() {
    forgetGoneKeys();
    bindTop();
    mountPhone();
    mountQueue();
    renderChip();
    await Promise.all([refresh(true), loadDemos(), loadSaved(), carryOverLabelSettings()]);
    await receiveShared();
    S.history.forEach(h => { if (h.state === 'printing' && !h.job) { h.state = 'failed'; h.message = 'The page was closed while printing'; } });
    resolvePending();
    route();
    if (S.edit && S.edit.copies) $('#copies').value = S.edit.copies;
    setInterval(poll, 20000);
    setInterval(follow, 2000);
    // Settings changed in another browser show when coming back
    addEventListener('visibilitychange', async () => {
        if (document.hidden) return;
        refresh();
        loadSaved();
        if (await loadLabelSettings()) { redrawLabelSettings(); printerChanged(); }
    });
    window.labelEditor = { S, editorUI, hooks, views, API, poll, follow };
})();
