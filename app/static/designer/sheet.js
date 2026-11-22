// Sheets of labels on office printers: the line under the label, the dialog
// "Label sheet" and the question where a partly used sheet goes on. The
// server lays out the labels and tells how a print fills them; nothing of
// this shows for other printers.

import { $, $$, esc, icon, S, SIZES, kept, isDemo, isPaper, perSheet, takesSheets, keptStart, sheetStartOf, printHooks } from './core.js';

// Set by settings.js: keeps printer settings on the server, see saveSettings()
export const sheetHooks = { save: null };

const plainPaper = p => kept(p).cut_lines === '1';
export const chosenSheet = p => !!p && !!p.tape && (!!p.tape.manual || isDemo(p));

// The sheet to scale, height px high: labels before from used, from from to
// to printed; with tap each label a button
function drawn(id, from, to, height, tap = false) {
    const s = SIZES[id].sheet, k = height / s.size[1], px = mm => `${Math.round(mm * k * 10) / 10}px`;
    const labels = s.labels.map(([x, y, w, h], i) => {
        const n = i + 1, cls = n < from ? 'used' : n <= to ? 'on' : '';
        const style = `left:${px(x)};top:${px(y)};width:${px(w)};height:${px(h)};border-radius:${px(s.corner)}`;
        return tap ? `<button type="button" class="${cls}" data-at="${n}" style="${style}" aria-label="Label ${n}"${n === from ? ' aria-pressed="true"' : ''}></button>`
            : `<i class="${cls}" style="${style}"></i>`;
    }).join('');
    return `<b class="sheet office${tap ? ' big' : ''}" role="img" aria-label="${esc(s.short)}" style="width:${px(s.size[0])};height:${px(s.size[1])}">${labels}</b>`;
}

// The line under the label: the sheet and the labels this print takes,
// state as the preview tells it (X-Label-Sheet)
export function paperLine(state, id, p = S.printer) {
    if (!isPaper(id) || !takesSheets(p)) return '';
    if (!chosenSheet(p)) return `${icon('warn')}<span>Which label sheets are in the printer?</span><button type="button" class="pill" data-sheet>Choose</button>`;
    const s = SIZES[id].sheet, per = perSheet(id);
    const start = state ? state.start : sheetStartOf(p, id), sheets = state ? state.sheets : 1, last = state ? state.filled : start;
    const name = plainPaper(p) && per > 1 ? `Plain paper as ${s.chip}` : s.short;
    const what = per === 1 ? (sheets > 1 ? `${name}: ${sheets} sheets` : name)
        : sheets > 1 ? `${name}: ${sheets} sheets${start > 1 ? `, from label ${start}` : ''}`
            : `${name}: ${start === last ? `label ${start}` : `labels ${start} to ${last}`} of ${per}`;
    return `<button type="button" class="sheetbtn" data-sheet aria-label="Label sheet">${drawn(id, start, sheets > 1 ? per : last, 44)}</button>`
        + `<span>${esc(what)}</span><button type="button" class="pill" data-sheet>Change</button>`;
}

// A layer of its own, above the Templates
function layer() {
    let box = $('#sheetLayer');
    if (!box) {
        box = document.createElement('div');
        box.id = 'sheetLayer';
        box.className = 'layer center sheet-layer';
        document.body.appendChild(box);
    }
    box.hidden = false;
    return box;
}
let closing = null;
function close() {
    const box = $('#sheetLayer');
    if (!box || box.hidden) return;
    box.hidden = true;
    box.innerHTML = '';
    const done = closing;
    closing = null;
    if (done) done();
}
document.addEventListener('keydown', e => { if (e.key === 'Escape' && $('#sheetLayer') && !$('#sheetLayer').hidden) { e.stopPropagation(); close(); } }, true);

let redraw = null;
// The dialog follows the preview, e.g. whether the sheet is full
export function sheetShown() { if (redraw) redraw(); }

