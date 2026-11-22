// The text field under the label: one row per line of the label, Enter
// starts a new line. Templates, e.g. {{blank:Name}} or {{datetime:%d.%m.%Y}},
// show as chips; "[" offers them. A line break inside a line, as saved
// labels can have them, shows as a chip too.

import {
    $, $$, esc, icon, clone, S, C, L, plainFont, templateOf, blanksOf, fieldType, blankChoices, choicesOf, blankStart, startOf,
    BLANK_KINDS, guessKind, isVariable, dateFormat, DATE_OFFSET_RE, loadSymbols, loadIcons, loadSymbolSets, findIcons, SYMBOL_SETS, today,
    openLayer
} from './core.js';

const ZW = '\u200b';
const TOKEN_RE = /\{\{[^{}]*\}\}|\n/g;
let ctx = null, root = null;

// --- Lines as tokens ---------------------------------------------------------
// A line is text and chips; a chip counts as one place for the caret.

export function tokensOf(text) {
    const out = [];
    let last = 0;
    text = String(text || '');
    for (const m of text.matchAll(TOKEN_RE)) {
        if (m.index > last) out.push({ s: text.slice(last, m.index) });
        out.push({ tpl: m[0] });
        last = m.index + m[0].length;
    }
    if (last < text.length) out.push({ s: text.slice(last) });
    return out;
}
const textOf = tokens => tokens.map(t => t.tpl !== undefined ? t.tpl : t.s).join('');
const unitsOf = tokens => tokens.reduce((n, t) => n + (t.tpl !== undefined ? 1 : t.s.length), 0);
// The tokens before and after a place
function splitAt(tokens, at) {
    const before = [], after = [];
    let n = 0;
    for (const t of tokens) {
        const len = t.tpl !== undefined ? 1 : t.s.length;
        if (n + len <= at) before.push(t);
        else if (n >= at) after.push(t);
        else { before.push({ s: t.s.slice(0, at - n) }); after.push({ s: t.s.slice(at - n) }); }
        n += len;
    }
    return [before, after];
}

// --- What a chip shows ---------------------------------------------------------

const UNIT_NAMES = { d: ['day', 'days'], w: ['week', 'weeks'], m: ['month', 'months'], y: ['year', 'years'], h: ['hour', 'hours'], min: ['minute', 'minutes'] };
export function dateParts(tpl) {
    const m = tpl.match(/^\{\{datetime(?:([+-])(\d+)(min|[hdwmy]))?:(.*)\}\}$/);
    if (!m) return null;
    return { sign: m[1] || '+', n: m[2] ? +m[2] : 0, unit: m[3] || 'd', format: m[4], time: /%[HIMp]/.test(m[4]) };
}
const dateTpl = p => `{{datetime${p.n ? (p.sign || '+') + p.n + p.unit : ''}:${p.format}}}`;
const SYMBOL_TPL = /^\{\{(mdi|fastener|electronic):([^}]+)\}\}$/;
const ENV_TPL = /^\{\{env:([^}]+)\}\}$/;
const RANDOM_TPL = /^\{\{random(?::(\d+))?(?::(s(?:hift)?))?\}\}$/;
const glyphs = {};

export function chipInfo(tpl) {
    if (tpl === '\n') return { kind: 'break', label: '', icon: 'break', title: 'A line break, the next part keeps the style of this line' };
    let m;
    if ((m = tpl.match(/^\{\{blank:([^}]+)\}\}$/))) return { kind: 'blank', label: m[1], icon: 'blank', name: m[1] };
    const date = dateParts(tpl);
    if (date) {
        const base = date.time ? 'now' : 'today';
        const label = date.n ? `${base} ${date.sign === '-' ? '-' : '+'} ${date.n} ${UNIT_NAMES[date.unit][date.n === 1 ? 0 : 1]}` : base;
        return { kind: 'date', label, icon: 'cal', date };
    }
    if ((m = tpl.match(/^\{\{counter(?::([0-9A-Za-z]+)(?::(-?\d+))?)?\}\}$/))) {
        const label = m[1] || m[2] ? `counter ${m[1] || '1'}${m[2] && m[2] !== '1' ? `, step ${m[2]}` : ''}` : 'counter';
        return { kind: 'counter', label, icon: 'hash', start: m[1] || '', step: m[2] || '' };
    }
    if (tpl === '{{total}}') return { kind: 'total', label: 'number of labels', icon: 'hash' };
    if ((m = tpl.match(/^\{\{asn(?::(\d{1,2}))?\}\}$/))) return { kind: 'variable', label: m[1] ? `ASN, ${m[1]} digits` : 'ASN', icon: 'braces', digits: m[1] || '' };
    if (tpl === '{{short-uuid}}') return { kind: 'variable', label: 'ID', icon: 'braces' };
    if (tpl === '{{uuid}}') return { kind: 'variable', label: 'UUID', icon: 'braces' };
    if ((m = tpl.match(SYMBOL_TPL))) return { kind: 'symbol', label: m[2], icon: 'star', set: m[1], name: m[2], glyph: glyphs[m[1] + ':' + m[2]] };
    if ((m = tpl.match(ENV_TPL))) return { kind: 'variable', label: m[1], icon: 'braces', env: m[1] };
    if ((m = tpl.match(RANDOM_TPL))) return { kind: 'variable', label: `random, ${m[1] || 64}`, icon: 'braces', random: { length: m[1] || '', shift: !!m[2] } };
    return { kind: 'variable', label: tpl.slice(2, -2), icon: 'braces' };
}

const KIND_NAMES = { blank: 'Blank', date: 'Date', counter: 'Counter', symbol: 'Symbol', variable: 'Variable', break: 'Line break' };
// A chip is a button: Enter or Space open its options
function chipHTML(tpl, at) {
    const c = chipInfo(tpl);
    const glyph = c.glyph ? `<span class="glyph ${c.set === 'mdi' ? 'mdi' : 'sx'}">${esc(c.glyph)}</span>` : icon(c.icon);
    const name = [KIND_NAMES[c.kind], c.label].filter(Boolean).join(': ');
    return `<span class="chip ${esc(c.kind)}" contenteditable="false" role="button" tabindex="0" aria-label="${esc(name)}" data-tpl="${esc(tpl)}" data-at="${esc(at)}"${c.title ? ` title="${esc(c.title)}"` : ''}>${glyph}${c.label ? `<span>${esc(c.label)}</span>` : ''}</span>`;
}

