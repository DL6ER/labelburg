// The font menu: families only, a search field and the families used last
// first, each name drawn in its own font by the server; below it the weight
// and width of the family picked. Also bold and italic of a line, which the
// toolbar of a line has as toggles.

import { $, $$, esc, icon, API, own, load, storeQuiet, FAMILIES, WIDTHS, faceOf, familyNamed, nearestFont } from './core.js';
import { phone } from './phone.js';

// The weights and widths of the font files in plain words
const WEIGHTS = { 100: 'Thin', 200: 'Extra light', 300: 'Light', 400: 'Regular', 500: 'Medium', 600: 'Semi bold', 700: 'Bold', 800: 'Extra bold', 900: 'Black' };
const WIDTH_NAMES = ['Ultra condensed', 'Extra condensed', 'Condensed', 'Semi condensed', 'Normal', 'Semi expanded', 'Expanded', 'Extra expanded', 'Ultra expanded'];
const widthName = w => WIDTH_NAMES[WIDTHS.indexOf(w)];
const weightsOf = fam => [...new Set(fam.styles.map(s => s.weight))].sort((a, b) => a - b);
const widthsOf = fam => [...new Set(fam.styles.map(s => s.width))].sort((a, b) => WIDTHS.indexOf(a) - WIDTHS.indexOf(b));
export const hasItalic = fam => !!fam && fam.styles.some(s => s.italic);

// --- A line in another style ---------------------------------------------------

const BY_WEIGHT = ['weight', 'width', 'italic'];
// The weight a line had before bold, while it has the bold it was given
const beforeBold = new WeakMap();
// Bold and italic, kept as wishes in labels saved before, no longer hold once a style is chosen here
const chosen = (l, font) => { if (font) l.font = font; delete l.bold; delete l.italic; };

// Picked in another family: the style nearest to the line's, a font not
// installed by the bold and italic it was kept with
export function familyLine(l, name) {
    const face = faceOf(l.font) || { weight: l.bold ? 700 : 400, width: 'normal', italic: !!l.italic };
    chosen(l, nearestFont(name, face, ['italic', 'width', 'weight']));
}
export function weightLine(l, weight) {
    const face = faceOf(l.font);
    if (face) chosen(l, nearestFont(face.family, Object.assign({}, face, { weight }), BY_WEIGHT));
}
export function widthLine(l, width) {
    const face = faceOf(l.font);
    if (face) chosen(l, nearestFont(face.family, Object.assign({}, face, { width }), ['width', 'weight', 'italic']));
}
// Bold moves to the family's bold weight, and back to the weight before
export function boldLine(l) {
    const face = faceOf(l.font), fam = face && familyNamed(face.family);
    if (!fam || !fam.bold) return;
    if (face.weight >= fam.bold) {
        const was = beforeBold.get(l);
        chosen(l, nearestFont(fam.name, Object.assign({}, face, { weight: was && was.font === l.font ? was.weight : 400 }), BY_WEIGHT));
        return;
    }
    chosen(l, nearestFont(fam.name, Object.assign({}, face, { weight: fam.bold }), BY_WEIGHT));
    beforeBold.set(l, { font: l.font, weight: face.weight });
}
export function italicLine(l) {
    const face = faceOf(l.font);
    if (face && hasItalic(familyNamed(face.family))) chosen(l, nearestFont(face.family, Object.assign({}, face, { italic: !face.italic }), ['italic', 'width', 'weight']));
}

// --- The menu -------------------------------------------------------------------

const RECENT = own('designer_next_fonts');
const recent = () => { const kept = load(RECENT, []); return (Array.isArray(kept) ? kept : []).filter(n => familyNamed(n)); };
function remember(name) {
    storeQuiet(RECENT, [name].concat(recent().filter(n => n !== name)).slice(0, 5));
}

