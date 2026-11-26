// Images and codes in the editor: adding them, the toolbar of each when it
// is picked, files chosen, dropped or pasted, and stepping through several
// files or PDF pages.

import {
    $, $$, esc, icon, latest, toast, S, C, isColorTape, redTape, withImage, withCode, labelFiles, setWide, dpiOf, dotsToMm, mmToDots,
    preview, imageKey, templateOf, collated, listJoin, onTape
} from './core.js';
import { swatchHTML, openColors } from './colors.js';

// What editor.js hands over: hooks, its state, apply(), pick() and the preview
let ed = null;
// The file or PDF page shown (unit), the units of the label, and what the
// last preview told: the order of the parts, the distance to the text
const M = { edit: null, unit: 0, units: [], keep: null, order: '', direction: '', toldFor: null, gap: 0, highRes: false, counts: new Map() };

// --- Kinds of code ----------------------------------------------------------------

// What the server tells of a kind of code: its name, whether it is a 2D code
// and the levels of error correction it has
const codeKind = type => S.codes.find(c => c.type === type) || { type, name: type, two_d: false, corrections: [] };
const LEVELS = [['L', 'Low, 7 %'], ['M', 'Medium, 15 %'], ['Q', 'Quartile, 25 %'], ['H', 'High, 30 %']];
// The level kept for another kind of code: the same, else the next lower one
// it has, else its lowest
function levelFor(kind, level) {
    const order = LEVELS.map(([v]) => v), has = kind.corrections || [];
    if (!has.length || has.includes(level)) return level;
    return has.filter(v => order.indexOf(v) < order.indexOf(level)).pop() || has[0];
}

// --- The content of a code, its templates shown as [Name] -----------------------

// As typed: [Name] for a blank,
// [today+5d] or [now] for a date, [counter], [ASN] and [id]
const TEMPLATE_RE = /\{\{(?:blank:([^}]+)|(datetime)([+-]\d+(?:min|[hdwmy]))?:[^}]+|counter(:[0-9A-Za-z]+(?::-?\d+)?)?|asn(:\d{1,2})?|short-uuid)\}\}/g;
function shownAs(m) {
    if (m[1] !== undefined) return `[${m[1]}]`;
    if (m[2]) return `[${/%[HIMp]/.test(m[0]) ? 'now' : 'today'}${m[3] || ''}]`;
    if (m[0].startsWith('{{counter')) return `[counter${m[4] || ''}]`;
    if (m[0].startsWith('{{asn')) return `[ASN${m[5] || ''}]`;
    return '[id]';
}
function displayed(text) {
    text = String(text || '');
    const tokens = [];
    let out = '', last = 0;
    for (const m of text.matchAll(TEMPLATE_RE)) {
        out += text.slice(last, m.index);
        tokens.push({ at: out.length, shown: shownAs(m), template: m[0] });
        out += shownAs(m);
        last = m.index + m[0].length;
    }
    return { text: out + text.slice(last), tokens };
}
// A template shown before the change and after it stays as it was, e.g. a
// [today] keeps its format; caret, where the change ends, tells which of two
// same ones was deleted
function fromDisplay(text, previous, caret) {
    const old = displayed(previous), was = old.text, now = String(text || '');
    const maxPre = caret === null ? now.length : caret - Math.max(0, now.length - was.length), maxSuf = caret === null ? now.length : now.length - caret;
    let pre = 0, suf = 0;
    while (pre < was.length && pre < now.length && pre < maxPre && was[pre] === now[pre]) pre++;
    while (suf < was.length - pre && suf < now.length - pre && suf < maxSuf && was[was.length - 1 - suf] === now[now.length - 1 - suf]) suf++;
    const kept = [...now.matchAll(/\[([^\]\n]+)\]/g)].map(({ 0: m, index: i }) => {
        const at = i + m.length <= pre ? i : i >= now.length - suf ? i - now.length + was.length : -1;
        return old.tokens.find(t => t.at === at && t.shown === m);
    });
    let seen = 0;
    return now.replace(/\[([^\]\n]+)\]/g, (m, name) => {
        const own = kept[seen++];
        return own ? own.template : templateOf(name);
    });
}
const codeRows = text => Math.min(8, Math.max(1, String(text).split('\n').length));

// --- The kind of label follows from what it has ------------------------------------

const hasTextLines = d => d.text.some(l => String(l.text || '').trim());
// Whether the code or image has something next to it: text, or the code of
// an image label with a code as well
const paired = d => hasTextLines(d) || d.print_type === 'image_qrcode_text';
const lookOf = type => type === 'qrcode_text' ? 'code' : type === 'image' || type === 'image_qrcode_text' ? 'image' : null;
// Rotation, cropping and scale belong to the image, without one to the code:
// they start anew where they would pass from one to the other
function setType(d, type) {
    const was = lookOf(d.print_type), now = lookOf(type);
    if (now && now !== was && (was || now === 'code')) Object.assign(d, { image_rotation: '0', image_crop: '0', image_scaling_factor: '100' });
    // A text label getting its first image or code is made wide, the lines
    // next to it start on the left
    if (d.print_type === 'text' && type !== 'text') {
        setWide(d, true);
        d.text.forEach(l => { if (l.align === 'center') l.align = 'left'; });
    }
    d.print_type = type;
}

function addCode() {
    const d = S.edit.design;
    if (!withCode(d)) {
        ed.apply(x => {
            setType(x, withImage(x) ? 'image_qrcode_text' : 'qrcode_text');
            // Next to text, a code without content of its own holds the text
            if (!x.code_text && !hasTextLines(x)) x.code_text = 'https://example.com';
            if (x.qrcode_size === '10') x.qrcode_size = '14';
        });
    }
    ed.pick({ part: 'code' });
    const content = $('#ltb [data-ctext]');
    if (content) { content.focus(); content.select(); }
}