function lineHTML(text, i, on) {
    const tokens = tokensOf(text);
    let html = '', at = 0;
    tokens.forEach((t, k) => {
        if (t.tpl !== undefined) {
            if (k === 0) html += ZW;
            html += chipHTML(t.tpl, at);
            // The caret finds a place after a chip at the end or before another
            if (!tokens[k + 1] || tokens[k + 1].tpl !== undefined) html += ZW;
            at++;
        } else {
            html += esc(t.s);
            at += t.s.length;
        }
    });
    return `<div class="ln${on ? ' on' : ''}" data-i="${esc(i)}">${html || '<br>'}</div>`;
}

// --- Reading the field ---------------------------------------------------------

function readNode(node) {
    if (node.nodeType === Node.TEXT_NODE) return node.nodeValue.replaceAll(ZW, '');
    if (node.nodeType !== Node.ELEMENT_NODE) return '';
    if (node.dataset && node.dataset.tpl !== undefined) return node.dataset.tpl;
    if (node.tagName === 'BR') return '';
    return [...node.childNodes].map(readNode).join('');
}
// Units before (node, offset) within a line
function unitsBefore(line, node, offset) {
    let n = 0;
    const walk = el => {
        for (const child of el.childNodes) {
            if (child === node) {
                if (child.nodeType === Node.TEXT_NODE) n += child.nodeValue.slice(0, offset).replaceAll(ZW, '').length;
                return true;
            }
            if (child.nodeType === Node.TEXT_NODE) n += child.nodeValue.replaceAll(ZW, '').length;
            else if (child.dataset && child.dataset.tpl !== undefined) {
                if (child.contains(node)) { n++; return true; }
                n++;
            } else if (child.nodeType === Node.ELEMENT_NODE) {
                if (child.contains(node) && walk(child)) return true;
                if (child === node) return true;
            }
        }
        return false;
    };
    if (node === line) {
        [...line.childNodes].slice(0, offset).forEach(c => { n += unitsOf(tokensOf(readNode(c))); });
        return n;
    }
    walk(line);
    return n;
}

// The caret as line (in the field's order) and place in it, null outside
export function caret() {
    const sel = getSelection();
    if (!root || !sel.rangeCount || !root.contains(sel.focusNode)) return null;
    const lines = $$('.ln', root);
    let node = sel.focusNode, offset = sel.focusOffset;
    if (!lines.length) return null;
    if (node === root) {
        const line = Math.min(offset, lines.length - 1);
        return { line, at: offset >= lines.length ? unitsOf(tokensOf(readNode(lines[line]))) : 0, collapsed: sel.isCollapsed };
    }
    const line = lines.findIndex(l => l.contains(node));
    if (line < 0) return null;
    return { line, at: unitsBefore(lines[line], node, offset), collapsed: sel.isCollapsed };
}

// Puts the caret at a place of a line
export function placeCaret(line, at) {
    const div = $$('.ln', root)[line];
    if (!div) return;
    let rest = at;
    const range = document.createRange();
    // An empty line has a <br> only, the caret goes before it
    if (at <= 0 && !(div.firstChild && div.firstChild.nodeType === Node.TEXT_NODE)) {
        range.setStart(div, 0);
        range.collapse(true);
        const sel = getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        return;
    }
    for (const child of div.childNodes) {
        if (child.nodeType === Node.TEXT_NODE) {
            const v = child.nodeValue;
            const plain = v.replaceAll(ZW, '').length;
            if (rest <= plain) {
                // The offset in the node, the zero width spaces skipped
                let k = 0, seen = 0;
                while (k < v.length && (seen < rest || v[k] === ZW)) { if (v[k] !== ZW) seen++; k++; }
                range.setStart(child, k);
                range.collapse(true);
                const sel = getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
                return;
            }
            rest -= plain;
        } else if (child.dataset && child.dataset.tpl !== undefined) {
            rest -= 1;
            if (rest === 0 && !(child.nextSibling && child.nextSibling.nodeType === Node.TEXT_NODE)) {
                range.setStartAfter(child);
                range.collapse(true);
                const sel = getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
                return;
            }
        }
    }
    range.selectNodeContents(div);
    range.collapse(false);
    const sel = getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
}

// --- Drawing ---------------------------------------------------------------------

const design = () => S.edit.design;

export function renderField(keep = null) {
    const d = design();
    if (!d.text.length) d.text.push(L('', C.defaults.font_size, true));
    const at = keep === true ? caret() : keep;
    root.innerHTML = d.text.map((l, i) => lineHTML(l.text, i, i === ctx.pickedLine())).join('');
    root.classList.toggle('empty', d.text.length === 1 && !String(d.text[0].text).length);
    if (at) {
        root.focus({ preventScroll: true });
        placeCaret(Math.min(at.line, d.text.length - 1), at.at);
    }
}

export function markLine(i) {
    $$('.ln', root).forEach((l, k) => l.classList.toggle('on', k === i));
}

export function focusLine(i, atEnd = true) {
    const d = design();
    root.focus({ preventScroll: true });
    placeCaret(i, atEnd ? unitsOf(tokensOf(d.text[i] ? d.text[i].text : '')) : 0);
}

// Each change is read back from the field. Lines the browser joined or
// split, e.g. when a selection over several lines is deleted, keep the
// style of the line they started in.
function readBack() {
    const d = design();
    const divs = [...root.childNodes];
    const plain = divs.every((n, k) => n.nodeType === Node.ELEMENT_NODE && n.classList.contains('ln') && n.dataset.i === String(k)) && divs.length === d.text.length;
    if (plain) {
        divs.forEach((div, k) => { d.text[k].text = readNode(div); });
        root.classList.toggle('empty', d.text.length === 1 && !String(d.text[0].text).length);
        return false;
    }
    const sel = getSelection(), focus = sel.rangeCount ? sel.focusNode : null;
    let at = caret(), stray = '';
    const used = new Set(), lines = [];
    for (const n of divs) {
        const text = readNode(n);
        const i = n.nodeType === Node.ELEMENT_NODE && n.dataset ? n.dataset.i : undefined;
        // Text a browser put outside the lines belongs to the line before it,
        // before the first one to that, and the caret stays in it
        if (i === undefined && n.nodeType !== Node.ELEMENT_NODE) {
            const last = lines[lines.length - 1], typed = n.nodeValue.slice(0, sel.focusOffset).replaceAll(ZW, '').length;
            if (!at && n === focus) at = last ? { line: lines.length - 1, at: unitsOf(tokensOf(last.text)) + typed } : { line: 0, at: unitsOf(tokensOf(stray)) + typed };
            if (last) last.text += text; else stray += text;
            continue;
        }
        const from = d.text[+i] || lines[lines.length - 1] || d.text[0] || L('', C.defaults.font_size, true);
        const line = used.has(from) ? clone(from) : from;
        used.add(from);
        line.text = (lines.length ? '' : stray) + text;
        lines.push(line);
    }
    if (!lines.length && stray) lines.push(Object.assign(d.text[0] || L('', C.defaults.font_size, true), { text: stray }));
    d.text = lines.length ? lines : [L('', C.defaults.font_size, true)];
    renderField(at);
    return true;
}

