import os, json, asyncio, re

BASE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(BASE, 'kb', 'raw')
os.makedirs(RAW, exist_ok=True)

from pyrogram import Client

app = Client('leadgen', api_id=int(os.environ['API_ID']), api_hash=os.environ['API_HASH'], workdir=BASE)

def msg_to_dict(m):
    fu = None
    if m.from_user:
        fu = {'id': m.from_user.id, 'username': m.from_user.username, 'name': m.from_user.first_name}
    elif m.sender_chat:
        fu = {'chat': m.sender_chat.title}
    d = {'id': m.id, 'date': str(m.date), 'from': fu, 'text': (m.text or m.caption or '')[:2000]}
    if m.document:
        d['document'] = {'file_name': m.document.file_name, 'mime': m.document.mime_type, 'size': m.document.file_size}
    if m.photo:
        d['photo'] = {'size': m.photo.file_size}
    return d

async def download_if_media(m, tag, report):
    name, size = None, 0
    if m.document:
        name = m.document.file_name or 'doc_%s' % m.id
        size = m.document.file_size or 0
    elif m.photo:
        name = 'photo_%s.jpg' % m.id
        size = m.photo.file_size or 0
    else:
        return
    if size and size > 25 * 1024 * 1024:
        report['downloads'].append({'skipped_large': name, 'size': size})
        return
    safe = re.sub(r'[^\w.\-]+', '_', name)
    dest = os.path.join(RAW, '%s_%s_%s' % (tag, m.id, safe))
    try:
        path = await app.download_media(m, file_name=dest)
        if path:
            report['downloads'].append({'file': path})
    except Exception as e:
        report['downloads'].append({'download_error': name, 'error': str(e)[:120]})

async def fetch_user(report, uname):
    try:
        u = await app.get_users(uname)
        full = await app.get_chat(u.id)
        info = {'id': u.id, 'name': u.first_name, 'username': u.username, 'bio': getattr(full, 'bio', None)}
        hist = []
        async for m in app.get_chat_history(u.id, limit=100):
            hist.append(msg_to_dict(m))
            await download_if_media(m, 'curator_%s' % uname, report)
        with open(os.path.join(RAW, 'curator_%s_history.json' % uname), 'w', encoding='utf-8') as f:
            json.dump(hist, f, ensure_ascii=False, indent=1)
        info['messages_saved'] = len(hist)
        report['curators'][uname] = info
    except Exception as e:
        report['curators'][uname] = {'error': '%s: %s' % (type(e).__name__, str(e)[:150])}

async def fetch_group(report):
    group = None
    async for dlg in app.get_dialogs():
        t = (dlg.chat.title or '').lower()
        if 'оператор' in t or 'hr group' in t:
            group = dlg.chat
            break
    if not group:
        report['group'] = {'error': 'group not found in dialogs'}
        return
    report['group'] = {'id': group.id, 'title': group.title, 'type': str(group.type)}
    pins = []
    try:
        async for m in app.get_chat_pinned_messages(group.id):
            pins.append(msg_to_dict(m))
            await download_if_media(m, 'pin', report)
    except AttributeError:
        try:
            m = await app.get_chat_pinned_message(group.id)
            if m:
                pins.append(msg_to_dict(m))
        except Exception as e:
            pins.append({'error': str(e)[:120]})
    except Exception as e:
        pins.append({'error': '%s: %s' % (type(e).__name__, str(e)[:120])})
    with open(os.path.join(RAW, 'group_pins.json'), 'w', encoding='utf-8') as f:
        json.dump(pins, f, ensure_ascii=False, indent=1)
    report['group']['pins_saved'] = len(pins)
    try:
        topics = []
        async for t in app.get_forum_topics(group.id):
            topics.append({'id': t.id, 'title': t.title})
        report['group']['topics'] = topics
    except Exception:
        pass
    hist = []
    async for m in app.get_chat_history(group.id, limit=200):
        hist.append(msg_to_dict(m))
        await download_if_media(m, 'group', report)
    with open(os.path.join(RAW, 'group_history.json'), 'w', encoding='utf-8') as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)
    report['group']['history_saved'] = len(hist)

async def main():
    report = {'curators': {}, 'group': {}, 'downloads': []}
    async with app:
        for uname in ['pankraider', 'Ob0rm0Tt']:
            await fetch_user(report, uname)
        await fetch_group(report)
    with open(os.path.join(RAW, '_report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print('=== CURATORS ===')
    for k, v in report['curators'].items():
        print(k, '->', json.dumps(v, ensure_ascii=False)[:300])
    print('=== GROUP ===')
    print(json.dumps(report['group'], ensure_ascii=False)[:800])
    ok = [d for d in report['downloads'] if 'file' in d]
    bad = [d for d in report['downloads'] if 'file' not in d]
    print('=== DOWNLOADS ===')
    print('downloaded:', len(ok))
    for d in ok:
        print(' ', d['file'])
    print('errors/skipped:', len(bad))
    for d in bad:
        print(' ', json.dumps(d, ensure_ascii=False)[:150])

asyncio.run(main())
