// What the parts of the label designer share: the configuration, the labels
// as the API stores them, previews and printing. The requests are those the
// API documents in openapi.json.

export const C = window.DESIGNER_CONFIG;
export const BASE = C.base;
export const API = BASE + '/api';
export const SIZES = Object.fromEntries(C.label_sizes.map(s => [s.id, s]));
// Static files, e.g. the symbol lists, next to this page's style sheet
export const STATIC = (document.querySelector('link[href*="designer/main.css"]') || { getAttribute: () => '/static/designer/main.css' })
    .getAttribute('href').replace(/designer\/main\.css.*$/, '');

// --- Small helpers -----------------------------------------------------------

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
export const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const clone = o => JSON.parse(JSON.stringify(o));
export const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
export const icon = (name, cls = 's') => `<svg class="ic ${esc(cls)}" aria-hidden="true"><use href="#i-${esc(name)}"/></svg>`;
// Runs fn at once. Called while it runs, it runs once more afterwards with
// the newest arguments: a preview follows typing without requests piling up.
export function latest(fn) {
    let running = false, next = null;
    const run = async args => {
        running = true;
        try { await fn(...args); } catch (e) { console.error(e); }
        running = false;
        if (next) { const a = next; next = null; run(a); }
    };
    return (...args) => { if (running) next = args; else run(args); };
}
// The date of the browser, not of UTC
export const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
export const listJoin = a => a.length < 2 ? a.join('') : a.slice(0, -1).join(', ') + ' and ' + a[a.length - 1];

// What belongs to this instance is kept apart from other instances below
// the same host, which share the browser's storage
export const own = key => C.storage_prefix + key;
export function load(key, fallback) {
    try {
        const v = localStorage.getItem(key);
        return v === null ? fallback : JSON.parse(v);
    } catch (e) { return fallback; }
}
export function storeQuiet(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch (e) { return false; }
}
export function forget(key) {
    try { localStorage.removeItem(key); } catch (e) { }
}
// What only the label designers before this page kept, e.g. a draft with
// images of a few MB, goes: nothing reads it any more. The oldest kept its
// settings per printer as <name>@<printer>, without the prefix of the
// instance, and its images in IndexedDB.
const GONE_KEYS = ['designer_edit', 'designer_edit_images'];
const GONE_NAMES = ['labeldesigner_settings_v1', 'labeldesigner_settings_history_v1', 'labeldesigner_printer_v1',
    'labeldesigner_demo_language_v1', 'themeMode'];
export function forgetGoneKeys() {
    GONE_KEYS.forEach(key => forget(own(key)));
    let keys = [];
    try { keys = Object.keys(localStorage); } catch (e) { }
    keys.filter(key => GONE_NAMES.some(name => key === name || key.startsWith(name + '@'))).forEach(forget);
    try { if (window.indexedDB) indexedDB.deleteDatabase('brother_ql_images'); } catch (e) { }
}

// A message at the bottom, gone after ms. The queue of the printer shown
// there gives way to it and comes back after it, see queue.js.
let toastTimer, toastId = 0;
export function queueBack() {
    const q = $('#queue');
    if (q) q.hidden = !q.childElementCount || !$('#toast').hidden;
}
export function toast(text, action, ms = 0) {
    const el = $('#toast');
    el.innerHTML = `<span>${esc(text)}</span>` + (action ? `<button class="tbtn" type="button">${esc(action.label)}</button>` : '');
    el.hidden = false;
    queueBack();
    if (action) el.querySelector('button').onclick = () => { el.hidden = true; queueBack(); action.run(); };
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; queueBack(); }, ms || (action ? 6000 : 3500));
    return ++toastId;
}
// Given an id, a message shown after that one stays
export function hideToast(id = toastId) {
    if (id !== toastId) return;
    clearTimeout(toastTimer);
    $('#toast').hidden = true;
    queueBack();
}

export function fmtTime(ts) {
    const d = new Date(ts), now = new Date();
    const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    if (d.toDateString() === now.toDateString()) return hm;
    return d.toLocaleDateString([], { day: 'numeric', month: 'short' });
}

// --- Tapes and printers ------------------------------------------------------

export const isEndless = s => s.form === 'endless' || s.form === 'ptouch_endless';
export const isPtouch = id => !!SIZES[id] && SIZES[id].models.length > 0 && SIZES[id].models.every(m => m.startsWith('PT-'));
export const isColorTape = id => !!SIZES[id] && SIZES[id].palette === 'color';
export const redTape = d => !!(SIZES[d.label_size] && SIZES[d.label_size].red);
const ANY_PRINTER = { qualities: ['fast', 'quality', 'high'], high_res: true, cutter: true, half_cut: false, chain: false, feed_waste: false, colors: 'black', never_cut: '', leaves_turned: false, lead_mm: 0, vendor: '', manual_cutter: false, calibration: false };
export const capsOf = model => (C.models || {})[model] || ANY_PRINTER;
export const printerCaps = (p = S.printer) => capsOf(p && p.model);
export const modelOnly = model => model === 'Unknown' ? '' : capsOf(model).model_shown || (model || '').split('/').pop();
export const printerName = p => [capsOf(p && p.model).vendor, p && modelOnly(p.model)].filter(Boolean).join(' ');
export const printerLabel = p => (p && (p.name || printerName(p))) || 'Printer';
// The demo printer, the simulator of the server, prints as any model
export const isDemo = p => !!p && /simulat/i.test(p.status_type || p.phase_type || '');
export const thePrinter = (p, start = false) => p && p.name ? p.name : `${start ? 'The' : 'the'} ${(p && printerName(p)) || 'printer'}`;

export function sizeName(id) {
    const s = SIZES[id];
    if (!s) return id;
    if (isPtouch(id)) return s.name;
    if (isColorTape(id)) return `${s.tape[0]} mm color tape`;
    if (s.form === 'endless') return `${s.tape[0]} mm endless${s.red ? ', black and red' : ''}`;
    if (s.form === 'round_die_cut') return `${s.tape[0]} mm round`;
    if (isSheet(id)) return `${s.tape[0]} x ${s.tape[1]} mm, ${s.pieces[0] * s.pieces[1]} labels of ${s.piece[0]} x ${s.piece[1]}`;
    return `${s.tape[0]} x ${s.tape[1]} mm`;
}
// A roll with several labels to a page, of which one is designed
export const isSheet = id => !!SIZES[id] && (SIZES[id].pieces || [1, 1])[0] * (SIZES[id].pieces || [1, 1])[1] > 1;
// The short name in the printer chip, e.g. "62 mm" or "29 x 90 mm"
export function sizeShort(id) {
    const s = SIZES[id];
    if (!s) return id;
    if (isColorTape(id)) return `${s.tape[0]} mm color`;
    if (isEndless(s) || isPtouch(id)) return `${s.tape[0]} mm${s.red ? ' red' : ''}`;
    if (s.form === 'round_die_cut') return `${s.tape[0]} mm round`;
    return `${s.tape[0]} x ${s.tape[1]} mm`;
}

// --- Fonts and lines ---------------------------------------------------------

