// The Settings tab: the printers with what the server keeps for each of
// them, what it keeps for labels, the labels printed here, the
// addresses for other programs and about this page. Shown at #settings/<part>.

import {
    $, $$, esc, icon, S, C, API, familyOf, chosenFamily, saveLabelSettings, toast, hideToast, own, load, storeQuiet, post, capsOf,
    printerLabel, printerName, isDemo, printerState, tapeLeft, sizeName, isPtouch, isColorTape, detectTape, tapeState, CUTS, noCutter,
    neverWhy, halfOf, chainOf, baseDesign, L, runsAlong, printerCaps, printJob, entryName, preview, fmtTime, browserDateFormat, settingsSaves,
    itemDesign, demoItem, originOf, kept, printerCut, cutText, leavesFirst, NO_IMAGE, printAgain, announcePrint
} from './core.js';
import { openEditor, printerChanged } from './editor.js';
import { fontButton, openFontMenu } from './fontmenu.js';
import { queuePage } from './queue.js';
import { calibrationHTML, openCalibration, dismissCalibration } from './calibration.js';

const PARTS = [['printers', 'Printers', 'printer'], ['queue', 'Print queue', 'queue'], ['labels', 'Labels', 'tag'], ['printed', 'Printed labels', 'clock'],
    ['connections', 'Connections', 'link'], ['about', 'About', 'info']];
// Line icons of this page only, the others are symbols of the page
const PATHS = {
    tag: '<path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z"/><path d="M7.5 7.5h.01"/>',
    queue: '<path d="M9 6h12M9 12h12M9 18h12"/><path d="M4 6h.01M4 12h.01M4 18h.01"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    copy: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
    out: '<path d="M14 3h7v7M21 3l-9 9M19 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    screen: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    robot: '<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 8V5M2 13v3M22 13v3M9 13v1M15 13v1M9.5 17h5"/><circle cx="12" cy="4" r="1"/>',
};
const ic = name => Object.hasOwn(PATHS, name) ? `<svg class="ic s" viewBox="0 0 24 24" aria-hidden="true">${PATHS[name]}</svg>` : icon(name);
const SOURCE = 'https://github.com/DL6ER/labelburg';

// main.js hands over the printer chip, the status refresh and the theme
const host = { chip: () => { }, refresh: async () => { }, theme: () => { } };
// Printers that cannot be reached, shown with their settings anyway
const shownAnyway = new Set();
// Tapes set by hand on their way to the server, by printer: the select shows
// the one chosen until the server tells whether it takes it
const tapesSaving = {};

