// The editor: the label as the server's preview on a stage, the field under
// it, one floating toolbar for what is picked and the Advanced card for the
// whole label.

import {
    $, $$, esc, icon, clone, debounce, latest, toast, hideToast, own, load, storeQuiet, forget, S, C, SIZES, L, familyOf, faceOf, familyNamed, isBold, baseDesign, isEmptyDesign, withImage, withCode, labelFiles, codeWithText, textFit, setTextFit, wrapWhy, verticalAlign,
    hasRoom, NO_ROOM, sizeName, isColorTape, isEndless, redTape, isDieCut, runsAlong, setRunsAlong, flagOn, hasLength, hasFixedLength,
    placeEdit, madeDesign, flagLost, placed, tapeKnown, thePrinter, isDemo, onTape, preview, dpiOf, dotsToMm, mmToDots, blanksOf, defaults,
    fieldType, blankChoices, startOf,
    valuesNow, choicesMissing, printJob, entryName, openLayer, closeLayer, cutOf, printerCut, sizeCaps, noCutter, noCutterWhy, neverWhy,
    cutShort, cutStrip, CUTS, halfOf, chainOf, imageKey, printerCaps, saveLabel, savedAs, leavesFirst, printerState, printerLabel, printerName, designSig,
    loadSaved, roomFrom, isSheet, sheetNote, untold
} from './core.js';
import { mountField, renderField, markLine, focusLine, closePop } from './field.js';
import { mountMedia } from './media.js';
import { swatchHTML, openColors, closeColors } from './colors.js';
import { fontButton, openFontMenu, closeFontMenu, refreshFontMenu, hasItalic, familyLine, weightLine, widthLine, boldLine, italicLine } from './fontmenu.js';

// Later parts of the editor register here: the toolbars of a code and an
// image, adding them, files dropped or pasted on the page, the file or page
// of several the preview shows (shown) and what a preview told (previewed)
export const hooks = { toolbars: {}, addImage: null, addCode: null, files: null, shown: null, previewed: null };

const ui = { pick: null, adv: false, last: null, full: null, grow: null, failed: false };
export const editorUI = ui;

// --- The label in the editor ---------------------------------------------------

export function newEdit() {
    return { title: 'New label', design: Object.assign(baseDesign(S.tape), { text: [L('', C.defaults.font_size, true)] }), origin: null, values: {} };
}
export const editItem = E => ({ kind: E.origin && E.origin.kind === 'saved' ? 'saved' : 'edit', id: E.origin && E.origin.id, design: E.design });

// Another label starts a new undo history, undo never mixes two labels
export function setEdit(edit) {
    S.edit = edit;
    S.edit.values = S.edit.values || {};
    // The steps kept for the next visit belong to this label, see storeHistory()
    S.edit.histId = edit.histId || Math.random().toString(36).slice(2);
    // Copies belong to the label, another one starts with one
    if ($('#copies')) $('#copies').value = edit.copies || 1;
    hist.undo = [];
    hist.redo = [];
    hist.last = null;
    histImages.clear();
    ui.pick = null;
    ui.full = null;
    ui.failed = false;
    ui.fixedLength = false;
    ui.customMargins = false;
    ui.customFrame = false;
}

// Another label in the editor: one with changes not saved asks first, as New
// does; opened, then() follows, e.g. a note of what was opened
export function openEditor(item, then = null) {
    const open = () => { showLabel(item); if (then) then(); };
    // A page started elsewhere, e.g. in the Settings, has the draft kept
    if (!S.edit) restoreEdit();
    if (unsaved(S.edit)) askFirst(`Open "${item.title || 'New label'}"?`, open);
    else open();
}
// The label kept for the next visit with its steps, else a new one
function restoreEdit() {
    const kept = loadEdit();
    setEdit(kept || newEdit());
    if (kept) loadHistory(S.edit);
}
function showLabel({ title, design, origin, values }) {
    setEdit({ title: title || 'New label', design: codeWithText(clone(design)), origin: origin || null, values: Object.assign({}, values || {}) });
    S.edit.kept = keptAs(S.edit);
    // Drawn at once where the editor is shown, e.g. under Templates; only
    // another view, e.g. Settings, goes back to it by the address
    if (document.body.dataset.view === 'settings') { location.hash = 'edit'; return; }
    if (location.hash !== '#edit') history.replaceState(null, '', '#edit');
    closePop();
    renderEditor();
    focusLine(0);
}

// --- Undo, redo and the draft ----------------------------------------------------

const hist = { undo: [], redo: [], last: null };
const histImages = new Map();
function snapshot(E) {
    const keep = im => { const k = imageKey(im); histImages.set(k, im); return { ref: k }; };
    const d = Object.assign({}, E.design);
    if (d.image) d.image = keep(d.image);
    if (d.more_images) d.more_images = d.more_images.map(keep);
    return JSON.stringify({ t: E.title, d });
}
function fromSnapshot(snap) {
    const o = JSON.parse(snap);
    const get = r => (r && r.ref ? histImages.get(r.ref) : r);
    if (o.d.image) o.d.image = get(o.d.image);
    if (o.d.more_images) o.d.more_images = o.d.more_images.map(get);
    return o;
}
function record() {
    const E = S.edit;
    if (!E) return;
    const snap = snapshot(E);
    if (snap !== hist.last) {
        if (hist.last !== null) {
            hist.undo.push(hist.last);
            if (hist.undo.length > 80) hist.undo.shift();
            hist.redo = [];
        }
        hist.last = snap;
    }
    updateHistoryButtons();
}
function updateHistoryButtons() {
    if (!$('#undo')) return;
    const pending = S.edit && hist.last !== null && snapshot(S.edit) !== hist.last;
    const steps = hist.undo.length + (pending ? 1 : 0);
    $('#undo').disabled = !steps;
    $('#redo').disabled = !hist.redo.length;
    // The steps to undo counted on the button, so that it is clear how far back it goes
    let n = $('#undo .n');
    if (!n) { n = document.createElement('span'); n.className = 'n'; $('#undo').appendChild(n); }
    n.textContent = steps > 99 ? '99+' : String(steps);
    n.hidden = !steps;
    $('#undo').title = steps ? `Undo (Ctrl Z), ${steps} ${steps === 1 ? 'step' : 'steps'}` : 'Undo (Ctrl Z)';
}
export function stepHistory(dir) {
    if (!S.edit) return;
    record();
    const from = dir < 0 ? hist.undo : hist.redo, to = dir < 0 ? hist.redo : hist.undo;
    if (!from.length) return;
    to.push(hist.last);
    hist.last = from.pop();
    const o = fromSnapshot(hist.last);
    S.edit.title = o.t;
    S.edit.design = o.d;
    ui.full = null;
    if (ui.pick && ui.pick.part === 'line' && ui.pick.index >= o.d.text.length) ui.pick = { part: 'line', index: o.d.text.length - 1 };
    closePop();
    renderEditor();
    storeEdit();
}

// The label is kept for the next visit, its images apart and only when
// they change: they are large, the text changes with each key. Before the
// label goes without them, the images of the steps to undo make room.
// Images that do not fit even so are tried again a minute later at the
// earliest or once they change, not at each pause in typing.
const DRAFT = own('designer_next_edit'), DRAFT_IMAGES = own('designer_next_edit_images');
let storedImages = null, imagesTooLarge = null;
const triedLately = (failed, keys) => !!failed && failed.keys === keys && Date.now() - failed.time < 60000;
function storeEdit() {
    const E = S.edit, d = E.design;
    const images = [d.image, ...(d.more_images || [])].filter(Boolean);
    const keys = images.map(imageKey).join(' ');
    let withImages = keys === storedImages;
    if (!withImages && !triedLately(imagesTooLarge, keys)) {
        withImages = storeQuiet(DRAFT_IMAGES, images) || (forgetStepImages(), storeQuiet(DRAFT_IMAGES, images));
        storedImages = withImages ? keys : null;
        imagesTooLarge = withImages ? null : { keys, time: Date.now() };
    }
    // Kept without them, the images stored before belong to no label kept
    if (!withImages) forget(DRAFT_IMAGES);
    const ref = im => ({ ref: imageKey(im) });
    const plain = Object.assign({}, d, { image: undefined, more_images: undefined });
    const keptDesign = withImages ? Object.assign({}, d, { image: d.image ? ref(d.image) : undefined, more_images: d.more_images ? d.more_images.map(ref) : undefined }) : plain;
    const keep = { title: E.title, origin: E.origin, values: E.values, made: E.made, copies: E.copies, kept: E.kept, histId: E.histId };
    if (!storeQuiet(DRAFT, Object.assign({}, keep, { design: keptDesign }))) {
        // The newest text before any image
        forgetStepImages();
        forget(DRAFT_IMAGES);
        storedImages = null;
        imagesTooLarge = { keys, time: Date.now() };
        withImages = false;
        storeQuiet(DRAFT, Object.assign({}, keep, { design: plain }));
    }
    storeHistory(withImages ? new Set(images.map(imageKey)) : null);
}
// Another tab kept its own: the images go again with the next change
addEventListener('storage', e => {
    if (e.key === null || e.key === DRAFT_IMAGES) storedImages = null;
    if (e.key === null || e.key === HISTORY_IMAGES) storedHistImages = null;
});
export function loadEdit() {
    const edit = load(DRAFT, null);
    if (!edit || !edit.design) return null;
    const images = new Map(load(DRAFT_IMAGES, []).map(im => [imageKey(im), im]));
    const get = r => (r && r.ref ? images.get(r.ref) : r);
    const d = codeWithText(edit.design);
    if (d.image) d.image = get(d.image);
    if (!d.image) delete d.image;
    if (d.more_images) d.more_images = d.more_images.map(get).filter(Boolean);
    if (d.more_images && !d.more_images.length) delete d.more_images;
    return edit;
}
const keep = debounce(() => { record(); if (S.edit) storeEdit(); }, 400);
addEventListener('pagehide', () => { if (S.edit) { record(); storeEdit(); } });

