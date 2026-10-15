import os, json, asyncio, re

BASE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(BASE, 'kb', 'raw')
GROUP_ID = -1004326030400
TOPICS = {1: 'flood', 7: 'daily', 30: 'knowledge', 3: 'important', 2: 'reglament'}

from pyrogram import Client, enums
app = Client('leadgen', api_id=int(os.environ['API_ID']), api_hash=os.environ['API_HASH'], workdir=BASE)

def msg_to_dict(m):
    fu = None
    if m.from_user:
        fu = {'id': m.from_user.id, 'username': m.from_user.username, 'name': m.from_user.first_name}
    elif m.sender_chat:
        fu = {'chat': m.sender_chat.title}
    d = {'id': m.id, 'date': str(m.date), 'from': fu, 'text': (m.text or m.caption or '')[:2000]}
    if m.document:
        d['document'] = {'file_name': m.document.file_name, 'mime': m.document.mime_type}
    if m.photo:
        d['photo'] = True
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
        report.append({'skipped_large': name, 'size': size})
        return
    safe = re.sub(r'[^\w.\-]+', '_', name)
    dest = os.path.join(RAW, '%s_%s_%s' % (tag, m.id, safe))
    try:
        path = await app.download_media(m, file_name=dest)
        if path:
            report.append({'file': path})
    except Exception as e:
        report.append({'download_error': name, 'error': str(e)[:120]})

async def main():
    report = []
    summary = {}
    async with app:
        pins = []
        try:
            async for m in app.search_messages(GROUP_ID, filter=enums.MessagesFilter.PINNED, limit=50):
                pins.append(msg_to_dict(m))
                await download_if_media(m, 'pin', report)
        except Exception as e:
            pins.append({'error': '%s: %s' % (type(e).__name__, str(e)[:120])})
        with open(os.path.join(RAW, 'group_pins.json'), 'w', encoding='utf-8') as f:
            json.dump(pins, f, ensure_ascii=False, indent=1)
        summary['pins'] = len(pins)
        for tid, tag in TOPICS.items():
            hist = []
            try:
                async for m in app.search_messages(GROUP_ID, query='', message_thread_id=tid, limit=100):
                    hist.append(msg_to_dict(m))
                    await download_if_media(m, 'topic_%s' % tag, report)
            except Exception as e:
                summary[tag] = 'error: %s' % str(e)[:100]
                continue
            with open(os.path.join(RAW, 'topic_%s.json' % tag), 'w', encoding='utf-8') as f:
                json.dump(hist, f, ensure_ascii=False, indent=1)
            summary[tag] = len(hist)
    with open(os.path.join(RAW, '_topic_downloads.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print('SUMMARY:', json.dumps(summary, ensure_ascii=False))
    ok = [d for d in report if 'file' in d]
    bad = [d for d in report if 'file' not in d]
    print('downloaded:', len(ok))
    for d in ok:
        print(' ', d['file'])
    print('errors:', len(bad))
    for d in bad:
        print(' ', json.dumps(d, ensure_ascii=False)[:150])

asyncio.run(main())