// The part of the Settings the address names, as PARTS has it
const partOf = () => {
    const named = location.hash.replace(/^#/, '').split(/[/?]/)[1];
    const found = PARTS.find(([id]) => id === named);
    return found ? found[0] : 'printers';
};
const settingsShown = () => document.body.dataset.view === 'settings' && !!$('#setPart');

export function renderSettings(el, hooks) {
    Object.assign(host, hooks || {});
    const part = partOf();
    el.innerHTML = `<div class="set">
        <nav class="set-nav" aria-label="Settings">${PARTS.map(([id, name, i]) =>
            `<a href="#settings/${esc(id)}" data-part="${esc(id)}"${id === part ? ' class="on" aria-current="page"' : ''}>${ic(i)}<span>${esc(name)}</span></a>`).join('')}</nav>
        <div class="set-main"><div class="set-inner" id="setPart" data-part="${esc(part)}"></div></div></div>`;
    (Object.hasOwn(DRAW, part) ? DRAW[part] : DRAW.printers)($('#setPart'));
}

// After each status of the printers: the cards follow it, drawn again only
// where it changed, so that what is focused stays focused
export function redrawSettings() {
    if (!settingsShown()) return;
    if ($('#setPrinters')) drawCards();
}
// After the label settings were read again
export function redrawLabelSettings() {
    if (settingsShown() && $('#setPart').dataset.part === 'labels') DRAW.labels($('#setPart'));
}

// --- Printers --------------------------------------------------------------------

const DRAW = {};
DRAW.printers = box => {
    box.innerHTML = `<header class="set-h"><div class="grow"><h2>Printers</h2><p class="muted">Set once for each printer. The server keeps it for every browser and the API.</p></div>
        <button class="btn ghost" type="button" id="setLook">${icon('search')}Look for printers</button></header>
        <div class="set-list" id="setPrinters"></div>
        <section class="set-card" id="setAdd" hidden></section>`;
    drawCards(true);
    loadAdded();
    bindAdd($('#setAdd'));
    $('#setLook').onclick = async () => {
        $('#setLook').disabled = true;
        try { await host.refresh(true); } finally { if ($('#setLook')) $('#setLook').disabled = false; }
        drawCards();
    };
    bindCards($('#setPrinters'));
};

const unreachable = p => p.status_type === 'Error';
const cardKey = p => `${p.path}|${p.name || ''}`;
const tapeOf = p => p.tape || { id: C.default_label_size, assumed: true };

// The label sizes the printer takes, grouped by their form. The chosen one
// is always offered.
function sizeOptions(p, chosen) {
    const takes = p.takes || C.takes, candidates = tapeOf(p).candidates || [];
    const offered = s => s.id === chosen || takes.includes(s.id);
    const measured = s => candidates.includes(s.id);
    const groups = [['Of the measured length', measured], ['Endless', s => s.form === 'endless' && !isPtouch(s.id) && !isColorTape(s.id)], ['Die-cut', s => s.form === 'die_cut'],
        ['Round', s => s.form === 'round_die_cut'], ['P-touch tape', s => isPtouch(s.id)], ['Color tape', s => isColorTape(s.id)]];
    return groups.map(([group, inGroup], i) => {
        const sizes = C.label_sizes.filter(s => inGroup(s) && offered(s) && (i === 0 || !measured(s)));
        return sizes.length ? `<optgroup label="${esc(group)}">${sizes.map(s => `<option value="${esc(s.id)}"${s.id === chosen ? ' selected' : ''}>${esc(sizeName(s.id))}</option>`).join('')}</optgroup>` : '';
    }).join('');
}

function tapeHTML(p) {
    const t = tapeOf(p);
    if (t.known && !t.manual) return `<div class="row"><span class="set-val">${esc(sizeName(t.id))}</span><small class="set-ok">${icon('check')}read from the printer</small></div>`;
    const chosen = tapesSaving[p.path] > 0 ? kept(p).label_size || '' : t.manual ? t.id : '';
    const note = isDemo(p) ? 'Any roll this model takes.' : t.manual ? 'Set by hand, the printer does not report its tape.'
        : !t.known && (t.candidates || []).length ? `The printer measures labels ${t.unknown}, choose which ones.`
        : t.unknown ? `The printer reports ${t.unknown} tape, which is not offered here.`
        : `This printer does not report its tape, new labels are ${sizeName(t.id)}.`;
    return `<div class="set-stack"><select class="in set-sel" data-k="label_size" aria-label="Tape in ${esc(printerLabel(p))}">
            <option value="">Automatic${t.manual ? '' : `: ${esc(sizeName(t.id))}`}</option>${sizeOptions(p, chosen)}</select>
        <small class="${t.manual || isDemo(p) ? 'muted' : 'set-warn'}">${esc(note)}</small></div>`;
}

// The demo printer prints as any model, grouped by maker, e.g. QL-500 before QL-1100
function modelHTML(p) {
    const groups = new Map();
    Object.keys(C.models).sort((a, b) => a.localeCompare(b, 'en', { numeric: true }))
        .forEach(m => groups.set(C.models[m].vendor, [...(groups.get(C.models[m].vendor) || []), m]));
    const chosen = kept(p).demo_model || p.model;
    return `<div class="set-stack"><select class="in set-sel" data-k="demo_model" aria-label="Model of ${esc(printerLabel(p))}">${[...groups]
        .sort(([a], [b]) => a.localeCompare(b, 'en')).map(([vendor, models]) => `<optgroup label="${esc(vendor)}">${models.map(m =>
            `<option value="${esc(m)}"${m === chosen ? ' selected' : ''}>${esc(m.split('/').pop())}</option>`).join('')}</optgroup>`).join('')}</select>
        <small class="muted">Try the label designer as with this printer: labels are made for it, nothing is printed.</small></div>`;
}

const seg = (k, opts, on, label) => `<div class="seg" role="group" aria-label="${esc(label)}">${opts.map(([v, n, off]) =>
    `<button type="button" data-k="${esc(k)}" data-v="${esc(v)}"${v === on ? ' class="on" aria-pressed="true"' : ' aria-pressed="false"'}${off ? ` disabled title="${esc(off)}"` : ''}>${esc(n)}</button>`).join('')}</div>`;

function qualityHTML(p) {
    const caps = capsOf(p.model);
    const dense = caps.qualities.includes('high') && caps.high_res;
    const opts = [['fast', 'Fast'], ['quality', 'Quality'], ['high', 'Dense, slow']]
        .filter(([q]) => q === 'quality' || (q === 'high' ? dense : caps.qualities.includes(q)));
    const set = kept(p).quality || 'quality', on = opts.some(([q]) => q === set) ? set : 'quality';
    if (opts.length === 1) return `<div class="set-stack"><span class="set-val">Quality</span><small class="muted">This printer prints in one quality only.</small></div>`;
    const about = caps.colors === 'color' ? 'Fast is a few seconds quicker, colors come out lighter.'
        : 'Fast is quicker, the print a little less sharp.' + (dense ? ' Dense prints twice the lines along the label, slower and often darker.' : '');
    return `<div class="set-stack">${seg('quality', opts, on, 'Quality')}<small class="muted">${esc(about)} The same for all printers.</small></div>`;
}

function cuttingHTML(p) {
    const caps = capsOf(p.model), c = printerCut(p);
    if (noCutter(caps)) return `<div class="set-stack"><span class="set-val">${caps.manual_cutter ? 'Cut by hand' : 'Torn off'}</span><small class="muted">${esc(cutText(c, caps))}</small></div>`;
    const cuts = CUTS.map(([v, n]) => [v, v === 'last' ? 'After the last' : n, v === 'none' ? neverWhy(caps) : '']);
    const chainWhy = c.cut === 'none' ? 'Always on when not cutting, else the printer cuts after the last label' : '';
    const checks = (caps.half_cut ? `<label class="set-check"><input type="checkbox" data-k="half_cut"${halfOf(c, caps) ? ' checked' : ''}>Half cut between labels</label>` : '')
        + (caps.chain ? `<label class="set-check"${chainWhy ? ` title="${esc(chainWhy)}"` : ''}><input type="checkbox" data-k="chain"${chainOf(c, caps) ? ' checked' : ''}${chainWhy ? ' disabled' : ''}>Chain printing</label>` : '');
    return `<div class="set-stack"><div class="row">${seg('cut', cuts, c.cut, 'Cutting')}${checks}</div>
        <small class="muted">${esc(cutText(c, caps))} Labels saved with their own cutting keep it.</small></div>`;
}

function cardHTML(p, i) {
    const st = printerState(p), name = printerLabel(p);
    if (unreachable(p) && !shownAnyway.has(cardKey(p))) {
        return `<section class="set-card off" data-card="${esc(i)}"><div class="set-collapsed">
            <span class="d bad"></span><b>${esc(name)}</b><span class="muted grow" title="${esc(st.text)}">cannot be reached</span>
            ${removeHTML(p)}<button class="btn ghost sm" type="button" data-show>Show settings</button></div></section>`;
    }
    const where = isDemo(p) ? '' : (p.path || '').replace('file://', '');
    const sub = [p.name ? printerName(p) : '', where, st.text, tapeLeft(p), p.battery != null ? `Battery ${p.battery}%` : ''].filter(Boolean).join(', ');
    const warns = (p.weak_connection ? '<small class="set-warn">The Wi-Fi connection seems weak, try moving the printer closer to the Wi-Fi router.</small>' : '')
        + (p.battery_low ? '<small class="set-warn">The battery is low, charge the printer.</small>' : '');
    // Simulators and printers named twice in PRINTERS are one device
    const same = S.printers.filter(o => o !== p && o.path === p.path).map(printerLabel);
    return `<section class="set-card" data-card="${esc(i)}">
        <div class="set-card-h"><span class="d ${esc(st.cls)}"></span>
            <div class="grow"><b>${esc(name)}</b><small class="muted">${esc(sub)}</small>${warns}</div>
            ${unreachable(p) ? '<button class="btn ghost sm" type="button" data-hide>Hide settings</button>' : ''}
            ${removeHTML(p)}<button class="btn soft sm" type="button" data-test${unreachable(p) ? ' disabled title="The printer cannot be reached"' : ' title="A label that shows which end leaves the printer first"'}>${icon('printer')}Print a test label</button></div>
        <div class="set-grid">
            ${isDemo(p) ? `<span class="k">Model</span>${modelHTML(p)}` : ''}
            <span class="k">Tape</span>${tapeHTML(p)}
            <span class="k">Quality</span>${qualityHTML(p)}
            <span class="k">Cutting</span>${cuttingHTML(p)}
            <span class="k">Leaves first</span><div class="set-stack">${seg('leaves_first', [['top', 'Top'], ['bottom', 'Bottom']], leavesFirst(printerCaps(p), p), 'Leaves the printer first')}
                <small class="muted">The end that comes out first, e.g. to stick labels on as they come out. The test label shows it.</small></div>
            ${capsOf(p.model).calibration ? `<span class="k">Position</span><div class="set-stack">${calibrationHTML(p)}
                <small class="muted">Labels that come out off to one side are lined up with a calibration label.</small></div>` : ''}
        </div>
        ${same.length ? `<p class="set-foot muted small">${esc(same.join(', '))} ${same.length > 1 ? 'are' : 'is'} the same device and ${same.length > 1 ? 'share' : 'shares'} these settings.</p>` : ''}
    </section>`;
}

function cardsHTML() {
    const hints = S.printerHints.map(h => `<p class="muted small">${esc(h)}</p>`).join('');
    return (S.printers.map(cardHTML).join('') || `<section class="set-card"><p class="set-empty muted">No printer answered. Check that it is switched on and connected, then look for printers.</p></section>`) + hints;
}

// Drawn again keeps the focus on the control that had it
function drawCards(force = false) {
    const box = $('#setPrinters');
    if (!box) return;
    const fresh = document.createElement('div');
    fresh.innerHTML = cardsHTML();
    if (!force && fresh.innerHTML === box.innerHTML) return;
    const a = document.activeElement, card = a && a.closest && a.closest('[data-card]');
    const focus = card && box.contains(a) ? [card.dataset.card, a.dataset.k, a.dataset.v, Object.keys(a.dataset).join()] : null;
    box.replaceChildren(...fresh.childNodes);
    if (!focus) return;
    const at = CSS.escape(focus[0]);
    const again = $$(`[data-card="${at}"] button, [data-card="${at}"] input, [data-card="${at}"] select`, box)
        .find(el => el.dataset.k === focus[1] && el.dataset.v === focus[2] && Object.keys(el.dataset).join() === focus[3]);
    if (again) again.focus();
}

function bindCards(box) {
    box.addEventListener('click', e => {
        const card = e.target.closest('[data-card]'), p = card && S.printers[+card.dataset.card];
        if (!p) return;
        const b = e.target.closest('button');
        if (!b || b.disabled) return;
        if (b.dataset.k && b.dataset.v) { if (!b.classList.contains('on')) saveSettings(p, { [b.dataset.k]: b.dataset.v }); }
        else if ('show' in b.dataset) { shownAnyway.add(cardKey(p)); drawCards(true); }
        else if ('hide' in b.dataset) { shownAnyway.delete(cardKey(p)); drawCards(true); }
        else if ('test' in b.dataset) testLabel(p);
        else if ('calOpen' in b.dataset) openCalibration(p, () => drawCards(true));
        else if ('calDismiss' in b.dataset) dismissCalibration(p, () => drawCards(true));
        else if ('remove' in b.dataset) removePrinter(p);
    });
    box.addEventListener('change', e => {
        const card = e.target.closest('[data-card]'), p = card && S.printers[+card.dataset.card], k = e.target.dataset.k;
        if (!p || !k) return;
        if (e.target.type === 'checkbox') saveSettings(p, { [k]: e.target.checked ? '1' : '0' });
        else saveSettings(p, { [k]: e.target.value });
    });
}

// --- Adding printers ----------------------------------------------------------------

// Printers the search does not find, added by their address. The server
// asks what answers there before it adds anything, ADD_PRINTERS of its
// configuration says where it may look.
const EXAMPLES = [['usbip://192.168.1.50', 'USB printer on another device'], ['tcp://192.168.1.60', 'Network printer'],
    ['vc500w://192.168.1.70', 'VC-500W']];
const adding = { mode: '', allows: '', unused: [], address: '', name: '', asking: false, found: null, error: '' };
const removeHTML = p => p.added ? `<button class="btn ghost sm" type="button" data-remove title="Remove the printer added here">${icon('trash')}Remove</button>` : '';

function send(path, values) {
    const fd = new FormData();
    Object.entries(values).forEach(([k, v]) => fd.set(k, v));
    return post(path, fd).then(async r => {
        const answer = await r.json().catch(() => ({}));
        if (!r.ok || !answer.success) throw new Error(answer.message || 'The label designer could not do it');
        return answer;
    });
}

async function loadAdded() {
    try {
        const data = await (await fetch(API + '/printers/added')).json();
        Object.assign(adding, { mode: data.mode, allows: data.allows, unused: data.printers.filter(p => !p.used) });
    } catch (e) {
        adding.mode = '';
    }
    drawAdd();
}

const foundText = f => f.errors.length ? f.errors[0]
    : f.tape && f.tape.known ? `${sizeName(f.tape.id)} in the printer` : 'It does not say which tape it has';

function addHTML() {
    const head = `<div class="set-card-h"><span class="set-add-ic">${icon('plus')}</span><div class="grow"><b>Add a printer</b>
        <small class="muted">For one the search does not find, e.g. a USB printer another device shares, or a network printer elsewhere.</small></div></div>`;
    const allows = `<small class="muted">${esc(adding.allows)}</small>`;
    if (adding.mode === 'off') {
        return head + `<div class="set-add">${allows}${adding.unused.map(e => `<div class="set-unused"><span class="d off"></span>
            <div class="grow"><b>${esc(e.name || printerName(e))}</b><small class="muted">${esc(e.address)}, not used</small></div>
            <button class="btn ghost sm" type="button" data-remove="${esc(e.address)}">${icon('trash')}Remove</button></div>`).join('')}</div>`;
    }
    const f = adding.found;
    const found = !f ? '' : `<div class="set-found"><span class="d ${f.errors.length ? 'bad' : ''}"></span>
            <div class="grow"><b>${esc(printerName(f) || f.model)}</b><small class="muted">${esc(foundText(f))}, ${esc(f.address)}</small></div></div>`
        + (f.listed ? `<small class="muted">It is in the list already as ${esc(f.listed)}.</small>`
            : `<label class="k" for="addName">Name, if you like</label>
            <div class="row"><input class="in set-name" id="addName" maxlength="40" autocomplete="off" placeholder="${esc(printerName(f) || 'e.g. Garage')}" value="${esc(adding.name)}">
                <button class="btn primary" type="button" id="addSave">Add</button><button class="btn ghost" type="button" id="addCancel">Cancel</button></div>`);
    return head + `<div class="set-add">
        <label class="k" for="addAddress">Address</label>
        <div class="row"><input class="in set-address" id="addAddress" autocomplete="off" autocapitalize="off" spellcheck="false"
            placeholder="usbip://192.168.1.50" value="${esc(adding.address)}"${adding.asking ? ' disabled' : ''}>
            <button class="btn soft" type="button" id="addAsk"${adding.asking ? ' disabled' : ''}>${icon('search')}${adding.asking ? 'Asking the printer...' : 'Identify'}</button></div>
        <div class="set-examples">${EXAMPLES.map(([v, n]) => `<button type="button" class="set-ex" data-example="${esc(v)}">${esc(n)} <code>${esc(v)}</code></button>`).join('')}</div>
        ${found}
        ${adding.error ? `<small class="set-bad" role="alert">${esc(adding.error)}</small>` : allows}
    </div>`;
}

// Drawn again, then the field named by focus has the focus
function drawAdd(focus = '') {
    const box = $('#setAdd');
    if (!box) return;
    box.hidden = !adding.mode;
    box.innerHTML = adding.mode ? addHTML() : '';
    const el = focus && $(focus, box);
    if (el) {
        el.focus();
        el.setSelectionRange(el.value.length, el.value.length);
    }
}

function bindAdd(box) {
    box.addEventListener('input', e => {
        if (e.target.id === 'addName') adding.name = e.target.value;
        if (e.target.id !== 'addAddress') return;
        adding.address = e.target.value;
        // What answered was at the address before
        if (adding.found || adding.error) {
            Object.assign(adding, { found: null, error: '' });
            drawAdd('#addAddress');
        }
    });
    box.addEventListener('keydown', e => {
        if (e.key !== 'Enter') return;
        if (e.target.id === 'addAddress') identify();
        else if (e.target.id === 'addName') save();
        else return;
        e.preventDefault();
    });
    box.addEventListener('click', e => {
        const b = e.target.closest('button');
        if (!b || b.disabled) return;
        if (b.id === 'addAsk') identify();
        else if (b.id === 'addSave') save();
        else if (b.id === 'addCancel') {
            Object.assign(adding, { address: '', name: '', found: null, error: '' });
            drawAdd('#addAddress');
        } else if (b.dataset.example) {
            Object.assign(adding, { address: b.dataset.example.replace(/\/\/.*/, '//'), found: null, error: '' });
            drawAdd('#addAddress');
        } else if (b.dataset.remove) removePrinter(adding.unused.find(p => p.address === b.dataset.remove));
    });
}

async function identify() {
    const address = adding.address.trim();
    if (adding.asking) return;
    Object.assign(adding, { asking: !!address, found: null, error: address ? '' : 'Type the address of the printer first.' });
    drawAdd(address ? '' : '#addAddress');
    if (!address) return;
    try {
        adding.found = (await send('/printers/identify', { address })).printer;
    } catch (e) {
        adding.error = e.message;
    }
    adding.asking = false;
    drawAdd(adding.found && !adding.found.listed ? '#addName' : '#addAddress');
}

async function save() {
    const f = adding.found;
    if (!f || f.listed || $('#addSave').disabled) return;
    $('#addSave').disabled = true;
    try {
        const p = (await send('/printers/add', { address: f.address, name: adding.name.trim() })).printer;
        Object.assign(adding, { address: '', name: '', found: null, error: '' });
        drawAdd();
        toast(`Added ${p.name || printerName(p)}`);
        await host.refresh();
    } catch (e) {
        adding.error = e.message;
        drawAdd('#addName');
    }
}

async function removePrinter(p) {
    if (!p) return;
    const address = p.path || p.address, name = p.name || '';
    try {
        await send('/printers/remove', { printer: address });
    } catch (e) {
        toast(e.message);
        return;
    }
    // Not kept as the printer chosen, which would stay as one that does not answer
    if (S.printer && S.printer.path === address) S.printer = null;
    for (const key of [own('designer_printer'), own('designer_next_printer')]) {
        const kept = load(key, null);
        if (kept === address || (kept && kept.path === address)) storeQuiet(key, null);
    }
    toast(`Removed ${name || printerName(p)}`, { label: 'Undo', run: () => send('/printers/add', { address, name }).then(() => host.refresh()).then(loadAdded).catch(e => toast(e.message)) });
    await host.refresh();
    loadAdded();
}

// Shown at once, then kept on the server:
// only the settings changed go there, another browser may change others
const COMMON = ['quality'];
function saveSettings(p, changes) {
    const printers = [...new Set([S.printer, ...S.printers].filter(Boolean))];
    const before = new Map(printers.map(o => [o.path, o.settings]));
    const path = p.path, tape = 'label_size' in changes;
    printers.forEach(o => {
        o.settings = Object.assign({}, o.settings);
        Object.entries(changes).filter(([k]) => o.path === path || COMMON.includes(k))
            .forEach(([k, v]) => { if (v) o.settings[k] = v; else delete o.settings[k]; });
    });
    if (tape) tapesSaving[path] = (tapesSaving[path] || 0) + 1;
    let saving = tape;
    const answered = () => { if (saving) { saving = false; tapesSaving[path]--; } };
    shown();
    const fd = new FormData();
    fd.set('printer', path);
    if (C.models[p.model]) fd.set('model', p.model);
    Object.entries(changes).forEach(([k, v]) => fd.set(k, v));
    settingsSaves.pending++;
    return post('/settings', fd).then(async r => {
        const answer = await r.json().catch(() => ({}));
        answered();
        if (!r.ok) throw new Error(answer.message || 'The settings could not be saved');
        const model = 'demo_model' in changes;
        if (!tape && !model) return;
        // The tape the server finds now, the status of all printers follows.
        // Another model comes with a roll and cutting it takes.
        settingsSaves.tapes++;
        [S.printer, ...S.printers].forEach(o => {
            if (o && o.path === path) Object.assign(o, { tape: answer.tape }, model ? { settings: answer.settings } : {});
        });
        shown();
        host.refresh();
    }).catch(e => {
        answered();
        // The printers shown now, a status may have replaced them meanwhile
        [S.printer, ...S.printers].forEach(o => { if (o && before.has(o.path)) o.settings = before.get(o.path); });
        toast(e.message);
        shown();
    }).finally(() => { settingsSaves.pending--; settingsSaves.answered++; });
}
// The editor places its label anew on another tape once it is shown again
function shown() {
    const tape = tapeState();
    detectTape();
    host.chip();
    drawCards(true);
    if (tapeState() !== tape) printerChanged();
}

// A label on the tape of printer p with its ends named, to see which one
// comes out first
function endsTest(p) {
    const d = baseDesign((p.tape && p.tape.id) || C.default_label_size), ask = L('Which end came out first?', 20);
    if (!runsAlong(d)) return Object.assign(d, { text: [L('TOP', 40, true), ask, L('BOTTOM', 40, true)] });
    const [top, bottom] = printerCaps(p).leaves_turned ? ['right', 'left'] : ['left', 'right'];
    return Object.assign(d, { text: [L('TOP', 40, true, top), ask, L('BOTTOM', 40, true, bottom)] });
}
function testLabel(p) {
    printJob({ title: 'Which end first', design: endsTest(p), values: {}, copies: 1, source: { kind: 'test' } }, entry => announce(entry, () => testLabel(p)), p);
}

// The list of printed labels shows the new one too
function announce(entry, again = () => reprint(entry)) {
    announcePrint(entry, again);
    if ($('#setHist')) drawHistory();
}
const reprint = h => printAgain(h, announce);

// --- Labels -----------------------------------------------------------------------

const formatDate = (iso, fmt) => { const [y, m, d] = iso.split('-'); return fmt.replace('%Y', y).replace('%m', m).replace('%d', d); };
const DATES = () => [['', 'Like the browser: ' + formatDate('2026-11-01', browserDateFormat())], ...C.date_formats.map(f => [f, formatDate('2026-11-01', f)])];

// Shown at once, then kept on the server, as the printer settings
async function keepLabels(box, changes, told) {
    const saving = saveLabelSettings(changes);
    DRAW.labels(box);
    let text;
    try {
        await saving;
        text = told();
    } catch (e) {
        text = e.message;
    }
    if (box.isConnected) DRAW.labels(box);
    toast(text);
}

DRAW.labels = box => {
    const server = familyOf(C.default_font), chosen = chosenFamily();
    box.innerHTML = `<header class="set-h"><div class="grow"><h2>Labels</h2><p class="muted">Kept on the server, the same in every browser.</p></div></header>
        <section class="set-card"><div class="set-grid">
            <label class="k" for="setDate">Dates on labels</label>
            <div class="set-stack"><select class="in set-sel" id="setDate">${DATES().map(([v, n]) => `<option value="${esc(v)}"${(S.settings.date_format || '') === v ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select>
                <small class="muted">For dates put on labels from now on and for date blanks. A date already on a label keeps its format.</small></div>
            <span class="k">Copies of several files</span>
            <div class="set-stack">${seg('collate', [['1', 'As sets: ABC ABC'], ['0', 'Grouped: AA BB CC']], S.settings.collate === '0' ? '0' : '1', 'Copies of several files')}
                <small class="muted">For several images or PDF pages printed more than once.</small></div>
            <span class="k" id="setFontK">Font of new labels</span>
            <div class="set-stack">${fontButton(`id="setFont" data-value="${esc(chosen)}" aria-labelledby="setFontK setFont"`, chosen || `The server's default: ${server}`, 'in set-sel fpick')}
                <small class="muted">New labels start with it, a new line takes the font of the line above. Labels you made keep their fonts.</small></div>
        </div></section>`;
    $('#setDate').onchange = e => {
        const f = e.target.value;
        keepLabels(box, { date_format: f }, () => 'Dates on new labels are written as ' + formatDate('2026-11-01', f || browserDateFormat()));
    };
    $$('[data-k="collate"]', box).forEach(b => b.onclick = () => keepLabels(box, { collate: b.dataset.v },
        () => b.dataset.v === '0' ? 'Copies of several files are grouped' : 'Copies of several files are printed as sets'));
    // A family only, new labels start with its regular style
    $('#setFont').onclick = () => openFontMenu({
        owner: 'labels', anchor: () => $('#setFont'), label: 'Font of new labels', closeOnPick: true,
        first: { value: '', label: `The server's default: ${server}` }, state: () => ({ family: chosenFamily() }),
        pick: f => keepLabels(box, { font_family: f }, () => `New labels start with ${f || server}`),
    });
};

