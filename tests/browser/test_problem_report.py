"""Report a problem in the Settings under About: the bug form on GitHub with
the details of the server filled in, opened only by the user, and the
details to copy."""
import json
from urllib.request import urlopen

import pytest

from tests.browser.test_editor import wait_for_preview
from tests.browser.test_settings import open_settings


@pytest.fixture
def page(browser, server):
    context = browser.new_context(viewport={'width': 1600, 'height': 1000}, locale='en-US')
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(server + '/labeldesigner/')
    page.wait_for_selector('#field .ln')
    wait_for_preview(page)
    yield page
    context.close()
    assert errors == []


def test_report_a_problem_opens_the_filled_in_form_and_copies_the_details(page, server):
    requests = []
    page.on('request', lambda r: requests.append(r.url))
    open_settings(page, 'about')
    page.wait_for_selector('#setDetails:not([hidden])')
    with urlopen(f'{server}/labeldesigner/api/problem_report') as r:
        expected = json.load(r)
    link = page.locator('#setReport')
    assert link.inner_text() == 'Report a problem'
    assert (link.get_attribute('href'), link.get_attribute('target'), link.get_attribute('rel')) == (expected['url'], '_blank', 'noopener')
    assert expected['details']['printer'] == 'QL-800 as the demo printer'
    assert page.inner_text('#setDetails') == expected['text']
    # Drawn again for another appearance, the details are not asked again
    page.click('button[data-theme="dark"]')
    assert page.locator('#setReport').get_attribute('href') == expected['url']
    assert page.inner_text('#setDetails') == expected['text']
    assert len([u for u in requests if '/api/problem_report' in u]) == 1
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    page.click('#setReportCopy')
    page.wait_for_function('text => navigator.clipboard.readText().then(t => t === text)', arg=expected['text'])
    # Nothing went anywhere but to this server
    assert [u for u in requests if not u.startswith(server)] == []
    # Asked first like every link to the internet, the form opens in a tab of its own
    page.evaluate('() => { window.open = (...args) => { window.opened = args; return null; }; }')
    page.click('#setReport')
    page.wait_for_selector('.dialog.ask h2:text-is("Open github.com?")')
    page.click('#leaveGo')
    assert page.evaluate('() => window.opened') == [expected['url'], '_blank', 'noopener']