function removeCode() {
    closeMenu();
    ed.apply(x => setType(x, withImage(x) ? 'image' : 'text'));
    ed.pick({ part: 'line', index: 0 });
}

function removeImage() {
    closeMenu();
    startUnits();
    ed.apply(x => {
        delete x.image;
        delete x.more_images;
        setType(x, withCode(x) ? 'qrcode_text' : 'text');
    });
    ed.pick({ part: 'line', index: 0 });
}

// --- Files -----------------------------------------------------------------------------

const isImageFile = f => /^image\/|pdf$/.test(f.type);
const isPdf = f => /pdf/i.test(f.mime);
function readFile(file) {
    return new Promise((resolve, reject) => {
        const fr = new FileReader();
        fr.onload = () => {
            const [head, data] = String(fr.result).split(',');
            resolve({ data, mime: (head.match(/data:([^;]+)/) || [])[1] || file.type, name: file.name || 'image' });
        };
        fr.onerror = () => reject(fr.error);
        fr.readAsDataURL(file);
    });
}

// Images and PDFs, chosen, dropped or pasted, replace those of the label.
// Several print one after the other, each as a label of its own.
async function readImages(list) {
    const files = Array.from(list || []).filter(isImageFile);
    if (!files.length) { toast('Images and PDFs can go on the label'); return; }
    ed.markBusy();
    let read;
    try {
        read = await Promise.all(files.map(readFile));
    } catch (e) {
        toast('The file could not be read');
        ed.redraw();
        return;
    }
    const [first, ...more] = read;
    closeMenu();
    startUnits();
    ed.apply(x => {
        x.image = first;
        if (more.length) x.more_images = more; else delete x.more_images;
        if (!withImage(x)) setType(x, withCode(x) ? 'image_qrcode_text' : 'image');
    });
    ed.pick({ part: 'image' });
}

// The file shown stays shown when another one is removed
function removeFile(i) {
    const files = labelFiles(S.edit.design), now = unitNow();
    if (files.length < 2) { removeImage(); return; }
    M.keep = now && now.file !== i ? { key: now.key, page: now.page } : null;
    startUnits();
    ed.apply(x => {
        const left = labelFiles(x).filter((_, k) => k !== i);
        x.image = left[0];
        if (left.length > 1) x.more_images = left.slice(1); else delete x.more_images;
    });
}
// New files are shown from the first one on
function startUnits() {
    unitNow();
    M.unit = 0;
    M.units = [];
}

function chooseFiles() {
    const input = $('#imageFile');
    input.value = '';
    input.click();
}

function addImage() {
    const d = S.edit.design;
    if (withImage(d) && d.image) ed.pick({ part: 'image' });
    else chooseFiles();
}

// --- Several files and PDF pages ----------------------------------------------------------

// The pages a selection like "1-3, 5", "4-" or "all" names, as
// selected_pdf_pages() on the server; null where it names none
function selectedPages(selection, count) {
    selection = String(selection || '').trim().toLowerCase();
    if (!selection) return [1];
    if (selection === 'all') return Array.from({ length: count }, (_, i) => i + 1);
    const pages = [];
    for (const part of selection.split(',').map(p => p.trim()).filter(Boolean)) {
        const m = part.match(/^(\d+)\s*(-\s*(\d*))?$/);
        if (!m || (m[3] && +m[3] < +m[1])) return null;
        const first = +m[1], last = m[2] === undefined ? first : m[3] ? +m[3] : count;
        if (first < 1 || last > count) return null;
        for (let p = first; p <= last; p++) pages.push(p);
    }
    return pages.length ? pages : null;
}

// The page count of a PDF, from a preview of it alone
function pagesOf(d, f) {
    const k = imageKey(f);
    if (!M.counts.has(k)) {
        const p = preview(Object.assign({}, d, { image: f, more_images: undefined, pdf_pages: '' }), {})
            .then(r => { M.counts.set(k, r.pageCount || 1); return r.pageCount || 1; }, () => { M.counts.delete(k); return 1; });
        M.counts.set(k, p);
    }
    return M.counts.get(k);
}

// Each file is a unit, each page of a PDF that prints too
async function unitsOf(d) {
    const units = [];
    for (const [i, f] of labelFiles(d).entries()) {
        const key = imageKey(f);
        if (!isPdf(f)) { units.push({ file: i, key, page: null, name: f.name }); continue; }
        const pages = selectedPages(d.pdf_pages, await pagesOf(d, f)) || [null];
        pages.forEach((page, k) => units.push({ file: i, key, page, first: k === 0, name: f.name }));
    }
    return units;
}

// The labels each copy of a label prints
export const perCopyOf = async d => (await unitsOf(d)).length;

function unitNow() {
    if (M.edit !== S.edit) { M.edit = S.edit; M.unit = 0; M.units = []; }
    return M.units[Math.min(M.unit, M.units.length - 1)] || null;
}

// The label as the preview shows it: the unit stepped to, alone
function shown(d) {
    const u = unitNow();
    if (!u || !withImage(d) || (u.file === 0 && (u.page === null || u.first))) return d;
    const f = labelFiles(d)[u.file];
    if (!f) return d;
    return Object.assign({}, d, { image: f, more_images: undefined, pdf_pages: u.page ? String(u.page) : d.pdf_pages });
}

