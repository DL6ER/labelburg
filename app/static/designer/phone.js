// The page on a phone: the same pieces stacked, Advanced, copies and Print
// in a bar at the bottom, the toolbar of what is picked as a sheet above it
// and Advanced as a sheet over the page. The layout itself is main.css.

import { $ } from './core.js';

const PHONE = matchMedia('(max-width: 699.98px)');
export const phone = () => PHONE.matches;

export function mountPhone() {
    const root = document.documentElement;
    // The stage leaves room for the toolbar's sheet, the toast stays above it
    const sheet = new ResizeObserver(() => {
        const bar = $('#ltb');
        root.style.setProperty('--sheet-h', `${phone() && bar && !bar.hidden ? bar.offsetHeight : 0}px`);
    });
    // Advanced is a sheet with the page dimmed behind it on the phone
    const sync = () => {
        const adv = $('#adv'), open = !!adv && !adv.hidden && document.body.dataset.view !== 'settings';
        $('#scrim').hidden = !(open && phone());
        $('#footAdv').classList.toggle('on', open);
        $('#footAdv').setAttribute('aria-expanded', String(open));
        const bar = $('#ltb');
        if (bar && !bar.dataset.watched) { bar.dataset.watched = '1'; sheet.observe(bar); }
        if (!bar || bar.hidden) root.style.setProperty('--sheet-h', '0px');
    };
    new MutationObserver(sync).observe($('#view'), { subtree: true, childList: true, attributes: true, attributeFilter: ['hidden'] });
    new MutationObserver(sync).observe(document.body, { attributes: true, attributeFilter: ['data-view'] });
    PHONE.addEventListener('change', () => { sync(); dispatchEvent(new Event('resize')); });
    $('#footAdv').onclick = () => { const b = $('#advBtn'); if (b) b.click(); };
    $('#scrim').onclick = () => { const b = $('[data-close-adv]'); if (b) b.click(); };
}