// The steps to undo and redo are kept with the draft, for its label only: the
// last ones up to a size, the oldest go first, and the images they show
// besides the draft's apart, once each, the newest up to a size. They give
// way first where the storage is full; where the browser keeps nothing,
// undo works until the page is left.
const HISTORY = own('designer_next_history'), HISTORY_IMAGES = own('designer_next_history_images');
const KEPT_STEPS = 50, KEPT_CHARS = 400000, KEPT_IMAGE_CHARS = 1000000;
let storedHistImages = null, stepImagesGone = 0;
const refsOf = snap => [...snap.matchAll(/"ref":"([^"]+)"/g)].map(m => m[1]);
function forgetStepImages() {
    forget(HISTORY_IMAGES);
    storedHistImages = null;
    stepImagesGone = Date.now();
}
// Where a printed label needs the room, the steps keep only what the draft shows
roomFrom(() => { forgetStepImages(); if (S.edit) storeEdit(); });
// inDraft: the keys of the images kept with the draft, null where it is
// kept without them: its steps would show images not kept
function storeHistory(inDraft) {
    const E = S.edit;
    if (!E || hist.last === null) return;
    if (!inDraft) { forget(HISTORY); forgetStepImages(); return; }
    let undo = hist.undo.slice(-KEPT_STEPS), redo = hist.redo.slice(-KEPT_STEPS);
    const newest = [hist.last, ...undo.slice().reverse(), ...redo.slice().reverse()];
    let kept = [], size = 0;
    if (Date.now() - stepImagesGone >= 60000) {
        for (const k of new Set(newest.flatMap(refsOf))) {
            if (inDraft.has(k) || !histImages.has(k)) continue;
            size += String(histImages.get(k).data || '').length;
            if (size > KEPT_IMAGE_CHARS) break;
            kept.push(k);
        }
    }
    if (kept.join(' ') !== storedHistImages && !storeQuiet(HISTORY_IMAGES, kept.map(k => histImages.get(k)))) {
        forgetStepImages();
        kept = [];
    } else storedHistImages = kept.join(' ');
    // The steps that show images not kept go
    const shown = new Set([...inDraft, ...kept]), plain = snap => refsOf(snap).every(k => shown.has(k));
    undo = undo.filter(plain);
    redo = redo.filter(plain);
    const oldest = () => (undo.length ? undo : redo).shift();
    while (undo.length + redo.length && JSON.stringify([undo, redo, hist.last]).length > KEPT_CHARS) oldest();
    while (!storeQuiet(HISTORY, { id: E.histId, undo, redo, last: hist.last })) {
        if (!undo.length && !redo.length) { forget(HISTORY); return; }
        for (let n = Math.ceil((undo.length + redo.length) / 4); n > 0; n--) oldest();
    }
}
// The steps of the draft's label as kept, where they are its own
function loadHistory(E) {
    const kept = load(HISTORY, null);
    const steps = list => Array.isArray(list) ? list.filter(s => typeof s === 'string') : [];
    if (!kept || kept.id !== E.histId || typeof kept.last !== 'string') return;
    for (const im of [...load(DRAFT_IMAGES, []), ...load(HISTORY_IMAGES, [])]) if (im && im.data) histImages.set(imageKey(im), im);
    hist.undo = steps(kept.undo);
    hist.redo = steps(kept.redo);
    hist.last = kept.last;
}

// --- Changes -------------------------------------------------------------------------

// Every change of the label comes here: the toolbar and the card follow at
// once, the preview after the one on its way
function changed(structural = false, keepFull = false) {
    const E = S.edit;
    if (!keepFull) { ui.full = null; ui.growFrom = null; }
    // Changed, it is saved for the tape in the printer, see madeDesign()
    if (E.made && designSig(E.design) !== E.made.sig) { E.made = null; drawTape(); }
    if (ui.pick && ui.pick.part === 'line' && ui.pick.index >= E.design.text.length) ui.pick = { part: 'line', index: E.design.text.length - 1 };
    if (hist.last === null) record();
    keep();
    updateHistoryButtons();
    if (structural) renderToolbar();
    if (ui.adv) renderAdvanced();
    updateActions();
    drawFill();
    markBusy();
    drawPreview();
}
// A change made by a control is a step to undo of its own, quick clicks in a
// row on the same control (opts.merge) are one. The field shows it as well.
let lastApply = { key: null, time: 0 };
function apply(fn, opts = {}) {
    const now = Date.now();
    if (!(opts.merge && lastApply.key === opts.merge && now - lastApply.time < 1000)) record();
    lastApply = { key: opts.merge || null, time: now };
    fn(S.edit.design);
    if (opts.field) renderField();
    changed(true, opts.keepFull);
    if (!opts.merge) record();
}

// --- Drawing the editor ------------------------------------------------------------------

export function renderEditor() {
    if (!S.edit) restoreEdit();
    placeEdit(S.edit);
    const view = $('#view');
    if (!$('#stage', view)) {
        view.innerHTML = `<div class="ed">
            <section class="stage" id="stage">
                <div class="stage-head"><input class="title" id="title" aria-label="Name of the label" title="Name of the label" spellcheck="false" autocomplete="off"><div class="fits" id="fits"></div><p class="tapenote" id="tapeNote" hidden></p></div>
                <div class="stage-acts"><button class="btn ghost" type="button" id="templates">${icon('grid')}Templates</button><button class="btn ghost" type="button" id="newLabel">${icon('plus')}New</button></div>
                <div class="center" id="center">
                    <div class="float" id="ltb" hidden></div>
                    <div class="labelbox" id="labelbox">
                        <span class="printer vslot" id="vslot" aria-hidden="true"><span class="led"></span><span class="model"></span><span class="mouth"></span></span>
                        <div class="lead" id="lead" hidden></div>
                        <div class="paper" id="paper"><img id="labelImg" alt="Label preview"><svg class="pagecuts" id="pagecuts" aria-hidden="true"></svg><div class="picks" id="picks"></div></div>
                        <span class="cutmark" id="cutmark" aria-hidden="true" hidden>${icon('scissors')}</span>
                        <span class="dim" id="dimW" title="Width of the tape"><b></b><i></i></span>
                        <span class="dim" id="dimL" title="Length as cut"><b></b><i></i></span>
                        <span class="spin" aria-hidden="true"></span>
                    </div>
                    <div class="printer slot" id="slot" aria-hidden="true"><span class="led"></span><span class="model"></span><span class="mouth"></span></div>
                    <p class="made" id="made" hidden></p>
                    <p class="mnote sheetnote" id="sheetNote" hidden></p>
                    <div class="fill" id="fill" hidden></div>
                    <div class="composer">
                        <div class="field" id="field" contenteditable="true" spellcheck="false" role="textbox" aria-multiline="true" aria-label="Label text" data-ph="Type the label text, [ for blanks and dates"></div>
                        <div class="cbar">
                            <button class="btn ghost" type="button" id="addImage">${icon('image')}<span>Image</span></button>
                            <button class="btn ghost" type="button" id="addCode">${icon('qr')}<span>Code</span></button>
                            <span class="sp"></span>
                            <span class="hist">
                                <button class="iconbtn" id="undo" type="button" title="Undo (Ctrl Z)" aria-label="Undo" disabled>${icon('undo', '')}</button>
                                <button class="iconbtn" id="redo" type="button" title="Redo (Ctrl Y)" aria-label="Redo" disabled>${icon('undo', 'flip')}</button>
                            </span>
                            <button class="btn soft" type="button" id="advBtn" aria-expanded="false">${icon('sliders')}Advanced</button>
                        </div>
                    </div>
                </div>
                <aside class="adv" id="adv" hidden></aside>
                <button class="iconbtn zoom" type="button" id="zoom" aria-pressed="false" aria-label="Large preview"></button>
            </section></div>`;
        bindStage();
        mountField($('#field'), {
            pickedLine: () => ui.pick && ui.pick.part === 'line' ? ui.pick.index : -1,
            changed: structural => changed(structural),
            caretLine: i => { if (!ui.pick || ui.pick.part !== 'line' || ui.pick.index !== i) pick({ part: 'line', index: i }, false); },
            files: files => hooks.files(files),
        });
    }
    if (!ui.pick) ui.pick = { part: 'line', index: 0 };
    // The label as it starts is where undo leads back to
    if (hist.last === null) record();
    renderField();
    renderToolbar();
    if (ui.adv) renderAdvanced();
    updateActions();
    updateHistoryButtons();
    drawFill();
    drawTape();
    drawPrinter();
    // Open before another view was shown, e.g. Settings, it is open again
    if (ui.adv && $('#adv').hidden) toggleAdvanced(true);
    markBusy();
    drawPreview();
}

function bindStage() {
    $('#newLabel').onclick = () => unsaved(S.edit) ? askFirst('Start a new label?', startNew) : startNew();
    $('#undo').onclick = () => stepHistory(-1);
    $('#redo').onclick = () => stepHistory(1);
    $('#templates').onclick = () => hooks.templates && hooks.templates();
    $('#advBtn').onclick = () => toggleAdvanced();
    $('#zoom').onclick = () => toggleLarge();
    $('#sheetNote').onclick = e => {
        const fill = e.target.closest('[data-fill]');
        if (!fill) return;
        // Filled for this page, they go back on a printer without pages
        if (S.edit && S.edit.filledFrom === undefined) S.edit.filledFrom = copies();
        $('#copies').value = fill.dataset.fill;
        if (S.edit) S.edit.copies = +fill.dataset.fill;
        copiesChanged();
    };
    // The label takes the room left once Advanced has moved in or out
    $('#center').addEventListener('transitionend', e => { if (e.target.id === 'center' && e.propertyName === 'margin-right') fitLabel(); });
    // The name the label is saved under, a saved one is renamed when saved
    const title = $('#title');
    title.addEventListener('input', () => { S.edit.title = title.value; keep(); updateHistoryButtons(); updateActions(); });
    title.addEventListener('change', () => { if (!title.value.trim()) { S.edit.title = title.value = 'New label'; keep(); } });
    title.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === 'Escape') { e.preventDefault(); title.blur(); } });
    $('#addImage').onclick = () => hooks.addImage();
    $('#addCode').onclick = () => hooks.addCode();
    $('#picks').addEventListener('click', e => {
        const b = e.target.closest('[data-part]');
        if (!b) return;
        const p = { part: b.dataset.part, index: b.dataset.i === undefined ? undefined : +b.dataset.i };
        pick(p, true);
    });
    // A click on the stage around the label lets the part go
    $('#stage').addEventListener('mousedown', e => {
        if (e.target.id === 'stage' || e.target.id === 'center') unpick();
    });
}