// The button showing the font, it opens the menu
export function fontButton(attrs, text, cls = 'fsel') {
    return `<button type="button" class="${cls}" ${attrs} aria-haspopup="dialog" aria-expanded="false"><span>${esc(text)}</span>${icon('down')}</button>`;
}

let menu = null;
// The families whose names the server drew, and those it cannot draw
const samples = { drawn: new Set(), none: new Set() };
export function closeFontMenu(focus = false) {
    if (!menu) return;
    const { el, opts, outside, keys, moved } = menu;
    menu = null;
    el.remove();
    document.removeEventListener('mousedown', outside, true);
    document.removeEventListener('keydown', keys, true);
    removeEventListener('resize', moved);
    document.removeEventListener('scroll', moved, true);
    const anchor = opts.anchor();
    if (anchor) {
        anchor.setAttribute('aria-expanded', 'false');
        if (focus) anchor.focus();
    }
}

// opts: anchor() the button, label, state() the family shown ('' for
// several) and the faces of the lines it sets, pick(name), weight(w) and
// width(w) where it sets them, first an entry above the families
// ({ label, value }), closeOnPick
export function openFontMenu(opts) {
    if (menu && menu.opts.owner === opts.owner) { closeFontMenu(true); return; }
    closeFontMenu();
    const el = document.createElement('div');
    el.className = 'menu font-menu';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', opts.label);
    el.innerHTML = `<label class="fm-search">${icon('search')}<input type="search" placeholder="Search fonts" aria-label="Search fonts" role="combobox" aria-expanded="true"
            aria-controls="fmList" aria-autocomplete="list" autocomplete="off" spellcheck="false"></label>
        <div class="fm-list" id="fmList" role="listbox" aria-label="Fonts"></div>
        <div class="fm-face" hidden></div>`;
    document.body.appendChild(el);
    const outside = e => { const a = opts.anchor(); if (!el.contains(e.target) && !(a && a.contains(e.target))) closeFontMenu(); };
    const keys = e => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeFontMenu(true); } };
    const moved = e => { if (!e || !el.contains(e.target)) place(); };
    document.addEventListener('mousedown', outside, true);
    document.addEventListener('keydown', keys, true);
    addEventListener('resize', moved);
    document.addEventListener('scroll', moved, true);
    menu = { el, opts, outside, keys, moved, active: -1, shown: null };
    const q = $('input', el), list = $('.fm-list', el);
    q.addEventListener('input', () => { menu.active = q.value.trim() ? 0 : -1; drawList(); });
    q.addEventListener('keydown', e => {
        const rows = $$('.fm-o', list);
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            if (!rows.length) return;
            menu.active = Math.max(0, Math.min(rows.length - 1, menu.active + (e.key === 'ArrowDown' ? 1 : -1)));
            markActive();
        } else if (e.key === 'Enter') {
            e.preventDefault();
            const row = rows[Math.max(0, menu.active)];
            if (row) choose(row.dataset.family);
        }
    });
    // The search field keeps the focus, a click picks
    list.addEventListener('mousedown', e => { if (e.target.closest('.fm-o')) e.preventDefault(); });
    list.addEventListener('click', e => { const row = e.target.closest('.fm-o'); if (row) choose(row.dataset.family); });
    // The name in its own font once drawn, as text where the font cannot draw it
    const told = (e, set) => { if (!e.target.matches('img')) return; const row = e.target.closest('.fm-o'); set.add(row.dataset.family); return row; };
    list.addEventListener('load', e => { const row = told(e, samples.drawn); if (row) row.classList.add('drawn'); }, true);
    list.addEventListener('error', e => { if (told(e, samples.none)) e.target.remove(); }, true);
    drawList();
    refreshFontMenu();
    const shown = $('.fm-o[aria-selected="true"]', list);
    if (shown) list.scrollTop = shown.offsetTop - list.offsetTop - 4;
    if (!phone()) q.focus();
}