function setLineTokens(i, tokens) {
    design().text[i].text = textOf(tokens);
}

// A new line after line i: at the end of a line it starts smaller and
// regular; a line split keeps its style
function newLineAfter(i, tokens, split) {
    const d = design(), was = d.text[i];
    const line = split ? clone(was) : L('', Math.max(20, Math.round(+was.size * .6)), false, was.align, { font: plainFont(was.font), line_spacing: was.line_spacing });
    line.text = textOf(tokens);
    d.text.splice(i + 1, 0, line);
}

function insertTokens(insert, { line, at }) {
    const d = design();
    const [before, after] = splitAt(tokensOf(d.text[line].text), at);
    setLineTokens(line, [...before, ...insert, ...after]);
    return { line, at: at + unitsOf(insert) };
}

// Text with [Name] or [today] typed in it: each becomes its template
const withTemplates = text => text.replace(/\[([^\]\n]+)\]/g, (m, name) => templateOf(name));

const placeOf = chip => ({ line: $$('.ln', root).indexOf(chip.closest('.ln')), at: +chip.dataset.at });
function onKeyDown(e) {
    if (menu && menuKeys(e)) return;
    const chip = e.target.closest && e.target.closest('.chip');
    if (chip) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openChip(placeOf(chip), false, true); }
        else if (e.key === 'Backspace' || e.key === 'Delete') { e.preventDefault(); removeChip(placeOf(chip)); }
        return;
    }
    const c = caret();
    if (!c) return;
    const d = design();
    // A chip next to the caret goes with one key, the zero width spaces
    // around it are no characters of the line
    if ((e.key === 'Backspace' || e.key === 'Delete') && c.collapsed) {
        const at = e.key === 'Backspace' ? c.at - 1 : c.at;
        const [, rest] = splitAt(tokensOf(d.text[c.line].text), at);
        if (at >= 0 && rest[0] && rest[0].tpl !== undefined) {
            e.preventDefault();
            removeChip({ line: c.line, at });
            return;
        }
    }
    if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        if (!c.collapsed) { document.execCommand('delete'); readBack(); }
        const now = caret();
        const tokens = tokensOf(d.text[now.line].text);
        if (e.shiftKey) {
            const place = insertTokens([{ tpl: '\n' }], now);
            renderField(place);
        } else {
            const [before, after] = splitAt(tokens, now.at);
            setLineTokens(now.line, before);
            newLineAfter(now.line, after, after.length > 0 && before.length > 0);
            renderField({ line: now.line + 1, at: 0 });
        }
        ctx.changed(true);
        return;
    }
    if (e.key === 'Backspace' && c.collapsed && c.at === 0 && c.line > 0) {
        e.preventDefault();
        const prev = tokensOf(d.text[c.line - 1].text), cur = tokensOf(d.text[c.line].text);
        setLineTokens(c.line - 1, [...prev, ...cur]);
        d.text.splice(c.line, 1);
        renderField({ line: c.line - 1, at: unitsOf(prev) });
        ctx.changed(true);
        return;
    }
    if (e.key === 'Delete' && c.collapsed && c.line < d.text.length - 1 && c.at === unitsOf(tokensOf(d.text[c.line].text))) {
        e.preventDefault();
        const cur = tokensOf(d.text[c.line].text), next = tokensOf(d.text[c.line + 1].text);
        setLineTokens(c.line, [...cur, ...next]);
        d.text.splice(c.line + 1, 1);
        renderField(c);
        ctx.changed(true);
    }
}

