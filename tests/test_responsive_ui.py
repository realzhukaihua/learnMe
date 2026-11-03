"""Public desktop/mobile assets with intercepted HTTP and a synthetic WebSocket.

No console, credentials, model, real messages, memory or service is touched.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright, expect


WEB = Path(__file__).resolve().parents[1] / 'web'


@pytest.mark.parametrize('width,path', [(1440, '/'), (390, '/'), (360, '/task/main')])
def test_menus_scroll_follow_and_reading_position(width, path):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': width, 'height': 844},
                                      is_mobile=width < 480, has_touch=width < 480)
        context.add_init_script('''window.WebSocket = class {
          static OPEN=1; readyState=1;
          constructor(){setTimeout(()=>this.onopen?.(),0)} send(){} close(){}
        };''')
        page = context.new_page()
        errors, mutations = [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        manager = dict(id='main', title='Fixture manager', status='idle', cwd='/tmp/fixture',
                       pending=[], backend='codex', session_id='synthetic', created_at=1, updated_at=1,
                       capabilities={'compact': True})

        def intercept(route):
            request = route.request
            url = urlsplit(request.url)
            if request.method != 'GET':
                mutations.append(url.path)
                route.fulfill(status=403, body='Read-only fixture')
            elif url.path == '/api/state':
                route.fulfill(json={'manager': manager, 'tasks': [], 'settings': {}, 'backends': []})
            elif url.path.startswith('/api/'):
                route.fulfill(json=[])
            elif url.path == '/lang.js':
                route.fulfill(content_type='application/javascript', body="window.LEARNME_LANG='zh';")
            else:
                file = WEB / (url.path.removeprefix('/static/') if url.path.startswith('/static/') else 'index.html')
                route.fulfill(path=str(file))

        page.route('**/*', intercept)
        page.goto('http://fixture.local' + path)
        panel = page.locator('.win[data-id="main"]')
        expect(panel).to_be_visible()
        page.wait_for_function("S.wins.get('main') && !S.wins.get('main').syncing")
        if width < 480 and path == '/':
            expect(page.locator('#console-tools')).to_be_visible()
            before = panel.locator('.log').bounding_box()
            page.locator('#console-tools > summary').click()
            expect(page.locator('#history-btn')).to_be_visible()
            expect(page.locator('#manager-backend')).to_be_visible()
            page.keyboard.press('Escape')
            assert panel.locator('.log').bounding_box() == before
            panel.locator('.win-options > summary').click()
            expect(panel.locator('[data-act="compact"]')).to_be_visible()
            page.keyboard.press('Escape')
            assert page.locator('#topbar').bounding_box()['height'] <= 54
        elif width < 480:
            expect(page.locator('#topbar')).to_be_hidden()
            panel.locator('.win-options > summary').click()
            expect(panel.locator('[data-act="compact"]')).to_be_visible()
            page.keyboard.press('Escape')
        else:
            expect(page.locator('#history-btn')).to_be_visible()
            assert round(page.locator('#main-col').bounding_box()['width']) == 430
        page.evaluate('''() => {
          const w=S.wins.get('main');
          for(let i=0;i<80;i++) w.receive({type:'text',text:'Fixture '+i+' '+('content '.repeat(40))});
        }''')
        log = panel.locator('.log')
        page.wait_for_function("S.wins.get('main').atBottom()")
        log.evaluate('e=>e.scrollTop=0')
        page.wait_for_timeout(100)
        page.evaluate("S.wins.get('main').receive({type:'text',text:'New while reading'})")
        page.wait_for_timeout(100)
        assert log.evaluate('e=>e.scrollTop') == 0
        page.evaluate("S.wins.get('main').showLatest()")
        page.wait_for_function("S.wins.get('main').atBottom()")
        page.evaluate("S.wins.get('main').receive({type:'text',text:'Latest '+('long '.repeat(400))})")
        page.wait_for_timeout(100)
        assert log.evaluate('e=>e.scrollHeight-e.clientHeight-e.scrollTop') <= 2
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not mutations and not errors
        if width == 1440:
            page.set_viewport_size({'width': 390, 'height': 844})
            expect(page.locator('#console-tools')).to_be_visible()
            page.set_viewport_size({'width': 1440, 'height': 844})
            expect(page.locator('#history-btn')).to_be_visible()
        browser.close()
