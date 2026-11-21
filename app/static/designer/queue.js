// The queue of the printer chosen, at the place of the print message: the
// job printing, those waiting behind it, a line held by a job that did not
// print. Read while it has jobs or this browser waits for one of its own.

import { $, API, S, QUEUE, esc, icon, jobText, lineBusy, post, toast, queueBack } from './core.js';

// --- As markup, for this panel and the list of every printer -------------------

const labels = n => `${n} ${n === 1 ? 'label' : 'labels'}`;
export function printingText(job) {
    const how = job.progress ? jobText(job.progress) : '';
    return `Printing ${job.title}${how ? ', ' + how.charAt(0).toLowerCase() + how.slice(1) : ''}`;
}
export const heldText = line => `Not printed: ${line.held.replace(/\.$/, '')}. ${line.waiting.length} waiting.`;
// A job waiting: its first label, title, labels, where it came from
export function jobRow(job) {
    return `<div class="q-row"><span class="q-img">${job.preview ? `<img alt="" src="${esc(job.preview)}">` : ''}</span>
        <span class="grow"><b>${esc(job.title)}</b><small>${esc(labels(job.labels) + (job.from ? `, from ${job.from}` : ''))}</small></span>
        <button class="iconbtn" type="button" data-remove="${esc(job.id)}" title="Remove from the queue" aria-label="Remove from the queue">${icon('x')}</button></div>`;
}
// A line ({ printing, waiting, held }): one line, the jobs waiting below it
// where open; empty where nothing prints, waits or holds
export function lineHTML(line, open = false) {
    const n = line.waiting.length;
    if (line.held) return `<div class="q-line held"><span>${esc(heldText(line))}</span><button class="tbtn" type="button" data-continue>Continue</button></div>`;
    if (!line.printing && !n) return '';
    const more = n ? `<button class="q-more" type="button" data-more aria-expanded="${open}">${esc(n)} waiting${icon('down')}</button>` : '';
    return `<div class="q-line"><span>${line.printing ? esc(printingText(line.printing)) : ''}</span>${more}</div>`
        + (open && n ? `<div class="q-list">${line.waiting.map(jobRow).join('')}</div>` : '');
}

// --- Reading ---------------------------------------------------------------------

let reading = null, again = null, open = false, drawn = '', changed = () => { };

export const queueBusy = () => lineBusy(S.printer);

async function readOnce() {
    const p = S.printer;
    if (!p || !p.path || document.hidden) return;
    try {
        const r = await fetch(`${API}/queue?printer=${encodeURIComponent(p.path)}`);
        const data = r.ok ? await r.json() : null;
        if (data && S.printer && S.printer.path === p.path) {
            Object.assign(QUEUE, { path: p.path, status: p, line: { printing: data.printing || null, waiting: data.waiting || [], held: data.held || null } });
        }
    } catch (e) {
        // The next one reads it
    }
}
// One read at a time; asked while one runs, another follows it
export function readQueue() {
    if (reading) return again || (again = reading.then(() => { again = null; return readQueue(); }));
    reading = readOnce().finally(() => { reading = null; drawQueue(); });
    return reading;
}

// --- The panel -------------------------------------------------------------------

export function drawQueue() {
    const el = $('#queue'), p = S.printer;
    const line = p && QUEUE.path === p.path && QUEUE.line;
    if (!line || !line.waiting.length || line.held) open = false;
    const html = line ? lineHTML(line, open) : '';
    if (html !== drawn) {
        drawn = html;
        // Drawn anew while one of its buttons has the focus, that one keeps it
        const f = document.activeElement, d = el.contains(f) ? f.dataset : {};
        const focused = d.remove ? `[data-remove="${CSS.escape(d.remove)}"]` : 'more' in d ? '[data-more]' : 'continue' in d ? '[data-continue]' : '';
        el.innerHTML = html;
        el.classList.toggle('open', open);
        if (focused && $(focused, el)) $(focused, el).focus();
    }
    queueBack();
    changed();
}

// While it has jobs or holds, or this browser waits for one of its own
export function followQueue() {
    drawQueue();
    if (!reading && queueBusy()) readQueue();
}

async function send(path, body) {
    try {
        const r = await post(path, body);
        if (!r.ok) {
            const told = await r.json().catch(() => ({}));
            if (told.message) toast(told.message);
        }
        return r.ok;
    } catch (e) {
        toast(e.message);
        return false;
    }
}

async function clicked(e) {
    const b = e.target.closest('button');
    if (!b || !S.printer) return;
    if ('more' in b.dataset) {
        open = !open;
        drawQueue();
        return;
    }
    if (b.dataset.remove) {
        // Its sender here is told by the list, not by a message of its own
        const id = b.dataset.remove;
        QUEUE.removed.add(id);
        b.disabled = true;
        if (!await send(`/queue/${encodeURIComponent(id)}/remove`)) QUEUE.removed.delete(id);
    } else if ('continue' in b.dataset) {
        b.disabled = true;
        await send('/queue/continue', new URLSearchParams({ printer: S.printer.path }));
    } else return;
    // Drawn anew, also where the line stays as it was: its buttons work again
    drawn = '';
    readQueue();
}

// onChange: the print button follows the queue
export function mountQueue(onChange) {
    changed = onChange;
    QUEUE.read = () => { drawQueue(); return readQueue(); };
    $('#queue').addEventListener('click', clicked);
    setInterval(followQueue, 1500);
    document.addEventListener('visibilitychange', followQueue);
    followQueue();
}