const countUnits = latest(async () => {
    const E = S.edit;
    if (!E) return;
    const units = await unitsOf(E.design);
    if (S.edit !== E) return;
    // Before the units are known, the first one is shown
    const before = unitNow() || units[0], counted = M.units.length;
    M.units = units;
    // The file shown before stays shown, e.g. after another one was removed
    const kept = M.keep ? units.findIndex(u => u.key === M.keep.key && u.page === M.keep.page) : -1;
    if (M.keep) M.unit = Math.max(0, kept);
    M.keep = null;
    if (M.unit >= units.length) M.unit = 0;
    const now = unitNow();
    drawStepper();
    if (before && now && (before.file !== now.file || before.page !== now.page)) { ed.markBusy(); ed.redraw(); }
    // The note on the last page of a roll with several labels counts them all
    else if (units.length !== counted) ed.redraw();
});

function step(by) {
    const n = M.units.length;
    if (n < 2) return;
    M.unit = (Math.min(M.unit, n - 1) + by + n) % n;
    drawStepper();
    ed.markBusy();
    ed.redraw();
}

const unitName = u => u ? (u.page ? `${u.name}, page ${u.page}` : u.name) : '';
function stepperHTML() {
    const n = M.units.length, u = unitNow();
    return `<span class="unit-step"${n > 1 ? '' : ' hidden'}><span class="sep"></span>
        <button type="button" class="tb" data-unit="-1" title="Previous" aria-label="Previous">${icon('prev')}</button>
        <span class="unit-of" data-unitof title="${esc(unitName(u))}">${n > 1 ? esc(`${Math.min(M.unit, n - 1) + 1} of ${n}`) : ''}</span>
        <button type="button" class="tb" data-unit="1" title="Next" aria-label="Next">${icon('next')}</button></span>`;
}
// Changed in place, the buttons keep the focus
function drawStepper() {
    const box = $('#ltb .unit-step');
    if (!box) return;
    const n = M.units.length, of = $('[data-unitof]', box);
    box.hidden = n < 2;
    of.textContent = n > 1 ? `${Math.min(M.unit, n - 1) + 1} of ${n}` : '';
    of.title = unitName(unitNow());
    if (menu && !menu.el.contains(document.activeElement)) drawMenu();
}
function bindStepper(bar) {
    $$('[data-unit]', bar).forEach(b => b.onclick = () => step(+b.dataset.unit));
    const box = $('.unit-step', bar);
    if (box) box.onkeydown = e => {
        if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
        e.preventDefault();
        step(e.key === 'ArrowLeft' ? -1 : 1);
    };
}

// --- Shared pieces of the toolbars -----------------------------------------------------------

const STEPS = [1, 1, 2, 5, 10];
// Quick clicks in a row take bigger steps
function stepper() {
    let last = 0, n = 0;
    return () => { const now = Date.now(); n = now - last < 450 ? Math.min(n + 1, STEPS.length - 1) : 0; last = now; return STEPS[n]; };
}
const codeStep = stepper(), scaleStep = stepper();
const fitted = d => d.image_fit === '1';
// The size of a dot or the scale (now) at which the code or image is as
// large as fitted to the label: the preview of it fitted and one at now tell
async function sizeLike(d, part, now) {
    const width = p => { const b = ((p && p.parts) || []).find(x => x.part === part); return b ? b.box[2] - b.box[0] : 0; };
    const fit = width(ed.ui.last);
    if (!fit) return now;
    try {
        const fixed = width(await preview(Object.assign({}, shown(d), { image_fit: '0' }), {}));
        return fixed ? now * fit / fixed : now;
    } catch (e) { return now; }
}
// --- The order of image, code and text ------------------------------------------------

const PART_NAMES = { image: 'Image', code: 'Code', text: 'Text' };
// The parts the label has, by the names of parts_order
const partsOf = d => [withImage(d) && d.image && 'image', withCode(d) && 'code', hasTextLines(d) && 'text'].filter(Boolean);
// What the label's own fields stand for until a preview of it tells, as
// _arrangement() on the server reads them: the side of image_position,
// else left of the text next to a code, above it across the tape, left of
// it along; the code of an image label on the other side
function sideOrder(d) {
    const main = withImage(d) ? 'image' : 'code';
    const side = d.image_position || (d.print_type === 'image_qrcode_text' || d.orientation === 'rotated' ? 'left' : 'above');
    const order = main === 'image' ? ['image', 'text', 'code'] : ['code', 'text'];
    return { order: side === 'right' || side === 'below' ? order.reverse() : order, direction: side === 'left' || side === 'right' ? 'row' : 'column' };
}
// The last preview's order belongs to the label it was made of
const told = () => M.toldFor === S.edit ? M : {};
// As set, else as the server laid them out; parts added since follow in
// the order of the side
function orderOf(d) {
    const has = partsOf(d), named = order => (Array.isArray(order) ? order : String(order || '').split(',')).filter(p => has.includes(p));
    return [...new Set([...named(d.parts_order), ...named(told().order), ...named(sideOrder(d).order), ...has])];
}
const directionOf = d => d.parts_direction || told().direction || sideOrder(d).direction;

// One order for the server: it replaces the side of image_position
function setOrder(parts, direction, focus) {
    ed.apply(x => Object.assign(x, { parts_order: parts.join(','), parts_direction: direction, image_position: '' }));
    const el = focus && $(`#ltb ${focus}`);
    if (el) el.focus();
}
function moveTo(name, to) {
    const d = S.edit.design, parts = orderOf(d), from = parts.indexOf(name);
    if (from < 0 || to < 0 || to >= parts.length || to === from) return;
    parts.splice(to, 0, parts.splice(from, 1)[0]);
    setOrder(parts, directionOf(d), `[data-opart="${CSS.escape(name)}"]`);
}