function onInput() {
    const rebuilt = readBack();
    const c = caret();
    if (c && c.collapsed) {
        const tokens = tokensOf(design().text[c.line].text);
        const [before] = splitAt(tokens, c.at);
        const last = before[before.length - 1];
        const typed = last && last.s !== undefined ? last.s : '';
        // "[Name]" typed out becomes its chip
        const closed = typed.match(/\[([^\[\]\n]+)\]$/);
        if (closed) {
            closeMenu();
            const start = c.at - closed[0].length;
            const [head] = splitAt(tokens, start), [, tail] = splitAt(tokens, c.at);
            setLineTokens(c.line, [...head, { tpl: templateOf(closed[1]) }, ...tail]);
            renderField({ line: c.line, at: start + 1 });
            ctx.changed(true);
            return;
        }
        const open = typed.match(/\[([^\[\]\n]*)$/);
        if (open) showMenu(c.line, c.at - open[0].length, open[1]);
        else closeMenu();
    } else closeMenu();
    ctx.changed(rebuilt);
}

function onPaste(e) {
    const files = Array.from(e.clipboardData ? e.clipboardData.files : []);
    if (files.length) { e.preventDefault(); ctx.files(files); return; }
    const text = e.clipboardData ? e.clipboardData.getData('text/plain') : '';
    e.preventDefault();
    if (!text) return;
    const c = caret();
    if (!c) return;
    if (!c.collapsed) { document.execCommand('delete'); readBack(); }
    const now = caret(), d = design();
    const rows = text.replace(/\r\n?/g, '\n').split('\n').map(withTemplates);
    const [before, after] = splitAt(tokensOf(d.text[now.line].text), now.at);
    setLineTokens(now.line, [...before, ...tokensOf(rows[0])]);
    let line = now.line;
    for (const row of rows.slice(1)) { newLineAfter(line, tokensOf(row), true); line++; }
    const end = tokensOf(d.text[line].text);
    setLineTokens(line, [...end, ...after]);
    renderField({ line, at: unitsOf(end) });
    ctx.changed(true);
}

// --- The "[" menu -------------------------------------------------------------------

const MENU = [
    { id: 'blank', name: 'Blank', icon: 'blank', note: 'filled in when printing', words: 'blank name field fill' },
    { id: 'date', name: 'Date', icon: 'cal', note: 'today, or a day from now', words: 'date today now time day' },
    { id: 'counter', name: 'Counter', icon: 'hash', note: '1, 2, 3 ...', words: 'counter number count' },
    { id: 'total', name: 'Number of labels', icon: 'hash', note: 'e.g. the 6 in 1 of 6', words: 'number labels total of all count copies' },
    { id: 'symbol', name: 'Symbol', icon: 'star', note: '', words: 'symbol icon sign' },
    { id: 'variable', name: 'Variable', icon: 'braces', note: 'e.g. the ASN', words: 'variable asn id uuid' },
];
let menu = null;

function showMenu(line, start, query) {
    const q = query.trim().toLowerCase();
    let items = MENU.filter(m => !q || m.words.split(' ').some(w => w.startsWith(q)));
    // A name that is no variable makes a blank of that name
    if (q && !isVariable(query.trim())) items = [{ id: 'blank', name: `Blank "${query.trim()}"`, icon: 'blank', note: 'filled in when printing', named: query.trim() }, ...items.filter(m => m.id !== 'blank')];
    if (q && isVariable(query.trim())) items = [{ id: 'typed', name: `[${query.trim()}]`, icon: 'braces', note: 'as typed', named: query.trim() }, ...items];
    if (!items.length) { closeMenu(); return; }
    if (!menu) {
        menu = { el: document.createElement('div'), sel: 0 };
        menu.el.className = 'menu pop-menu';
        menu.el.setAttribute('role', 'listbox');
        document.body.appendChild(menu.el);
        menu.el.addEventListener('mousedown', e => e.preventDefault());
        menu.el.addEventListener('click', e => { const b = e.target.closest('[data-m]'); if (b) pickMenu(+b.dataset.m); });
    }
    Object.assign(menu, { line, start, query, items, sel: Math.min(menu.sel, items.length - 1) });
    menu.el.innerHTML = items.map((m, k) => `<button type="button" role="option" data-m="${esc(k)}" class="${k === menu.sel ? 'on' : ''}">${icon(m.icon)}<span>${esc(m.name)}</span>${m.note ? `<small>${esc(m.note)}</small>` : ''}</button>`).join('');
    const r = caretRect();
    if (r) placePop(menu.el, r);
}
function closeMenu() {
    if (!menu) return;
    menu.el.remove();
    menu = null;
}
function menuKeys(e) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        menu.sel = (menu.sel + (e.key === 'ArrowDown' ? 1 : -1) + menu.items.length) % menu.items.length;
        $$('[data-m]', menu.el).forEach((b, k) => b.classList.toggle('on', k === menu.sel));
        return true;
    }
    if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pickMenu(menu.sel); return true; }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeMenu(); return true; }
    return false;
}
function caretRect() {
    const sel = getSelection();
    if (!sel.rangeCount) return null;
    const range = sel.getRangeAt(0).cloneRange();
    let r = range.getClientRects()[0];
    if (!r) { const ln = sel.focusNode && (sel.focusNode.nodeType === 1 ? sel.focusNode : sel.focusNode.parentElement); r = ln && ln.getBoundingClientRect(); }
    return r;
}
// A popover below a place, kept in the window
function placePop(el, r) {
    el.style.left = '0px';
    el.style.top = '0px';
    const w = el.offsetWidth, h = el.offsetHeight;
    const left = Math.max(8, Math.min(innerWidth - w - 8, r.left));
    const below = r.bottom + 8;
    el.style.left = `${left}px`;
    el.style.top = `${below + h > innerHeight - 8 ? Math.max(8, r.top - h - 8) : below}px`;
}

const DEFAULT_BLANKS = ['What', 'Name', 'Where', 'Number', 'Note'];
function pickMenu(k) {
    const m = menu.items[k], { line, start } = menu;
    const c = caret();
    closeMenu();
    const d = design();
    const tokens = tokensOf(d.text[line].text);
    const [head] = splitAt(tokens, start), [, tail] = splitAt(tokens, c && c.line === line ? c.at : start + 1);
    const put = tpl => {
        setLineTokens(line, [...head, { tpl }, ...tail]);
        renderField({ line, at: unitsOf(head) + 1 });
        ctx.changed(true);
        return { line, at: unitsOf(head) };
    };
    const drop = () => {
        setLineTokens(line, [...head, ...tail]);
        renderField({ line, at: unitsOf(head) });
        ctx.changed(true);
        return { line, at: unitsOf(head) };
    };
    if (m.id === 'typed') { put(templateOf(m.named)); return; }
    if (m.id === 'blank') {
        const used = blanksOf(d);
        const name = m.named || DEFAULT_BLANKS.find(n => !used.includes(n)) || 'Blank ' + (used.length + 1);
        if (!m.named || !(d.fields || {})[name]) {
            d.fields = Object.assign({}, d.fields, { [name]: { type: guessKind(name) } });
        }
        const place = put(`{{blank:${name}}}`);
        // Typed after [, the blank has its name, else it asks for one
        if (!m.named) openChip(place, true, true);
    } else if (m.id === 'date') put(`{{datetime:${dateFormat()}}}`);
    else if (m.id === 'counter') put('{{counter}}');
    else if (m.id === 'total') put('{{total}}');
    else if (m.id === 'symbol') { const place = drop(); openSymbols(place, null); }
    else if (m.id === 'variable') { const place = drop(); openVariables(place, null); }
}

// --- Options of a chip --------------------------------------------------------------

let pop = null;
export function closePop() {
    if (!pop) return;
    pop.el.remove();
    $$('.chip.picked', root).forEach(c => c.classList.remove('picked'));
    document.removeEventListener('mousedown', pop.outside, true);
    pop = null;
}
// The options take the focus where they were opened by the user, or where
// they had it when drawn again; inserted chips leave it in the text
let focusNext = false;
function openPop(place, html, cls = '') {
    const had = !!pop && pop.el.contains(document.activeElement), focus = focusNext || had;
    focusNext = false;
    closePop();
    const el = document.createElement('div');
    el.className = 'chip-pop ' + cls;
    el.innerHTML = html;
    document.body.appendChild(el);
    const outside = e => { if (!el.contains(e.target) && !(e.target.closest && e.target.closest('.chip'))) closePop(); };
    document.addEventListener('mousedown', outside, true);
    el.addEventListener('keydown', e => {
        if (e.key !== 'Escape') return;
        e.stopPropagation();
        const chip = place && chipEl(place);
        closePop();
        if (chip && chip.isConnected) chip.focus(); else root.focus();
    });
    pop = { el, place, outside };
    anchor();
    const first = focus && $('input, select, textarea, button', el);
    if (first) first.focus({ preventScroll: true });
    return el;
}
function chipEl(place) {
    const div = $$('.ln', root)[place.line];
    return div && $(`.chip[data-at="${CSS.escape(place.at)}"]`, div);
}
function anchor() {
    if (!pop) return;
    const chip = pop.place && chipEl(pop.place);
    const r = chip ? chip.getBoundingClientRect() : caretRect() || root.getBoundingClientRect();
    placePop(pop.el, r);
    $$('.chip.picked', root).forEach(c => c.classList.remove('picked'));
    if (chip) chip.classList.add('picked');
}
function setChip(place, tpl) {
    const tokens = tokensOf(design().text[place.line].text);
    if (!tokens.length) return;
    let n = 0;
    for (const t of tokens) {
        const len = t.tpl !== undefined ? 1 : t.s.length;
        if (n === place.at && t.tpl !== undefined) { t.tpl = tpl; break; }
        n += len;
    }
    setLineTokens(place.line, tokens);
    renderField();
    ctx.changed(true);
    anchor();
}
function removeChip(place) {
    const tokens = tokensOf(design().text[place.line].text);
    const [head, rest] = splitAt(tokens, place.at);
    setLineTokens(place.line, [...head, ...rest.slice(1)]);
    closePop();
    renderField(place);
    ctx.changed(true);
}
const tplAt = place => { const div = $$('.ln', root)[place.line]; const c = div && $(`.chip[data-at="${CSS.escape(place.at)}"]`, div); return c ? c.dataset.tpl : null; };