// --- Printed labels -----------------------------------------------------------------

DRAW.queue = queuePage;
DRAW.printed = box => {
    box.innerHTML = `<header class="set-h"><div class="grow"><h2>Printed labels</h2><p class="muted">The labels printed in this browser, newest first.</p></div></header>
        <section class="set-card set-hist" id="setHist"></section>`;
    drawHistory();
    $('#setHist').addEventListener('click', e => {
        const b = e.target.closest('button[data-h]');
        const h = b && S.history.find(x => x.id === b.dataset.h);
        if (!h || b.disabled) return;
        if ('open' in b.dataset) openEditor({ title: h.title, design: h.design, values: h.values, origin: h.source && h.source.kind === 'saved' ? h.source : null });
        else reprint(h);
    });
};

function drawHistory() {
    const box = $('#setHist');
    if (!box) return;
    const list = S.history.slice(0, 60);
    box.innerHTML = list.length ? list.map(h => {
        const failed = h.state === 'failed' || h.state === 'unknown', who = h.printerName || (h.printer ? printerName({ model: h.printer }) : '');
        const sub = h.state === 'printing' ? 'Printing...' : failed ? `${h.state === 'unknown' ? 'Maybe not printed' : 'Not printed'}: ${h.message}` : [`${h.copies} x`, fmtTime(h.time), who].filter(Boolean).join(', ');
        return `<div class="set-hrow${failed ? ' failed' : ''}"><span class="set-thumb"><img alt="" data-thumb="${esc(h.id)}"></span>
            <div class="grow"><b>${esc(entryName(h))}</b><small class="${failed ? 'set-bad' : 'muted'}">${esc(sub)}</small></div>
            <button class="btn ghost sm" type="button" data-h="${esc(h.id)}" data-open>Open</button>
            <button class="btn soft sm" type="button" data-h="${esc(h.id)}" data-again${h.noImage ? ` disabled title="${esc(NO_IMAGE)}"` : ''}>${icon('refresh')}Print again</button></div>`;
    }).join('') : '<p class="set-empty muted">Nothing printed yet. Labels you print show up here.</p>';
    watchThumbs(box);
}

