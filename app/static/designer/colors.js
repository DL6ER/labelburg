// The color of a line, the code or the frame: any color on the color tape
// of the VC-500W, black and red on black and red tape, nothing to choose on
// other tapes. Colors are kept as saved labels have them: black, red or
// #rrggbb.

import { $, $$, esc, icon, S, isColorTape, redTape } from './core.js';

export const hexOf = c => c === 'red' ? '#ff0000' : /^#[0-9a-f]{6}$/i.test(c || '') ? c.toLowerCase() : '#000000';
export const colorOf = hex => hex === '#000000' ? 'black' : hex === '#ff0000' ? 'red' : hex;

// Common colors that print well on white color tape
const PALETTE = [['black', 'Black'], ['#6b7280', 'Gray'], ['red', 'Red'], ['#ea580c', 'Orange'], ['#ca8a04', 'Yellow'], ['#16a34a', 'Green'],
    ['#0d9488', 'Teal'], ['#2563eb', 'Blue'], ['#7c3aed', 'Purple'], ['#db2777', 'Pink'], ['#92400e', 'Brown']];
const TWO = [['black', 'Black'], ['red', 'Red']];

// What the tape prints of a color, as the preview of the label told it
function printed(color) {
    const told = Object.entries(S.printedAs).find(([c]) => same(c, color));
    return told ? told[1] : color || 'black';
}
const same = (a, b) => hexOf(a) === hexOf(b);
export const printable = color => same(printed(color), color || 'black');
// Whether the tape offers colors, or the color set is one it cannot print
export const offersColor = (color, d) => isColorTape(d.label_size) || redTape(d) || !printable(color);

function hint(color, d, what) {
    if (printable(color)) return '';
    const as = printed(color) === 'red' ? 'red' : 'black';
    return redTape(d) ? `This tape prints black and red only, ${what} prints ${as}.` : `This tape has no colors, ${what} prints black.`;
}

// The button showing the color, empty where the tape has nothing to choose
export function swatchHTML(attr, color, d, what) {
    if (!offersColor(color, d)) return '';
    const off = !printable(color);
    const title = off ? hint(color, d, what) : `Color of ${what}`;
    return `<button type="button" class="tb swb${off ? ' off' : ''}" ${attr} aria-haspopup="true" aria-expanded="false" title="${esc(title)}" aria-label="${esc(title)}"><i class="sw" style="background:${hexOf(color)}"></i></button>`;
}

let open = null;
export function closeColors() {
    if (!open) return;
    open.el.remove();
    open.btn.setAttribute('aria-expanded', 'false');
    document.removeEventListener('mousedown', open.outside, true);
    document.removeEventListener('keydown', open.keys, true);
    open = null;
}

// The colors to choose from below btn; pick(color) takes the one chosen
export function openColors(btn, { color, d, what, pick, box = $('#center') }) {
    if (open && open.btn === btn) { closeColors(); return; }
    closeColors();
    const full = isColorTape(d.label_size), choices = full ? PALETTE : redTape(d) ? TWO : TWO.slice(0, 1);
    const el = document.createElement('div');
    el.className = 'menu color-menu';
    const on = c => same(c, color || 'black');
    el.innerHTML = `${hint(color, d, what) ? `<p class="mnote">${icon('warn')}<span>${esc(hint(color, d, what))}</span></p>` : ''}
        <div class="swatches${full ? '' : ' two'}">${choices.map(([c, name]) => `<button type="button" class="swc${on(c) ? ' on' : ''}" data-c="${esc(c)}" title="${esc(name)}" aria-label="${esc(name)}" aria-pressed="${on(c)}"><i class="sw" style="background:${hexOf(c)}"></i>${full ? '' : `<span>${esc(name)}</span>`}</button>`).join('')}</div>
        ${full ? `<label class="mi">${icon('drop')}<span>Custom color</span><input type="color" data-custom value="${hexOf(color)}" aria-label="Custom color"></label>` : ''}`;
    box.appendChild(el);
    const r = btn.getBoundingClientRect(), c = box.getBoundingClientRect();
    el.style.left = `${Math.max(8, Math.min(r.left - c.left - 8, c.width - el.offsetWidth - 8))}px`;
    el.style.top = `${r.bottom - c.top + 8}px`;
    btn.setAttribute('aria-expanded', 'true');
    const outside = e => { if (!el.contains(e.target) && !btn.contains(e.target)) closeColors(); };
    const keys = e => { if (e.key === 'Escape') { e.stopPropagation(); closeColors(); btn.focus(); } };
    document.addEventListener('mousedown', outside, true);
    document.addEventListener('keydown', keys, true);
    open = { el, btn, outside, keys };
    $$('[data-c]', el).forEach(b => b.onclick = () => { closeColors(); pick(b.dataset.c); });
    const custom = $('[data-custom]', el);
    if (custom) custom.onchange = () => { closeColors(); pick(colorOf(custom.value)); };
}