// A new label; one with changes not saved asks first, see askFirst()
function startNew() {
    const before = S.edit;
    setEdit(newEdit());
    renderEditor();
    focusLine(0);
    if (!isEmptyDesign(before.design)) toast('Started a new label', { label: 'Undo', run: () => { setEdit(before); renderEditor(); } });
}
// What a label is like as saved or opened, its changes since are not saved
const keptAs = E => JSON.stringify([E.title, designSig(madeDesign(E))]);
const unsaved = E => !isEmptyDesign(E.design) && E.kept !== keptAs(E);
// Before the label in the editor makes way for another (then) with changes
// not saved: they are saved first, discarded, or the other one waits
function askFirst(question, then) {
    openLayer(`<div class="dialog narrow" role="alertdialog" aria-label="${esc(question)}">
        <h2>${esc(question)}</h2><p class="muted">"${esc(S.edit.title)}" has changes that are not saved.</p>
        <div class="acts"><button class="btn" type="button" data-close>Cancel</button><button class="btn" type="button" id="newDiscard">Discard</button><button class="btn primary" type="button" id="newSave">${icon('save')}Save</button></div></div>`, 'center');
    $('#newDiscard').onclick = () => { closeLayer(true); then(); };
    $('#newSave').onclick = () => { closeLayer(true); hooks.save(then); };
    $('#newSave').focus();
}

function unpick() {
    ui.pick = null;
    renderToolbar();
    markPicks();
    markLine(-1);
}
// Esc lets the picked part go where it closes nothing else, e.g. a menu
addEventListener('keydown', e => {
    if (e.key !== 'Escape' || !ui.pick || !$('#stage') || document.body.dataset.view === 'settings') return;
    if ($('.menu, .chip-pop') || !$('#layer').hidden || !$('#adv').hidden || document.activeElement === $('#title')) return;
    // The caret's line is picked too: the caret leaves the field
    const sel = getSelection();
    if (sel.rangeCount && $('#field').contains(sel.anchorNode)) { sel.removeAllRanges(); $('#field').blur(); }
    unpick();
}, true);

function pick(p, fromLabel) {
    ui.pick = p;
    closeColors();
    closeFontMenu();
    renderToolbar();
    markPicks();
    if (p.part === 'line') {
        markLine(p.index);
        if (fromLabel) focusLine(p.index);
    } else markLine(-1);
}

function updateActions() {
    const E = S.edit, empty = isEmptyDesign(E.design), missing = choicesMissing(editItem(E)).length > 0;
    $('#print').disabled = empty || ui.failed || missing;
    $('#save').disabled = empty || missing;
    $('#save').title = E.origin && E.origin.kind === 'saved' ? `Save "${E.title}"` : 'Save to your labels';
    if (document.activeElement !== $('#title')) $('#title').value = E.title;
    const files = labelFiles(E.design);
    $('#addImage').classList.toggle('on', withImage(E.design));
    $('#addImage span').textContent = files.length > 1 ? `${files.length} ${files.some(f => /pdf/i.test(f.mime)) ? 'files' : 'images'}` : files.length && /pdf/i.test(files[0].mime) ? 'PDF added' : withImage(E.design) ? 'Image added' : 'Image';
    $('#addCode').classList.toggle('on', withCode(E.design));
    $('#addCode span').textContent = withCode(E.design) ? 'Code added' : 'Code';
}

// The tape is named in the printer chip, its details are in the chip's
// list and the printer settings. The stage says only what the label needs:
// a tape the printer does not tell, and a label made for another tape.
function drawTape() {
    const E = S.edit, d = E.design, who = thePrinter(S.printer, true);
    const measured = !tapeKnown() && S.tapeCandidates.length > 0;
    // The demo printer has any roll of its model, none is missing
    const note = !S.printer || tapeKnown() || isDemo(S.printer) ? '' : measured ? `${who} measures labels ${S.tapeUnknown}.`
        : S.tapeUnknown ? `${who} reports ${S.tapeUnknown} tape, which is not offered here.` : `${who} does not say which tape it has.`;
    const tn = $('#tapeNote');
    tn.innerHTML = note ? `${icon('warn')}<span>${esc(note)} <a class="custom" href="#settings/printers" id="tapeChange">${measured ? 'Choose which ones' : 'Set the tape'}</a></span>` : '';
    tn.hidden = !note;
    const made = $('#made');
    if (E.made && placed(E.made.as.label_size)) {
        made.innerHTML = `${icon('warn')} Made for ${esc(sizeName(E.made.as.label_size))}, shown as it prints on the ${esc(sizeName(d.label_size))}.${flagLost(E) ? ' Cable flags need continuous tape.' : ''}`;
        made.hidden = false;
    } else made.hidden = true;
}

// --- The blanks to fill in ----------------------------------------------------------------

// The blanks of the label with their values for this print: the empty ones
// marked, Enter prints, a saved label offers the values printed with it
// before
function drawFill() {
    const E = S.edit, box = $('#fill');
    if (!box) return;
    const item = editItem(E), blanks = blanksOf(E.design);
    const valueOf = name => name in (E.values || {}) ? E.values[name] : startOf(item, name);
    const sig = JSON.stringify([blanks, blanks.map(b => [fieldType(item, b), blankChoices(item, b), startOf(item, b)]), item.id || '']);
    if (box.dataset.sig !== sig) {
        box.dataset.sig = sig;
        box.hidden = !blanks.length;
        box.innerHTML = !blanks.length ? '' : `<div class="fill-h"><h4>Fill in</h4><span class="badge" id="fillN" hidden></span><span class="sp"></span><small class="muted">Enter prints</small></div>
            <div class="fill-grid">${blanks.map((name, i) => fillField(item, name, valueOf(name), i)).join('')}</div>`;
        $$('[data-blank]', box).forEach(inp => {
            inp.addEventListener(inp.tagName === 'SELECT' ? 'change' : 'input', () => {
                S.edit.values = Object.assign({}, S.edit.values, { [inp.dataset.blank]: inp.value });
                changed(false);
            });
            inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); if (!e.repeat) printNow(); } });
        });
        $$('[data-reuse]', box).forEach(b => b.onclick = () => {
            const inp = $(`[data-blank="${CSS.escape(b.dataset.reuse)}"]`, box);
            inp.value = b.textContent;
            inp.dispatchEvent(new Event('input'));
            inp.focus();
        });
    }
    // Values set elsewhere, e.g. in a blank's options, show here as well
    $$('[data-blank]', box).forEach(inp => {
        const v = valueOf(inp.dataset.blank);
        if (document.activeElement !== inp && (inp.tagName !== 'SELECT' || [...inp.options].some(o => o.value === v))) inp.value = v;
    });
    const open = $$('input[data-blank]', box).filter(inp => { const empty = !inp.value.trim(); inp.classList.toggle('need', empty); return empty; }).length;
    const n = $('#fillN', box);
    if (n) { n.textContent = `${open} to fill in`; n.hidden = !open; }
}

function fillField(item, name, value, i) {
    const kind = fieldType(item, name), id = 'fill' + i;
    const head = `<label class="k" for="${esc(id)}">${esc(name)}</label>`;
    if (kind === 'choice') {
        const choices = blankChoices(item, name);
        return `<div>${head}<select class="in" id="${esc(id)}" data-blank="${esc(name)}"${choices.length ? '' : ' disabled'}>${choices.length ? choices.map(c => `<option${c === value ? ' selected' : ''}>${esc(c)}</option>`).join('') : '<option value="">No choices yet</option>'}</select></div>`;
    }
    if (kind !== 'text') return `<div>${head}<input class="in" id="${esc(id)}" type="${esc(kind)}" data-blank="${esc(name)}" value="${esc(value)}"></div>`;
    // Saved labels offer the values printed with them before
    const used = [];
    if (item.kind === 'saved') {
        for (const h of S.history) {
            if (!h.source || h.source.id !== item.id) continue;
            const v = (h.values || {})[name];
            if (v && !used.includes(v)) used.push(v);
            if (used.length === 4) break;
        }
    }
    return `<div>${head}<input class="in" id="${esc(id)}" data-blank="${esc(name)}" value="${esc(value)}" placeholder="Fill in ${esc(name)}" autocomplete="off">
        ${used.length ? `<div class="reuse">${used.map(v => `<button class="pill" type="button" data-reuse="${esc(name)}" title="Printed before">${esc(v)}</button>`).join('')}</div>` : ''}</div>`;
}

// --- The preview ----------------------------------------------------------------------------

let seq = 0, shownSeq = 0, asked = 0;
function markBusy() { $('#labelbox').classList.add('busy'); laidOut(false); }
// data-laid on the label box tells that the stage stands still: set once a
// fit is drawn and no transition moves it any more, cleared as soon as
// another preview or fit starts
let laidFrame = 0;
function laidOut(done) {
    const lb = $('#labelbox');
    if (!lb) return;
    cancelAnimationFrame(laidFrame);
    delete lb.dataset.laid;
    if (!done) return;
    const settle = () => {
        laidFrame = requestAnimationFrame(() => {
            if (!lb.isConnected || lb.classList.contains('busy')) return;
            if ($('#center').getAnimations().some(a => a.playState === 'running')) { settle(); return; }
            lb.dataset.laid = '1';
        });
    };
    settle();
}

