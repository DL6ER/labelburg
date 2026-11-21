// Calibrating a printer that takes rolls by hand, e.g. the Niimbot B1: it
// prints off to one side by how the roll sits, the server keeps how far its
// labels are moved. Offered at the printer chip and in the Settings.

import { $, $$, esc, icon, S, C, API, post, capsOf, toast, followJob, lineBusy, openLayer, closeLayer, UNREACHABLE } from './core.js';

const svg = paths => `<svg class="ic s" viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
const RULER = svg('<path d="M3 15 15 3l6 6L9 21z"/><path d="m7 11 2 2M10 8l2 2M13 5l2 2"/>');
const ARROWS = { left: 'M19 12H5M11 6l-6 6 6 6', right: 'M5 12h14M13 6l6 6-6 6', up: 'M12 19V5M6 11l6-6 6 6', down: 'M12 5v14M6 13l6 6 6-6' };
const NEW_ROLL = 'A new roll is in the printer. Calibrate the printer so the labels sit straight on it.';

// Another roll than the one calibrated for, of a size offered here: nothing
// prints on another one
const newRoll = p => !!p.roll && p.roll !== (p.calibration || {}).roll && !!(p.tape && p.tape.known);
const tapeOf = p => (p.tape && p.tape.id) || C.default_label_size;

// The button that opens the calibration of printer p, after another roll
// with the hint and a button that dismisses it
export function calibrationHTML(p) {
    if (!p || !capsOf(p.model).calibration) return '';
    const fresh = newRoll(p);
    return `<div class="cal">${fresh ? `<p class="cal-hint">${esc(NEW_ROLL)}</p>` : ''}
        <div class="row"><button class="btn sm ${fresh ? 'attention' : 'soft'}" type="button" data-cal-open>${RULER}Calibrate printer</button>
            ${fresh ? '<button class="btn ghost sm" type="button" data-cal-dismiss>Dismiss</button>' : ''}</div></div>`;
}

// The buttons of calibrationHTML(p) in box; changed() follows a
// calibration kept for p
export function bindCalibration(box, p, changed) {
    const open = $('[data-cal-open]', box), dismiss = $('[data-cal-dismiss]', box);
    if (open) open.onclick = () => openCalibration(p, changed);
    if (dismiss) dismiss.onclick = () => dismissCalibration(p, changed);
}
// The roll in p is kept as the one calibrated for, the hint goes
export async function dismissCalibration(p, changed) {
    if (await postCalibration(p, {})) changed();
}

function form(p, extra, roll = true) {
    const fd = new FormData();
    fd.set('printer', p.path);
    if (C.models[p.model]) fd.set('model', p.model);
    // The roll it holds for, the hint then stays away
    if (roll && p.roll) fd.set('roll', p.roll);
    Object.entries(extra).forEach(([k, v]) => fd.set(k, String(v)));
    return fd;
}

// The answer of the server, null with the reason shown. Kept for the
// printer unless only tried, which tells what it would be.
async function postCalibration(p, extra, shown = toast, trying = false) {
    try {
        const r = await post('/calibration', form(p, trying ? { ...extra, save: 0 } : extra, !trying));
        const j = await r.json().catch(() => ({}));
        if (!r.ok) return shown(j.message || 'The calibration could not be saved'), null;
        if (!trying) [S.printer, ...S.printers].forEach(o => { if (o && o.path === p.path) o.calibration = j.calibration; });
        return j;
    } catch (e) {
        return shown(UNREACHABLE), null;
    }
}

// Whether it printed, else the reason shown, after following it in the queue.
// Moved as in the open dialog, which is not saved yet.
async function printCalibration(p, shown, working, told) {
    try {
        const behind = lineBusy(p);
        const r = await post('/calibration/print', form(p, { label_size: tapeOf(p), ...working }, false));
        const j = await r.json().catch(() => ({}));
        if (!r.ok) shown(j.message || 'The calibration label could not be printed');
        else if (!j.job) return true;
        else {
            const entry = { job: j.job, start: j.start, seen: j.state };
            if (behind && j.state === 'waiting') told('The calibration label waits in the queue.');
            await followJob(entry, () => { if (entry.seen === 'printing') told('Printing the calibration label.'); });
            if (entry.state === 'printed') return true;
            shown(entry.message || 'The calibration label could not be printed');
        }
    } catch (e) {
        shown(UNREACHABLE);
    }
    return false;
}

export async function openCalibration(p, changed) {
    let kept;
    try {
        const r = await fetch(`${API}/calibration?${new URLSearchParams({ printer: p.path, model: p.model, label_size: tapeOf(p) })}`);
        kept = await r.json();
        if (!r.ok) return toast(kept.message || 'The calibration could not be read');
    } catch (e) {
        return toast(UNREACHABLE);
    }
    // Round labels are read as wide ones, on a ring instead of strips
    const round = kept.kind === 'round', narrow = kept.kind !== 'wide' && !round, step = narrow ? 0.5 : 0.25;
    const kind = narrow ? 'narrow' : round ? 'round' : 'wide';
    // Changed here, kept for the printer only once saved. Read numbers count
    // from the correction the last calibration label was printed with.
    let working = { across: kept.across, along: kept.along }, printed = { ...working };
    const readings = ['<option value=""></option>', ...Array.from({ length: 25 }, (_, i) => -3 + i / 4)
        .map(v => `<option value="${esc(v)}">${v > 0 ? '+' : ''}${esc(v)}</option>`)].join('');
    const move = (dir, label) => `<button class="btn sm" type="button" data-move="${esc(dir)}">${svg(`<path d="${esc(ARROWS[dir])}"/>`)}${esc(label)}</button>`;
    const alt = narrow ? 'A calibration label printed 1 mm too far right: read where the black starts at each edge of the label'
        : round ? 'A calibration label printed 1 mm too far right: the white ring is wider left of the circle than right of it'
            : 'A calibration label printed 1 mm too far right: the white strip left of the frame is wider than the one right of it';
    openLayer(`<div class="dialog narrow cal-dialog" role="dialog" aria-label="Calibrate printer">
        <div class="dialog-h"><div><h2>Calibrate printer</h2><p class="muted">Print a calibration label, then look at it as the picture shows.</p></div><span class="sp"></span>
            <button class="iconbtn" type="button" data-close aria-label="Close">${icon('x', '')}</button></div>
        <img class="cal-example" alt="${esc(alt)}" src="${esc(`${API}/calibration/example.png?${new URLSearchParams({ printer: p.path, model: p.model, kind })}`)}">
        <button class="btn primary" type="button" data-cal-print>${icon('printer')}Print calibration label</button>
        ${narrow ? `<p>Where does the black start at the edge of the label? Enter the number next to that point, on each side.</p>
        <div class="row"><label class="cal-read">Left <select class="in" id="calLeft">${readings}</select></label>
            <label class="cal-read">Right <select class="in" id="calRight">${readings}</select></label>
            <button class="btn sm" type="button" id="calApply">Apply</button></div>`
        : `<p>${round ? 'Compare the white ring left and right of the circle.' : 'Compare the white strips left and right of the frame.'}</p>`}
        <p id="calResult" class="cal-result" aria-live="polite"></p>
        <div class="row">${move('left', `${step} mm`)}${move('right', `${step} mm`)}</div>
        <p class="muted small">${round ? 'Compare the white ring above the circle with the one below it.' : 'Compare the white strip above the top line with the one below the bottom line.'}</p>
        <div class="row">${move('up', '0.25 mm')}${move('down', '0.25 mm')}</div>
        <div class="row"><span id="calNow" class="small"></span><span class="sp"></span>
            <button class="btn ghost sm" type="button" id="calReset">No fine correction</button></div>
        <div class="acts"><button class="btn" type="button" id="calCancel" data-close>Cancel</button>
            <button class="btn primary" type="button" id="calSave">${icon('save')}Save</button></div></div>`, 'center');
    const box = $('#layer'), result = $('#calResult', box);
    const say = (text, warn = false) => { result.textContent = text; result.classList.toggle('warn', warn); };
    const mm = v => +Math.abs(v).toFixed(2);
    const now = () => {
        const parts = [working.across ? `${mm(working.across)} mm to the ${working.across < 0 ? 'left' : 'right'}` : '',
            working.along ? `${mm(working.along)} mm ${working.along < 0 ? 'up' : 'down'}` : ''].filter(Boolean);
        $('#calNow', box).textContent = parts.length ? `Labels are moved ${parts.join(' and ')}.` : 'Labels are not moved.';
    };
    now();
    // Worked out by the server, not kept yet. One at a time, each counts
    // from the one before.
    const steps = () => $$('[data-move], #calReset, #calApply, #calSave', box);
    const tryOut = async extra => {
        steps().forEach(b => { b.disabled = true; });
        const j = await postCalibration(p, { across: working.across, along: working.along, ...extra }, text => say(text, true), true);
        steps().forEach(b => { b.disabled = false; });
        if (!j) return null;
        working = { across: j.calibration.across, along: j.calibration.along };
        now();
        return j;
    };
    // Once at a time, a second click would print a second label
    const printButton = $('[data-cal-print]', box);
    printButton.onclick = async () => {
        printButton.disabled = true;
        say('Printing the calibration label.');
        const at = { ...working };
        if (await printCalibration(p, text => say(text, true), at, say)) {
            printed = at;
            say('');
        }
        printButton.disabled = false;
    };
    if (narrow) $('#calApply', box).onclick = async () => {
        const left = $('#calLeft', box), right = $('#calRight', box);
        if (!left.value || !right.value) return say('Choose the number on each side.', true);
        const before = printed.across;
        if (!await tryOut({ left: left.value, right: right.value, across: before })) return;
        // Read on a label printed before, applied again it would move the labels again
        left.value = right.value = '';
        const moved = working.across - before, n = mm(moved);
        say(moved ? `The printer prints ${n} mm too far ${moved < 0 ? 'right' : 'left'}. Labels are moved ${n} mm to the ${moved < 0 ? 'left' : 'right'} once you save.`
            : 'The labels sit straight.');
    };
    $$('[data-move]', box).forEach(b => b.onclick = () => {
        const d = b.dataset.move, along = d === 'up' || d === 'down';
        const by = along ? (d === 'down' ? 0.25 : -0.25) : (d === 'right' ? step : -step);
        say('');
        tryOut(along ? { along: working.along + by } : { across: working.across + by });
    });
    $('#calReset', box).onclick = () => { say(''); tryOut({ across: 0, along: 0 }); };
    $('#calSave', box).onclick = async () => {
        if (!await postCalibration(p, { across: working.across, along: working.along }, text => say(text, true))) return;
        closeLayer(true);
        changed();
    };
}