// The font families as the server lists them from the font files: each with
// its regular font, the weight of its bold and its styles, each with its
// weight (100 to 900), width and whether it is italic
export const FAMILIES = C.font_families;
const FACES = new Map(FAMILIES.flatMap(f => f.styles.map(s => [s.font, Object.assign({ family: f.name }, s)])));
export const faceOf = font => FACES.get(font) || null;
export const familyNamed = name => FAMILIES.find(f => f.name === name) || null;
// A font that is not installed by the family it was saved with
export const familyOf = font => { const face = faceOf(font); return face ? face.family : String(font || C.default_font).split(',')[0]; };
// Narrow to wide, as the server names them
export const WIDTHS = ['ultra-condensed', 'extra-condensed', 'condensed', 'semi-condensed', 'normal', 'semi-expanded', 'expanded', 'extra-expanded', 'ultra-expanded'];
// The style of a family nearest to want ({ weight, width, italic }), order
// says which of them comes closest first. Between two weights the lighter
// one below 500, the heavier one above, as browsers choose.
export function nearestFont(name, want, order = ['italic', 'width', 'weight']) {
    const fam = familyNamed(name);
    if (!fam) return null;
    const off = {
        italic: s => s.italic === !!want.italic ? 0 : 1,
        width: s => Math.abs(WIDTHS.indexOf(s.width) - WIDTHS.indexOf(want.width || 'normal')),
        weight: s => 2 * Math.abs(s.weight - want.weight) + ((want.weight >= 500 ? s.weight < want.weight : s.weight > want.weight) ? 1 : 0),
    };
    const closer = (a, b) => { for (const k of order) { const d = off[k](a) - off[k](b); if (d) return d < 0; } return false; };
    return fam.styles.reduce((best, s) => closer(s, best) ? s : best).font;
}
export const isBold = font => { const face = faceOf(font), fam = face && familyNamed(face.family); return !!(fam && fam.bold && face.weight >= fam.bold); };
// The regular font of a family, or its bold
export function familyFont(name, bold = false) {
    const fam = familyNamed(name);
    if (!fam) return null;
    return bold && fam.bold ? nearestFont(name, Object.assign({}, faceOf(fam.regular), { weight: fam.bold }), ['weight', 'width', 'italic']) : fam.regular;
}
// The font of a new line after font: its family and width, not bold or italic
export function plainFont(font) {
    const face = faceOf(font);
    if (!face) return font;
    return nearestFont(face.family, { weight: isBold(font) ? 400 : face.weight, width: face.width, italic: false }, ['weight', 'width', 'italic']);
}

// New labels start with the font of the label settings, else with the
// server's default font
const installed = f => typeof f === 'string' && familyNamed(f) ? f : '';
export const chosenFamily = () => installed(S.settings.font_family);
export let DEFAULT_FAMILY = installed(C.label_settings.font_family) || familyOf(C.default_font);

export function L(text, size, bold = false, align = 'center', extra = {}) {
    return Object.assign({
        text, size: String(size), font: familyFont(DEFAULT_FAMILY, bold) || C.default_font, align,
        inverted: false, checkbox: false, color: 'black', line_spacing: String(C.defaults.line_spacing)
    }, extra);
}

// With the defaults of the server's configuration, so that a label made here
// prints as one sent without them
export function baseDesign(size) {
    const def = C.defaults;
    return {
        label_size: size, orientation: def.orientation, print_type: 'text', text: [],
        code_text: '', barcode_type: 'QR', qrcode_size: String(def.qrcode_size), qrcode_correction: 'M',
        margin_top: String(def.margin_top), margin_bottom: String(def.margin_bottom), margin_left: String(def.margin_left), margin_right: String(def.margin_right),
        border_thickness: '0', border_roundness: '0', border_distance_x: '0', border_distance_y: '0', border_color: 'black',
        image_mode: def.image_mode, image_bw_threshold: String(def.image_bw_threshold), image_fit: '1', image_crop: '0', image_contrast: '0',
        image_scaling_factor: '100', image_rotation: '0', text_fit: 'shrink', text_wrap: '0', print_color: 'black', high_res: '0', pdf_pages: '',
        image_text_gap: '', image_position: '', parts_order: '', parts_direction: '',
        cut: '', half_cut: '', chain: '',
        cable_flag: '0', cable_diameter: '',
        label_length: '', vertical_align: 'center', mirror: '0'
    };
}

export const withImage = d => d.print_type === 'image' || d.print_type === 'image_qrcode_text';
export const withCode = d => d.print_type === 'qrcode_text' || d.print_type === 'image_qrcode_text' || d.print_type === 'qrcode';
export const labelFiles = d => withImage(d) && d.image ? [d.image, ...(d.more_images || [])] : [];
export const isEmptyDesign = d => d.print_type === 'text' && !d.text.some(l => String(l.text || '').trim());

// How the text is sized: none (as set), shrink (where it does not fit),
// fill (as large as fits). Labels kept from before have shrink_text only.
export const textFit = d => d.text_fit || (d.shrink_text === '1' ? 'shrink' : 'none');
export function setTextFit(d, fit) {
    d.text_fit = fit;
    delete d.shrink_text;
}
// Why lines cannot wrap where the label does not limit their length
export function wrapWhy(d) {
    if (!SIZES[d.label_size] || !isEndless(SIZES[d.label_size]) || hasFixedLength(d)) return '';
    if (runsAlong(d)) return 'Lines along endless tape have room to grow';
    return textFit(d) === 'fill' ? 'Filled, each line is as wide as the tape' : '';
}
export const verticalAlign = d => d.vertical_align || 'center';
export const NO_ROOM = 'Endless labels end where the content ends, there is no room to place it in';
export const hasRoom = d => !(SIZES[d.label_size] && isEndless(SIZES[d.label_size]) && !runsAlong(d) && !hasFixedLength(d));

// Labels as the API stores them (text as JSON, booleans as true/false)
export function normalize(raw) {
    const d = baseDesign(raw.label_size || C.default_label_size);
    for (const k of Object.keys(d)) if (raw[k] !== undefined && raw[k] !== null) d[k] = raw[k];
    let text = raw.text;
    if (typeof text === 'string') { try { text = JSON.parse(text); } catch (e) { text = []; } }
    d.text = (Array.isArray(text) ? text : []).map(l => Object.assign(L('', 40), l, { size: String(l.size || 40) }));
    const given = v => v !== undefined && v !== null && v !== '';
    for (const [k, def] of [['image_fit', '1'], ['image_crop', '0'], ['text_wrap', '0'], ['mirror', '0']]) d[k] = given(raw[k]) ? String(raw[k]) : def;
    d.text_fit = given(raw.text_fit) ? String(raw.text_fit) : '';
    for (const k of CUT_KEYS) d[k] = !given(raw[k]) ? '' : raw[k] === true ? '1' : raw[k] === false ? '0' : String(raw[k]);
    d.cable_flag = !given(raw.cable_flag) ? '0' : raw.cable_flag === true ? '1' : raw.cable_flag === false ? '0' : String(raw.cable_flag);
    d.image_contrast = !given(raw.image_contrast) ? '0' : [true, 'true'].includes(raw.image_contrast) ? '1' : [false, 'false'].includes(raw.image_contrast) ? '0' : String(raw.image_contrast);
    if (!given(raw.text_fit) && given(raw.shrink_text)) d.shrink_text = String(raw.shrink_text);
    if (raw.image_data) d.image = { data: raw.image_data, mime: raw.image_mime || 'image/png', name: raw.image_name || 'image.png' };
    if (raw.fields && typeof raw.fields === 'object') d.fields = raw.fields;
    if (raw.blanks && typeof raw.blanks === 'object' && !Array.isArray(raw.blanks)) {
        d.blanks = Object.fromEntries(Object.entries(raw.blanks).map(([name, v]) => [name, String(v)]));
    }
    return d;
}

// A code alone from before is a code with its text left empty
export function codeWithText(d) {
    if (d.print_type !== 'qrcode') return d;
    d.print_type = 'qrcode_text';
    if (String(d.code_text || '').trim()) d.text = [Object.assign(d.text[0] || L('', C.defaults.font_size, true), { text: '' })];
    return d;
}

// --- Blanks, dates and other templates --------------------------------------