// A preview asked for while one is drawn follows it, the label stays busy
// until the last one asked for is shown
function drawPreview() { asked++; nextPreview(); }
const nextPreview = latest(async () => {
    const E = S.edit;
    if (!E || !$('#labelImg')) return;
    const me = ++seq, upTo = asked;
    const empty = isEmptyDesign(E.design);
    // An empty label shows as large as a line of its first size makes it
    const shown = empty ? Object.assign(clone(Object.assign({}, E.design, { image: undefined, more_images: undefined })), { text: E.design.text.slice(0, 1).map(l => Object.assign({}, l, { text: 'Ag' })) })
        : hooks.shown ? hooks.shown(E.design) : E.design;
    const values = previewValues(E);
    let p;
    try {
        p = await preview(shown, values, copies(), hooks.perCopy ? hooks.perCopy() : 0);
    } catch (e) {
        if (me < shownSeq) return;
        shownSeq = me;
        ui.failed = !!e.refused;
        ui.last = null;
        if (upTo === asked) $('#labelbox').classList.remove('busy');
        $('#labelbox').classList.add('failed');
        $('#picks').innerHTML = '';
        drawSheet(null);
        fitsNote('', e.message);
        updateActions();
        laidOut(true);
        return;
    }
    if (me < shownSeq) return;
    const img = new Image();
    img.src = p.url;
    try { await img.decode(); } catch (e) { }
    if (me < shownSeq) return;
    shownSeq = me;
    ui.failed = false;
    ui.last = Object.assign({}, p, { w: img.naturalWidth, h: img.naturalHeight, empty });
    // A color the tape prints otherwise is said next to it
    const colorsTold = JSON.stringify(S.printedAs) !== JSON.stringify(p.printedAs);
    S.printedAs = p.printedAs;
    if (colorsTold) { renderToolbar(); if (ui.adv) renderAdvanced(); }
    const el = $('#labelImg');
    el.src = p.url;
    $('#labelbox').classList.toggle('empty', empty);
    $('#labelbox').classList.remove('failed');
    if (!S.edit || upTo !== asked) $('#labelbox').classList.add('busy'); else $('#labelbox').classList.remove('busy');
    fitLabel();
    // + on a line that did not grow: the label has no room for more
    if (ui.grow && ui.grow.seq <= me) {
        const box = lineBox(ui.grow.index), g = ui.grow, from = g.from;
        // What the steps since the last box that told something should add:
        // the server draws whole font sizes, a line drawn small changes only
        // every few steps, it is told once they should add 2 dots or more
        const ratio = g.size / from.size, want = [from.w * (ratio - 1), from.h * (ratio - 1)];
        if (box && Math.max(...want) >= 2) {
            const grew = [box[2] - box[0] - from.w, box[3] - box[1] - from.h];
            if (grew.every((n, k) => n < want[k] / 2)) ui.full = g.index;
            ui.growFrom = { index: g.index, w: box[2] - box[0], h: box[3] - box[1], size: g.size };
        } else ui.growFrom = from;
        ui.grow = null;
        renderToolbar();
    }
    fitsNote(empty ? null : p.warning, '');
    drawSheet(empty ? null : p.sheet);
    updateActions();
    if (!empty && hooks.previewed) hooks.previewed(ui.last, shown);
});

// Where a page of several labels is cut into them and the gap between lanes,
// over the page as the server tells it: shown, never printed
function drawCuts(w, h) {
    const box = $('#pagecuts'), sheet = ui.last && ui.last.sheet;
    if (!box) return;
    if (!sheet) { box.innerHTML = ''; return; }
    const kx = w / sheet.size[0], ky = h / sheet.size[1], r = n => Math.round(n * 10) / 10;
    const rects = sheet.labels.map(([x, y, bw, bh]) => [r(x * kx), r(y * ky), r(bw * kx), r(bh * ky)]);
    const holes = rects.map(([x, y, rw, rh]) => `M${x} ${y}h${rw}v${rh}h${-rw}z`).join('');
    box.setAttribute('viewBox', `0 0 ${w} ${h}`);
    box.innerHTML = '<defs><pattern id="gapHatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="5"/></pattern></defs>'
        + `<path class="gap" d="M0 0H${w}V${h}H0z${holes}"/>`
        + rects.map(([x, y, rw, rh]) => `<rect x="${x + .5}" y="${y + .5}" width="${rw - 1}" height="${rh - 1}"/>`).join('');
}

// Where the copies leave the last page of a roll with several labels to a
// page not full, as the server tells it
function drawSheet(sheet) {
    const note = $('#sheetNote'), html = sheetNote(sheet);
    note.innerHTML = html;
    note.hidden = !html;
}
// Only a roll with several labels to a page previews the copies
export function copiesChanged() {
    if (ui.last && isSheet(ui.last.labelSize)) drawPreview();
}

function previewValues(E) {
    const item = editItem(E);
    return Object.assign(defaults(item), E.values || {});
}

// The label at a size that fits the stage, never larger than about its
// real size on a common screen. The large preview shows it up to about four
// times its real size, for small print and fine lines.
const LARGE_PX_PER_MM = 16;
const largePreview = () => !!load(own('designer_settings'), {}).largePreview && matchMedia('(min-width: 761px)').matches;
function toggleLarge() {
    const all = load(own('designer_settings'), {});
    all.largePreview = !all.largePreview;
    storeQuiet(own('designer_settings'), all);
    fitLabel();
}

// How the label leaves the printer, so that it is clear which way up it
// comes out: labels across the tape leave it top or bottom first, those
// along the tape sideways (side), the printer on their left, on their right
// (back) where the other end leaves first. Some printers feed blank paper
// before each label (lead).
function stageOf(v) {
    const size = SIZES[v.labelSize] || { tape: [62, 0], dots: [696, 0], form: 'endless' };
    const scale = v.highRes ? 2 : 1, dpi = dpiOf(v.labelSize, v.highRes), span = size.dots[0] * scale;
    const side = Math.abs(v.w - span) > 4 * scale && Math.abs(v.h - span) <= 4 * scale;
    const caps = sizeCaps(v.labelSize), top = leavesFirst(caps) === 'top';
    const lead = isEndless(size) ? caps.lead_mm || 0 : 0;
    const along = Math.round((side ? v.w : v.h) / dpi * 25.4);
    const d = S.edit.design;
    // An endless label of a given length is as long as that when cut
    const length = !isEndless(size) ? size.tape[1] : hasLength(d) && +d.label_length > 0 ? +d.label_length : along + lead;
    return { size, dpi, side, top, back: side && top !== !!caps.leaves_turned, lead, leadDots: lead / 25.4 * dpi, length, caps, endless: isEndless(size) };
}

export function fitLabel() {
    const v = ui.last, img = $('#labelImg');
    if (!v || !img) return;
    laidOut(false);
    const center = $('#center'), g = stageOf(v);
    const wide = v.w + (g.side ? g.leadDots : 0), tall = v.h + (g.side ? 0 : g.leadDots);
    // Room on both sides for the printer, the dimensions and the scissors
    const maxW = Math.max(160, Math.min(640, center.clientWidth - 190)), maxH = Math.max(90, Math.min(380, innerHeight * .38));
    let k = Math.min(.85 / (v.highRes ? 2 : 1), maxW / wide, maxH / tall);
    const large = largePreview();
    if (large) k = Math.max(k, Math.min(LARGE_PX_PER_MM * 25.4 / g.dpi, (center.clientWidth - 160) / wide, (innerHeight - 340) / tall));
    const w = Math.round(v.w * k), h = Math.round(v.h * k), leadPx = Math.round(g.leadDots * k);
    img.style.width = `${w}px`;
    img.style.height = `${h}px`;
    // Dots of black and red labels stay sharp where they are shown large
    img.classList.toggle('dots', large && !isColorTape(v.labelSize) && k * (devicePixelRatio || 1) >= 2);
    center.classList.toggle('side', g.side);
    center.classList.toggle('back', g.back);
    center.classList.toggle('from-top', !g.side && !g.top);
    center.classList.toggle('large', large);
    const lead = $('#lead');
    lead.hidden = !leadPx;
    lead.style.width = g.side ? `${leadPx}px` : `${w}px`;
    lead.style.height = g.side ? `${h}px` : `${leadPx}px`;
    lead.title = `Blank paper, ${g.lead} mm`;
    const cut = cutOf(S.edit.design), cutEnd = noCutter(g.caps) ? 'none' : chainOf(cut, g.caps) ? 'chain' : cut.cut === 'none' ? 'none' : 'full';
    $('#cutmark').hidden = !g.endless || cutEnd !== 'full';
    $('#slot').style.width = `${w + 72}px`;
    $('#vslot').style.height = `${h + 24}px`;
    drawDims(g);
    drawCuts(w, h);
    const zoom = $('#zoom');
    zoom.setAttribute('aria-pressed', String(large));
    zoom.title = large ? 'Smaller preview' : 'Larger preview';
    zoom.innerHTML = icon(large ? 'shrink' : 'expand');
    drawPicks(k);
    positionToolbar();
    laidOut(true);
}
addEventListener('resize', () => fitLabel());

// The width of the tape and the length of the label as cut beside it, each
// bar as long as what it measures, in the label's orientation on screen:
// across the tape its width runs across the screen, along it up and down.
// The width is on the side away from the printer, the length on the right,
// clear of the printer's slot (on the left where the printer is on the
// right), off the cut line.
function drawDims(g) {
    const lb = $('#labelbox'), strip = [$('#paper'), $('#lead')].filter(e => !e.hidden);
    const left = Math.min(...strip.map(e => e.offsetLeft)), top = Math.min(...strip.map(e => e.offsetTop));
    const right = Math.max(...strip.map(e => e.offsetLeft + e.offsetWidth)), bottom = Math.max(...strip.map(e => e.offsetTop + e.offsetHeight));
    // The cut at the end that leaves first, reaching out on the side of the
    // scissors only, so that they stay off the label
    const cut = $('#cutmark');
    cut.className = 'cutmark ' + (g.side ? 'v' : 'h');
    Object.assign(cut.style, g.side
        ? { left: `${(g.back ? left : right) - 1}px`, top: `${top}px`, width: '0', height: `${bottom - top + 26}px` }
        : { left: `${left - 26}px`, top: `${(g.top ? top : bottom) - 1}px`, width: `${right - left + 26}px`, height: '0' });
    const width = $('#dimW'), length = $('#dimL'), known = !!SIZES[ui.last.labelSize];
    width.hidden = length.hidden = !known;
    if (!known) return;
    $('b', width).textContent = `${g.size.tape[0]} mm`;
    $('b', length).textContent = `${g.length} mm`;
    const across = g.side ? length : width, upDown = g.side ? width : length;
    const below = !g.side && !g.top, atLeft = g.side && g.back;
    across.className = 'dim h' + (below ? ' below' : '');
    Object.assign(across.style, { left: `${left}px`, width: `${right - left}px`, right: '', height: '',
        top: below ? `${bottom + 8}px` : '', bottom: below ? '' : `${lb.offsetHeight - top + (g.side ? 14 : 8)}px` });
    upDown.className = 'dim v' + (atLeft ? ' left' : '');
    Object.assign(upDown.style, { top: `${top}px`, height: `${bottom - top}px`, bottom: '', width: '',
        left: atLeft ? '' : `${right + (g.side ? 10 : 44)}px`, right: atLeft ? `${lb.offsetWidth - left + 10}px` : '' });
}

