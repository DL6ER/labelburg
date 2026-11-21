// The scan station: a saved label chosen once, each code a scanner types
// (it types like a keyboard) is sent to the server, which fills the blanks
// of the label with it and prints it.

import { $, $$, esc, icon, S, own, load, storeQuiet, savedItem, itemDesign, blanksOf, drawThumb, loadSaved, post, openLayer, closeLayer, layerOpen } from './core.js';

// ms: a scanner types faster than FAST between characters; after Enter or
// Tab it may go on within END_WAIT, a scanner without Enter stops for PAUSE
export const END_WAIT = 80, PAUSE = 200, FAST = 35;

// Reads what is typed into target, a textarea, and calls onScan(text) at the
// end of each scan: Enter or Tab, else a pause after a fast one
export function scanInput(target, onScan, clock = performance) {
    let times = [], pasted = false, timer = null;
    const fast = () => !pasted && times.length >= 3 && target.value.trim().length >= 3 && times.every((t, i) => !i || t - times[i - 1] < FAST);
    const later = (fn, ms) => { clearTimeout(timer); timer = setTimeout(fn, ms); };
    const end = () => {
        clearTimeout(timer);
        const text = target.value.replace(/\n+$/, '');
        target.value = '';
        times = [];
        pasted = false;
        if (text.trim()) onScan(text);
    };
    const changed = () => {
        times.push(clock.now());
        if (fast()) later(end, PAUSE); else clearTimeout(timer);
    };
    const onInput = e => {
        if (!target.value) { clearTimeout(timer); times = []; pasted = false; return; }
        if (/^insertFrom(Paste|Drop)/.test(e.inputType || '')) pasted = true;
        changed();
    };
    // Enter or Tab ends the scan, unless more follows at once: a line break
    // of the code, e.g. of a contact
    const onKey = e => {
        if (!['Enter', 'Tab'].includes(e.key) || e.ctrlKey || e.altKey || e.metaKey || e.shiftKey || e.isComposing) return;
        e.preventDefault();
        if (!target.value) return;
        target.value += '\n';
        changed();
        later(end, END_WAIT);
    };
    // The field keeps the focus: the scanner types where it is. A layer
    // open, e.g. the search, keeps its own.
    const refocus = () => {
        if (!target.isConnected || layerOpen() || document.activeElement === target) return;
        if (document.activeElement && document.activeElement !== document.body && document.activeElement.matches('input, select, textarea')) return;
        target.focus();
    };
    const onBlur = e => { if (!e.relatedTarget) setTimeout(refocus); };
    const onShown = () => { if (!document.hidden) refocus(); };
    const layer = new MutationObserver(refocus);
    target.addEventListener('input', onInput);
    target.addEventListener('keydown', onKey);
    target.addEventListener('focusout', onBlur);
    document.addEventListener('click', refocus);
    document.addEventListener('visibilitychange', onShown);
    addEventListener('focus', refocus);
    layer.observe($('#layer'), { attributes: true, attributeFilter: ['hidden'] });
    target.focus();
    return {
        stop() {
            clearTimeout(timer);
            target.removeEventListener('input', onInput);
            target.removeEventListener('keydown', onKey);
            target.removeEventListener('focusout', onBlur);
            document.removeEventListener('click', refocus);
            document.removeEventListener('visibilitychange', onShown);
            removeEventListener('focus', refocus);
            layer.disconnect();
        }
    };
}

// --- The view --------------------------------------------------------------------

const REMEMBERED = own('designer_scan_label');
// One request at a time, in the order scanned; kept while the view is drawn again
const queue = [];
let sending = null, last = null;
const titleOf = name => { const s = S.saved.find(x => x.name === name); return s ? s.title : name.replace(/\.json$/i, ''); };
const GONE = name => `The label ${titleOf(name)} is no longer saved. Choose another one.`;