// The dialog "Label sheet" of printer p: which sheets, label sheets or
// plain paper, and where this print starts. state() is the last preview's,
// fill(n) sets the copies, changed() follows a change.
export function openSheet(p, { state = () => null, fill = null, changed = () => { } } = {}) {
    const box = layer();
    const draw = () => {
        const t = p.tape || {}, id = chosenSheet(p) ? t.id : '', per = id ? perSheet(id) : 1, st = state();
        const takes = (p.takes || []).filter(isPaper);
        const pages = [...new Set(takes.map(i => SIZES[i].sheet.page))];
        const at = id ? sheetStartOf(p, id) : 1, plain = plainPaper(p);
        box.innerHTML = `<div class="dialog sheet-dialog" role="dialog" aria-label="Label sheet">
            <div class="dialog-h"><h2>Label sheet</h2><span class="sp"></span><button class="iconbtn" type="button" data-shut aria-label="Close">${icon('x', '')}</button></div>
            <select class="in" id="shSel" aria-label="Label sheet"><option value="" disabled${id ? '' : ' selected'}>Choose the number on the pack</option>${pages.map(g =>
                `<optgroup label="${esc(g)}">${takes.filter(i => SIZES[i].sheet.page === g).map(i => `<option value="${esc(i)}"${i === id ? ' selected' : ''}>${esc(SIZES[i].name)}</option>`).join('')}</optgroup>`).join('')}</select>
            <div class="sh-row"><span class="k">Paper</span><div class="seg" role="group" aria-label="Paper">${[['', 'Label sheets'], ['1', 'Plain paper']].map(([v, n]) =>
                `<button type="button" data-paper="${v}"${(v === '1') === plain ? ' class="on" aria-pressed="true"' : ' aria-pressed="false"'}>${n}</button>`).join('')}</div></div>
            ${plain ? '<small class="muted">Thin lines show where to cut.</small>' : ''}
            ${id && per > 1 ? `<div class="sh-start">${drawn(id, at, at, 220, true)}
                <div class="sh-at"><label>Start at label <input class="in num" id="sheetAt" type="number" min="1" max="${per}" value="${at}"> of ${per}</label>
                <small class="muted">Tap the first free label. For this print only.</small></div></div>` : ''}
            ${p.blank_edge ? `<small class="set-warn">The printer leaves ${esc(p.blank_edge)} mm at the paper edge blank.</small>` : ''}
            <div class="acts">${fill && st && st.fill && per > 1 ? `<button class="btn" type="button" data-fill="${esc(st.fill)}">Fill the sheet</button>` : ''}
                <button class="btn primary" type="button" data-shut>Done</button></div></div>`;
        bind(id, per);
    };
    const startAt = (id, n) => {
        S.sheetStart = { path: p.path, id, at: n };
        draw();
        changed();
    };
    const bind = (id, per) => {
        $('#shSel', box).onchange = async e => {
            S.sheetStart = null;
            await sheetHooks.save(p, { label_size: e.target.value });
            draw();
            changed();
        };
        $$('[data-paper]', box).forEach(b => b.onclick = async () => {
            if (b.classList.contains('on')) return;
            await sheetHooks.save(p, { cut_lines: b.dataset.paper });
            draw();
            changed();
        });
        $$('[data-at]', box).forEach(b => b.onclick = () => startAt(id, +b.dataset.at));
        const inp = $('#sheetAt', box);
        if (inp) inp.onchange = () => { const n = parseInt(inp.value, 10); if (n >= 1 && n <= per) startAt(id, n); else inp.value = sheetStartOf(p, id); };
        const more = $('[data-fill]', box);
        if (more) more.onclick = () => { fill(+more.dataset.fill); close(); };
        $$('[data-shut]', box).forEach(b => b.onclick = close);
        box.onclick = e => { if (e.target === box) close(); };
    };
    redraw = draw;
    closing = () => { redraw = null; };
    draw();
    $('#shSel', box).focus();
}

// Asked before a print on a sheet partly used: where it goes on, the label
// after the last print preselected. Resolves the label, null when closed.
function askStart(id, at) {
    return new Promise(resolve => {
        const box = layer();
        let n = at, answered = false;
        const answer = value => { answered = true; close(); resolve(value); };
        const draw = () => {
            box.innerHTML = `<div class="dialog narrow sheet-dialog" role="dialog" aria-label="Where to continue?">
                <div class="dialog-h"><h2>Where to continue?</h2><span class="sp"></span><button class="iconbtn" type="button" data-shut aria-label="Close">${icon('x', '')}</button></div>
                <div class="sh-start">${drawn(id, n, n, 220, true)}<small class="muted">Tap a label to start there.</small></div>
                <div class="acts"><button class="btn" type="button" data-go="1">New sheet</button>
                    <button class="btn primary" type="button" data-go="${n}">${n === at ? 'Continue' : 'Start'} at label ${n}</button></div></div>`;
            $$('[data-at]', box).forEach(b => b.onclick = () => { n = +b.dataset.at; draw(); });
            $$('[data-go]', box).forEach(b => b.onclick = () => answer(+b.dataset.go));
            $$('[data-shut]', box).forEach(b => b.onclick = close);
            box.onclick = e => { if (e.target === box) close(); };
            $('.btn.primary', box).focus();
        };
        closing = () => { if (!answered) resolve(null); };
        draw();
    });
}

// Before each print on a sheet of labels: the label chosen for it, else
// asked where a sheet is partly used, else the first
printHooks.start = (p, id) => {
    if (!isPaper(id) || !takesSheets(p)) return undefined;
    const chosen = S.sheetStart;
    if (chosen && chosen.path === p.path && chosen.id === id) {
        S.sheetStart = null;
        return chosen.at;
    }
    const at = keptStart(p, id);
    return at > 1 ? askStart(id, at) : 1;
};