// The printer the label comes out of, with its name and its state
export function drawPrinter() {
    if (!$('#slot')) return;
    const st = printerState(S.printer);
    for (const el of [$('#slot'), $('#vslot')]) {
        $('.led', el).className = 'led ' + st.cls;
        $('.model', el).textContent = !S.printer ? 'No printer' : isDemo(S.printer) ? `Demo ${printerName(S.printer)}` : printerLabel(S.printer);
        el.title = st.text;
    }
}

// How copies are cut, in Advanced next to the setting: three copies with
// their cuts drawn, and the same in words
function cutsHTML(d, caps) {
    const files = labelFiles(d).length, labels = files > 1 ? files : Math.max(1, (ui.last && ui.last.pages) || 1);
    const c = cutOf(d), short = cutShort(c, caps);
    return `<span class="k"></span><span class="row cutshow" id="cutShow">${cutStrip(c, caps, labels)}<small>${esc(noCutter(caps) ? short : short.replace(/^After/, 'Cut after'))}</small></span>`;
}

// Where the parts are on the label, as the server tells it: a click picks one
const PICK_GAP = 4, PICK_PAD = 8;
function drawPicks(k) {
    const v = ui.last, box = $('#picks');
    if (!v || v.empty) { box.innerHTML = ''; return; }
    // A cable flag lists its parts twice, the first label's are picked
    const merged = [];
    for (const p of v.parts) if (!merged.some(m => m.part === p.part && m.index === p.index)) merged.push(p);
    const shown = merged.filter(p => p.part !== 'line' || (p.index >= 0 && p.index < S.edit.design.text.length));
    const boxes = shown.map(p => p.box.map(n => n * k));
    box.innerHTML = shown.map((p, i) => {
        const b = boxes[i], others = boxes.filter((_, j) => j !== i);
        // The dashes a few pixels off what is printed, the button a little
        // further out for an easier click
        const pad = reach(b, others, PICK_PAD), gap = reach(b, others, PICK_GAP);
        const w = b[2] - b[0] + pad[0] + pad[2], h = b[3] - b[1] + pad[1] + pad[3];
        const ow = b[2] - b[0] + gap[0] + gap[2], oh = b[3] - b[1] + gap[1] + gap[3];
        const what = p.part === 'line' ? `Line ${p.index + 1}` : p.part === 'code' ? 'The code' : 'The image';
        return `<button type="button" class="pk" data-part="${esc(p.part)}"${p.index !== undefined ? ` data-i="${esc(p.index)}"` : ''} title="${esc(what)}" aria-label="${esc(what)}" aria-pressed="false"
            style="left:${esc(b[0] - pad[0])}px;top:${esc(b[1] - pad[1])}px;width:${esc(w)}px;height:${esc(h)}px"><svg class="ants" width="${esc(ow)}" height="${esc(oh)}" aria-hidden="true" style="left:${esc(pad[0] - gap[0])}px;top:${esc(pad[1] - gap[1])}px"><rect x=".75" y=".75" width="${esc(Math.max(0, ow - 1.5))}" height="${esc(Math.max(0, oh - 1.5))}" rx="3"/></svg></button>`;
    }).join('');
    markPicks();
}
// How far the outline of a part and its button reach out from its box b, in
// CSS pixels: most, at most half way to a part beside it so that a click on
// a part never picks its neighbor. Left, top, right, bottom.
function reach(b, others, most) {
    const r = [most, most, most, most];
    for (const o of others) {
        const gx = Math.max(o[0] - b[2], b[0] - o[2]), gy = Math.max(o[1] - b[3], b[1] - o[3]), gap = Math.max(gx, gy);
        if (gap >= 2 * most) continue;
        const side = gy >= gx ? (o[1] >= b[1] ? 3 : 1) : (o[0] >= b[0] ? 2 : 0);
        r[side] = Math.min(r[side], Math.max(0, gap / 2));
    }
    return r;
}
function markPicks() {
    $$('#picks .pk').forEach(b => {
        const on = !!ui.pick && b.dataset.part === ui.pick.part && (ui.pick.part !== 'line' || +b.dataset.i === ui.pick.index);
        b.classList.toggle('on', on);
        b.setAttribute('aria-pressed', String(on));
    });
}
function lineBox(i) {
    const p = ((ui.last && ui.last.parts) || []).find(x => x.part === 'line' && x.index === i);
    return p ? p.box : null;
}

// --- Whether everything fits ----------------------------------------------------------------

const frameOf = d => d.border_thickness === '0' ? 'none' : d.border_roundness === '0' ? 'square' : 'rounded';
function layoutProblem(d) {
    if (isColorTape(d.label_size)) return '';
    const colored = c => (c || 'black') !== 'black';
    const parts = d.text.map(l => l.color).concat(frameOf(d) !== 'none' ? [d.border_color] : [], d.print_type !== 'text' ? [d.print_color] : []).filter(colored);
    const redParts = parts.length || (withImage(d) && ['red_and_black', 'colored'].includes(d.image_mode));
    if (redParts && !redTape(d)) return 'This tape has no red, the red parts print black.';
    if (parts.some(c => c !== 'red')) return 'This tape prints black and red only, other colors print in one of them.';
    return '';
}
function fitsNote(warning, error) {
    const el = $('#fits'), d = S.edit.design;
    if (warning === null) { el.className = 'fits'; el.innerHTML = ''; return; }
    if (!warning && !error) warning = layoutProblem(onTape(d));
    const missing = choicesMissing(editItem(S.edit));
    if (missing.length && !error) warning = `Give [${missing[0]}] its choices: click it in the text.`;
    if (error) {
        el.className = 'fits warn';
        el.innerHTML = `${icon('warn')}<span><b>Fix the label to print it.</b> ${esc(error)}</span>`;
        return;
    }
    // The server finds an image light on color tape, stronger contrast helps
    const faint = !warning && ui.last && ui.last.faint && withImage(d) && d.image_contrast !== '1';
    el.className = warning || faint ? 'fits warn' : 'fits ok';
    const turned = ui.last && ui.last.auto ? ' <span class="muted">Turned to fit the label best.</span>' : '';
    if (faint) {
        el.innerHTML = `${icon('warn')}<span><b>This image looks light and may print faint.</b>${turned}</span><button class="btn soft sm" type="button" data-fix="contrast">Stronger contrast</button>`;
        $('[data-fix]', el).onclick = () => apply(x => { x.image_contrast = '1'; });
        return;
    }
    if (!warning) { el.innerHTML = `${icon('check')}<span>Everything fits${turned}</span>`; return; }
    const [, first, rest] = warning.match(/^(.*?[.!?])(?:\s+|$)(.*)$/s) || [null, warning, ''];
    el.innerHTML = `${icon('warn')}<span><b>${esc(first)}</b>${rest ? ' ' + esc(rest) : ''}</span>`;
    // Text that keeps its size and does not fit can be wrapped or shrunk at once
    if (/text does not fit/i.test(warning) && textFit(d) === 'none') {
        const wrap = d.text_wrap !== '1' && !wrapWhy(d);
        el.insertAdjacentHTML('beforeend', (wrap ? `<button class="btn soft sm" type="button" data-fix="wrap">Wrap long lines</button>` : '') + `<button class="btn soft sm" type="button" data-fix="shrink">Shrink to fit</button>`);
        $$('[data-fix]', el).forEach(b => b.onclick = () => apply(x => { if (b.dataset.fix === 'wrap') x.text_wrap = '1'; else setTextFit(x, 'shrink'); }));
    }
}

// --- The toolbar of what is picked ------------------------------------------------------------

const STEPS = [1, 1, 2, 5, 10];
// Quick clicks in a row take bigger steps
function stepper() {
    let last = 0, n = 0;
    return () => { const now = Date.now(); n = now - last < 450 ? Math.min(n + 1, STEPS.length - 1) : 0; last = now; return STEPS[n]; };
}
const sizeStep = stepper(), spacingStep = stepper(), allStep = stepper();
const ALIGNS = { left: ['left', 'Left'], center: ['center', 'Centered'], right: ['right', 'Right'] };

// Above the label with its dimensions and the printer
function positionToolbar() {
    const bar = $('#ltb');
    if (!bar || bar.hidden) return;
    const lb = $('#labelbox'), above = Math.min(0, ...$$('.dim', lb).filter(e => !e.hidden).map(e => e.offsetTop));
    // The printer is above the label where the bottom leaves first
    const slot = $('#slot'), top = Math.min(lb.offsetTop + above, slot.offsetParent ? slot.offsetTop : Infinity);
    bar.style.top = `${Math.max(8, top - bar.offsetHeight - 12)}px`;
}