function choose(value) {
    if (value) remember(value);
    menu.opts.pick(value);
    if (menu && menu.opts.closeOnPick) closeFontMenu(true);
    else refreshFontMenu();
}

function drawList() {
    const { el, opts } = menu, list = $('.fm-list', el), query = $('input', el).value.trim().toLowerCase();
    const current = opts.state().family;
    const row = (value, text, i, sample = !samples.none.has(value)) => `<div class="fm-o${samples.drawn.has(value) ? ' drawn' : ''}" role="option" id="fm-o-${i}" data-family="${esc(value)}" aria-selected="${value === current}">
        ${sample ? `<img class="fs" src="${API}/fonts/sample?family=${encodeURIComponent(value)}" alt="">` : ''}<span class="fn">${esc(text)}</span>${icon('check')}</div>`;
    const found = FAMILIES.filter(f => !query || f.name.toLowerCase().includes(query)).map(f => f.name);
    let html = '', i = 0;
    if (query) html = found.map(name => row(name, name, i++)).join('') || '<p class="fm-none">No font is named like that</p>';
    else {
        const first = opts.first ? row(opts.first.value, opts.first.label, i++, false) : '';
        const last = recent();
        html = first + (last.length ? `<div class="fm-h" role="presentation">Recent</div>${last.map(name => row(name, name, i++)).join('')}<div class="fm-h" role="presentation">All</div>` : '')
            + found.map(name => row(name, name, i++)).join('');
    }
    list.innerHTML = html;
    markActive();
}

function markActive() {
    const rows = $$('.fm-o', menu.el), q = $('input', menu.el);
    rows.forEach((r, i) => r.classList.toggle('act', i === menu.active));
    const row = rows[menu.active];
    if (row) { q.setAttribute('aria-activedescendant', row.id); row.scrollIntoView({ block: 'nearest' }); } else q.removeAttribute('aria-activedescendant');
}

// The menu follows the label: what the lines have now, the button moved
export function refreshFontMenu() {
    if (!menu) return;
    const anchor = menu.opts.anchor();
    if (!anchor || !anchor.getClientRects().length) { closeFontMenu(); return; }
    anchor.setAttribute('aria-expanded', 'true');
    const state = menu.opts.state();
    $$('.fm-o', menu.el).forEach(r => r.setAttribute('aria-selected', String(r.dataset.family === state.family)));
    drawFace(state);
    place();
}