// The parts as chips in their order, a button between two to swap them,
// and the direction; filled in by drawOrder() where the label has two
const orderHTML = () => '<span class="order" data-order hidden></span>';
function drawOrder() {
    const box = $('#ltb [data-order]');
    if (!box || ed.heldBack(drawOrder)) return;
    const d = S.edit.design, parts = orderOf(d), dir = directionOf(d), sig = `${parts}|${dir}`;
    if (box.dataset.sig === sig) return;
    const focused = box.contains(document.activeElement) ? document.activeElement : null;
    const refocus = focused && (focused.dataset.opart ? `[data-opart="${CSS.escape(focused.dataset.opart)}"]` : focused.dataset.oswap ? `[data-oswap="${CSS.escape(focused.dataset.oswap)}"]` : `[data-odir="${CSS.escape(focused.dataset.odir || '')}"]`);
    box.dataset.sig = sig;
    box.hidden = parts.length < 2;
    const name = p => PART_NAMES[p].toLowerCase();
    box.innerHTML = box.hidden ? '' : `<span class="sep"></span><span class="olabel" aria-hidden="true">Order</span>
        <span class="ochips" role="group" aria-label="Order on the label" title="Drag a part to change the order, or move it with the arrow keys">${parts.map((p, i) =>
            `${i ? `<button type="button" class="oswap" data-oswap="${esc(i - 1)}" title="Swap ${esc(name(parts[i - 1]))} and ${esc(name(p))}" aria-label="Swap ${esc(name(parts[i - 1]))} and ${esc(name(p))}">${icon('swap', '')}</button>` : ''}`
            + `<button type="button" class="ochip" data-opart="${esc(p)}" aria-label="${esc(PART_NAMES[p])}, ${esc(i + 1)} of ${esc(parts.length)}">${icon('grip')}${esc(PART_NAMES[p])}</button>`).join('')}</span>
        <span class="seg" title="How the parts follow each other"><button type="button" data-odir="row" class="${dir === 'row' ? 'on' : ''}" title="Side by side, as the text reads">Row</button><button type="button" data-odir="column" class="${dir === 'column' ? 'on' : ''}" title="One below the other">Column</button></span>`;
    // Each reads the order the label has when used
    $$('[data-opart]', box).forEach(chip => {
        chip.onkeydown = e => {
            const by = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 }[e.key];
            if (!by) return;
            e.preventDefault();
            moveTo(chip.dataset.opart, orderOf(S.edit.design).indexOf(chip.dataset.opart) + by);
        };
        chip.onpointerdown = e => dragChip(e, chip, box);
    });
    $$('[data-oswap]', box).forEach(b => b.onclick = () => {
        const i = +b.dataset.oswap, swapped = orderOf(S.edit.design);
        [swapped[i], swapped[i + 1]] = [swapped[i + 1], swapped[i]];
        setOrder(swapped, directionOf(S.edit.design), `[data-oswap="${CSS.escape(String(i))}"]`);
    });
    $$('[data-odir]', box).forEach(b => b.onclick = () => setOrder(orderOf(S.edit.design), b.dataset.odir, `[data-odir="${CSS.escape(b.dataset.odir)}"]`));
    const back = refocus && $(refocus, box);
    if (back) back.focus();
}

// A chip follows the pointer, the others make room where it would land
function dragChip(e, chip, box) {
    if (e.button !== 0) return;
    const chips = $$('[data-opart]', box), from = chips.indexOf(chip), x0 = e.clientX;
    const rects = chips.map(c => c.getBoundingClientRect()), mid = r => r.left + r.width / 2;
    let to = from, dragging = false;
    chip.setPointerCapture(e.pointerId);
    // It stays between the first and the last chip
    const least = rects[0].left - rects[from].left, most = rects[rects.length - 1].right - rects[from].right;
    chip.onpointermove = ev => {
        const dx = Math.min(most, Math.max(least, ev.clientX - x0));
        if (!dragging && Math.abs(ev.clientX - x0) < 4) return;
        dragging = true;
        box.classList.add('dragging');
        chip.classList.add('lifted');
        chip.style.transform = `translateX(${dx}px)`;
        // Past the middle of a chip with its leading edge
        to = from + rects.filter((r, i) => i > from && rects[from].right + dx > mid(r)).length
            - rects.filter((r, i) => i < from && rects[from].left + dx < mid(r)).length;
        chips.forEach((c, i) => {
            if (c === chip) return;
            const shift = i > from && i <= to ? rects[i - 1].left - rects[i].left : i < from && i >= to ? rects[i + 1].left - rects[i].left : 0;
            c.style.transform = shift ? `translateX(${shift}px)` : '';
        });
    };
    chip.onpointerup = chip.onpointercancel = ev => {
        chip.onpointermove = chip.onpointerup = chip.onpointercancel = null;
        box.classList.remove('dragging');
        chips.forEach(c => { c.style.transform = ''; c.classList.remove('lifted'); });
        if (dragging && ev.type === 'pointerup') moveTo(chip.dataset.opart, to);
    };
}

function gapRow(d) {
    const on = paired(d);
    const auto = M.gap ? `Automatic, ${dotsToMm(M.gap, dpiOf(d.label_size, M.highRes))}` : 'Automatic';
    return `<label class="mi${on ? '' : ' off'}" title="${on ? 'Leave it empty for a distance that suits the size of the text' : 'Only with text on the label'}">${icon('gap')}<span>Distance to the text</span>
        <input class="in num" type="number" min="0" max="50" step="0.5" data-gap value="${d.image_text_gap === '' ? '' : esc(dotsToMm(d.image_text_gap, dpiOf(d.label_size)))}" placeholder="${on ? esc(auto) : ''}"${on ? '' : ' disabled'} aria-label="Distance to the text in mm"><small>mm</small></label>`;
}
function bindGap(el) {
    const gap = $('[data-gap]', el);
    if (gap) gap.addEventListener('input', () => {
        const v = gap.value.trim();
        if (v !== '' && !(+v >= 0)) return;
        S.edit.design.image_text_gap = v === '' ? '' : String(mmToDots(+v, dpiOf(S.edit.design.label_size)));
        ed.changed(false);
    });
}