export const BLANK_RE = /\{\{blank:([^}]+)\}\}/g;
export function browserDateFormat() {
    try {
        const parts = new Intl.DateTimeFormat(navigator.language, { year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(2026, 10, 1));
        let f = '';
        for (const part of parts) {
            if (part.type === 'day') f += '%d';
            else if (part.type === 'month') f += '%m';
            else if (part.type === 'year') f += '%Y';
            else if (part.type === 'literal' && /^[\s./-]+$/.test(part.value)) f += part.value.replace(/\s+/g, ' ');
            else return '%Y-%m-%d';
        }
        return ['%d', '%m', '%Y'].every(x => f.includes(x)) ? f : '%Y-%m-%d';
    } catch (e) { return '%Y-%m-%d'; }
}
export const dateFormat = () => S.settings.date_format || browserDateFormat();

// [today], [today+5d], [now+2h], dates typed between brackets
export const TODAY_RE = /^(?:today|now)(?:\s*([+-])\s*(\d+)\s*(d|days?|w|weeks?|m|months?|y|years?|h|hours?|min|minutes?))?$/i;
const offsetOf = m => m[1] ? m[1] + m[2] + (/^min/i.test(m[3]) ? 'min' : m[3][0].toLowerCase()) : '';
const withTime = m => /^now/i.test(m[0]) || (!!m[3] && /^(h|min)/i.test(m[3]));
export const COUNTER_RE = /^counter(?:\s*:\s*([0-9A-Za-z]+)(?:\s*:\s*(-?\d+))?)?$/i;
export const ASN_RE = /^asn(?:\s*:\s*(\d{1,2}))?$/i;
export const isVariable = n => TODAY_RE.test(n) || COUNTER_RE.test(n) || ASN_RE.test(n) || /^id$/i.test(n);

// The template a name typed between brackets stands for, e.g. [Name] for a
// blank, [today+5d] for a date
export function templateOf(name) {
    const n = name.trim();
    const day = n.match(TODAY_RE);
    if (day) {
        const format = withTime(day) ? dateFormat() + ' %H:%M' : dateFormat();
        return `{{datetime${offsetOf(day)}:${format}}}`;
    }
    const counter = n.match(COUNTER_RE);
    if (counter) return `{{counter${counter[1] ? ':' + counter[1] : ''}${counter[2] ? ':' + counter[2] : ''}}}`;
    const asn = n.match(ASN_RE);
    if (asn) return `{{asn${asn[1] ? ':' + asn[1] : ''}}}`;
    if (n.toLowerCase() === 'id') return '{{short-uuid}}';
    return `{{blank:${n}}}`;
}

export function blanksOf(design) {
    const names = [];
    const scan = s => { for (const m of String(s || '').matchAll(BLANK_RE)) if (!names.includes(m[1])) names.push(m[1]); };
    (design.text || []).forEach(l => scan(l.text));
    if (design.print_type !== 'text' && design.print_type !== 'image') scan(design.code_text);
    return names;
}
export const blankFields = item => (item.design && item.design.fields) || {};
export const BLANK_KINDS = [['text', 'Text'], ['number', 'Number'], ['date', 'Date'], ['choice', 'Choice']];
export const guessKind = name => /(^|\s)on$|\bdate\b/i.test(name) ? 'date' : /^(box|number|portions|no\.?|count)$/i.test(name) ? 'number' : 'text';
// A kind of blank a saved label names that is none of BLANK_KINDS is guessed by its name
export const fieldType = (item, name) => {
    const type = (blankFields(item)[name] || {}).type;
    return BLANK_KINDS.some(([kind]) => kind === type) ? type : guessKind(name);
};
export const blankChoices = (item, name) => ((blankFields(item)[name] || {}).choices || []).map(String);
export const choicesOf = text => [...new Set(String(text).split('\n').map(c => c.trim()).filter(Boolean))];
export const choicesMissing = item => blanksOf(item.design).filter(b => fieldType(item, b) === 'choice' && !blankChoices(item, b).length);

export function blankRequest(design, values) {
    const blanks = {}, fields = {};
    for (const name of blanksOf(design)) {
        const f = Object.assign({}, (design.fields || {})[name], { type: fieldType({ design }, name) });
        // The server writes the others as set there
        if (f.type === 'date' && !f.format && !S.settings.date_format) f.format = browserDateFormat();
        fields[name] = f;
        const v = values ? values[name] : undefined;
        if (v !== undefined) blanks[name] = String(v);
    }
    return { blanks, fields };
}

export const DATE_OFFSET_RE = /^[+-]\d+[dwmy]$/;
export function shiftDate(iso, offset) {
    const n = parseInt(offset, 10), unit = offset.slice(-1);
    const [y, m, d] = iso.split('-').map(Number);
    if (unit === 'd' || unit === 'w') return new Date(Date.UTC(y, m - 1, d + n * (unit === 'w' ? 7 : 1))).toISOString().slice(0, 10);
    const months = y * 12 + m - 1 + n * (unit === 'y' ? 12 : 1);
    const year = Math.floor(months / 12), month = months - year * 12;
    const last = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
    return `${String(year).padStart(4, '0')}-${String(month + 1).padStart(2, '0')}-${String(Math.min(d, last)).padStart(2, '0')}`;
}
export function blankStart(item, name) {
    const saved = (item.design && item.design.blanks) || {};
    return name in saved ? saved[name] : (blankFields(item)[name] || {}).default || '';
}
export function startOf(item, name) {
    const start = blankStart(item, name);
    if (fieldType(item, name) === 'choice') return blankChoices(item, name).includes(String(start)) ? String(start) : blankChoices(item, name)[0] || '';
    if (fieldType(item, name) === 'date' && DATE_OFFSET_RE.test(start)) return shiftDate(today(), start);
    return start || name in ((item.design && item.design.blanks) || {}) || fieldType(item, name) !== 'date' ? start : today();
}
export const defaults = item => Object.fromEntries(blanksOf(item.design).map(name => [name, startOf(item, name)]));
export function valuesNow(values, starts, item) {
    const v = Object.assign({}, values);
    for (const [name, start] of Object.entries(starts || {})) if (v[name] === start) v[name] = startOf(item, name);
    return v;
}

// --- Cutting -----------------------------------------------------------------

export const CUTS = [['each', 'After each label'], ['last', 'After the last label'], ['none', 'Never']];
export const CUT_KEYS = ['cut', 'half_cut', 'chain'];
export const sizeCaps = (id, p = S.printer) => {
    const models = SIZES[id] ? SIZES[id].models : [], model = p && p.model;
    return capsOf(models.includes(model) ? model : models.find(m => !noCutter(capsOf(m))) || models[0]);
};
export const neverWhy = k => k.never_cut || '';
export const noCutter = k => k.cutter === false;
export const noCutterWhy = k => k.manual_cutter ? 'This printer has no automatic cutter' : 'This printer has no cutter';
const usableCut = (c, k) => noCutter(k) ? Object.assign(c, { cut: 'none' })
    : neverWhy(k) && c.cut === 'none' ? Object.assign(c, { cut: 'last' }) : c;
export const kept = (p = S.printer) => (p && p.settings) || {};
export const printerCut = (p = S.printer) => { const k = kept(p); return usableCut({ cut: k.cut || 'each', half_cut: k.half_cut || '1', chain: k.chain || '0' }, printerCaps(p)); };
const leavesSet = (p = S.printer) => kept(p).leaves_first || '';
// The end of the labels that leaves the printer first, as set for it, else
// as its driver prints them
export const leavesFirst = (k = printerCaps(), p = S.printer) => leavesSet(p) || (k.leaves_turned ? 'top' : 'bottom');
export const chainOf = (c, k) => k.chain && (c.chain === '1' || c.cut === 'none');
export const halfOf = (c, k) => k.half_cut && c.half_cut === '1';
export const cutOf = (d, p = S.printer) => d && d.cut ? usableCut({ cut: d.cut, half_cut: d.half_cut || '1', chain: d.chain || '0' }, sizeCaps(d.label_size, p)) : printerCut(p);
export function cutShort(c, k) {
    if (noCutter(k)) return k.manual_cutter ? 'Cut by hand' : 'Torn off';
    const half = halfOf(c, k) && c.cut !== 'each';
    return { each: 'After each label', last: 'After the last label', none: 'Not cut' }[c.cut] + (half ? ', half cuts' : '') + (k.chain && c.chain === '1' && c.cut !== 'none' ? ', chain' : '');
}
export const collated = () => S.settings.collate !== '0';
// What happens to the copies, in words
export function cutText(c, k) {
    if (noCutter(k)) return k.manual_cutter ? 'No automatic cutter, cut the labels off with the lever on the printer.' : 'No cutter, the labels are torn off.';
    const half = halfOf(c, k), chain = chainOf(c, k);
    const text = c.cut === 'each' ? 'Cut after each label'
        : (half ? 'Half cut between the labels, ' : '') + (c.cut === 'last' ? (half ? 'cut after the last' : 'Cut after the last label') : (half ? 'not cut' : 'Not cut'));
    return text + '.' + (chain ? ' The last label stays in the printer, the next print cuts it off.' : c.cut === 'none' ? ' The last label stays in the printer.' : '')
        + (k.feed_waste ? ' The tape fed before the first label is waste, short labels get some extra tape.' : '');
}
// The files of copies in the order they are printed: as sets, ABAB, or
// grouped, AABB, two copies of several files, three of one
const printOrder = files => {
    const ids = [...Array(files).keys()], copies = files > 1 ? 2 : 3;
    return collated() ? Array(copies).fill(ids).flat() : ids.flatMap(i => Array(copies).fill(i));
};
// Three copies leaving the printer, drawn: the printer on the left, the
// waste fed before the first label, cuts and half cuts, the last label left
// in the printer
export function cutStrip(c, k, files = 1) {
    const half = halfOf(c, k);
    const between = c.cut === 'each' ? 'full' : half ? 'half' : 'none';
    const end = noCutter(k) ? 'none' : chainOf(c, k) || c.cut === 'none' ? 'chain' : 'full';
    const [third, second, first] = printOrder(files).slice(0, 3).reverse().map(i => `<span class="lab">${esc('ABC'[Math.min(i, 2)])}</span>`);
    const mark = m => `<span class="cm ${esc(m)}"></span>`;
    const text = esc(cutText(c, k));
    const waste = k.feed_waste ? `${mark(half || c.cut === 'each' ? 'half' : 'full')}<span class="waste"></span>` : '';
    return `<span class="cut-strip" title="${text}" role="img" aria-label="${text}"><span class="out"></span>${mark(end)}${third}${mark(between)}${second}${mark(between)}${first}${waste}</span>`;
}

// --- Talking to the server ---------------------------------------------------

function b64ToBlob(b64, mime) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Blob([bytes], { type: mime });
}
export const highRes = (p = S.printer) => kept(p).quality === 'high' && (!p || printerCaps(p).high_res);
export const dpiOf = (labelSize, high = highRes()) => SIZES[labelSize] ? SIZES[labelSize].dpi[high ? 1 : 0] : 300 * (high ? 2 : 1);
export const dotsToMm = (dots, dpi) => Math.round((+dots || 0) / dpi * 25.4 * 10) / 10;
export const mmToDots = (mm, dpi) => Math.round(mm * dpi / 25.4);

