import os, json, asyncio, re

BASE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(BASE, 'kb', 'raw')
GROUP_ID = -1004326030400

from pyrogram import Client
app = Client('leadgen', api_id=int(os.environ['API_ID']), api_hash=os.environ['API_HASH'], workdir=BASE)

def msg_to_dict(m):
    fu = None
    if m.from_user:
        fu = {'id': m.from_user.id, 'username': m.from_user.username, 'name': m.from_user.first_name}
    elif m.sender_chat:
        fu = {'chat': m.sender_chat.title}
    d = {'id': m.id, 'date': str(m.date), 'from': fu, 'text': (m.text or m.caption or '')[:3000]}
    if m.document:
        d['document'] = {'file_name': m.document.file_name, 'mime': m.document.mime_type}
    if m.photo: d['photo'] = True
    if m.voice: d['voice'] = {'duration': m.voice.duration}
    if m.audio: d['audio'] = True
    if m.video_note: d['video_note'] = True
    return d

async def download_media(m, tag, stats):
    kind, name, size = None, None, 0
    if m.photo:
        kind, name, size = 'photo', 'photo_%s.jpg' % m.id, m.photo.file_size or 0
    elif m.voice:
        kind, name, size = 'voice', 'voice_%s.ogg' % m.id, m.voice.file_size or 0
    elif m.audio:
        kind, name, size = 'audio', 'audio_%s' % m.id, m.audio.file_size or 0
    elif m.video_note:
        kind, name, size = 'videonote', 'vn_%s.mp4' % m.id, m.video_note.file_size or 0
    elif m.document:
        kind, name, size = 'doc', m.document.file_name or 'doc_%s' % m.id, m.document.file_size or 0
    else:
        return
    if size and size > 30 * 1024 * 1024:
        stats['skipped'] = stats.get('skipped', 0) + 1
        return
    safe = re.sub(r'[^\w.\-]+', '_', name)
    dest = os.path.join(RAW, '%s_%s_%s' % (tag, m.id, safe))
    try:
        path = await app.download_media(m, file_name=dest)
        if path:
            stats[kind] = stats.get(kind, 0) + 1
    except Exception:
        stats['err'] = stats.get('err', 0) + 1

async def pull_topic(tid, tag, max_pages, stats, do_download):
    hist = []
    offset_id = 0
    for _ in range(max_pages):
        batch = []
        async for m in app.search_messages(GROUP_ID, query='', message_thread_id=tid, limit=200, offset_id=offset_id):
            batch.append(m)
        if not batch:
            break
        for m in batch:
            hist.append(msg_to_dict(m))
            if do_download:
                await download_media(m, tag, stats)
        offset_id = batch[-1].id
        if len(batch) < 200:
            break
    with open(os.path.join(RAW, '%s_full.json' % tag), 'w', encoding='utf-8') as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)
    return len(hist)

async def main():
    stats = {}
    async with app:
        n_flood = await pull_topic(1, 'floodF', 6, stats, True)
        n_daily = await pull_topic(7, 'dailyF', 3, stats, False)
    tr = [m for m in dir(app) if 'transcr' in m.lower()]
    print('flood messages:', n_flood, '| daily messages:', n_daily)
    print('downloads:', json.dumps(stats))
    print('transcribe methods:', tr)
    data = json.load(open(os.path.join(RAW, 'floodF_full.json')))
    voices = [d for d in data if d.get('voice')]
    print('voice messages in flood:', len(voices), [v['id'] for v in voices][:20])

asyncio.run(main())