// "..." of the code and the image: drawn anew after each change it makes
let menu = null;
function openMenu(btn, render) {
    if (menu && menu.render === render) { closeMenu(); return; }
    closeMenu();
    const el = document.createElement('div');
    el.className = 'menu media-menu';
    $('#center').appendChild(el);
    menu = { el, render };
    drawMenu();
    const r = btn.getBoundingClientRect(), c = $('#center').getBoundingClientRect();
    el.style.left = `${Math.max(8, Math.min(r.left - c.left - 8, c.width - el.offsetWidth - 8))}px`;
    el.style.top = `${r.bottom - c.top + 8}px`;
    btn.setAttribute('aria-expanded', 'true');
    document.addEventListener('mousedown', outside, true);
}
function outside(e) {
    if (!menu || menu.el.contains(e.target) || e.target.closest('#ltb [data-more]')) return;
    closeMenu();
}
function closeMenu() {
    if (!menu) return;
    menu.el.remove();
    menu = null;
    document.removeEventListener('mousedown', outside, true);
    const b = $('#ltb [data-more]');
    if (b) b.setAttribute('aria-expanded', 'false');
}
function drawMenu() {
    if (!menu) return;
    if (!menu.el.isConnected) { closeMenu(); return; }
    menu.render(menu.el);
}
// A change from the menu: the toolbar follows, the menu shows it too
const fromMenu = (fn, opts) => { ed.apply(fn, opts); drawMenu(); };

// --- The code -------------------------------------------------------------------------------

function codeBar(bar) {
    const d = S.edit.design;
    if (!withCode(d)) { closeMenu(); ed.pick({ part: 'line', index: 0 }); return; }
    const kind = codeKind(d.barcode_type), withImg = d.print_type === 'image_qrcode_text';
    const fit = fitted(d);
    // A 2D code is sized by its dots, a barcode by a scale; next to an image
    // the code takes the image's way of sizing
    const sizing = kind.two_d ? 'dots' : withImg ? '' : 'scale';
    const value = sizing === 'dots' ? d.qrcode_size : d.image_scaling_factor;
    const sizeTitle = withImg && fit ? 'Fitted with the image, set the size of the image in % to size the code' : sizing === 'dots' ? 'Size of a dot' : 'Scale in %';
    const shownText = displayed(d.code_text).text;
    bar.className = 'float media';
    bar.innerHTML = `<label class="fsel" title="Kind of code"><select data-kind aria-label="Kind of code">${[['2D codes', true], ['Barcodes', false]].map(([group, twoD]) =>
        `<optgroup label="${esc(group)}">${S.codes.filter(c => !!c.two_d === twoD).map(c => `<option value="${esc(c.type)}"${c.type === d.barcode_type ? ' selected' : ''}>${esc(c.name)}</option>`).join('')}</optgroup>`).join('')}
            ${S.codes.some(c => c.type === d.barcode_type) ? '' : `<option selected>${esc(d.barcode_type)}</option>`}</select>${icon(kind.two_d ? 'qr' : 'barcode')}<span>${esc(kind.name)}</span>${icon('down')}</label>
        <textarea class="cin" data-ctext rows="${esc(codeRows(shownText))}" wrap="off" spellcheck="false" placeholder="${hasTextLines(d) ? 'Empty: the text of the label' : 'Link or text'}" aria-label="Content of the code">${esc(shownText)}</textarea>
        ${sizing ? `<span class="sep"></span><span class="az" title="${esc(sizeTitle)}"><button type="button" class="azb" data-csize="-1" aria-label="Smaller"${withImg && fit ? ' disabled' : ''}>${icon('minus', '')}</button>
            <input class="anum num" data-csizein value="${fit ? '' : esc(value)}" placeholder="Fit" inputmode="numeric" aria-label="${esc(sizeTitle)}"${withImg && fit ? ' disabled' : ''}>
            <button type="button" class="azb" data-csize="1" aria-label="Larger"${withImg && fit ? ' disabled' : ''}>${icon('plus', '')}</button></span>` : ''}
        ${orderHTML()}
        ${swatchHTML('data-ccolor', d.print_color, onTape(d), 'the code')}
        <span class="sep"></span>
        <button type="button" class="tb${d.qrcode_correction !== 'M' && kind.corrections.length || d.image_text_gap !== '' ? ' dot' : ''}" data-more aria-expanded="${!!menu}" title="More for the code" aria-label="More for the code">${icon('more')}</button>`;
    $('[data-kind]', bar).onchange = e => ed.apply(x => {
        x.barcode_type = e.target.value;
        const k = codeKind(x.barcode_type);
        // A 2D code has the size of a dot instead of a scale
        if (k.two_d && x.print_type !== 'image_qrcode_text') x.image_scaling_factor = '100';
        x.qrcode_correction = levelFor(k, x.qrcode_correction);
    });
    const content = $('[data-ctext]', bar);
    content.addEventListener('input', () => {
        content.rows = codeRows(content.value);
        S.edit.design.code_text = fromDisplay(content.value, S.edit.design.code_text, content.selectionStart);
        ed.changed(false);
    });
    const sizeTo = (v, opts) => ed.apply(x => {
        x.image_fit = '0';
        if (sizing === 'dots') x.qrcode_size = String(Math.max(1, Math.min(40, v)));
        else x.image_scaling_factor = String(Math.max(1, Math.min(500, v)));
    }, opts);
    $$('[data-csize]', bar).forEach(b => b.onclick = async () => {
        const x = S.edit.design, dir = +b.dataset.csize, by = codeStep() * (sizing === 'dots' ? 1 : 10);
        const now = +(sizing === 'dots' ? x.qrcode_size : x.image_scaling_factor) || 1;
        if (!fitted(x)) { sizeTo(now + dir * by, { merge: 'codesize' }); return; }
        // From the size it has fitted to the label, one step larger or smaller
        const like = await sizeLike(x, 'code', now);
        if (S.edit.design === x) sizeTo(dir > 0 ? Math.floor(like) + by : Math.ceil(like) - by);
    });
    const sizeIn = $('[data-csizein]', bar);
    if (sizeIn) {
        sizeIn.addEventListener('change', () => { const v = Math.round(+sizeIn.value); if (v > 0) sizeTo(v); else ed.renderToolbar(); });
        sizeIn.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); sizeIn.blur(); } });
    }
    drawOrder();
    const sw = $('[data-ccolor]', bar);
    if (sw) sw.onclick = () => openColors(sw, { color: d.print_color, d: onTape(d), what: 'the code', pick: c => ed.apply(x => { x.print_color = c; }) });
    $('[data-more]', bar).onclick = e => openMenu(e.currentTarget, codeMenu);
}

