"""Explicit anonymous local model downloads. Never run by an inference request."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
WHISPER = 'https://huggingface.co/Systran/faster-whisper-base/resolve/main/'
PIPER = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/'
VOICE = 'en_US-lessac-medium'


def download(url, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_file():
        temporary = destination.with_suffix(destination.suffix + '.download')
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'HR-Local-Voice-Setup'}), timeout=120) as response, temporary.open('wb') as target:
                if 'text/html' in response.headers.get('Content-Type', ''):
                    raise RuntimeError('Model download returned an HTML page.')
                count = 0
                while block := response.read(1024 * 1024):
                    count += len(block)
                    if count > 2 * 1024 ** 3:
                        raise RuntimeError('Model download exceeds its safety limit.')
                    target.write(block)
                expected = int(response.headers.get('Content-Length', 0))
                if not count or (expected and expected != count):
                    raise RuntimeError('Incomplete download; rerun setup.')
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    digest = hashlib.sha256()
    with destination.open('rb') as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    print('Ready:', destination.name, destination.stat().st_size, 'bytes', flush=True)
    return {'file': str(destination), 'url': url, 'sha256': digest.hexdigest(), 'bytes': destination.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models-dir', type=Path, default=ROOT / 'storage' / 'models')
    parser.add_argument('--whisper', action='store_true')
    parser.add_argument('--piper', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    models = args.models_dir.resolve()
    if args.check:
        print(json.dumps({'packages': {name: importlib.util.find_spec(name) is not None for name in ['faster_whisper', 'piper', 'av']},
            'whisper_present': (models / 'whisper-base' / 'model.bin').is_file(),
            'piper_present': (models / 'piper' / (VOICE + '.onnx')).is_file()}))
        return
    records = []
    if args.whisper:
        for name in ['config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt']:
            records.append(download(WHISPER + name, models / 'whisper-base' / name))
    if args.piper:
        for name in [VOICE + '.onnx', VOICE + '.onnx.json', 'MODEL_CARD']:
            records.append(download(PIPER + name, models / 'piper' / name))
    if records:
        manifest = models / 'voice-install-manifest.json'
        previous = json.loads(manifest.read_text('utf-8')) if manifest.exists() else {'files': []}
        merged = {entry['file']: entry for entry in previous['files']}
        merged.update({entry['file']: entry for entry in records})
        manifest.write_text(json.dumps({'files': list(merged.values())}, indent=2), 'utf-8')
        print('Model files installed. Inference must be verified separately. Review the Piper voice MODEL_CARD and GPL-3.0 engine license.')


if __name__ == '__main__':
    main()
