// The scan station: a saved label chosen once, each code a scanner types
// (it types like a keyboard) is sent to the server, which fills the blanks
// of the label with it and prints it.

import { $, $$, esc, icon, S, own, load, storeQuiet, savedItem, itemDesign, blanksOf, drawThumb, loadSaved, post, openLayer, closeLayer, layerOpen, UNREACHABLE, SOURCE } from './core.js';
import { camera, cameraBlocked } from './camera.js';

// ms: a scanner types faster than FAST between characters; after Enter or
// Tab it may go on within END_WAIT, a scanner without Enter stops for PAUSE.
// Typing that starts after IDLE is a run of its own.
export const END_WAIT = 80, PAUSE = 200, FAST = 35, IDLE = 2000;
const TEXT_ENTRY = 'textarea, select, input:not([type=checkbox], [type=radio], [type=button], [type=submit], [type=reset], [type=image], [type=file], [type=range], [type=color])';

// Reads what is typed into target, a textarea, and calls onScan(text) at the
// end of each scan: Enter or Tab, else a pause after a fast one
export function scanInput(target, onScan, clock = performance) {
    // The current run: its change times, the text before it, e.g. a key
    // pressed by mistake, and the text after the last change
    let times = [], before = '', typed = '', pasted = false, timer = null;
    const run = () => target.value.slice(before.length);
    const fast = () => !pasted && times.length >= 3 && run().trim().length >= 3 && times.every((t, i) => !i || t - times[i - 1] < FAST);
    const later = (fn, ms) => { clearTimeout(timer); timer = setTimeout(fn, ms); };
    // A screen keyboard opens only for a tap on the field, a scanner types without it
    const touch = matchMedia('(pointer: coarse)').matches;
    const screenKeys = on => { if (touch) target.inputMode = on ? 'text' : 'none'; };
    const reset = () => { times = []; before = typed = ''; pasted = false; };
    const end = () => {
        clearTimeout(timer);
        // A fast run is the scan, without what was left in the field before it
        const text = (fast() ? run() : target.value).replace(/\n+$/, '');
        target.value = '';
        reset();
        screenKeys(false);
        if (text.trim()) onScan(text);
    };
    const changed = () => {
        const now = clock.now();
        if (times.length && now - times[times.length - 1] > IDLE) {
            times = [];
            pasted = false;
            before = target.value.startsWith(typed) ? typed : '';
        }
        times.push(now);
        typed = target.value;
        if (fast()) later(end, PAUSE); else clearTimeout(timer);
    };
    const onInput = e => {
        if (!target.value) { clearTimeout(timer); reset(); return; }
        if (/^insertFrom(Paste|Drop)/.test(e.inputType || '')) pasted = true;
        changed();
    };
    // Enter or Tab ends the scan, unless more follows at once: a line break
    // of the code, e.g. of a contact. Tab in the empty field moves on.
    const onKey = e => {
        if (!['Enter', 'Tab'].includes(e.key) || e.ctrlKey || e.altKey || e.metaKey || e.shiftKey || e.isComposing) return;
        if (!target.value && e.key === 'Tab') return;
        e.preventDefault();
        if (!target.value) return;
        target.value += '\n';
        changed();
        later(end, END_WAIT);
    };
    // The field keeps the focus: the scanner types where it is. A layer
    // open, e.g. the search, keeps its own, so does text selected with the mouse.
    let pressed = false;
    const refocus = () => {
        if (!target.isConnected || layerOpen() || document.activeElement === target || String(getSelection())) return;
        if (document.activeElement && document.activeElement !== document.body && document.activeElement.matches('input, select, textarea')) return;
        target.focus();
    };
    // Focus given back by the page keeps the keyboard of the screen closed
    const onBlur = e => { screenKeys(false); if (!e.relatedTarget && !pressed) setTimeout(refocus); };
    const onShown = () => { if (!document.hidden) refocus(); };
    // A press released outside the window ends with the next move without a button
    const onPress = e => { pressed = e.type === 'pointerdown' || (e.type === 'pointermove' && pressed && !!e.buttons); };
    const onTap = () => screenKeys(true);
    // A key typed with the focus nowhere, e.g. with text selected in the
    // card, lands in the field: focused before the key's default action
    const onStray = e => {
        const at = document.activeElement;
        if (at === target || e.key.length !== 1 || e.ctrlKey || e.metaKey || e.altKey || !target.isConnected || layerOpen()) return;
        // Space presses a button, link or switch that has the focus
        if (at && at !== document.body && (at.matches(TEXT_ENTRY) || at.isContentEditable || e.key === ' ')) return;
        getSelection().removeAllRanges();
        target.focus();
    };
    const layer = new MutationObserver(refocus);
    // The pointer and stray keys are seen before anything else
    const on = [[target, 'input', onInput], [target, 'keydown', onKey], [target, 'focusout', onBlur], [target, 'pointerdown', onTap],
        [document, 'click', refocus], [document, 'visibilitychange', onShown], [window, 'focus', refocus], [window, 'blur', onPress], [document, 'keydown', onStray, true],
        ...['pointerdown', 'pointermove', 'pointerup', 'pointercancel'].map(type => [document, type, onPress, true])];
    on.forEach(([el, type, fn, capture]) => el.addEventListener(type, fn, !!capture));
    layer.observe($('#layer'), { attributes: true, attributeFilter: ['hidden'] });
    screenKeys(false);
    target.focus();
    return {
        stop() {
            clearTimeout(timer);
            on.forEach(([el, type, fn, capture]) => el.removeEventListener(type, fn, !!capture));
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
    // The camera and its video outlive the view drawn again
    let input = null, live = true, cam = null, note = '';
    const video = document.createElement('video');
    video.muted = true;
    video.playsInline = true;
    video.setAttribute('aria-label', 'The picture of the camera');
    const onScan = text => {
        queue.push({ name, code: text, previewOnly: !!($('#scanShow') || {}).checked });
        send();
    };
    const stopCamera = () => { if (cam) cam.stop(); cam = null; };
    // A hidden page lets go of the camera; a press starts it again
    const onHidden = () => { if (document.hidden && cam) { stopCamera(); drawCamera(); } };
    document.addEventListener('visibilitychange', onHidden);
    const drawCamera = () => {
        const box = $('#scanCam'), says = $('#scanCamNote');
        if (!box) return;
        $('#scanCamera').innerHTML = `${icon('camera')}${cam ? 'Stop the camera' : 'Use the camera'}`;
        box.hidden = !cam;
        if (cam && video.parentElement !== box) box.append(video);
        says.hidden = !note;
        says.innerHTML = note ? `${icon('warn')}<span>${note}</span>` : '';
    };
    const toggleCamera = () => {
        note = '';
        if (cam) stopCamera();
        else if (cameraBlocked()) note = `${esc('The camera works only over a secure address (https).')} <a href="${esc(SOURCE + '/blob/master/docs/reverse-proxy.md')}" target="_blank" rel="noopener">The guide</a> shows how to set one up.`;
        else cam = camera(video, onScan, { paused: layerOpen, onError: message => { cam = null; note = esc(message); drawCamera(); } });
        drawCamera();
    };
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
                    <div class="scan-cam" id="scanCam" hidden></div>
                    <p class="scan-state warn" id="scanCamNote" hidden></p>
                    <div class="scan-acts">
                        <label class="scan-switch"><input type="checkbox" role="switch" id="scanShow"><span>Show only, don't print</span></label>
                        <button class="btn soft" type="button" id="scanCamera"${s ? '' : ' disabled'}>${icon('camera')}Use the camera</button>
                    </div>
                </section>
                <section class="set-card scan-result" id="scanResult" aria-live="polite" hidden></section>`}
        </div></div>`;
        if (!s) stopCamera();
        if (!S.saved.length) return;
        $('#scanChange').onclick = () => pick(name);
        drawResult();
        if (!s) { if (!name) pick(null); return; }
        const item = savedItem(s), img = $('#scanThumb');
        img.onload = () => img.parentElement.classList.add('shown');
        drawThumb(img, item);
        itemDesign(item).then(design => {
            const blanks = blanksOf(design), fills = $('#scanFills');
            if (fills) fills.textContent = blanks.length ? `Blanks of the label: ${blanks.join(', ')}` : 'The label has no blank for the code yet, add one in the editor.';
        }, () => { });
        $('#scanShow').onchange = () => $('#scanField').focus();
        $('#scanCamera').onclick = toggleCamera;
        drawCamera();
        input = scanInput($('#scanField'), onScan);
    };
    draw();
    // Saved meanwhile in another browser
    loadSaved().then(changed => { if (changed && live) draw(); });
    return { stop: () => { live = false; if (input) input.stop(); input = null; stopCamera(); document.removeEventListener('visibilitychange', onHidden); } };
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
        <p class="scan-state ok">${icon('check')}<span>${esc(shown.printed ? `Printed: ${titleOf(shown.name)}` : 'Shown, not printed')}</span></p>
        ${shown.filled.length ? `<p class="scan-filled muted">${esc(`Filled: ${shown.filled.join(', ')}`)}</p>` : ''}`;
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
            // Only an answer that says so is a success, e.g. not a sign-in page in between
            else if (!r.ok || data.success !== true) scan.message = data.message || (r.ok ? UNREACHABLE : 'The printer answered ' + r.status);
            else Object.assign(scan, { printed: !!data.printed, image: data.image || '', filled: Object.keys(data.filled || {}) });
        } catch (e) {
            scan.message = e.message;
        }
        last = scan;
        sending = null;
        drawResult();
    }
}
