import os, glob, json
from faster_whisper import WhisperModel

RAW = 'kb/raw'
files = sorted(glob.glob(os.path.join(RAW, 'floodF_*_voice_*.ogg')))
print('voices found:', len(files))

model = WhisperModel('small', device='cpu', compute_type='int8')

out = {}
for f in files:
    vid = os.path.basename(f).split('_')[1]
    try:
        segments, info = model.transcribe(f, language='ru', beam_size=5, vad_filter=True)
        text = ' '.join(s.text.strip() for s in segments)
        out[vid] = {'file': os.path.basename(f), 'text': text}
        print('=' * 10, vid, '| dur %.1fs' % info.duration)
        print(text)
    except Exception as e:
        out[vid] = {'file': os.path.basename(f), 'error': str(e)[:150]}
        print(vid, 'ERROR', str(e)[:150])

with open(os.path.join(RAW, 'voice_transcripts.json'), 'w', encoding='utf-8') as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
print('SAVED voice_transcripts.json')