// Pictures of past labels as the server draws them now, a few at a time
// and only those scrolled into view
let thumbs = null;
const thumbQueue = [];
let thumbsRunning = 0;
function watchThumbs(box) {
    if (thumbs) thumbs.disconnect();
    thumbs = 'IntersectionObserver' in window ? new IntersectionObserver(seen => seen.forEach(e => {
        if (!e.isIntersecting) return;
        thumbs.unobserve(e.target);
        thumbQueue.push(e.target);
        nextThumb();
    }), { root: box.closest('.set-main'), rootMargin: '200px' }) : null;
    $$('img[data-thumb]', box).forEach(img => thumbs ? thumbs.observe(img) : (thumbQueue.push(img), nextThumb()));
}
async function nextThumb() {
    if (thumbsRunning >= 2 || !thumbQueue.length) return;
    const img = thumbQueue.shift();
    const h = S.history.find(x => x.id === img.dataset.thumb);
    if (!img.isConnected || !h) return nextThumb();
    thumbsRunning++;
    try {
        const p = await preview(h.design, h.values || {});
        if (img.isConnected) { img.src = p.url; img.parentElement.classList.add('shown'); }
    } catch (e) {
        if (img.isConnected) img.parentElement.classList.add('none');
    } finally {
        thumbsRunning--;
        nextThumb();
    }
}