function codeMenu(el) {
    const d = S.edit.design;
    if (!withCode(d)) { closeMenu(); return; }
    const kind = codeKind(d.barcode_type), withImg = d.print_type === 'image_qrcode_text', fit = fitted(d);
    const choosable = listJoin(S.codes.filter(c => (c.corrections || []).length).map(c => c.name));
    el.innerHTML = `${withImg ? `<p class="mnote">${icon('fit')}<span>${fit ? 'Fits the label with the image' : 'Sized with the image'}</span></p>`
            : `<button type="button" data-cfit class="${fit ? 'on' : ''}">${icon('fit')}<span>The code fits the label</span>${fit ? icon('check') : ''}</button>`}
        ${kind.two_d ? `<label class="mi${fit ? ' off' : ''}"${fit ? ' title="The code fits the label"' : ''}>${icon('grid')}<span>Size of a dot</span><input class="in num" type="number" min="1" max="40" data-dot value="${esc(d.qrcode_size)}"${fit ? ' disabled' : ''} aria-label="Size of a dot"></label>`
            : withImg ? '' : `<label class="mi${fit ? ' off' : ''}"${fit ? ' title="The code fits the label"' : ''}>${icon('grid')}<span>Scale</span><input class="in num" type="number" min="1" max="500" data-cscale value="${esc(d.image_scaling_factor)}"${fit ? ' disabled' : ''} aria-label="Scale in %"><small>%</small></label>`}
        ${kind.corrections.length ? `<div class="mi">${icon('shield')}<span>Error correction</span><span class="seg">${LEVELS.filter(([v]) => kind.corrections.includes(v)).map(([v, n]) =>
            `<button type="button" data-level="${esc(v)}" class="${d.qrcode_correction === v ? 'on' : ''}" title="${esc(n)}">${esc(v)}</button>`).join('')}</span></div>`
            : `<p class="mnote">${icon('shield')}<span>${kind.two_d ? `${esc(kind.name)} codes set their own error correction. It` : 'Barcodes have no error correction. It'} can be chosen for ${esc(choosable)} codes.</span></p>`}
        ${withImg ? '' : gapRow(d)}
        <button type="button" class="danger" data-cremove>${icon('trash')}<span>Remove the code</span></button>`;
    const cfit = $('[data-cfit]', el);
    if (cfit) cfit.onclick = () => fromMenu(x => { x.image_fit = fitted(x) ? '0' : '1'; });
    const number = (sel, key, max) => {
        const inp = $(sel, el);
        if (inp) inp.addEventListener('change', () => { const v = Math.round(+inp.value); if (v > 0) fromMenu(x => { x[key] = String(Math.min(max, v)); }); });
    };
    number('[data-dot]', 'qrcode_size', 40);
    number('[data-cscale]', 'image_scaling_factor', 500);
    $$('[data-level]', el).forEach(b => b.onclick = () => fromMenu(x => { x.qrcode_correction = b.dataset.level; }));
    bindGap(el);
    $('[data-cremove]', el).onclick = removeCode;
}

// --- The image ------------------------------------------------------------------------------

const MODES = [['black_and_white', 'B/W', 'Black and white'], ['grayscale', 'Gray', 'Grayscale'], ['red_and_black', 'Red', 'Red and black'], ['colored', 'Color', 'Full color']];