export function renderToolbar() {
    const bar = $('#ltb');
    if (!bar) return;
    const p = ui.pick, d = S.edit.design;
    if (p && p.part !== 'line' && hooks.toolbars[p.part]) {
        bar.hidden = false;
        bar.className = 'float';
        hooks.toolbars[p.part](bar, { changed: (s = true) => changed(s), apply, unpick: () => pick({ part: 'line', index: 0 }, false) });
        positionToolbar();
        return;
    }
    if (!p || p.part !== 'line' || !d.text[p.index]) { bar.hidden = true; refreshFontMenu(); return; }
    const i = p.index, l = d.text[i], face = faceOf(l.font), fam = face && familyNamed(face.family), name = familyOf(l.font);
    const auto = textFit(d) === 'fill';
    // Bold and italic where the family has them, else said why not
    const bold = isBold(l.font), italic = !!(face && face.italic);
    const noStyle = what => face ? `${name} has no ${what} style` : `${name} is not installed`;
    const al = ALIGNS[l.align] || ALIGNS.center;
    const full = ui.full === i;
    bar.hidden = false;
    bar.className = 'float';
    bar.innerHTML = `${fontButton(`data-font title="Font of this line" aria-label="Font of line ${esc(i + 1)}: ${esc(name)}"`, face ? name : `${name}, not installed`)}
        <span class="sep"></span>
        <span class="az"><button type="button" class="azb" data-size="-1" title="Smaller" aria-label="Smaller">${icon('minus', '')}</button>
            <input class="anum num" data-sizein value="${auto ? '' : esc(l.size)}" placeholder="${auto ? 'Auto' : ''}" inputmode="numeric" title="${auto ? 'Auto: the label decides, the lines keep the ratio of their sizes' : 'Size of the line'}" aria-label="Size of line ${esc(i + 1)}">
            <button type="button" class="azb" data-size="1" title="${full ? 'Fills the label' : 'Larger'}" aria-label="Larger"${full ? ' disabled' : ''}>${icon('plus', '')}</button></span>
        <span class="sep"></span>
        <button type="button" class="tb${bold ? ' on' : ''}" data-bold aria-pressed="${bold}" title="${fam && fam.bold ? 'Bold' : esc(noStyle('bold'))}" aria-label="Bold"${fam && fam.bold ? '' : ' disabled'}>${icon('bold')}</button>
        <button type="button" class="tb${italic ? ' on' : ''}" data-italic aria-pressed="${italic}" title="${hasItalic(fam) ? 'Italic' : esc(noStyle('italic'))}" aria-label="Italic"${hasItalic(fam) ? '' : ' disabled'}>${icon('italic')}</button>
        <button type="button" class="tb${l.underline ? ' on' : ''}" data-underline aria-pressed="${!!l.underline}" title="Underline" aria-label="Underline">${icon('underline')}</button>
        <span class="sep"></span>
        <button type="button" class="tb" data-align title="Alignment: ${esc(al[1])}" aria-label="Alignment: ${esc(al[1])}">${icon(al[0])}</button>
        ${swatchHTML('data-lcolor', l.color, onTape(d), 'the line')}
        <span class="sep"></span>
        <button type="button" class="tb${l.inverted || l.checkbox || +l.line_spacing !== 100 ? ' dot' : ''}" data-more aria-expanded="false" title="More for this line" aria-label="More for this line">${icon('more')}</button>`;
    $('[data-font]', bar).onclick = () => openFontMenu({
        owner: 'line', anchor: () => $('#ltb [data-font]'), label: `Font of line ${i + 1}`,
        state: () => { const m = S.edit.design.text[i], f = m && faceOf(m.font); return { family: m ? familyOf(m.font) : '', faces: [f] }; },
        pick: name => apply(x => familyLine(x.text[i], name)),
        weight: w => apply(x => weightLine(x.text[i], w), { merge: 'weight' + i }),
        width: w => apply(x => widthLine(x.text[i], w)),
    });
    $$('[data-size]', bar).forEach(b => b.onclick = () => {
        const dir = +b.dataset.size, step = sizeStep();
        const box = lineBox(i), was = +S.edit.design.text[i].size || 1, size = Math.max(4, Math.min(999, Math.round(was + dir * step)));
        // Whether the line grew is told by the preview of the change; only
        // a label that shrinks or fills its text can be full
        const fits = ['shrink', 'fill'].includes(textFit(S.edit.design));
        const from = ui.growFrom && ui.growFrom.index === i ? ui.growFrom : box && { index: i, w: box[2] - box[0], h: box[3] - box[1], size: was };
        ui.grow = dir > 0 && fits && from ? { index: i, from, size, seq: seq + 1 } : null;
        apply(x => { x.text[i].size = String(size); }, { merge: 'size' + i, keepFull: dir > 0 });
    });
    const sizeIn = $('[data-sizein]', bar);
    sizeIn.addEventListener('change', () => {
        const v = Math.round(+sizeIn.value);
        if (!(v > 0)) { renderToolbar(); return; }
        // A size typed is the size printed, the label no longer decides
        apply(x => { x.text[i].size = String(Math.min(999, v)); if (textFit(x) === 'fill') setTextFit(x, 'shrink'); });
    });
    sizeIn.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); sizeIn.blur(); } });
    // Bold and italic keep the width of the line where the family has it
    $('[data-bold]', bar).onclick = () => apply(x => boldLine(x.text[i]));
    $('[data-italic]', bar).onclick = () => apply(x => italicLine(x.text[i]));
    $('[data-underline]', bar).onclick = () => apply(x => { const m = x.text[i]; if (m.underline) delete m.underline; else m.underline = true; });
    $('[data-align]', bar).onclick = () => apply(x => { const m = x.text[i]; m.align = { left: 'center', center: 'right', right: 'left' }[m.align] || 'left'; });
    const sw = $('[data-lcolor]', bar);
    if (sw) sw.onclick = () => openColors(sw, { color: l.color, d: onTape(d), what: 'the line', pick: c => apply(x => { x.text[i].color = c; }) });
    $('[data-more]', bar).onclick = e => lineMenu(e.currentTarget, i);
    positionToolbar();
    refreshFontMenu();
}

// "..." of a line: white on black, the to-do box, line spacing, removing it
function lineMenu(btn, i) {
    const d = S.edit.design, l = d.text[i];
    const old = $('.line-menu');
    if (old) { old.remove(); btn.setAttribute('aria-expanded', 'false'); return; }
    const el = document.createElement('div');
    el.className = 'menu line-menu';
    const spacing = +(l.line_spacing || 100);
    el.innerHTML = `<button type="button" data-act="invert" class="${l.inverted ? 'on' : ''}">${icon('contrast')}<span>White on black</span>${l.inverted ? icon('check') : ''}</button>
        <button type="button" data-act="todo" class="${l.checkbox ? 'on' : ''}">${icon('todo')}<span>To-do box</span><small>in front</small>${l.checkbox ? icon('check') : ''}</button>
        <div class="mi">${icon('spacing')}<span>Line spacing</span><span class="az sm"><button type="button" class="azb" data-sp="-1" aria-label="Less">${icon('minus', '')}</button><span class="anum" data-spv>${esc(spacing)} %</span><button type="button" class="azb" data-sp="1" aria-label="More">${icon('plus', '')}</button></span></div>
        <button type="button" data-act="remove" class="danger"${d.text.length > 1 || String(l.text).length ? '' : ' disabled'}>${icon('trash')}<span>Remove the line</span></button>`;
    $('#center').appendChild(el);
    const r = btn.getBoundingClientRect(), c = $('#center').getBoundingClientRect();
    el.style.left = `${Math.min(r.left - c.left - 8, c.width - el.offsetWidth - 8)}px`;
    el.style.top = `${r.bottom - c.top + 8}px`;
    btn.setAttribute('aria-expanded', 'true');
    const close = ev => { if (!el.contains(ev.target) && ev.target !== btn && !btn.contains(ev.target)) { el.remove(); document.removeEventListener('mousedown', close, true); btn.setAttribute('aria-expanded', 'false'); } };
    document.addEventListener('mousedown', close, true);
    const again = () => { el.remove(); document.removeEventListener('mousedown', close, true); const b = $('#ltb [data-more]'); if (b) lineMenu(b, i); };
    $$('[data-act]', el).forEach(b => b.onclick = () => {
        const act = b.dataset.act;
        if (act === 'remove') {
            el.remove();
            document.removeEventListener('mousedown', close, true);
            apply(x => { x.text.splice(i, 1); if (!x.text.length) x.text.push(L('', C.defaults.font_size, true)); }, { field: true });
            pick({ part: 'line', index: Math.max(0, i - 1) }, false);
            return;
        }
        apply(x => {
            const m = x.text[i];
            if (act === 'invert') m.inverted = !m.inverted;
            if (act === 'todo') m.checkbox = !m.checkbox;
        });
        again();
    });
    $$('[data-sp]', el).forEach(b => b.onclick = () => {
        const v = Math.max(50, Math.min(400, +(S.edit.design.text[i].line_spacing || 100) + +b.dataset.sp * 10 * spacingStep()));
        apply(x => { x.text[i].line_spacing = String(v); }, { merge: 'spacing' + i });
        $('[data-spv]', el).textContent = `${v} %`;
    });
}

// --- Advanced: the whole label ----------------------------------------------------------------

// Text size and long lines of the card set the server's text_fit and
// text_wrap, so labels saved before open and print the same:
//   Text size Auto      text_fit fill: as large as fits, the lines keep the
//                       ratio of their sizes
//   Text size a number  every line scaled so that the largest has it,
//                       text_fit shrink (fill turns into shrink)
//   Long lines Shrink   text_wrap 0, and text_fit shrink unless it is fill
//   Long lines Wrap     text_wrap 1, text_fit as it is
//   Long lines Keep     text_wrap 0, text_fit none: the lines keep their
//   size                size where they do not fit
const MARGINS = { none: 0, tight: .5, normal: 1, roomy: 2 };
const SIDES = ['top', 'bottom', 'left', 'right'];
function marginPreset(d) {
    const def = C.defaults;
    return Object.keys(MARGINS).find(k => SIDES.every(s => +d['margin_' + s] === Math.round(def['margin_' + s] * MARGINS[k]))) || 'custom';
}
function setFrame(d, kind) {
    const base = baseDesign(d.label_size);
    if (kind === 'none') {
        Object.assign(d, { border_thickness: '0', border_roundness: '0', border_distance_x: '0', border_distance_y: '0' });
        for (const k of ['margin_top', 'margin_bottom', 'margin_left', 'margin_right']) d[k] = base[k];
        return;
    }
    const was = frameOf(d);
    Object.assign(d, { border_roundness: kind === 'rounded' ? (+d.border_roundness ? d.border_roundness : '24') : '0' });
    if (was === 'none') Object.assign(d, { border_thickness: '5', border_distance_x: '20', border_distance_y: '14' });
    fitMarginsToFrame(d);
}
// The frame is drawn at a distance from the edge of the label, the margins
// grow with it to keep the text inside
function fitMarginsToFrame(d) {
    const x = +d.border_distance_x + +d.border_thickness + 25, y = +d.border_distance_y + +d.border_thickness + 21;
    d.margin_left = d.margin_right = String(Math.max(x, +d.margin_left || 0, +d.margin_right || 0));
    d.margin_top = d.margin_bottom = String(Math.max(y, +d.margin_top || 0, +d.margin_bottom || 0));
}
// Details of a frame other than those it starts with are shown
const frameCustom = d => frameOf(d) !== 'none' && (d.border_thickness !== '5' || d.border_distance_x !== '20' || d.border_distance_y !== '14'
    || (frameOf(d) === 'rounded' && d.border_roundness !== '24'));