// --- Connections --------------------------------------------------------------------

function connections() {
    const root = location.origin + C.base, site = location.origin + C.base.replace(/\/labeldesigner$/, '');
    const grocy = S.saved.find(s => s.title.toLowerCase() === 'grocy');
    const asn = `office/015-paperless-asn-17x54${S.lang === 'de' ? '-de' : ''}.json`;
    const mcp = `${site}/mcp`;
    return [
        {
            name: 'MCP for direct AI connections', icon: 'robot', doc: 'docs/mcp.md',
            what: 'AI assistants see the printers and the saved labels and print. It has no login, keep it in your home network.',
            lead: 'Assistants in the cloud, e.g. claude.ai or ChatGPT, cannot reach a server in your home network.',
            rows: [['Address', mcp]],
            // The setup of each client, as its documentation gives it for a
            // server reached over HTTP (see docs/mcp.md)
            clients: [
                ['Claude Code', 'Run', `claude mcp add --transport http labelburg ${mcp}`],
                ['VS Code', 'In .vscode/mcp.json', `{"servers": {"labelburg": {"type": "http", "url": "${mcp}"}}}`],
                ['Cursor', 'In ~/.cursor/mcp.json', `{"mcpServers": {"labelburg": {"url": "${mcp}"}}}`],
                ['Gemini CLI', 'Run', `gemini mcp add --transport http labelburg ${mcp}`],
                ['Codex CLI', 'Run', `codex mcp add labelburg --url ${mcp}`],
                ['Zed', 'In its settings.json', `"context_servers": {"labelburg": {"url": "${mcp}"}}`],
                ['LM Studio', 'In mcp.json (Program, Install, Edit mcp.json)', `{"mcpServers": {"labelburg": {"url": "${mcp}"}}}`],
                ['Home Assistant', 'Add the integration "Model Context Protocol" with the address', mcp],
                ['Open WebUI', 'Admin settings, Integrations, External Tool Servers: add a connection of the type "MCP (Streamable HTTP)" with the address', mcp],
            ],
        },
        {
            name: 'Homebox', doc: 'docs/homebox.md',
            what: 'Homebox gets the labels of items and locations from here, made for the tape in your printer, and prints them with its print button.',
            rows: [['HBOX_LABEL_MAKER_LABEL_SERVICE_URL', `${root}/api/homebox`],
                ['HBOX_LABEL_MAKER_PRINT_COMMAND', `wget -q -O - --header=Content-Type:image/png --post-file={{.FileName}} ${root}/api/homebox`]],
        },
        {
            name: 'Grocy', doc: 'docs/grocy.md',
            what: 'Grocy prints labels of products, stock entries, chores, recipes and batteries with your saved label "Grocy".',
            rows: [['GROCY_LABEL_PRINTER_WEBHOOK', `${root}/api/repository/print?name=${encodeURIComponent(grocy ? grocy.name : 'Grocy.json')}`]],
            note: grocy ? '' : 'Save a label as "Grocy" first, the template "Grocy" is a good start.',
            open: grocy ? null : S.demoList.find(d => d.title === 'Grocy' && d.language === S.lang) || S.demoList.find(d => d.title === 'Grocy'),
        },
        {
            name: 'Paperless-ngx', doc: 'docs/paperless.md',
            what: 'Labels with the next archive serial number (ASN), to stick on paper before it is scanned. Each label gets the next free number.',
            rows: [['Print three ASN labels', `curl -X POST '${root}/api/demos/print?id=${asn}&print_count=3'`]],
            note: 'With PAPERLESS_URL and PAPERLESS_TOKEN set for this server the numbers continue where those in Paperless end.',
        },
        {
            name: 'Webhook', doc: 'README.md#api',
            what: 'Other programs send images to print. It is off unless WEBHOOK_PASSWORD is set for this server.',
            rows: [['Address', `${root}/api/webhook/print`],
                ['Example', `curl -X POST ${root}/api/webhook/print -H "Authorization: Bearer $WEBHOOK_PASSWORD" -F images=@label.png`]],
        },
    ];
}