function imageBar(bar) {
    const d = S.edit.design;
    if (!withImage(d)) { closeMenu(); ed.pick({ part: 'line', index: 0 }); return; }
    bar.className = 'float media';
    if (!d.image) {
        bar.innerHTML = `<button type="button" class="btn ghost" data-choose>${icon('upload')}Choose images or PDFs</button><span class="sep"></span>
            <button type="button" class="tb" data-iremove title="Remove the image" aria-label="Remove the image">${icon('trash')}</button>`;
        $('[data-choose]', bar).onclick = chooseFiles;
        $('[data-iremove]', bar).onclick = removeImage;
        return;
    }
    const red = redTape(d), color = isColorTape(d.label_size), fit = fitted(d), rot = +d.image_rotation || 0;
    const needsRed = v => (v === 'red_and_black' || v === 'colored') && !red && d.image_mode !== v;
    const bw = d.image_mode === 'black_and_white';
    const more = d.image_crop === '1' || rot % 90 !== 0 || d.image_text_gap !== '' || (d.pdf_pages || '') !== ''
        || (bw && !color && +d.image_bw_threshold !== +C.defaults.image_bw_threshold);
    const colors = color
        ? `<span class="tnote" title="Images print in full color on this tape">Full color</span>
           <button type="button" class="tb${d.image_contrast === '1' ? ' on' : ''}" data-contrast aria-pressed="${d.image_contrast === '1'}" title="Stronger contrast, for light drawings" aria-label="Stronger contrast">${icon('contrast')}</button>`
        : `<span class="seg" title="Colors">${MODES.map(([v, n, title]) => `<button type="button" data-imode="${esc(v)}" class="${d.image_mode === v ? 'on' : ''}" title="${esc(needsRed(v) ? 'Needs black and red tape' : title)}"${needsRed(v) ? ' disabled' : ''}>${esc(n)}</button>`).join('')}</span>`;
    bar.innerHTML = `${colors}
        <span class="sep"></span>
        <span class="seg" title="Size"><button type="button" data-fit="1" class="${fit ? 'on' : ''}" title="The image fits the label">Fit</button><button type="button" data-fit="0" class="${fit ? '' : 'on'}" title="Scaled in %">%</button></span>
        ${fit ? '' : `<span class="az"><button type="button" class="azb" data-scale="-1" aria-label="Smaller">${icon('minus', '')}</button><input class="anum num" data-scalein value="${esc(d.image_scaling_factor)}" inputmode="numeric" aria-label="Scale in %"><button type="button" class="azb" data-scale="1" aria-label="Larger">${icon('plus', '')}</button></span>`}
        <span class="sep"></span>
        <button type="button" class="tb wide" data-turn title="A quarter turn" aria-label="A quarter turn, now ${esc(rot)} degrees">${icon('rotate')}<span>${esc(rot)}&deg;</span></button>
        ${orderHTML()}
        <span class="sep"></span>
        <button type="button" class="tb${more ? ' dot' : ''}" data-more aria-expanded="${!!menu}" title="More for the image" aria-label="More for the image">${icon('more')}</button>
        ${stepperHTML()}`;
    $$('[data-imode]', bar).forEach(b => b.onclick = () => ed.apply(x => { x.image_mode = b.dataset.imode; }));
    const contrast = $('[data-contrast]', bar);
    if (contrast) contrast.onclick = () => ed.apply(x => { x.image_contrast = x.image_contrast === '1' ? '0' : '1'; });
    $$('[data-fit]', bar).forEach(b => b.onclick = async () => {
        const x = S.edit.design;
        if (b.dataset.fit === x.image_fit) return;
        // Scaled from now on, at first as large as it was fitted
        const like = b.dataset.fit === '0' ? Math.round(await sizeLike(x, 'image', +x.image_scaling_factor || 100)) : 0;
        if (S.edit.design !== x) return;
        ed.apply(d => {
            d.image_fit = b.dataset.fit;
            if (like > 0) d.image_scaling_factor = String(Math.max(1, Math.min(500, like)));
        });
    });
    $$('[data-scale]', bar).forEach(b => b.onclick = () => {
        const v = Math.max(1, Math.min(500, (+S.edit.design.image_scaling_factor || 100) + +b.dataset.scale * 10 * scaleStep()));
        ed.apply(x => { x.image_scaling_factor = String(v); }, { merge: 'scale' });
    });
    const scaleIn = $('[data-scalein]', bar);
    if (scaleIn) {
        scaleIn.addEventListener('change', () => { const v = Math.round(+scaleIn.value); if (v > 0) ed.apply(x => { x.image_scaling_factor = String(Math.min(500, v)); }); else ed.renderToolbar(); });
        scaleIn.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); scaleIn.blur(); } });
    }
    $('[data-turn]', bar).onclick = () => ed.apply(x => { x.image_rotation = String(((+x.image_rotation || 0) + 90) % 360); });
    drawOrder();
    bindStepper(bar);
    $('[data-more]', bar).onclick = e => openMenu(e.currentTarget, imageMenu);
}