// The request of a label, field by field as /api/preview and /api/print take it
export function formFor(design, copies = 1, forPrint = false, values = null, marks = false, blanks = blankRequest(design, values), printer = S.printer) {
    const fd = new FormData();
    for (const [k, v] of Object.entries(design)) {
        if (k === 'text' || k === 'image' || k === 'more_images' || k === 'fields' || k === 'blanks') continue;
        if (CUT_KEYS.includes(k)) continue;
        fd.append(k, typeof v === 'boolean' ? (v ? '1' : '0') : String(v));
    }
    // Colors go as they are set, the server prints them as the tape can and
    // tells which it prints otherwise
    const red = redTape(design) || isColorTape(design.label_size);
    if (!red && ['red_and_black', 'colored'].includes(design.image_mode)) fd.set('image_mode', 'black_and_white');
    fd.append('text', JSON.stringify(design.text.map(l => ({
        text: l.text, size: String(l.size), font: l.font, align: l.align, inverted: !!l.inverted,
        checkbox: !!l.checkbox, color: l.color || 'black', line_spacing: String(l.line_spacing || '100'),
        ...(l.underline ? { underline: true } : {})
    }))));
    if (design.image && withImage(design)) {
        for (const im of [design.image].concat(forPrint ? design.more_images || [] : [])) fd.append('image', b64ToBlob(im.data, im.mime), im.name);
    }
    if (Object.keys(blanks.fields).length) {
        fd.append('blanks', JSON.stringify(blanks.blanks));
        fd.append('fields', JSON.stringify(blanks.fields));
        if (marks) fd.append('mark_blanks', '1');
    }
    fd.append('print_count', String(copies));
    fd.set('high_res', highRes(printer) ? '1' : '0');
    if (forPrint) {
        fd.set('fast_printing', kept(printer).quality === 'fast' ? '1' : '0');
        const c = cutOf(design, printer);
        fd.set('cut', c.cut);
        fd.set('half_cut', c.half_cut);
        fd.set('chain', c.chain);
        if (leavesSet(printer)) fd.set('leaves_first', leavesSet(printer));
    }
    if (printer && printer.path) fd.append('printer', printer.path);
    if (printer && printer.model) fd.append('model', printer.model);
    return fd;
}