DRAW.connections = box => {
    const list = connections();
    box.innerHTML = `<header class="set-h"><div class="grow"><h2>Connections</h2><p class="muted">Addresses for other programs, with this server's address filled in.</p></div></header>
        ${list.map((c, i) => `<section class="set-card set-conn">
            <div class="set-conn-h">${c.icon ? ic(c.icon) : ''}<h3>${esc(c.name)}</h3><span class="sp"></span><a class="btn ghost sm" href="${esc(`${SOURCE}/blob/master/${c.doc}`)}" target="_blank" rel="noopener">How to set it up${ic('out')}</a></div>
            <p class="muted">${esc(c.what)}</p>
            ${c.lead ? `<p class="set-note small">${esc(c.lead)}</p>` : ''}
            ${c.rows.map(([k, v], j) => `<div class="set-copy"><span class="k">${esc(k)}</span><div class="set-copy-v"><code id="conn${esc(i)}-${esc(j)}">${esc(v)}</code>
                <button class="btn soft sm" type="button" data-copy="conn${esc(i)}-${esc(j)}" aria-label="Copy ${esc(k)}">${ic('copy')}Copy</button></div></div>`).join('')}
            ${c.clients ? `<div class="set-clients" data-clients="${esc(i)}"><span class="seg" role="group" aria-label="Clients">${c.clients.map(([n], j) => `<button type="button" data-client="${esc(j)}" class="${j ? '' : 'on'}" aria-pressed="${!j}">${esc(n)}</button>`).join('')}</span>
                <div class="set-copy"><span class="k" id="conn${esc(i)}-k">${esc(c.clients[0][1])}</span><div class="set-copy-v"><code id="conn${esc(i)}-client">${esc(c.clients[0][2])}</code>
                <button class="btn soft sm" type="button" data-copy="conn${esc(i)}-client" aria-label="Copy the setup">${ic('copy')}Copy</button></div></div></div>` : ''}
            ${c.note || c.open ? `<p class="set-note small">${esc(c.note || '')}${c.open ? ` <button class="custom" type="button" data-tpl="${esc(c.open.id)}">Open the template</button>` : ''}</p>` : ''}
        </section>`).join('')}`;
    $$('[data-copy]', box).forEach(b => b.onclick = () => copy($('#' + b.dataset.copy)));
    // One client at a time, its line of setup below
    $$('[data-clients]', box).forEach(el => {
        const i = +el.dataset.clients, clients = list[i].clients;
        $$('[data-client]', el).forEach(b => b.onclick = () => {
            const [, where, line] = clients[+b.dataset.client];
            $$('[data-client]', el).forEach(x => { x.classList.toggle('on', x === b); x.setAttribute('aria-pressed', String(x === b)); });
            $('#conn' + i + '-k').textContent = where;
            $('#conn' + i + '-client').textContent = line;
        });
    });
    $$('[data-tpl]', box).forEach(b => b.onclick = async () => {
        const d = S.demoList.find(x => x.id === b.dataset.tpl);
        try { openEditor({ title: d.title, design: await itemDesign(demoItem(d)), origin: originOf('demo', d.id) }); } catch (e) { toast(e.message); }
    });
};

