// Codes read with the camera of the scan station. The reader (zxing-wasm)
// is loaded from the server only when the camera is switched on.

const READER = new URL('./vendor/zxing-wasm/reader/index.js', import.meta.url).href;
const WASM = new URL('./vendor/zxing-wasm/zxing_reader.wasm', import.meta.url).href;
// The codes Labelburg prints (PZN is a Code 39), and UPC-E of products
const FORMATS = ['QRCode', 'MicroQRCode', 'RMQRCode', 'DataMatrix', 'Aztec', 'PDF417', 'EAN13', 'EAN8', 'UPCA', 'UPCE', 'Code128', 'Code39', 'PZN', 'ITF', 'Codabar'];

export const REFUSED = 'The browser did not allow the camera. Allow it in the address bar and try again.';
export const NO_CAMERA = 'No camera found.';
export const NOT_STARTED = 'The camera could not be started. Close other apps that use it and try again.';
export const NO_READER = 'The code reader could not be loaded. Load the page again and try again.';
export const ENDED = 'The camera stopped. Press "Use the camera" to start it again.';

export const cameraBlocked = () => !isSecureContext || !navigator.mediaDevices;

let reader = null;
function zxing() {
    // The reader's own default fetches the wasm from a CDN
    if (!reader) reader = import(READER).then(m => {
        m.prepareZXingModule({ overrides: { locateFile: (path, prefix) => path.endsWith('.wasm') ? WASM : prefix + path }, fireImmediately: false });
        return m;
    });
    return reader;
}

// The text of the first code in source, an ImageData or a canvas, or null
export async function decode(source) {
    try {
        const { readBarcodes } = await zxing();
        const image = source instanceof ImageData ? source : source.getContext('2d').getImageData(0, 0, source.width, source.height);
        const [found] = await readBarcodes(image, { formats: FORMATS, maxNumberOfSymbols: 1 });
        return found && found.text ? found.text : null;
    } catch (e) {
        // Tried again the next time the camera is switched on
        reader = null;
        throw e;
    }
}

function problem(e) {
    if (e && (e.name === 'NotAllowedError' || e.name === 'SecurityError')) return REFUSED;
    if (e && (e.name === 'NotFoundError' || e.name === 'OverconstrainedError')) return NO_CAMERA;
    return NOT_STARTED;
}

const openStream = () => navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
const ended = stream => { if (stream) stream.getTracks().forEach(track => track.stop()); };

// The camera's picture in video and each code read in it passed to
// onCode(text), a frame about every `every` ms. A code still in view is
// taken once: the same text seen less than `again` ms before is left out.
// No frame is read while paused() is true, and the time paused does not
// count for the codes seen before. onError(message) when the camera or the
// reader cannot start, or the camera ends, e.g. unplugged.
export function camera(video, onCode, { clock = performance, again = 3000, every = 150, open = openStream, read = null, paused = () => false, onError = () => { } } = {}) {
    let stopped = false, timer = null, pausedAt = null;
    const seen = new Map(), canvas = document.createElement('canvas');
    const frame = async () => {
        const w = video.videoWidth, h = video.videoHeight;
        if (video.readyState < 2 || !w || !h) return null;
        if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
        const g = canvas.getContext('2d', { willReadFrequently: true });
        g.drawImage(video, 0, 0, w, h);
        return decode(g.getImageData(0, 0, w, h));
    };
    const stop = () => {
        stopped = true;
        clearTimeout(timer);
        ended(video.srcObject);
        video.srcObject = null;
    };
    const fail = message => { if (!stopped) { stop(); onError(message); } };
    const tick = async () => {
        let text = null;
        if (paused()) {
            if (pausedAt === null) pausedAt = clock.now();
        } else {
            if (pausedAt !== null) {
                const gap = clock.now() - pausedAt;
                for (const [t, when] of seen) seen.set(t, when + gap);
                pausedAt = null;
            }
            try {
                text = await (read || frame)();
            } catch (e) {
                fail(NO_READER);
                return;
            }
        }
        if (stopped) return;
        if (text) {
            const now = clock.now(), at = seen.get(text);
            for (const [t, when] of seen) if (now - when >= again) seen.delete(t);
            seen.set(text, now);
            if (at === undefined || now - at >= again) onCode(text);
        }
        timer = setTimeout(tick, every);
    };
    // Stopped while the browser asks for permission, the camera is let go at once
    Promise.resolve().then(open).then(stream => {
        if (stopped) { ended(stream); return; }
        video.srcObject = stream;
        if (stream) stream.getVideoTracks().forEach(track => track.addEventListener('ended', () => fail(ENDED)));
        video.play().catch(() => fail(NOT_STARTED));
        tick();
    }, e => {
        if (!stopped) { stopped = true; onError(problem(e)); }
    });
    return { stop };
}