const removeBtn = '<button type="button" class="btn ghost danger" data-remove>' + icon('trash') + 'Remove</button>';
const OFFSET_UNITS = [['d', 'days'], ['w', 'weeks'], ['m', 'months'], ['y', 'years']];
const TIME_UNITS = [['h', 'hours'], ['min', 'minutes']];

export function openChip(place, naming = false, focus = false) {
    const tpl = tplAt(place);
    if (tpl === null) return;
    focusNext = focus;
    const c = chipInfo(tpl);
    if (c.kind === 'blank') return blankPop(place, c.name, naming);
    if (c.kind === 'date') return datePop(place, c.date);
    if (c.kind === 'counter') return counterPop(place, c);
    if (c.kind === 'total') {
        const el = openPop(place, `<h4>Number of labels</h4><p class="muted">How many labels are printed together, e.g. the 6 in Box 1 of 6. Set it with the copies.</p>
            <div class="pop-acts"><span></span>${removeBtn}</div>`);
        $('[data-remove]', el).onclick = () => removeChip(place);
        return;
    }
    if (c.kind === 'symbol') return openSymbols(place, tpl);
    if (c.kind === 'break') {
        const el = openPop(place, `<h4>Line break</h4><p class="muted">The text after it is a line of its own on the label, in the style of this line.</p>
            <div class="pop-acts"><button type="button" class="btn soft" data-split>${icon('break')}Style it on its own</button>${removeBtn}</div>`);
        $('[data-split]', el).onclick = () => {
            const d = design(), tokens = tokensOf(d.text[place.line].text);
            const [head, rest] = splitAt(tokens, place.at);
            setLineTokens(place.line, head);
            newLineAfter(place.line, rest.slice(1), true);
            closePop();
            renderField({ line: place.line + 1, at: 0 });
            ctx.changed(true);
        };
        $('[data-remove]', el).onclick = () => removeChip(place);
        return;
    }
    return openVariables(place, tpl);
}