async function copy(code) {
    try {
        await navigator.clipboard.writeText(code.textContent);
        toast('Copied');
    } catch (e) {
        // Without a secure context the browser keeps the clipboard to itself
        getSelection().selectAllChildren(code);
        toast('Press Ctrl C to copy');
    }
}

// --- About -----------------------------------------------------------------------------

DRAW.about = (box, report) => {
    const b = window.DESIGNER_BUILD || {};
    const theme = document.documentElement.getAttribute('data-theme') || '';
    const version = b.release ? `<a href="${esc(SOURCE)}/releases/tag/${esc(b.release)}" target="_blank" rel="noopener">${esc(b.release)}</a>${b.commit ? `, commit ${esc(b.commit.slice(0, 7))}` : ''}`
        : b.commit ? `<a href="${esc(SOURCE)}/commit/${esc(b.commit)}" target="_blank" rel="noopener">Commit ${esc(b.commit.slice(0, 7))}</a>` : 'A development version';
    box.innerHTML = `<header class="set-h"><div class="grow"><h2>About</h2><p class="muted">Labelburg makes and prints labels on label printers in your home network.</p></div></header>
        <section class="set-card"><div class="set-grid">
            <span class="k">Appearance</span>
            <div class="set-stack"><div class="seg" role="group" aria-label="Appearance">${[['', 'Like the system', 'screen'], ['light', 'Light', 'sun'], ['dark', 'Dark', 'moon']].map(([v, n, i]) =>
                `<button type="button" data-theme="${esc(v)}"${v === theme ? ' class="on" aria-pressed="true"' : ' aria-pressed="false"'}>${ic(i)}${esc(n)}</button>`).join('')}</div>
                <small class="muted">Kept in this browser.</small></div>
            <span class="k">Version</span>
            <div class="set-stack"><span class="set-val" id="setVersion">${version}</span>${b.date ? `<small class="muted">Built ${esc(b.date)}</small>` : ''}</div>
            <span class="k">Source code</span>
            <div class="set-stack"><a href="${esc(SOURCE)}" target="_blank" rel="noopener">github.com/DL6ER/labelburg</a><small class="muted">Free software under the GNU GPL v3. Questions, ideas and printers that do not work yet are welcome there.</small></div>
            <span class="k">Problems</span>
            <div class="set-stack"><div class="set-report"><a class="btn soft sm" id="setReport" href="${esc(SOURCE)}/issues/new?template=bug_report.yml" target="_blank" rel="noopener">Report a problem${ic('out')}</a>
                <button class="btn ghost sm" type="button" id="setReportCopy" hidden>${ic('copy')}Copy details</button></div>
                <div class="set-details" id="setDetails" hidden></div>
                <small class="muted">Opens the form on GitHub with these details filled in. Nothing is sent until you send the form. Without a GitHub account, copy the details into your question.</small></div>
        </div></section>`;
    let shown = report;
    // From the server, which knows the printers, see /api/problem_report
    const show = answer => {
        shown = answer;
        $('#setReport', box).href = answer.url;
        $('#setDetails', box).textContent = answer.text;
        $('#setDetails', box).hidden = $('#setReportCopy', box).hidden = false;
        $('#setReportCopy', box).onclick = () => copy($('#setDetails', box));
    };
    if (report) show(report);
    else fetch(API + '/problem_report').then(r => r.ok ? r.json() : null)
        .then(answer => answer && $('#setReport', box) && show(answer)).catch(() => { });
    $$('[data-theme]', box).forEach(btn => btn.onclick = () => { host.theme(btn.dataset.theme || null); DRAW.about(box, shown); });
};