const FRAME_KEYS = [['border_thickness', 'Line'], ['border_distance_x', 'Sides'], ['border_distance_y', 'Top, bottom'], ['border_roundness', 'Corners']];
const largest = d => Math.max(...d.text.map(l => +l.size || 0), 1);

function toggleAdvanced(open = !ui.adv) {
    ui.adv = open;
    $('#adv').hidden = !open;
    $('#stage').classList.toggle('adv-open', open);
    $('#advBtn').setAttribute('aria-expanded', String(open));
    $('#advBtn').classList.toggle('on', open);
    if (open) renderAdvanced();
    fitLabel();
}

function seg(attr, options, on, extra = '') {
    return `<span class="seg"${extra}>${options.map(([v, label, title, off]) => `<button type="button" data-${esc(attr)}="${esc(v)}" class="${on === v ? 'on' : ''}"${title ? ` title="${esc(title)}" aria-label="${esc(title)}"` : ''}${off ? ' disabled' : ''}>${label}</button>`).join('')}</span>`;
}

export function renderAdvanced() {
    const box = $('#adv'), d = S.edit.design;
    if (!box || box.hidden) return;
    const used = [...new Set(d.text.map(l => familyOf(l.font)))], fam = used.length === 1 ? used[0] : '';
    const fit = textFit(d), auto = fit === 'fill';
    const why = wrapWhy(d);
    // Lines that cannot wrap here keep the wish, it holds again e.g. on another tape
    const longLines = d.text_wrap === '1' && !why ? 'wrap' : fit === 'none' ? 'keep' : 'shrink';
    const frame = frameOf(d), frameMore = frame !== 'none' && (ui.customFrame || frameCustom(d));
    const room = hasRoom(d), preset = marginPreset(d), dpi = dpiOf(d.label_size);
    const endless = SIZES[d.label_size] && isEndless(SIZES[d.label_size]);
    const s = SIZES[d.label_size];
    const caps = sizeCaps(d.label_size), pc = printerCut(), own = !!d.cut;
    const custom = ui.customMargins || preset === 'custom';
    box.innerHTML = `<div class="adv-h"><h4>Advanced</h4><button type="button" class="iconbtn" data-close-adv aria-label="Close">${icon('x', '')}</button></div>
        <div class="ft">
            <span class="k">Font</span><span class="row">${fontButton('data-afont aria-label="Font of all lines"', !fam ? 'Several fonts' : familyNamed(fam) ? fam : `${fam}, not installed`, 'fsel field-like')}<small>all lines</small></span>
            <span class="k">Text size</span><span class="row"><span class="az"><button type="button" class="azb" data-all="-1" aria-label="Smaller">${icon('minus', '')}</button><input class="anum num" data-allin value="${esc(largest(d))}" inputmode="numeric" aria-label="Text size of all lines"><button type="button" class="azb" data-all="1" aria-label="Larger">${icon('plus', '')}</button></span>
                <button type="button" class="auto${auto ? ' on' : ''}" data-auto aria-pressed="${auto}" title="As large as fits on the label, the lines keep the ratio of their sizes">Auto</button><small>all lines</small></span>
            <span class="k">Direction</span><span class="row">${seg('dir', [['across', icon('type') + 'Across'], ['along', `<svg class="ic s rot" aria-hidden="true"><use href="#i-type"/></svg>Along`]], runsAlong(d) ? 'along' : 'across')}
                <button type="button" class="tb${d.mirror === '1' ? ' on' : ''}" data-mirror aria-pressed="${d.mirror === '1'}" title="Mirrored, to be read through clear tape behind glass" aria-label="Mirrored">${icon('mirror')}</button></span>
            <span class="k${room ? '' : ' off'}">Position</span>${seg('valign', [['top', icon('vtop'), 'Top', !room], ['center', icon('vpos'), 'Middle', !room], ['bottom', icon('vbottom'), 'Bottom', !room]], verticalAlign(d), room ? '' : ` title="${esc(NO_ROOM)}"`)}
            <span class="k">Long lines</span>${seg('long', [['shrink', 'Shrink', 'Smaller where they do not fit'], ['wrap', 'Wrap', why || 'Wrapped into several lines', !!why], ['keep', 'Keep size', 'The lines keep their size, also where they do not fit']], longLines)}
            <span class="k">Frame</span><span class="row wrap">${seg('frame', [['none', 'None'], ['square', 'Square'], ['rounded', 'Rounded']], frame)}${frame !== 'none' ? swatchHTML('data-fcolor', d.border_color, onTape(d), 'the frame') : ''}<button type="button" class="custom${frameMore ? ' on' : ''}" data-fcustom aria-expanded="${!!frameMore}">Custom</button></span>
            ${frameMore ? `<span class="k"></span><span class="mm4 frame4">${FRAME_KEYS.map(([k, n]) => `<label${k === 'border_roundness' && frame !== 'rounded' ? ' class="off" title="Only for rounded frames"' : ''}><small>${esc(n)}</small><input class="in num" type="number" min="${k === 'border_thickness' ? '0.1' : '0'}" max="${k === 'border_thickness' ? '5' : '20'}" step="${k === 'border_thickness' ? '0.1' : '0.5'}" data-fk="${esc(k)}" value="${esc(dotsToMm(d[k], dpi))}"${k === 'border_roundness' && frame !== 'rounded' ? ' disabled' : ''} aria-label="${esc(n)} of the frame in mm"></label>`).join('')}<small class="unit">mm</small></span>
            <span class="k"></span><small class="muted fnote">Sides, top and bottom: the distance from the edge of the label. The margins grow so that the text stays inside.</small>` : ''}
            <span class="k">Margins</span><span class="row wrap">${seg('margins', [['none', 'None'], ['tight', 'Tight'], ['normal', 'Normal'], ['roomy', 'Roomy']], custom ? '' : preset)}<button type="button" class="custom${custom ? ' on' : ''}" data-custom>Custom</button></span>
            ${custom ? `<span class="k"></span><span class="mm4">${SIDES.map(side => `<label><small>${esc(side[0].toUpperCase() + side.slice(1))}</small><input class="in num" type="number" min="0" max="20" step="0.5" data-margin="${esc(side)}" value="${esc(dotsToMm(d['margin_' + side], dpi))}"></label>`).join('')}<small class="unit">mm</small></span>` : ''}
            <span class="k${hasLength(d) ? '' : ' off'}">Length</span><span class="row">${hasLength(d)
                ? `${seg('len', [['auto', 'Auto'], ['fixed', 'Fixed']], hasFixedLength(d) || ui.fixedLength ? 'fixed' : 'auto')}${hasFixedLength(d) || ui.fixedLength ? `<input class="in num" type="number" min="${esc(s.length[0])}" max="${esc(s.length[1])}" step="0.5" data-length value="${esc(d.label_length)}" placeholder="mm" aria-label="Length in mm"><small>mm</small>` : ''}`
                : `<small>${isDieCut(d.label_size) ? 'Die-cut labels have their own length' : flagOn(d) ? 'Cable flags are as long as their content' : 'As long as the content'}</small>`}</span>
            ${endless ? `<span class="k">Cable flag</span><span class="row"><button type="button" class="auto${flagOn(d) ? ' on' : ''}" data-flag aria-pressed="${flagOn(d)}">${flagOn(d) ? 'On' : 'Off'}</button>${flagOn(d) ? `<input class="in num" type="number" min="1" max="50" step="0.5" data-cable value="${esc(d.cable_diameter)}" placeholder="6.5" aria-label="Diameter of the cable in mm"><small>mm cable</small>` : ''}</span>
                ${flagOn(d) ? '<span class="k"></span><small class="muted fnote" id="flagHint">Place the cable on the triangles, fold both ends together and press them firmly.</small>' : ''}` : ''}
            <span class="k">Cutting</span><span class="row cutrow">${noCutter(caps) ? `<small>${esc(noCutterWhy(caps))}</small>`
                : own ? `<select class="in" data-cut aria-label="Cutting of this label"><option value="">Like the printer: ${esc(cutShort(pc, printerCaps()).toLowerCase())}</option>${CUTS.map(([v, n]) => `<option value="${esc(v)}"${d.cut === v ? ' selected' : ''}${v === 'none' && neverWhy(caps) ? ' disabled' : ''}>${esc(n)}</option>`).join('')}</select>`
                : `<small>Like the printer</small><button type="button" class="custom" data-cutchange>Change</button>`}</span>
            ${cutsHTML(d, caps)}
            ${own && !noCutter(caps) && (caps.half_cut || caps.chain) ? `<span class="k"></span><span class="row">${caps.half_cut ? `<button type="button" class="auto${halfOf(cutOf(d), caps) ? ' on' : ''}" data-half>Half cuts</button>` : ''}${caps.chain ? `<button type="button" class="auto${chainOf(cutOf(d), caps) ? ' on' : ''}" data-chain${d.cut === 'none' ? ' disabled' : ''}>Chain printing</button>` : ''}</span>` : ''}
        </div>`;
    $('[data-close-adv]', box).onclick = () => toggleAdvanced(false);
    $('[data-afont]', box).onclick = () => openFontMenu({
        owner: 'all', anchor: () => $('#adv:not([hidden]) [data-afont]'), label: 'Font of all lines',
        state: () => { const lines = S.edit.design.text, names = new Set(lines.map(l => familyOf(l.font))); return { family: names.size === 1 ? [...names][0] : '', faces: lines.map(l => faceOf(l.font)) }; },
        // Each line keeps its weight and width where the family has them
        pick: name => apply(x => x.text.forEach(l => familyLine(l, name))),
        weight: w => apply(x => x.text.forEach(l => weightLine(l, w)), { merge: 'weight' }),
        width: w => apply(x => x.text.forEach(l => widthLine(l, w))),
    });
    const scaleAll = target => apply(x => {
        const k = target / largest(x);
        x.text.forEach(l => { l.size = String(Math.max(4, Math.round(+l.size * k))); });
        if (textFit(x) === 'fill') setTextFit(x, 'shrink');
    }, { merge: 'all' });
    $$('[data-all]', box).forEach(b => b.onclick = () => scaleAll(Math.max(4, largest(S.edit.design) + +b.dataset.all * allStep())));
    const allIn = $('[data-allin]', box);
    allIn.addEventListener('change', () => { const v = Math.round(+allIn.value); if (v > 0) scaleAll(Math.min(999, v)); else renderAdvanced(); });
    allIn.addEventListener('keydown', e => { if (e.key === 'Enter') allIn.blur(); });
    $('[data-auto]', box).onclick = () => apply(x => setTextFit(x, textFit(x) === 'fill' ? 'shrink' : 'fill'));
    $$('[data-dir]', box).forEach(b => b.onclick = () => apply(x => setRunsAlong(x, b.dataset.dir === 'along')));
    $('[data-mirror]', box).onclick = () => apply(x => { x.mirror = x.mirror === '1' ? '0' : '1'; });
    $$('[data-valign]', box).forEach(b => b.onclick = () => apply(x => { x.vertical_align = b.dataset.valign; }));
    $$('[data-long]', box).forEach(b => b.onclick = () => apply(x => {
        if (b.dataset.long === 'wrap') { x.text_wrap = '1'; return; }
        x.text_wrap = '0';
        if (b.dataset.long === 'keep') setTextFit(x, 'none');
        else if (textFit(x) !== 'fill') setTextFit(x, 'shrink');
    }));
    $$('[data-frame]', box).forEach(b => b.onclick = () => apply(x => setFrame(x, b.dataset.frame)));
    $('[data-fcustom]', box).onclick = () => {
        ui.customFrame = !frameMore;
        if (ui.customFrame && frameOf(S.edit.design) === 'none') apply(x => setFrame(x, 'square'));
        else renderAdvanced();
    };
    $$('[data-fk]', box).forEach(inp => inp.addEventListener('change', () => {
        const mm = parseFloat(inp.value);
        if (!(mm >= 0)) return;
        apply(x => { x[inp.dataset.fk] = String(Math.max(inp.dataset.fk === 'border_thickness' ? 1 : 0, mmToDots(mm, dpiOf(x.label_size)))); fitMarginsToFrame(x); });
    }));
    const fcolor = $('[data-fcolor]', box);
    if (fcolor) fcolor.onclick = () => openColors(fcolor, { color: d.border_color, d: onTape(d), what: 'the frame', pick: c => apply(x => { x.border_color = c; }), box });
    $$('[data-margins]', box).forEach(b => b.onclick = () => {
        ui.customMargins = false;
        apply(x => SIDES.forEach(side => { x['margin_' + side] = String(Math.round(C.defaults['margin_' + side] * MARGINS[b.dataset.margins])); }));
    });
    $('[data-custom]', box).onclick = () => { ui.customMargins = !custom; renderAdvanced(); };
    $$('[data-margin]', box).forEach(inp => inp.addEventListener('input', () => {
        const mm = parseFloat(inp.value);
        if (!(mm >= 0)) return;
        S.edit.design['margin_' + inp.dataset.margin] = String(mmToDots(mm, dpiOf(S.edit.design.label_size)));
        changed(false);
    }));
    $$('[data-len]', box).forEach(b => b.onclick = () => {
        ui.fixedLength = b.dataset.len === 'fixed';
        if (!ui.fixedLength) apply(x => { x.label_length = ''; });
        else { renderAdvanced(); const inp = $('[data-length]', box); if (inp) inp.focus(); }
    });
    const len = $('[data-length]', box);
    if (len) len.addEventListener('change', () => {
        const v = len.value.trim();
        if (v !== '' && !(+v > 0)) return;
        apply(x => { x.label_length = v; });
    });
    const flag = $('[data-flag]', box);
    if (flag) flag.onclick = () => apply(x => { x.cable_flag = flagOn(x) ? '0' : '1'; });
    const cable = $('[data-cable]', box);
    if (cable) cable.addEventListener('input', () => { if (cable.validity.badInput) return; S.edit.design.cable_diameter = cable.value.trim(); changed(false); });
    const change = $('[data-cutchange]', box);
    if (change) change.onclick = () => apply(x => { const c = cutOf(x); Object.assign(x, { cut: c.cut, half_cut: c.half_cut, chain: c.chain }); });
    const cut = $('[data-cut]', box);
    if (cut) cut.onchange = () => apply(x => {
        const c = cutOf(x);
        Object.assign(x, cut.value ? { cut: cut.value, half_cut: c.half_cut, chain: c.chain } : { cut: '', half_cut: '', chain: '' });
    });
    const half = $('[data-half]', box);
    if (half) half.onclick = () => apply(x => { x.half_cut = halfOf(cutOf(x), caps) ? '0' : '1'; });
    const chain = $('[data-chain]', box);
    if (chain) chain.onclick = () => apply(x => { x.chain = chainOf(cutOf(x), caps) ? '0' : '1'; });
    refreshFontMenu();
}