function blankPop(place, name, naming) {
    const d = design(), item = { design: d };
    const kind = fieldType(item, name), f = (d.fields || {})[name] || {};
    const value = name in (S.edit.values || {}) ? S.edit.values[name] : startOf(item, name);
    const start = blankStart(item, name);
    const offset = DATE_OFFSET_RE.test(start || '') ? start : '';
    const fill = kind === 'choice' ? `<select class="in" data-value>${blankChoices(item, name).map(ch => `<option${ch === value ? ' selected' : ''}>${esc(ch)}</option>`).join('') || '<option value="">No choices yet</option>'}</select>`
        : `<input class="in" data-value type="${kind === 'number' ? 'number' : kind === 'date' ? 'date' : 'text'}" value="${esc(kind === 'date' ? value || today() : value)}" placeholder="Empty">`;
    const el = openPop(place, `<h4>Blank</h4>
        <div class="ft">
            <label class="k" for="cpName">Name</label><input class="in" id="cpName" value="${esc(name)}" autocomplete="off">
            <span class="k">Kind</span><span class="seg">${BLANK_KINDS.map(([v, n]) => `<button type="button" data-kind="${esc(v)}" class="${kind === v ? 'on' : ''}">${esc(n)}</button>`).join('')}</span>
            ${kind === 'choice' ? `<label class="k" for="cpChoices">Choices</label><textarea class="in" id="cpChoices" rows="3" placeholder="One per line, the first is the default">${esc(blankChoices(item, name).join('\n'))}</textarea>`
            : kind === 'date' ? `<span class="k">Starts at</span><span class="row">today + <input class="in num" type="number" data-off-n value="${offset ? esc(parseInt(offset, 10)) : ''}" placeholder="0"><select class="in" data-off-u>${OFFSET_UNITS.map(([v, n]) => `<option value="${esc(v)}"${offset && offset.slice(-1) === v ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select></span>`
            : `<label class="k" for="cpDef">Default</label><input class="in" id="cpDef" type="${kind === 'number' ? 'number' : 'text'}" value="${esc(f.default || '')}" placeholder="Optional">`}
            <span class="k">This print</span>${fill}
        </div>
        <p class="err" hidden></p>
        <div class="pop-acts"><span class="muted small">Filled in when printing</span>${removeBtn}</div>`);
    const err = $('.err', el);
    const say = t => { err.textContent = t; err.hidden = !t; };
    const setField = patch => {
        d.fields = Object.assign({}, d.fields);
        d.fields[name] = Object.assign({}, d.fields[name] || { type: guessKind(name) }, patch);
        if (d.fields[name].default === '') delete d.fields[name].default;
        if (d.fields[name].type !== 'choice') delete d.fields[name].choices;
        if (d.blanks && name in d.blanks) { d.blanks = Object.assign({}, d.blanks); delete d.blanks[name]; }
        ctx.changed(false);
    };
    const nameIn = $('#cpName', el);
    nameIn.addEventListener('change', () => {
        const n = nameIn.value.trim();
        if (n === name) return;
        const why = !n ? 'Give the blank a name.' : /[\[\]{}\n]/.test(n) ? 'Brackets cannot be part of the name.' : isVariable(n) ? `[${n}] is a variable already.` : '';
        if (why) { say(why); return; }
        // Every chip of the blank takes the name, with its settings and value
        const from = `{{blank:${name}}}`, to = `{{blank:${n}}}`;
        d.text.forEach(l => { l.text = String(l.text).split(from).join(to); });
        if (d.code_text) d.code_text = String(d.code_text).split(from).join(to);
        if (d.fields && d.fields[name]) { d.fields = Object.assign({}, d.fields, { [n]: d.fields[name] }); delete d.fields[name]; }
        if (S.edit.values && name in S.edit.values) { S.edit.values[n] = S.edit.values[name]; delete S.edit.values[name]; }
        renderField();
        ctx.changed(true);
        blankPop(place, n, false);
        $('#cpName', pop.el).focus();
    });
    $$('[data-kind]', el).forEach(b => b.onclick = () => {
        const toChoice = b.dataset.kind === 'choice' && kind !== 'choice';
        setField(Object.assign({ type: b.dataset.kind, default: '' }, toChoice ? { choices: kind === 'date' ? [] : choicesOf(value || '') } : {}));
        if (S.edit.values) delete S.edit.values[name];
        blankPop(place, name, false);
        if (toChoice) $('#cpChoices', pop.el).focus();
    });
    const choices = $('#cpChoices', el);
    if (choices) choices.addEventListener('change', () => { setField({ choices: choicesOf(choices.value) }); blankPop(place, name, false); });
    const def = $('#cpDef', el);
    if (def) def.addEventListener('change', () => setField({ default: def.value.trim() }));
    $$('[data-off-n], [data-off-u]', el).forEach(x => x.addEventListener('change', () => {
        const n = parseInt($('[data-off-n]', el).value, 10) || 0;
        setField({ default: n ? `${n > 0 ? '+' : ''}${n}${$('[data-off-u]', el).value}` : '' });
    }));
    const val = $('[data-value]', el);
    val.addEventListener(kind === 'choice' ? 'change' : 'input', () => {
        S.edit.values = Object.assign({}, S.edit.values, { [name]: val.value });
        ctx.changed(false);
    });
    $('[data-remove]', el).onclick = () => removeChip(place);
    if (naming) { nameIn.focus(); nameIn.select(); }
}

const settingsFormat = time => time ? dateFormat() + ' %H:%M' : dateFormat();
function datePop(place, date) {
    const units = date.time ? OFFSET_UNITS.concat(TIME_UNITS) : OFFSET_UNITS;
    const own = date.format !== settingsFormat(date.time);
    const el = openPop(place, `<h4>Date</h4>
        <div class="ft">
            <span class="k">Shows</span><span class="seg"><button type="button" data-time="0" class="${date.time ? '' : 'on'}">The day</button><button type="button" data-time="1" class="${date.time ? 'on' : ''}">Day and time</button></span>
            <span class="k">From now</span><span class="row"><span class="az"><button type="button" class="azb" data-step="-1" aria-label="Earlier">${icon('minus', '')}</button><input class="anum num" data-n value="${esc((date.sign === '-' ? -1 : 1) * date.n)}" inputmode="numeric" aria-label="How many"><button type="button" class="azb" data-step="1" aria-label="Later">${icon('plus', '')}</button></span>
                <select class="in" data-unit>${units.map(([v, n]) => `<option value="${esc(v)}"${v === date.unit ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select></span>
            <label class="k" for="cpFormat">Format</label><span class="row"><input class="in" id="cpFormat" value="${esc(date.format)}" spellcheck="false" autocomplete="off" aria-label="Date format"><button type="button" class="custom" data-codes>Codes</button></span>
        </div>
        <div class="pop-acts"><span class="muted small">${own ? 'Its own format' : 'The date format of the settings'}</span>${own ? '<button type="button" class="custom" data-like>Like the settings</button>' : ''}${removeBtn}</div>`);
    const apply = () => {
        const n = parseInt($('[data-n]', el).value, 10) || 0;
        const next = Object.assign({}, date, { n: Math.abs(n), sign: n < 0 ? '-' : '+', unit: $('[data-unit]', el).value });
        setChip(place, dateTpl(next));
        Object.assign(date, next);
    };
    $$('[data-step]', el).forEach(b => b.onclick = () => { const inp = $('[data-n]', el); inp.value = (parseInt(inp.value, 10) || 0) + +b.dataset.step; apply(); });
    $('[data-n]', el).addEventListener('input', apply);
    $('[data-unit]', el).addEventListener('change', apply);
    // A format of its own, as {{datetime:<format>}}, with or without the time
    const format = $('#cpFormat', el);
    format.addEventListener('change', () => {
        const f = format.value.replace(/[{}]/g, '').trim() || settingsFormat(date.time);
        const time = /%[HIMp]/.test(f), unit = !time && TIME_UNITS.some(([u]) => u === date.unit) ? 'd' : date.unit;
        const next = Object.assign({}, date, { format: f, time, unit });
        setChip(place, dateTpl(next));
        datePop(place, next);
    });
    format.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); format.blur(); } });
    $('[data-codes]', el).onclick = showDateCodes;
    const like = $('[data-like]', el);
    if (like) like.onclick = () => { const next = Object.assign({}, date, { format: settingsFormat(date.time) }); setChip(place, dateTpl(next)); datePop(place, next); };
    $$('[data-time]', el).forEach(b => b.onclick = () => {
        const time = b.dataset.time === '1';
        if (time === date.time) return;
        const format = time ? date.format + ' %H:%M' : date.format.replace(/[ ,]*%[HIMp](:%[MS])?(:%S)?/g, '').trim() || dateFormat();
        const unit = !time && TIME_UNITS.some(([u]) => u === date.unit) ? 'd' : date.unit;
        const next = Object.assign({}, date, { time, format, unit, n: unit !== date.unit ? 0 : date.n });
        setChip(place, dateTpl(next));
        datePop(place, next);
    });
    $('[data-remove]', el).onclick = () => removeChip(place);
}

function counterPop(place, c) {
    const el = openPop(place, `<h4>Counter</h4>
        <div class="ft">
            <label class="k" for="cpStart">Starts at</label><input class="in" id="cpStart" value="${esc(c.start)}" placeholder="1" autocomplete="off">
            <label class="k" for="cpStep">Step</label><input class="in num" id="cpStep" type="number" value="${esc(c.step)}" placeholder="1">
        </div>
        <p class="muted small">Each copy gets the next number. It counts as the start looks, e.g. 001, 002 or A, B, C.</p>
        <div class="pop-acts"><span></span>${removeBtn}</div>`);
    const apply = () => {
        const start = $('#cpStart', el).value.trim().replace(/[^0-9A-Za-z]/g, ''), step = $('#cpStep', el).value.trim();
        const s = /^-?\d+$/.test(step) && step !== '1' ? step : '';
        setChip(place, `{{counter${start || s ? ':' + (start || '1') : ''}${s ? ':' + s : ''}}}`);
    };
    $('#cpStart', el).addEventListener('input', apply);
    $('#cpStep', el).addEventListener('input', apply);
    $('[data-remove]', el).onclick = () => removeChip(place);
}

