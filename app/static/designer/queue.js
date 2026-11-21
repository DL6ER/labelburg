// The queue of the printer chosen, at the place of the print message: the
// job printing, those waiting behind it, a line held by a job that did not
// print. Read while it has jobs or this browser waits for one of its own.

import { $, API, S, QUEUE, esc, icon, jobText, lineBusy, post, toast, queueBack, printerLabel } from './core.js';

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
export const listHTML = line => `<div class="q-list">${line.waiting.map(jobRow).join('')}</div>`;
// A line ({ printing, waiting, held }): one line, the jobs waiting below it
// where open; empty where nothing prints, waits or holds. A hold is told to
// screen readers too, it stands for the "Not printed" message.
export function lineHTML(line, open = false) {
    const n = line.waiting.length;
    if (!line.printing && !n && !line.held) return '';
    const more = n ? `<button class="q-more" type="button" data-more aria-expanded="${open}">${esc(n)} waiting${icon('down')}</button>` : '';
    const head = line.held ? `<div class="q-line held"><span role="alert">${esc(heldText(line))}</span>${more}<button class="tbtn" type="button" data-continue>Continue</button></div>`
        : `<div class="q-line"><span>${line.printing ? esc(printingText(line.printing)) : ''}</span>${more}</div>`;
    return head + (open && n ? listHTML(line) : '');
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

const shownLine = () => !!S.printer && QUEUE.path === S.printer.path && QUEUE.line;

export function drawQueue() {
    const el = $('#queue');
    const line = shownLine();
    if (!line || !line.waiting.length) open = false;
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

// Opened and closed in place: the line stays, a hold is not told again
function toggle(b) {
    const el = $('#queue'), line = shownLine();
    if (!line) return;
    open = !open;
    b.setAttribute('aria-expanded', open);
    el.classList.toggle('open', open);
    if (open) el.insertAdjacentHTML('beforeend', listHTML(line));
    else if ($('.q-list', el)) $('.q-list', el).remove();
    drawn = lineHTML(line, open);
}

// Remove or Continue (of the printer at path), shared by the panel and the page
async function act(b, path) {
    if (b.dataset.remove) {
        // Its sender here is told by the list, not by a message of its own
        const id = b.dataset.remove;
        QUEUE.removed.add(id);
        b.disabled = true;
        if (!await send(`/queue/${encodeURIComponent(id)}/remove`)) QUEUE.removed.delete(id);
    } else if ('continue' in b.dataset) {
        b.disabled = true;
        await send('/queue/continue', new URLSearchParams({ printer: path }));
    } else return false;
    return true;
}

async function clicked(e) {
    const b = e.target.closest('button');
    if (!b || !S.printer) return;
    if ('more' in b.dataset) {
        toggle(b);
        return;
    }
    if (!await act(b, S.printer.path)) return;
    // Drawn anew, also where the line stays as it was: its buttons work again
    drawn = null;
    readQueue();
}

// onChange: the print button follows the queue
export function mountQueue(onChange) {
    changed = onChange;
    QUEUE.read = () => { drawQueue(); return readQueue(); };
    QUEUE.follow = followQueue;
    $('#queue').addEventListener('click', clicked);
    setInterval(followQueue, 1500);
    document.addEventListener('visibilitychange', followQueue);
    followQueue();
}

// --- The page of every printer's queue (Settings) -----------------------------------

const NOTHING = 'Nothing is printing or waiting.';
let pageBox = null, pageRead = null, pageDrawn = null, lines = null;

const printerOf = path => S.printers.find(p => p.path === path);
const lineName = path => { const p = printerOf(path); return p ? printerLabel(p) : path.replace('file://', ''); };
function cardHTML(l) {
    const held = !!l.held, state = held ? 'Stopped' : l.printing ? 'Printing' : 'Waiting';
    const printing = l.printing && `<div class="q-row"><span class="q-img">${l.printing.preview ? `<img alt="" src="${esc(l.printing.preview)}">` : ''}</span>
        <span class="grow"><b>${esc(printingText(l.printing))}</b></span></div>`;
    return `<section class="set-card q-card" data-queue="${esc(l.printer)}">
        <div class="set-card-h"><span class="d ${held ? 'bad' : ''}"></span><b class="grow">${esc(lineName(l.printer))}</b><small class="muted">${state}</small></div>
        ${held ? `<div class="q-held"><span role="alert">${esc(heldText(l))}</span><button class="tbtn" type="button" data-continue>Continue</button></div>` : ''}
        ${printing || ''}${l.waiting.map(jobRow).join('')}</section>`;
}
function pageHTML() {
    if (!lines) return '';
    const quiet = S.printers.filter(p => !lines.some(l => l.printer === p.path)).map(printerLabel);
    return lines.length ? lines.map(cardHTML).join('') + (quiet.length ? `<p class="muted">${esc(quiet.join(', '))}: nothing printing or waiting.</p>` : '')
        : `<p class="muted">${NOTHING}</p>`;
}
function drawPage(force = false) {
    if (!pageBox || !pageBox.isConnected) return;
    const list = $('#setQueue', pageBox), html = pageHTML();
    if (!force && html === pageDrawn) return;
    pageDrawn = html;
    const f = document.activeElement, d = list.contains(f) ? f.dataset : {};
    const focused = d.remove ? `[data-remove="${CSS.escape(d.remove)}"]` : 'continue' in d ? `[data-queue="${CSS.escape(f.closest('[data-queue]').dataset.queue)}"] [data-continue]` : '';
    list.innerHTML = html;
    if (focused && $(focused, list)) $(focused, list).focus();
}
const pageShown = () => !!pageBox && pageBox.isConnected && pageBox.dataset.part === 'queue' && document.body.dataset.view === 'settings';

// One read at a time, only while the page is shown by a visible tab
async function readPage() {
    if (!pageShown() || document.hidden) return;
    if (pageRead) return pageRead;
    pageRead = (async () => {
        try {
            const r = await fetch(`${API}/queue`);
            const data = r.ok ? await r.json() : null;
            if (data && pageShown()) { lines = data.printers || []; drawPage(); }
        } catch (e) {
            // The next one reads it
        }
    })().finally(() => { pageRead = null; });
    return pageRead;
}

export function queuePage(box) {
    pageBox = box;
    lines = null;
    pageDrawn = null;
    box.innerHTML = `<header class="set-h"><div class="grow"><h2>Print queue</h2><p class="muted">What the printers print and what waits, from this browser, other devices and the API. A waiting label can be removed.</p></div></header>
        <div class="set-list" id="setQueue"></div>`;
    $('#setQueue', box).addEventListener('click', async e => {
        const b = e.target.closest('button'), card = b && b.closest('[data-queue]');
        if (!card || !await act(b, card.dataset.queue)) return;
        // Drawn anew, also where the line stays as it was: its buttons work again
        drawPage(true);
        readPage();
    });
    if (!queuePage.timer) {
        queuePage.timer = setInterval(readPage, 1500);
        document.addEventListener('visibilitychange', readPage);
    }
    readPage();
}