// --- Printing and saving ----------------------------------------------------------------------

export function copies() {
    return Math.min(100, Math.max(1, parseInt($('#copies').value, 10) || 1));
}

export async function printNow() {
    const E = S.edit;
    if ($('#print').disabled) return;
    const item = editItem(E);
    const values = valuesNow(previewValues(E), defaults(item), item);
    const entry = await printJob({ title: E.title, design: madeDesign(E), values, copies: copies(), source: E.origin }, announce);
    return entry;
}

function announce(entry) {
    if (untold(entry)) return;
    hideToast();
    const n = entry.copies, what = `${n} ${n === 1 ? 'label' : 'labels'}`;
    if (entry.state === 'printed') {
        toast(`Printed ${what}: ${entryName(entry)}`, { label: 'Print again', run: () => printJob(Object.assign({}, entry), announce) });
        return;
    }
    const maybe = entry.state === 'unknown', head = maybe ? 'Maybe not printed' : 'Not printed';
    openLayer(`<div class="dialog narrow" role="alertdialog" aria-label="${head}">
        <div class="okc bad">${icon('x', '')}</div><h2>${head}</h2><p>${esc(entry.message)}</p>
        <div class="acts"><button class="btn" type="button" data-close>Close</button><button class="btn primary" type="button" id="retry">${icon('refresh')}${maybe ? 'Print again' : 'Try again'}</button></div></div>`, 'center');
    $('#retry').onclick = () => { closeLayer(true); printJob(Object.assign({}, entry), announce); };
}

// Saved labels are saved again, a new one asks for its name and tags first
export function saveNow(saveDialog, then = null) {
    const E = S.edit;
    const over = E.origin && E.origin.kind === 'saved' ? E.origin.id : null;
    const saveTo = over => async (title, replacing, tags) => {
        const made = madeDesign(E);
        const name = await saveLabel(title, made, over || replacing, tags);
        const now = S.saved.find(s => s.name === name);
        E.title = title;
        E.origin = { kind: 'saved', id: name, mtime: now && now.mtime };
        if (made === E.design) E.made = null;
        E.kept = keptAs(E);
        if (S.edit === E) { storeEdit(); if ($('#stage')) { updateActions(); drawTape(); } }
        // A label with blanks is filled in and printed in Templates
        toast(`Saved "${title}"${over ? '' : ' to your labels'} ${savedAs(made)}`, { label: blanksOf(E.design).length ? 'Fill in and print' : 'Show in Templates', run: () => { location.hash = 'fill/' + encodeURIComponent(name); } });
        if (then) then();
    };
    // A new label is offered the name of its first line
    const first = E.design.text.map(l => String(l.text).replace(/\{\{[^}]*\}\}|\n/g, ' ').replace(/\s+/g, ' ').trim()).find(Boolean);
    const asNew = () => saveDialog(E.title === 'New label' && first ? first : E.title, saveTo(null), madeDesign(E));
    if (!over) { asNew(); return; }
    savedElsewhere(E.origin).then(newer => {
        if (newer) askReplace(E.title, () => saveTo(over)(E.title, null).catch(e => toast(e.message)), asNew);
        else return saveTo(over)(E.title, null);
    }).catch(e => toast(e.message));
}
// Whether a saved label was saved again, e.g. in another browser, since it
// was opened or saved here
async function savedElsewhere(origin) {
    if (!origin.mtime) return false;
    await loadSaved();
    const now = S.saved.find(s => s.name === origin.id);
    return !!now && now.mtime !== origin.mtime;
}
function askReplace(title, replace, asNew) {
    openLayer(`<div class="dialog narrow" role="alertdialog" aria-label="Replace the newer version?">
        <h2>Replace the newer version?</h2><p class="muted">"${esc(title)}" was saved again elsewhere since it was opened here.</p>
        <div class="acts"><button class="btn" type="button" data-close>Cancel</button><button class="btn" type="button" id="saveNew">Save as a new label</button><button class="btn" type="button" id="saveOver">Replace it</button></div></div>`, 'center');
    $('#saveNew').onclick = () => { closeLayer(true); asNew(); };
    $('#saveOver').onclick = () => { closeLayer(true); replace(); };
    $('#saveNew').focus();
}

// The editor follows a new printer or tape: the label is placed anew
export function printerChanged() {
    if (!S.edit || !$('#stage')) return;
    placeEdit(S.edit);
    if (S.edit.filledFrom !== undefined && !isSheet(onTape(S.edit.design).label_size)) {
        S.edit.copies = S.edit.filledFrom;
        $('#copies').value = S.edit.copies;
        delete S.edit.filledFrom;
    }
    drawTape();
    drawPrinter();
    if (ui.adv) renderAdvanced();
    renderToolbar();
    markBusy();
    drawPreview();
}

mountMedia({ hooks, ui, apply, pick, changed, markBusy, renderToolbar, redraw: () => drawPreview() });