const VARIABLES = [
    ['{{asn}}', 'ASN', 'The next archive serial number for Paperless-ngx'],
    ['{{short-uuid}}', 'ID', 'A short random ID'],
    ['{{uuid}}', 'UUID', 'A random UUID'],
    [null, 'Date and time', 'When it is printed', () => `{{datetime:${dateFormat()} %H:%M}}`],
    [null, 'Random text', 'Letters, digits and signs, a new one each time', () => '{{random:8}}'],
    [null, 'Environment variable', 'Of the server, if it lists it in LABEL_TEMPLATE_ENV_VARS', null],
];
// What the variables do, shown from the bracket menu
const VARS_HELP = `<p>Variables put content into the label when it is printed. Type them between brackets or choose them after [.</p>
    <ul>
        <li><code>[Name]</code> - a blank: whoever prints the label fills it in. A name ending in "on" or containing "date" (like <code>[Frozen on]</code>) asks for a date, <code>[Box]</code>, <code>[Number]</code>, <code>[Portions]</code> and <code>[Count]</code> for a number, every other name for text.</li>
        <li><code>[today]</code> - today's date, in the date format of the settings. <code>[now]</code> - the date and the time.</li>
        <li><code>[today+5d]</code> - a date counted from today: + or -, a number, and d, w, m or y for days, weeks, months or years, with the time h or min, e.g., <code>[now+30min]</code>.</li>
        <li><code>[counter]</code> - a number that counts up, each copy gets the next one: <code>[counter:001]</code> 001, 002, <code>[counter:A]</code> A, B, C, <code>[counter:10:5]</code> 10, 15, 20.</li>
        <li>Number of labels, chosen after [ - how many labels are printed together: <code>Box [counter] of</code> and the number of labels print Box 1 of 6 to Box 6 of 6.</li>
        <li><code>[ASN]</code> - the next archive serial number for Paperless-ngx, <code>[ASN:5]</code> with 5 digits, e.g., 00042.</li>
        <li><code>[id]</code> - a short random ID.</li>
    </ul>
    <p>The variables of the server work as well:</p>
    <ul>
        <li><code>{{datetime:&lt;format&gt;}}</code> - the date and time in a format of <button type="button" class="custom" data-codes>date and time codes</button>, <code>{{datetime+5d:&lt;format&gt;}}</code> 5 days later.</li>
        <li><code>{{counter[:&lt;start&gt;[:&lt;step&gt;]]}}</code>, <code>{{total}}</code>, <code>{{asn[:&lt;digits&gt;]}}</code>, <code>{{uuid}}</code>, <code>{{short-uuid}}</code> - as above.</li>
        <li><code>{{mdi:&lt;name&gt;}}</code>, <code>{{fastener:&lt;name&gt;}}</code>, <code>{{electronic:&lt;name&gt;}}</code> - an icon or symbol, Symbol finds the names.</li>
        <li><code>{{env:&lt;var&gt;}}</code> - the environment variable &lt;var&gt;, if it is listed in <code>LABEL_TEMPLATE_ENV_VARS</code>.</li>
        <li><code>{{random[:&lt;len&gt;][:shift]}}</code> - random text of &lt;len&gt; characters (64 unless given), <code>shift</code> shifts it around to fill gaps.</li>
    </ul>`;