// The weight and width of the family shown, each where it has several: the
// weight on a slider, the width as buttons, on a slider too where they would
// take rows
const MANY_WIDTHS = 4;
function drawFace({ family, faces = [] }) {
    const box = $('.fm-face', menu.el), fam = family && familyNamed(family), opts = menu.opts;
    const weights = fam && opts.weight ? weightsOf(fam) : [], widths = fam && opts.width ? widthsOf(fam) : [];
    const shown = fam && (weights.length > 1 || widths.length > 1) ? `${fam.name}|${weights}|${widths}` : '';
    if (shown !== menu.shown) {
        menu.shown = shown;
        box.hidden = !shown;
        box.innerHTML = !shown ? '' : `<div class="fm-name">${esc(fam.name)}</div>
            ${weights.length > 1 ? `<div class="fm-row"><span class="k">Weight</span><b data-wname></b></div>
                <div class="fm-ends"><small>${WEIGHTS[weights[0]]}</small><input type="range" data-fweight min="0" max="${weights.length - 1}" step="1" aria-label="Weight"><small>${WEIGHTS[weights[weights.length - 1]]}</small></div>` : ''}
            ${widths.length > MANY_WIDTHS ? `<div class="fm-row"><span class="k">Width</span><b data-dname></b></div>
                <div class="fm-ends"><small>${widthName(widths[0])}</small><input type="range" data-fwidths min="0" max="${widths.length - 1}" step="1" aria-label="Width"><small>${widthName(widths[widths.length - 1])}</small></div>`
            : widths.length > 1 ? `<div class="fm-row"><span class="k">Width</span></div>
                <div class="seg fm-widths" role="group" aria-label="Width">${widths.map(w => `<button type="button" data-fwidth="${w}" aria-pressed="false">${widthName(w)}</button>`).join('')}</div>` : ''}`;
        const range = $('[data-fweight]', box), wide = $('[data-fwidths]', box);
        if (range) range.addEventListener('input', () => { opts.weight(weights[+range.value]); refreshFontMenu(); });
        if (wide) wide.addEventListener('input', () => { opts.width(widths[+wide.value]); refreshFontMenu(); });
        $$('[data-fwidth]', box).forEach(b => b.onclick = () => { opts.width(b.dataset.fwidth); refreshFontMenu(); });
    }
    if (!shown) return;
    const same = key => faces.length && faces.every(f => f && f[key] === faces[0][key]) ? faces[0][key] : null;
    const range = $('[data-fweight]', box);
    if (range && faces[0]) {
        const weight = same('weight'), name = weight === null ? 'Several' : WEIGHTS[weight];
        range.value = String(Math.max(0, weights.indexOf(faces[0].weight)));
        range.setAttribute('aria-valuetext', name);
        $('[data-wname]', box).textContent = name;
    }
    const width = same('width'), wide = $('[data-fwidths]', box);
    $$('[data-fwidth]', box).forEach(b => { const on = b.dataset.fwidth === width; b.classList.toggle('on', on); b.setAttribute('aria-pressed', String(on)); });
    if (wide && faces[0]) {
        const name = width === null ? 'Several' : widthName(width);
        wide.value = String(Math.max(0, widths.indexOf(faces[0].width)));
        wide.setAttribute('aria-valuetext', name);
        $('[data-dname]', box).textContent = name;
    }
}

// Below its button, above where there is more room there; where that covers
// the label, which shows what a font does, beside it, else below or above
// it, where there is more room. What does not fit scrolls inside. A sheet on
// the phone.
function place() {
    const { el } = menu, anchor = menu.opts.anchor();
    if (phone() || !anchor) { el.style.left = el.style.top = el.style.maxHeight = ''; return; }
    const r = anchor.getBoundingClientRect(), below = innerHeight - r.bottom - 12, above = r.top - 12, w = el.offsetWidth;
    const at = (left, top, room) => {
        el.style.maxHeight = `${Math.max(120, Math.min(560, room))}px`;
        el.style.left = `${Math.max(8, Math.min(left, innerWidth - w - 8))}px`;
        el.style.top = `${top}px`;
    };
    const up = below < Math.min(el.scrollHeight, 420) && above > below;
    at(r.left, 0, up ? above - 6 : below - 6);
    el.style.top = `${up ? Math.max(8, r.top - 6 - el.offsetHeight) : r.bottom + 6}px`;
    const paper = $('#paper'), label = paper && paper.getClientRects().length ? paper.getBoundingClientRect() : null;
    const box = el.getBoundingClientRect();
    if (!label || box.right <= label.left || box.left >= label.right || box.bottom <= label.top || box.top >= label.bottom) return;
    const top = Math.max(8, Math.min(r.top, innerHeight - 8 - Math.min(el.scrollHeight, 560)));
    if (label.right + 12 + w <= innerWidth - 8) { at(label.right + 12, top, innerHeight - 8 - top); return; }
    if (label.left - 12 - w >= 8) { at(label.left - 12 - w, top, innerHeight - 8 - top); return; }
    const need = Math.min(el.scrollHeight, 560), over = Math.min(r.top, label.top) - 12;
    const under = innerHeight - 8 - label.bottom - 12, overRoom = over - 8;
    if (under >= need || under >= overRoom) at(r.left, label.bottom + 12, under);
    else {
        at(r.left, 0, overRoom);
        el.style.top = `${Math.max(8, over - el.offsetHeight)}px`;
    }
}