// A request that does not reach the server says so in words; one given up,
// e.g. after a timeout, keeps its name
export const UNREACHABLE = 'The label designer could not be reached';
export const reached = request => request.catch(e => { throw ['AbortError', 'TimeoutError'].includes(e.name) ? e : new Error(UNREACHABLE); });
export async function post(path, body, signal = undefined) {
    if (body instanceof FormData && ![...body.values()].some(v => v instanceof Blob)) body = new URLSearchParams(body);
    return reached(fetch(API + path, { method: 'POST', body, signal, headers: { 'X-Requested-With': 'XMLHttpRequest' } }));
}
export async function postJSON(path, body) {
    return reached(fetch(API + path, { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' } }));
}

// A short fingerprint of an image, it names it in the undo history and the
// draft (FNV-1a, two hashes)
function fingerprint(s) {
    let h1 = 0x811c9dc5, h2 = 0x9e3779b9;
    for (let i = 0; i < s.length; i++) {
        const c = s.charCodeAt(i);
        h1 = Math.imul(h1 ^ c, 0x01000193);
        h2 = Math.imul(h2 ^ c, 0x5bd1e995);
        h2 ^= h2 >>> 15;
    }
    return (h1 >>> 0).toString(36) + (h2 >>> 0).toString(36) + s.length.toString(36);
}
const imageKeys = new WeakMap();
export function imageKey(im) {
    let k = imageKeys.get(im);
    if (!k) { k = im.mime + fingerprint(im.data); imageKeys.set(im, k); }
    return k;
}

// Where each part of the label is in the preview: [{part, index, box}], in
// pixels of the PNG. Empty where the server does not tell.
export function partsOf(header) {
    if (!header) return [];
    try {
        const parts = JSON.parse(header);
        return Array.isArray(parts) ? parts.filter(p => p && Array.isArray(p.box) && p.box.length === 4) : [];
    } catch (e) { return []; }
}

// Previews of the editor: the newest one wins, those still on their way
// are dropped by the caller. A cache keeps those of recent states, e.g. for
// undo and redo.
const previewCache = new Map();
const sheetOf = header => {
    try { return header ? JSON.parse(header) : null; } catch (e) { return null; }
};
// The last page of a roll with several labels to a page where the copies
// leave it not full: drawn as the label is seen, the labels filled marked
export function sheetNote(sheet, max = 100) {
    if (!sheet || sheet.filled >= sheet.labels.length) return '';
    const k = 44 / Math.max(...sheet.size), px = mm => `${Math.round(mm * k * 10) / 10}px`;
    const labels = sheet.labels.map(([x, y, w, h], i) => `<i${i < sheet.filled ? ' class="on"' : ''} style="left:${px(x)};top:${px(y)};width:${px(w)};height:${px(h)}"></i>`).join('');
    return `<b class="sheet" role="img" aria-label="The last page" style="width:${px(sheet.size[0])};height:${px(sheet.size[1])}">${labels}</b>`
        + '<span>The last page is not full.</span>'
        + (sheet.fill && sheet.fill <= max ? `<button type="button" class="pill" data-fill="${esc(sheet.fill)}">Fill it</button>` : '');
}

export async function preview(design, values, copies = 1, perCopy = 0) {
    const asked = Object.assign({}, onTape(design), { high_res: highRes() ? '1' : '0' });
    // On a roll with several labels to a page the server tells how the copies fill the last one
    const sheet = isSheet(asked.label_size);
    const blanks = blankRequest(asked, values || {});
    for (const [name, f] of Object.entries(blanks.fields)) {
        if (f.type === 'choice' && !(f.choices || []).length) { blanks.fields[name] = { type: 'text' }; delete blanks.blanks[name]; }
    }
    const shown = JSON.stringify(Object.assign({}, asked, { image: design.image ? imageKey(design.image) : null, more_images: undefined },
        Object.fromEntries(CUT_KEYS.map(k => [k, undefined]))));
    const dated = shown.includes('{{datetime') || Object.entries(blanks.fields).some(([name, f]) => f.type === 'date' && !(name in blanks.blanks));
    // Several printers can share a path, e.g. simulators of other models
    const key = JSON.stringify([shown, blanks, S.printer && S.printer.path, S.printer && S.printer.model, dated ? Math.floor(Date.now() / 60000) : 0, S.settings.date_format,
        sheet ? [copies, perCopy] : 0]);
    if (previewCache.has(key)) {
        const p = previewCache.get(key);
        previewCache.delete(key);
        previewCache.set(key, p);
        return p;
    }
    const p = (async () => {
        let r;
        try {
            const fd = formFor(asked, sheet ? copies : 1, false, values, true, blanks);
            // Each copy prints all files and PDF pages, the preview shows one
            if (sheet && perCopy > 0) fd.append('labels_per_copy', String(perCopy));
            r = await post('/preview', fd, AbortSignal.timeout ? AbortSignal.timeout(60000) : undefined);
        } catch (e) {
            throw new Error(['TimeoutError', 'AbortError'].includes(e.name) ? 'The preview took too long' : 'The preview could not be made');
        }
        if (!r.ok) {
            let msg = 'The preview could not be made';
            try { msg = (await r.json()).message || msg; } catch (e) { }
            throw Object.assign(new Error(msg), { refused: true });
        }
        const blob = await r.blob();
        return {
            url: URL.createObjectURL(blob), highRes: asked.high_res === '1', warning: r.headers.get('X-Label-Warning') || '',
            auto: r.headers.get('X-Label-Orientation') === 'auto', gap: +(r.headers.get('X-Label-Image-Text-Gap') || 0),
            order: r.headers.get('X-Label-Parts-Order') || '', direction: r.headers.get('X-Label-Parts-Direction') || '', faint: r.headers.get('X-Image-Faint') === '1',
            pages: +(r.headers.get('X-PDF-Selected-Pages') || 1), pageCount: +(r.headers.get('X-PDF-Pages') || 0), parts: partsOf(r.headers.get('X-Label-Parts')),
            printedAs: colorsOf(r.headers.get('X-Label-Colors')), labelSize: asked.label_size, sheet: sheetOf(r.headers.get('X-Label-Sheet'))
        };
    })();
    previewCache.set(key, p);
    p.catch(() => { if (previewCache.get(key) === p) previewCache.delete(key); });
    if (previewCache.size > 60) {
        const [oldKey, old] = previewCache.entries().next().value;
        previewCache.delete(oldKey);
        old.then(r => setTimeout(() => URL.revokeObjectURL(r.url), 30000), () => { });
    }
    return p;
}

// The colors the tape prints otherwise, as the server tells them
function colorsOf(header) {
    try {
        const told = JSON.parse(header || '{}');
        return told && typeof told === 'object' && !Array.isArray(told) ? told : {};
    } catch (e) { return {}; }
}

// --- State -------------------------------------------------------------------

export const S = {
    printers: [], printerHints: [], printer: null, tape: C.default_label_size, tapeKnown: false, tapeAssumed: true,
    tapeFits: [], tapeMoves: [], tapePlace: {}, tapeCandidates: [], tapeManual: false, tapeUnknown: '',
    demos: [], demoList: [], saved: [], codes: C.codes,
    history: load(own('designer_history'), []),
    lang: load('designer_lang', (navigator.language || 'en').toLowerCase().startsWith('de') ? 'de' : 'en'),
    // Of labels, kept on the server for every browser: date_format, collate
    // and font_family, see /api/settings/labels
    settings: Object.assign({}, C.label_settings),
    edit: null,
    // The colors of the label in the editor its tape prints otherwise, as its
    // preview told them
    printedAs: {},
};

// What gives way first where the browser's storage is full: the images of
// the steps to undo, a label printed keeps its image longer
let makeRoom = () => false;
export const roomFrom = fn => { makeRoom = fn; };
export function saveHistory() {
    S.history = S.history.slice(0, 60);
    if (storeQuiet(own('designer_history'), S.history)) return;
    makeRoom();
    for (const keep of [9, 3, 1, 0]) {
        S.history.forEach((h, i) => {
            if (i >= keep && (h.design.image || h.design.more_images)) { delete h.design.image; delete h.design.more_images; h.noImage = true; }
        });
        if (storeQuiet(own('designer_history'), S.history)) return;
    }
}

// --- Label settings ----------------------------------------------------------

// As the server answers them
export function takeLabelSettings(kept) {
    Object.assign(S.settings, kept);
    DEFAULT_FAMILY = chosenFamily() || familyOf(C.default_font);
    // The new label in the editor, still empty, is one of them
    const E = S.edit;
    if (E && !E.origin && isEmptyDesign(E.design)) E.design.text.forEach(l => { l.font = familyFont(DEFAULT_FAMILY, isBold(l.font)) || l.font; });
}
// Read again, e.g. when the page is shown again: another browser may have
// changed them. Whether they changed.
export async function loadLabelSettings() {
    const before = JSON.stringify(S.settings);
    try {
        const r = await fetch(API + '/settings/labels');
        if (r.ok) takeLabelSettings(await r.json());
    } catch (e) { }
    return JSON.stringify(S.settings) !== before;
}
// Changed for every browser: shown at once, taken back where the server does
// not take them, the error tells why
export async function saveLabelSettings(changes) {
    const before = Object.assign({}, S.settings);
    takeLabelSettings(changes);
    let r;
    try {
        r = await post('/settings/labels', new URLSearchParams(changes));
    } catch (e) {
        takeLabelSettings(before);
        throw e;
    }
    const answer = await r.json().catch(() => ({}));
    if (!r.ok || !answer.settings) {
        takeLabelSettings(before);
        throw new Error(answer.message || 'The settings could not be saved');
    }
    takeLabelSettings(answer.settings);
}
// Kept in each browser before the server kept them: carried over once where
// the server has none, then gone from the browser. Left for the next visit
// where the server cannot be reached.
export async function carryOverLabelSettings() {
    const stored = load(own('designer_settings'), null), font = load(own('designer_font'), null);
    const local = stored && typeof stored === 'object' ? stored : {};
    if (!('dateFmt' in local) && !('collate' in local) && font === null) return;
    const valid = { date_format: f => C.date_formats.includes(f), collate: c => c === '0' || c === '1', font_family: installed };
    const changes = Object.fromEntries(Object.entries({ date_format: local.dateFmt, collate: local.collate, font_family: font })
        .filter(([k, v]) => !S.settings[k] && valid[k](v)));
    if (Object.keys(changes).length) {
        let r;
        try { r = await post('/settings/labels', new URLSearchParams(changes)); } catch (e) { return; }
        const answer = await r.json().catch(() => ({}));
        if (r.ok && answer.settings) takeLabelSettings(answer.settings);
    }
    const rest = Object.assign({}, local);
    delete rest.dateFmt;
    delete rest.collate;
    try {
        if (Object.keys(rest).length) localStorage.setItem(own('designer_settings'), JSON.stringify(rest));
        else localStorage.removeItem(own('designer_settings'));
        localStorage.removeItem(own('designer_font'));
    } catch (e) { }
}

// --- Orientation, tape and placing -------------------------------------------

export const isDieCut = id => !!SIZES[id] && !isEndless(SIZES[id]);
export function setWide(d, wide) {
    d.orientation = wide === isDieCut(d.label_size) ? 'standard' : 'rotated';
    return d;
}
// Of a page of several labels, the label designed
const longAlongTape = id => !!SIZES[id] && (SIZES[id].piece || SIZES[id].tape)[1] > (SIZES[id].piece || SIZES[id].tape)[0];
export const runsAlong = d => (d.orientation !== 'rotated') === longAlongTape(d.label_size);
export function setRunsAlong(d, along) {
    d.orientation = along === longAlongTape(d.label_size) ? 'standard' : 'rotated';
    return d;
}
export function flagOn(d) {
    const v = String(d.cable_flag).trim().toLowerCase();
    return v === 'true' || (/^[+-]?\d+$/.test(v) && parseInt(v, 10) > 0);
}
export const hasLength = d => !!SIZES[d.label_size] && !!SIZES[d.label_size].length && !flagOn(d);
export const hasFixedLength = d => hasLength(d) && +d.label_length > 0;

export const tapeKnown = () => S.tapeKnown;
export const fitsTape = sizeId => sizeId === S.tape || S.tapeFits.includes(sizeId);
const movesTo = sizeId => S.tapeMoves.includes(sizeId);
export const placed = sizeId => tapeKnown() && !fitsTape(sizeId);
const putOnTape = d => Object.assign(d, S.tapePlace[`${d.label_size}|${d.orientation === 'rotated' ? 'rotated' : 'standard'}`]);
export const onTape = d => movesTo(d.label_size) ? putOnTape(Object.assign({}, d)) : d;
// Placed on the tape of a printer other than the one chosen
function onTapeOf(d, p) {
    const t = (p && p.tape) || {};
    return (t.moves || []).includes(d.label_size) ? Object.assign({}, d, (t.place || {})[`${d.label_size}|${d.orientation === 'rotated' ? 'rotated' : 'standard'}`]) : d;
}

// The label in the editor is placed on the tape in the printer. E.made keeps
// what placing it changed: unchanged since, it is saved for the tape it was
// made for. A new label, neither saved nor a template, has no tape of its
// own: it goes on the tape in the printer as an empty one.
const PLACED_KEYS = ['label_size', 'orientation', 'cable_flag'];
export function designSig(d) {
    const o = Object.assign({}, d);
    if (o.image) o.image = imageKey(o.image);
    if (o.more_images) o.more_images = o.more_images.map(imageKey);
    return JSON.stringify(o);
}
const unchangedSincePlaced = E => !!E.made && designSig(E.design) === E.made.sig;
export function placeEdit(E) {
    const d = E.design;
    if (!unchangedSincePlaced(E)) E.made = null;
    if (isEmptyDesign(d) || !E.origin) {
        E.made = null;
        if (movesTo(d.label_size)) putOnTape(d);
        return;
    }
    if (tapeKnown() ? !movesTo(d.label_size) : !E.made) return;
    if (unchangedSincePlaced(E)) Object.assign(d, E.made.as);
    E.made = null;
    if (!movesTo(d.label_size)) return;
    E.made = { as: Object.fromEntries(PLACED_KEYS.map(k => [k, d[k]])) };
    putOnTape(d);
    E.made.sig = designSig(d);
}
export const madeDesign = E => unchangedSincePlaced(E) ? Object.assign({}, E.design, E.made.as) : E.design;
// Where a label opened from Templates or the saved ones came from; a saved
// one with the time it was saved, to tell when it is saved again elsewhere
export const originOf = (kind, id, mtime) => kind === 'saved' ? { kind, id, mtime } : kind === 'demo' ? { kind, id } : null;
export const flagLost = E => !!E.made && flagOn(E.made.as) && !flagOn(E.design);

// --- Printer status ----------------------------------------------------------

export function detectTape() {
    const t = (S.printer && S.printer.tape) || { id: C.default_label_size, assumed: true };
    S.tape = t.id;
    S.tapeKnown = !!t.known;
    S.tapeAssumed = !!t.assumed;
    S.tapeManual = !!t.manual;
    S.tapeUnknown = t.unknown || '';
    S.tapeCandidates = t.candidates || [];
    S.tapeFits = t.fits || [];
    S.tapeMoves = t.moves || [];
    S.tapePlace = t.place || {};
}
export const tapeState = () => JSON.stringify([S.tape, S.tapeAssumed, S.tapeUnknown, S.printer && S.printer.path, S.printer && S.printer.model]);

// The printers of a status, a status of one printer alone lists none
const printersIn = data => Array.isArray(data.printers) ? data.printers : [data];
// A printer that does not answer for a moment keeps the tape it told
function keepTape(p, was) {
    if (was && p.errors && p.errors.length && !(p.tape && p.tape.known) && was.tape && was.tape.known) p.tape = was.tape;
}
// Changes of the printer settings on their way to the server and those it
// answered, see settings.js: a status asked for before keeps the settings as
// shown, and the tape where one was set by hand since
export const settingsSaves = { pending: 0, answered: 0, tapes: 0 };
function keepShown(printers, asked) {
    if (!settingsSaves.pending && asked.answered === settingsSaves.answered) return;
    for (const p of printers) {
        const shown = [S.printer, ...S.printers].find(o => o && o.path === p.path);
        if (shown) p.settings = shown.settings;
        if (shown && asked.tapes !== settingsSaves.tapes) p.tape = shown.tape;
    }
}

// look: also look for printers that did not answer before
export async function refreshPrinters(look = false) {
    try {
        const asked = { ...settingsSaves };
        const r = await fetch(API + '/printer_status' + (look ? '?look=1' : ''));
        const data = await r.json();
        const printers = printersIn(data);
        for (const p of printers) keepTape(p, S.printers.find(o => o.path === p.path && o.name === p.name));
        keepShown(printers, asked);
        // While the server looks for printers on Bluetooth, see main.js
        S.searching = !!data.searching;
        // The printer shown stays, also where an answer leaves it out, e.g. one
        // on Wi-Fi the search did not find this time: only a choice switches
        // printers
        const shown = S.printer, last = load(own('designer_next_printer'), null);
        const want = load(own('designer_printer'), null) || (shown && shown.path) || (last && last.path) || null;
        // Several simulators share a path, the one shown keeps its name
        const name = [shown, last].find(o => o && o.path === want);
        let kept = want && (printers.find(p => p.path === want && (!name || p.name === name.name)) || printers.find(p => p.path === want));
        if (!kept && name) {
            kept = Object.assign({}, name, { status_type: 'Error', errors: ['The printer does not answer'], job: undefined, missing: true });
            printers.push(kept);
        }
        S.printers = printers;
        S.printerHints = Array.isArray(data.hints) ? data.hints : [];
        S.printer = kept || printers.find(p => p.path === data.selected) || printers[0] || null;
        if (S.printer && !S.printer.missing) {
            const { path, name: shownName, model, tape, settings } = S.printer;
            storeQuiet(own('designer_next_printer'), { path, name: shownName, model, tape, settings });
        }
    } catch (e) {
        if (!S.printers.length) S.printer = null;
    }
    detectTape();
}

// The printer chosen asked alone, where the server says that costs nothing,
// e.g. one on USB: whether its status changed
let following = false;
export async function followPrinter() {
    const p = S.printer;
    if (document.hidden || following || !(p && p.follow)) return false;
    following = true;
    try {
        const asked = { ...settingsSaves };
        const r = await fetch(API + '/printer_status?printer=' + encodeURIComponent(p.path));
        const fresh = r.ok && ((await r.json()).printers || [])[0];
        // Chosen anew or asked for all meanwhile, that answer stays
        if (!fresh || S.printer !== p) return false;
        keepTape(fresh, p);
        keepShown([fresh], asked);
        // The name it was chosen by, PRINTERS may name a device twice
        if (p.name) fresh.name = p.name;
        if (JSON.stringify(fresh) === JSON.stringify(p)) return false;
        S.printers = S.printers.map(o => o === p ? fresh : o);
        S.printer = fresh;
        detectTape();
        return true;
    } catch (e) {
        // The next one corrects it
        return false;
    } finally {
        following = false;
    }
}

export function jobText(job) {
    const parts = [];
    if (job.stage) parts.push(job.stage + (job.percent !== undefined ? ` ${job.percent} %` : ''));
    if (job.label !== undefined && job.labels > 1) parts.push(`${job.unit === 'page' ? 'page' : 'label'} ${job.label} of ${job.labels}`);
    if (job.seconds_left !== undefined) parts.push(job.seconds_left < 10 ? 'a few seconds left' : job.seconds_left < 60 ? `about ${Math.round(job.seconds_left / 5) * 5} s left` : `about ${Math.round(job.seconds_left / 60)} min left`);
    const text = parts.join(', ');
    return text.charAt(0).toUpperCase() + text.slice(1);
}
export function tapeLeft(p) {
    if (!p || !p.tape_remaining_mm || !p.tape_length_mm) return '';
    const m = mm => (mm / 1000).toFixed(1);
    return `${m(p.tape_remaining_mm)} m of ${m(p.tape_length_mm)} m left`;
}
export function printerState(p) {
    if (!p) return { cls: 'off', text: 'No printer' };
    if (p.errors && p.errors.length) return { cls: 'bad', text: p.errors[0] };
    if (p.status_type === 'Asking the printer') return { cls: 'off', text: 'Asking the printer...' };
    if (p.job) return { cls: '', text: jobText(p.job) };
    if (p.status_type === 'Printing') return { cls: '', text: 'Printing' };
    if (isDemo(p)) return { cls: '', text: 'Ready, prints nothing' };
    return { cls: '', text: 'Ready' };
}

// --- Saved labels and templates ----------------------------------------------

export async function loadDemos() {
    try {
        const data = await (await fetch(API + '/demos/list')).json();
        S.demos = data.categories || [];
        S.demoList = [];
        for (const c of S.demos) for (const l of c.labels) S.demoList.push(Object.assign({ cat: c.id, catName: c.name }, l));
    } catch (e) { S.demos = []; }
}
// Read again where they are shown, another browser may have changed them;
// whether they changed. Not reached, they stay as they were.
export async function loadSaved() {
    const before = JSON.stringify(S.saved);
    try {
        const data = await (await fetch(API + '/repository/list')).json();
        S.saved = (data.files || []).map(f => ({ name: f.name, title: f.title || f.name.replace(/\.json$/i, '').replace(/[-_]/g, ' '), size: f.label_size_id || C.default_label_size, mtime: f.mtime, tags: f.tags || [] }));
    } catch (e) { }
    return JSON.stringify(S.saved) !== before;
}
export const demoItem = d => ({ kind: 'demo', id: d.id, title: d.title, size: d.label_size_id, sub: d.catName });
export const savedItem = s => ({ kind: 'saved', id: s.name, title: s.title, size: s.size, sub: s.tags && s.tags.length ? s.tags.join(', ') : 'Saved label', tags: s.tags || [], mtime: s.mtime });
// Saved labels as loaded, until they are saved again, e.g. for their
// thumbnails; fresh, as the server has it now, e.g. for the editor
const loadedDesigns = new Map();
export async function itemDesign(item, fresh = false) {
    if (item.design && !fresh) return item.design;
    const key = item.kind === 'saved' ? `${item.id}|${item.mtime}` : '';
    if (key && !fresh && loadedDesigns.has(key)) return (item.design = clone(loadedDesigns.get(key)));
    const url = item.kind === 'demo' ? `${API}/demos/load?id=${encodeURIComponent(item.id)}` : `${API}/repository/load?name=${encodeURIComponent(item.id)}`;
    const r = await reached(fetch(url));
    if (!r.ok) throw new Error('The label could not be loaded');
    item.design = normalize(await r.json());
    if (key) loadedDesigns.set(key, clone(item.design));
    return item.design;
}
// A saved label as the server has it now, with the time it was saved
export async function savedNow(id) {
    await loadSaved();
    const s = S.saved.find(x => x.name === id);
    if (!s) throw new Error('This label is not saved on this server');
    const item = savedItem(s);
    return { item, design: await itemDesign(item, true) };
}
// Templates come drawn by the server. Saved labels are drawn as the editor
// draws them, placed on the tape the page knows, a few at a time: the
// server's own preview of them would ask the printer for its tape.
export const thumbUrl = item => `${API}/demos/preview?id=${encodeURIComponent(item.id)}`;
const thumbsWaiting = [];
let thumbsRunning = 0;
// make: the label as drawn, e.g. with the text typed into the gallery
export function drawThumb(img, item, make = (design, values) => ({ design, values })) {
    const run = async () => {
        thumbsRunning++;
        try {
            if (img.isConnected) {
                const design = await itemDesign(item), shown = make(design, defaults({ design }));
                const p = await preview(shown.design, shown.values);
                if (img.isConnected) img.src = p.url;
            }
        } catch (e) { img.classList.add('failed'); }
        thumbsRunning--;
        if (thumbsWaiting.length) thumbsWaiting.shift()();
    };
    if (thumbsRunning < 2) run(); else thumbsWaiting.push(run);
}

// The file name the server gives a label (like werkzeug's secure_filename)
function serverName(title) {
    const base = String(title).normalize('NFKD').replace(/[^\x00-\x7F]/g, '').replace(/[^\w\s.-]/g, '').trim().replace(/\s+/g, '_').replace(/^[._]+|[._]+$/g, '');
    return (base || 'label') + '.json';
}
export function allTags() {
    const seen = new Map();
    for (const s of S.saved) for (const t of s.tags) if (!seen.has(t.toLowerCase())) seen.set(t.toLowerCase(), t);
    return [...seen.values()].sort((a, b) => a.localeCompare(b));
}
export const tagsOf = text => text.split(',').map(t => t.trim()).filter(Boolean);
export const hasTag = (s, tag) => s.tags.some(t => t.toLowerCase() === tag.toLowerCase());
// Saved with tags those to save it with, else with the ones of the label it
// replaces, so that replacing a label keeps it in its groups
export async function saveLabel(title, design, overwrite = null, tags = null) {
    let name = overwrite;
    if (!name) {
        name = serverName(title);
        for (let n = 2; S.saved.some(x => x.name.toLowerCase() === name.toLowerCase()); n++) name = serverName(`${title} ${n}`);
    }
    const body = Object.assign({}, design, { name, title, text: design.text, replace: !!overwrite });
    if (tags) body.tags = tags;
    body.fields = blankRequest(design, null).fields;
    if (!Object.keys(body.fields).length) delete body.fields;
    delete body.image;
    delete body.more_images;
    if (design.image) Object.assign(body, { image_data: design.image.data, image_mime: design.image.mime, image_name: design.image.name });
    const r = await postJSON('/repository/save', body);
    const data = await r.json().catch(() => ({}));
    if (r.status === 409) {
        await loadSaved();
        throw Object.assign(new Error(data.message), { taken: true });
    }
    if (!r.ok || !data.success) throw new Error(data.message || 'The label could not be saved');
    await loadSaved();
    return data.name;
}
export const savedAs = d => `as ${sizeName(d.label_size)} label`;

// --- Printing ----------------------------------------------------------------

export function entryName(e) {
    const blanks = blanksOf(e.design);
    const first = blanks.length ? (e.values || {})[blanks[0]] : '';
    if (first) return first;
    if (!blanks.length && e.source && e.source.kind !== 'wizard' && e.title) return e.title;
    const line = (e.design.text || []).map(l => String(l.text).replace(/\{\{[^}]*\}\}/g, '').trim()).find(Boolean);
    return line || e.title || 'Label';
}

// The queue of the printer chosen as queue.js last read it, shared with the
// prints here; jobs removed here are told by its list, not by a message
export const QUEUE = { path: '', line: null, status: null, removed: new Set(), read: () => Promise.resolve(), follow: () => { } };
const pendingOn = path => S.history.some(h => h.state === 'printing' && h.path === path);
// Whether a print on printer p waits behind others: its line as last read
// has a job or a hold, its status says so where it was not read since, or a
// print of this browser on it has no outcome yet
export function lineBusy(p = S.printer) {
    if (!p || !p.path) return false;
    const read = QUEUE.path === p.path && QUEUE.line, told = QUEUE.status !== p && p.queue;
    return !!(read && (read.printing || read.waiting.length || read.held)) || !!(told && (told.printing || told.waiting || told.held)) || pendingOn(p.path);
}
// Whether the outcome of a print of this browser goes without a message of
// its own: printed while others of this browser have none yet, taken out of
// the queue here, or told by the held queue of the printer chosen
export function untold(entry) {
    if (entry.state === 'printed') return S.history.some(h => h !== entry && h.state === 'printing');
    if (entry.job && QUEUE.removed.has(entry.job)) return true;
    return !!S.printer && entry.path === S.printer.path && QUEUE.path === entry.path && !!(QUEUE.line && QUEUE.line.held);
}

// job: { title, design, values, copies, source }. Kept in the history of
// this browser, Settings list it under Printed labels. Printed on printer,
// the one chosen unless another is given.
export async function printJob(job, onDone, printer = S.printer) {
    job = Object.assign({}, job, { design: clone(job.design) });
    const behind = lineBusy(printer), path = (printer && printer.path) || '';
    const entry = {
        id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), time: Date.now(), title: job.title,
        design: clone(job.design), values: Object.assign({}, job.values || {}), copies: job.copies || 1,
        source: job.source || null, printer: printer ? printer.model : '', printerName: (printer && printer.name) || '', path, state: 'printing', message: ''
    };
    S.history.unshift(entry);
    saveHistory();
    const printing = toast(`Printing ${entry.copies} ${entry.copies === 1 ? 'label' : 'labels'}...`, null, 120000);
    const shown = () => !!S.printer && S.printer.path === path;
    QUEUE.follow();
    try {
        const placedOn = printer === S.printer ? onTape(job.design) : onTapeOf(job.design, printer);
        const form = formFor(placedOn, entry.copies, true, job.values, false, undefined, printer);
        form.set('title', entryName(entry));
        form.set('source', 'designer');
        const r = await post('/print', form);
        let data = {};
        try { data = await r.json(); } catch (e) { }
        if (!r.ok || data.success === false) {
            entry.state = 'failed';
            entry.message = data.message || ('The printer answered ' + r.status);
        } else if (data.job && data.state !== 'printed') {
            // Queued: followed until its outcome is known. The server answers
            // waiting also for one about to print, so behind says which waits.
            Object.assign(entry, { job: data.job, start: data.start, seen: data.state });
            saveHistory();
            if (behind && data.state === 'waiting') toast(`Added ${entryName(entry)} to the queue`);
            else if (shown()) QUEUE.read().then(() => hideToast(printing));
            await followJob(entry);
        } else entry.state = 'printed';
    } catch (e) {
        entry.state = 'failed';
        entry.message = UNREACHABLE;
    }
    hideToast(printing);
    saveHistory();
    // Read before it is told: a job that did not print may hold the queue shown
    if (!shown()) QUEUE.follow();
    else if (entry.state === 'printed') QUEUE.read();
    else await QUEUE.read();
    if (onDone) onDone(entry);
    return entry;
}

// Asks for the job of entry ({ job, start, seen }) every 1.5 s, one request
// at a time and not from a tab in the background, until it is printed,
// failed or lost. Sets entry.state and entry.message.
export function followJob(entry) {
    return new Promise(resolve => {
        let asking = false;
        const end = (state, message = '') => {
            clearInterval(timer);
            document.removeEventListener('visibilitychange', ask);
            Object.assign(entry, { state, message });
            resolve();
        };
        const ask = async () => {
            if (asking || document.hidden) return;
            asking = true;
            try {
                const r = await fetch(API + '/queue/' + encodeURIComponent(entry.job));
                const data = await r.json().catch(() => ({}));
                if (r.status === 404) {
                    if (!data.start || data.start === entry.start) end('unknown', 'Its outcome is no longer known');
                    else if (entry.seen === 'printing') end('unknown', 'The label designer was restarted while it printed');
                    else end('failed', 'The label designer was restarted before it printed');
                } else if (data.state === 'printed') end('printed');
                else if (data.state === 'failed' || data.state === 'removed') end('failed', data.message || 'It was not printed');
                else if (data.state && data.state !== entry.seen) { entry.seen = data.state; saveHistory(); }
                else if (!data.state && entry.seen === 'failed') end('failed', 'It was not printed');
            } catch (e) {
                // The next one asks again, unless the answer told the outcome already
                if (entry.seen === 'failed') end('failed', 'It was not printed');
            } finally {
                asking = false;
            }
        };
        const timer = setInterval(ask, 1500);
        document.addEventListener('visibilitychange', ask);
        // A job the answer already calls failed is read at once for its message
        if (entry.seen === 'failed') ask();
    });
}

// Jobs left printing when the page was closed are followed on
export function resolvePending() {
    S.history.filter(h => h.state === 'printing' && h.job).forEach(h => followJob(h).then(saveHistory));
}

// A label printed before, printed again as it was on the printer chosen, the
// toast tells how it went; one whose image was dropped from the history
// cannot be
export const NO_IMAGE = 'Its image was not kept, open it in the editor to add it again';
export function printAgain(h, onDone = announcePrint) {
    if (h.noImage) { toast(NO_IMAGE); return null; }
    return printJob({ title: h.title, design: h.design, values: h.values, copies: h.copies, source: h.source }, onDone);
}
export function announcePrint(entry, again = () => printAgain(entry)) {
    if (untold(entry)) return;
    hideToast();
    if (entry.state === 'printed') toast(`Printed ${entry.copies === 1 ? '1 label' : `${entry.copies} labels`}: ${entryName(entry)}`, { label: 'Print again', run: again });
    else if (entry.state === 'unknown') toast(`Maybe not printed: ${entry.message}`, { label: 'Print again', run: again }, 10000);
    else toast(`Not printed: ${entry.message}`, { label: 'Try again', run: again }, 10000);
}
// What a label printed before is listed with: its copies, the values
// printed, when
export const printedSub = h => h.state === 'failed' ? `Not printed: ${h.message}` : h.state === 'unknown' ? `Maybe not printed: ${h.message}` : [`${h.copies} x`, ...Object.values(h.values || {}).filter(v => String(v).trim() && String(v) !== entryName(h)), fmtTime(h.time)].join(', ');

// --- Overlays ----------------------------------------------------------------

let onLayerClose = null;
export function openLayer(html, cls = '', onClose = null) {
    closeLayer(true);
    const layer = $('#layer');
    layer.className = 'layer ' + cls;
    layer.innerHTML = html;
    layer.hidden = false;
    onLayerClose = onClose;
    layer.onclick = e => { if (e.target === layer || e.target.closest('[data-close]')) closeLayer(); };
}
export function closeLayer(silent) {
    const layer = $('#layer');
    if (layer.hidden) return;
    layer.hidden = true;
    layer.innerHTML = '';
    const cb = onLayerClose;
    onLayerClose = null;
    if (cb && !silent) cb();
}
export const layerOpen = () => !$('#layer').hidden;

// --- Symbols -----------------------------------------------------------------

let symbolList = null, iconList = null, symbolSets = null;
export async function loadSymbols() {
    if (!symbolList) try { symbolList = await (await fetch(STATIC + 'designer/symbols.json')).json(); } catch (e) { return []; }
    return symbolList;
}
export async function loadIcons() {
    if (!iconList) try {
        const data = await (await fetch(STATIC + 'mdi/icons.json')).json();
        iconList = Object.entries(data.icons).map(([name, code]) => ({ name, char: String.fromCodePoint(parseInt(code, 16)) }));
    } catch (e) { return []; }
    return iconList;
}
export async function loadSymbolSets() {
    if (!symbolSets) try { symbolSets = await (await fetch(STATIC + 'symbols/symbols.json')).json(); } catch (e) { return {}; }
    return symbolSets;
}
export function findIcons(list, query) {
    const words = query.toLowerCase().replace(/^[a-z]+:/, '').split(/[\s:]+/).filter(Boolean);
    if (!words.length) return list;
    const rank = i => i.name === words.join('-') ? 0 : i.name.startsWith(words[0]) ? 1 : 2;
    return list.filter(i => words.every(w => i.name.includes(w)))
        .sort((a, b) => rank(a) - rank(b) || a.name.length - b.name.length || a.name.localeCompare(b.name));
}
export const SYMBOL_SETS = { mdi: 'Icons', fastener: 'Fasteners', electronic: 'Electronic components' };