export function renderScan(el, name) {
    if (!name) name = load(REMEMBERED, null);
    let input = null, live = true;
    const draw = () => {
        if (input) input.stop();
        const s = S.saved.find(x => x.name === name);
        if (s) storeQuiet(REMEMBERED, name);
        el.innerHTML = `<div class="scan"><div class="scan-inner">
            <header class="set-h"><div class="grow"><h2>Scan to print</h2><p class="muted">Each code a scanner reads is filled into the label and printed.</p></div></header>
            ${!S.saved.length ? `<section class="set-card set-empty">Save a label with a blank first, then scan into it. <a href="#edit">Go to the editor</a></section>`
                : `<section class="set-card scan-label">${s ? `<span class="set-thumb"><img alt="" id="scanThumb"></span>
                    <span class="grow"><b id="scanTitle">${esc(s.title)}</b><small class="muted" id="scanFills">&nbsp;</small></span>`
                    : `<span class="grow">${name ? `<b>${esc(GONE(name))}</b>` : '<b>Choose the label to print</b>'}</span>`}
                    <button class="btn soft" type="button" id="scanChange">${icon('grid')}${s ? 'Change' : 'Choose'}</button></section>
                <section class="set-card scan-in">
                    <textarea class="in scan-field" id="scanField" rows="2" placeholder="Scan a code, or type one and press Enter" aria-label="Scan a code, or type one and press Enter" spellcheck="false" autocomplete="off" autocapitalize="off"${s ? '' : ' disabled'}></textarea>
                    <label class="scan-switch"><input type="checkbox" role="switch" id="scanShow"><span>Show only, don't print</span></label>
                </section>
                <section class="set-card scan-result" id="scanResult" aria-live="polite" hidden></section>`}
        </div></div>`;
        if (!S.saved.length) return;
        $('#scanChange').onclick = () => pick(name);
        drawResult();
        if (!s) { if (!name) pick(null); return; }
        const item = savedItem(s), img = $('#scanThumb');
        img.onload = () => img.parentElement.classList.add('shown');
        drawThumb(img, item);
        itemDesign(item).then(design => {
            const blanks = blanksOf(design), fills = $('#scanFills');
            if (fills) fills.textContent = blanks.length ? `Each scan fills: ${blanks.join(', ')}` : 'The label has no blank for the code yet, add one in the editor.';
        }, () => { });
        const show = $('#scanShow');
        show.onchange = () => $('#scanField').focus();
        input = scanInput($('#scanField'), text => {
            queue.push({ name, code: text, previewOnly: show.checked });
            send();
        });
    };
    draw();
    // Saved meanwhile in another browser
    loadSaved().then(changed => { if (changed && live) draw(); });
    return { stop: () => { live = false; if (input) input.stop(); input = null; } };
}

function pick(chosen) {
    openLayer(`<div class="dialog narrow scan-pick" role="dialog" aria-label="Choose the label">
        <div class="dialog-h"><h2>Choose the label</h2><span class="sp"></span><button class="iconbtn" type="button" data-close aria-label="Close">${icon('x', '')}</button></div>
        <div class="scan-list">${S.saved.map(s => `<button class="res${s.name === chosen ? ' on' : ''}" type="button" data-name="${esc(s.name)}"><span class="ico"><img alt=""></span>
            <span class="grow"><b>${esc(s.title)}</b></span></button>`).join('')}</div></div>`, 'center');
    $$('.scan-pick [data-name]').forEach(b => {
        drawThumb($('img', b), savedItem(S.saved.find(s => s.name === b.dataset.name)));
        b.onclick = () => { closeLayer(true); location.hash = 'scan/' + encodeURIComponent(b.dataset.name); };
    });
}

// The last scan and how it went, or the one being sent
function drawResult() {
    const box = $('#scanResult');
    if (!box) return;
    const shown = sending ? sending : last;
    box.hidden = !shown;
    if (!shown) return;
    const code = shown.code.length > 200 ? shown.code.slice(0, 200) + '...' : shown.code;
    let state;
    if (shown === sending) state = `<p class="scan-state">${esc(shown.previewOnly ? 'Showing' : 'Printing')}${esc(queue.length ? ', the next scans wait their turn' : '...')}</p>`;
    else if (shown.message) state = `<p class="scan-state warn">${icon('warn')}<span>${esc(shown.message)}</span></p>`;
    else state = `${shown.image ? `<div class="scan-shot"><img alt="The label" src="data:image/png;base64,${esc(shown.image)}"></div>` : ''}
        <p class="scan-state ok">${icon('check')}<span>${esc(shown.printed ? `Printed: ${titleOf(shown.name)}` : 'Shown, not printed')}</span></p>`;
    box.innerHTML = `<p class="scan-code">${esc(code)}</p>${state}`;
}

async function send() {
    if (sending) { drawResult(); return; }
    while (queue.length) {
        const scan = sending = queue.shift();
        drawResult();
        const fd = new FormData();
        fd.append('name', scan.name);
        fd.append('code', scan.code);
        if (S.printer && S.printer.path) fd.append('printer', S.printer.path);
        if (S.printer && S.printer.model) fd.append('model', S.printer.model);
        if (scan.previewOnly) fd.append('preview_only', '1');
        try {
            const r = await post('/scan', fd);
            let data = {};
            try { data = await r.json(); } catch (e) { }
            if (r.status === 404) scan.message = GONE(scan.name);
            else if (!r.ok || data.success === false) scan.message = data.message || ('The printer answered ' + r.status);
            else Object.assign(scan, { printed: !!data.printed, image: data.image || '' });
        } catch (e) {
            scan.message = e.message;
        }
        last = scan;
        sending = null;
        drawResult();
    }
}