// The codes of {{datetime:<format>}}, with examples the server made
function showDateCodes() {
    const dc = C.datetime_codes || { example: '', codes: [] };
    const rows = dc.codes.map(([code, meaning, example]) => `<tr><td><code>${esc(code)}</code></td><td>${esc(meaning)}</td><td>${esc(example)}</td></tr>`).join('');
    openLayer(`<div class="dialog narrow codes" role="dialog" aria-label="Date and time codes">
        <div class="dialog-h"><div><h2>Date and time codes</h2><p class="muted">For a date of its own format, e.g. <code>%d.%m.%Y</code>. The examples are for ${esc(dc.example)}.</p></div><span class="sp"></span>
            <button class="iconbtn" type="button" data-close aria-label="Close">${icon('x', '')}</button></div>
        <div class="codes-scroll"><table><thead><tr><th>Code</th><th>Stands for</th><th>Example</th></tr></thead><tbody>${rows}</tbody></table></div></div>`, 'center');
    $('#layer [data-close]').focus();
}
// The variables to insert at place, or to replace the one there (tpl)
function openVariables(place, tpl, naming = false) {
    const c = tpl ? chipInfo(tpl) : null;
    const asn = tpl && /^\{\{asn/.test(tpl);
    const on = (v, n) => tpl && (v ? v === tpl || (asn && v === '{{asn}}') : (n === 'Random text' && c.random) || (n === 'Environment variable' && c.env));
    const known = asn || (c && (c.random || c.env)) || VARIABLES.some(([v]) => v === tpl);
    const el = openPop(tpl ? place : null, `<h4>${tpl ? 'Variable' : 'Insert a variable'}</h4>
        <div class="list">${VARIABLES.map(([v, n, note], k) => `<button type="button" data-var="${esc(k)}" class="${on(v, n) ? 'on' : ''}">${icon('braces')}<span><b>${esc(n)}</b><small>${esc(note)}</small></span></button>`).join('')}</div>
        ${asn ? `<div class="ft"><label class="k" for="cpDigits">Digits</label><input class="in num" id="cpDigits" type="number" min="1" max="20" value="${esc(c.digits)}" placeholder="As it comes"></div>` : ''}
        ${c && c.random ? `<div class="ft"><label class="k" for="cpLen">Characters</label><span class="row"><input class="in num" id="cpLen" type="number" min="1" max="10000" value="${esc(c.random.length)}" placeholder="64"><label class="row small"><input type="checkbox" id="cpShift"${c.random.shift ? ' checked' : ''}>Shifted around</label></span></div>` : ''}
        ${!tpl || (c && c.env) ? `<div class="ft env-row"${tpl || naming ? '' : ' hidden'}><label class="k" for="cpEnv">Name</label><span class="row"><input class="in" id="cpEnv" value="${esc(c && c.env ? c.env : '')}" placeholder="e.g. SITE_NAME" spellcheck="false" autocomplete="off">${tpl ? '' : '<button type="button" class="btn soft sm" data-envgo>Insert</button>'}</span></div>` : ''}
        ${tpl && !known ? `<p class="muted small"><code>${esc(tpl)}</code> is filled in by the server when printing.</p>` : ''}
        <details class="help"><summary>What the variables do</summary><div class="help-body">${VARS_HELP}</div></details>
        ${tpl ? `<div class="pop-acts"><span></span>${removeBtn}</div>` : ''}`, 'wide');
    if (!tpl) placePop(el, caretRect() || root.getBoundingClientRect());
    $$('[data-codes]', el).forEach(b => b.onclick = showDateCodes);
    $('details.help', el).addEventListener('toggle', () => anchor());
    const insert = chosen => {
        if (tpl) { setChip(place, chosen); openChip(place); return; }
        closePop();
        const after = insertTokens([{ tpl: chosen }], place);
        renderField(after);
        ctx.changed(true);
    };
    $$('[data-var]', el).forEach(b => b.onclick = () => {
        const [v, , , make] = VARIABLES[+b.dataset.var];
        // An environment variable is named first
        if (!v && !make) {
            if (tpl && c.env) { $('#cpEnv', el).focus(); return; }
            if (tpl) { setChip(place, '{{env:NAME}}'); openVariables(place, '{{env:NAME}}'); $('#cpEnv', pop.el).select(); return; }
            $('.env-row', el).hidden = false;
            $('#cpEnv', el).focus();
            return;
        }
        insert(v || make());
    });
    const env = $('#cpEnv', el);
    const envName = () => env.value.replace(/[{}\s]/g, '');
    if (env && tpl) env.addEventListener('change', () => { if (envName()) setChip(place, `{{env:${envName()}}}`); });
    if (env && !tpl) {
        const go = () => { if (envName()) insert(`{{env:${envName()}}}`); else env.focus(); };
        $('[data-envgo]', el).onclick = go;
        env.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
    }
    const len = $('#cpLen', el), shift = $('#cpShift', el);
    const random = () => { const n = parseInt(len.value, 10); setChip(place, `{{random${n > 0 ? ':' + Math.min(10000, n) : ''}${shift.checked ? (n > 0 ? '' : ':64') + ':shift' : ''}}}`); };
    if (len) { len.addEventListener('change', random); shift.addEventListener('change', random); }
    const digits = $('#cpDigits', el);
    if (digits) digits.addEventListener('input', () => { const n = parseInt(digits.value, 10); setChip(place, n > 0 && n <= 20 ? `{{asn:${n}}}` : '{{asn}}'); });
    if (tpl) $('[data-remove]', el).onclick = () => removeChip(place);
}

// Symbols are typed as they are, icons and the symbols of the server's sets
// are inserted by name and drawn in every font
async function openSymbols(place, tpl) {
    const el = openPop(tpl ? place : null, `<h4>${tpl ? 'Symbol' : 'Insert a symbol'}</h4>
        <input class="in" type="search" data-q placeholder="Search, e.g. lightbulb, torx or resistor" autocomplete="off">
        <div class="syms" data-grid><p class="muted small">Loading the symbols...</p></div>
        ${tpl ? `<div class="pop-acts"><span></span>${removeBtn}</div>` : ''}`, 'wide');
    if (!tpl) placePop(el, caretRect() || root.getBoundingClientRect());
    if (tpl) $('[data-remove]', el).onclick = () => removeChip(place);
    const [list, icons, sets] = await Promise.all([loadSymbols(), loadIcons(), loadSymbolSets()]);
    if (!el.isConnected) return;
    const named = [['mdi', icons], ...['fastener', 'electronic'].filter(s => sets[s]).map(s => [s, Object.entries(sets[s]).map(([name, code]) => ({ name, char: String.fromCodePoint(parseInt(code, 16)) }))])];
    for (const [set, items] of named) for (const i of items) glyphs[set + ':' + i.name] = i.char;
    const grid = $('[data-grid]', el), q = $('[data-q]', el);
    // The first ones of each set, all of them on request
    const all = new Set();
    const draw = () => {
        const query = q.value.trim();
        const plain = query ? [] : list.map(ch => `<button type="button" class="sym" data-ch="${esc(ch)}">${esc(ch)}</button>`).join('');
        const found = named.map(([set, items]) => {
            const hits = findIcons(items, query), shown = all.has(set) ? hits : hits.slice(0, query ? 60 : 24);
            return hits.length ? `<h5>${esc(SYMBOL_SETS[set])}</h5><div class="sym-grid">${shown.map(i => `<button type="button" class="sym ${set === 'mdi' ? 'mdi' : 'sx'}" data-tpl="${esc(set)}:${esc(i.name)}" title="${esc(i.name)}">${esc(i.char)}</button>`).join('')}</div>
                ${shown.length < hits.length ? `<button type="button" class="custom" data-all="${esc(set)}">Show all ${esc(hits.length)}</button>` : ''}` : '';
        }).join('');
        grid.innerHTML = (plain ? `<h5>Symbols</h5><div class="sym-grid">${plain}</div>` : '') + found || '<p class="muted small">Nothing found.</p>';
    };
    draw();
    q.addEventListener('input', () => { all.clear(); draw(); });
    q.focus();
    grid.addEventListener('click', e => {
        const more = e.target.closest('[data-all]');
        if (more) { all.add(more.dataset.all); draw(); return; }
        const b = e.target.closest('[data-ch], [data-tpl]');
        if (!b) return;
        closePop();
        if (tpl) {
            if (b.dataset.tpl) { setChip(place, `{{${b.dataset.tpl}}}`); return; }
            removeChip(place);
        }
        const tokens = b.dataset.tpl ? [{ tpl: `{{${b.dataset.tpl}}}` }] : [{ s: b.dataset.ch }];
        const after = insertTokens(tokens, place);
        renderField(after);
        ctx.changed(true);
    });
    // Chips of icons show their glyph once the lists are there
    renderField(caret() ? true : null);
}

// --- Mounting ---------------------------------------------------------------------

export function mountField(el, context) {
    root = el;
    ctx = context;
    root.addEventListener('keydown', onKeyDown);
    root.addEventListener('input', onInput);
    root.addEventListener('paste', onPaste);
    root.addEventListener('drop', e => { if (e.dataTransfer && e.dataTransfer.files.length) { e.preventDefault(); ctx.files(e.dataTransfer.files); } });
    root.addEventListener('click', e => {
        const chip = e.target.closest('.chip');
        if (chip) openChip(placeOf(chip), false, true);
    });
    root.addEventListener('blur', () => setTimeout(() => { if (menu && !root.contains(document.activeElement)) closeMenu(); }, 100));
    // The field is mounted again after another view, the page listens once
    if (mounted) return;
    mounted = true;
    document.addEventListener('selectionchange', () => {
        if (!root.isConnected) return;
        const c = caret();
        if (c) ctx.caretLine(c.line);
        else if (menu) closeMenu();
    });
    addEventListener('resize', () => { closeMenu(); anchor(); });
}
let mounted = false;