function imageMenu(el) {
    const d = S.edit.design;
    if (!withImage(d) || !d.image) { closeMenu(); return; }
    const color = isColorTape(d.label_size), bw = d.image_mode === 'black_and_white', crop = d.image_crop === '1';
    const files = labelFiles(d), pdfs = files.filter(isPdf);
    const count = pdfs.length === 1 ? M.counts.get(imageKey(pdfs[0])) : 0;
    const pageCount = typeof count === 'number' ? count : 0;
    const several = M.units.length > 1;
    el.innerHTML = `${color ? '' : `<label class="mi${bw ? '' : ' off'}" title="${bw ? 'Pixels darker than this print black' : 'Only for black and white'}">${icon('contrast')}<span>Threshold</span>
            <input type="range" min="1" max="254" data-thr value="${esc(d.image_bw_threshold)}"${bw ? '' : ' disabled'} aria-label="Pixels darker than this of 255 print black"><b class="anum" data-thrv>${esc(d.image_bw_threshold)}</b></label>`}
        <button type="button" data-crop class="${crop ? 'on' : ''}" title="Removes the empty border around the image">${icon('crop')}<span>Auto crop</span>${crop ? icon('check') : ''}</button>
        <label class="mi">${icon('angle')}<span>Any angle</span><input class="in num" type="number" min="0" max="359" data-angle value="${esc(d.image_rotation)}" aria-label="Any angle in degrees"><small>&deg;</small></label>
        ${gapRow(d)}
        ${pdfs.length ? `<label class="mi" title="For example 1-3, 5 or all. Each page prints as a label of its own.">${icon('file')}<span>PDF pages</span>
            <input class="in" data-pages value="${esc(d.pdf_pages || '')}" placeholder="1" aria-label="PDF pages"></label>
            ${pageCount > 1 ? `<p class="mnote"><span>This PDF has ${esc(pageCount)} pages, each one chosen prints as a label of its own.</span>${String(d.pdf_pages).trim().toLowerCase() === 'all' ? '' : '<button type="button" class="custom" data-allpages>All pages</button>'}</p>` : ''}` : ''}
        <div class="sepl"></div>
        <ul class="flist">${files.map((f, i) => `<li class="${files.length > 1 && unitNow() && unitNow().file === i ? 'on' : ''}">${icon(isPdf(f) ? 'file' : 'image')}<span title="${esc(f.name)}">${esc(f.name)}</span>
            <button type="button" class="tb" data-unfile="${esc(i)}" title="Remove ${esc(f.name)}" aria-label="Remove ${esc(f.name)}">${icon('x')}</button></li>`).join('')}</ul>
        ${files.length > 1 ? '<p class="mnote"><span>Each file prints as a label of its own with these settings. Saving the label keeps the first one only.</span></p>' : ''}
        ${several ? `<p class="mnote">${icon('copies')}<span>Copies print ${collated() ? 'as sets: ABC ABC' : 'grouped: AA BB CC'}, as set in <a href="#settings">Settings</a>.</span></p>` : ''}
        <button type="button" data-choose>${icon('upload')}<span>Choose other files</span><small>or drop, paste</small></button>
        <button type="button" class="danger" data-iremove>${icon('trash')}<span>Remove the image</span></button>`;
    const thr = $('[data-thr]', el);
    if (thr) thr.addEventListener('input', () => { S.edit.design.image_bw_threshold = thr.value; $('[data-thrv]', el).textContent = thr.value; ed.changed(false); });
    $('[data-crop]', el).onclick = () => fromMenu(x => { x.image_crop = x.image_crop === '1' ? '0' : '1'; });
    const angle = $('[data-angle]', el);
    angle.addEventListener('change', () => {
        const v = +angle.value;
        if (angle.value !== '' && v >= 0 && v < 360) fromMenu(x => { x.image_rotation = String(Math.round(v)); });
    });
    bindGap(el);
    const pages = $('[data-pages]', el);
    if (pages) pages.addEventListener('input', () => { S.edit.design.pdf_pages = pages.value.trim(); ed.changed(false); countUnits(); });
    const all = $('[data-allpages]', el);
    if (all) all.onclick = () => fromMenu(x => { x.pdf_pages = 'all'; });
    $$('[data-unfile]', el).forEach(b => b.onclick = () => { removeFile(+b.dataset.unfile); drawMenu(); });
    $('[data-choose]', el).onclick = chooseFiles;
    $('[data-iremove]', el).onclick = removeImage;
}

// --- What a preview told -----------------------------------------------------------------------

function previewed(p, design) {
    M.order = p.order || '';
    M.direction = p.direction || '';
    M.toldFor = S.edit;
    M.gap = p.gap || 0;
    M.highRes = !!p.highRes;
    if (design && design.image && isPdf(design.image) && p.pageCount) M.counts.set(imageKey(design.image), p.pageCount);
    const d = S.edit.design;
    // How the server laid the parts out, e.g. at image_position, the order shows that
    drawOrder();
    if (menu) {
        const gap = $('[data-gap]', menu.el);
        if (gap && paired(d)) gap.placeholder = M.gap ? `Automatic, ${dotsToMm(M.gap, dpiOf(d.label_size, M.highRes))}` : 'Automatic';
        if (!menu.el.contains(document.activeElement)) drawMenu();
    }
    if (withImage(d) && d.image) countUnits();
    else { M.units = []; drawStepper(); }
}

// --- Dropping and pasting ----------------------------------------------------------------------

const editing = () => document.body.dataset.view === 'edit' && !!$('#stage') && $('#layer').hidden;
const withFiles = e => !!e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
function dropping(on) {
    const stage = $('#stage');
    if (stage) stage.classList.toggle('dropping', on);
}

export function mountMedia(editor) {
    ed = editor;
    const { hooks } = ed;
    hooks.toolbars.code = codeBar;
    hooks.toolbars.image = imageBar;
    hooks.addCode = addCode;
    hooks.addImage = addImage;
    hooks.files = readImages;
    hooks.shown = shown;
    hooks.perCopy = () => M.units.length;
    hooks.previewed = previewed;
    const input = document.createElement('input');
    Object.assign(input, { type: 'file', id: 'imageFile', accept: 'image/*,application/pdf', multiple: true, hidden: true });
    input.onchange = () => { if (input.files.length) readImages(input.files); };
    document.body.appendChild(input);
    document.addEventListener('paste', e => {
        if (e.defaultPrevented || !editing()) return;
        const files = Array.from(e.clipboardData ? e.clipboardData.files : []).filter(isImageFile);
        if (files.length) { e.preventDefault(); readImages(files); }
    });
    document.addEventListener('dragover', e => {
        if (!editing() || !withFiles(e)) return;
        e.preventDefault();
        dropping(true);
    });
    document.addEventListener('dragleave', e => { if (!e.relatedTarget) dropping(false); });
    document.addEventListener('drop', e => {
        dropping(false);
        if (e.defaultPrevented || !editing() || !e.dataTransfer || !e.dataTransfer.files.length) return;
        e.preventDefault();
        readImages(e.dataTransfer.files);
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && menu) closeMenu(); });
}
