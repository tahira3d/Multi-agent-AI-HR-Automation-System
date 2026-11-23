"""Explicit real local speech probes. Synthetic TTS is not a human/accent benchmark."""
import argparse
import ctypes
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.integrations.local_voice import LocalVoiceError, get_voice_runtime  # noqa: E402

SYNTHETIC = [
    ('python_project', 'I have three years of Python experience and built a web application using FastAPI.'),
    ('authentication', 'I implemented authentication using JSON web tokens and used PostgreSQL to store user records.'),
    ('availability', 'My notice period is four weeks. I am available to start next month.')]


def words(text):
    return re.findall(r'\w+', text.casefold())


def word_errors(reference, actual):
    expected, recognized = words(reference), words(actual)
    row = list(range(len(recognized) + 1))
    for index, token in enumerate(expected, 1):
        updated = [index]
        for other, found in enumerate(recognized, 1):
            updated.append(min(updated[-1] + 1, row[other] + 1, row[other - 1] + (token != found)))
        row = updated
    return row[-1], len(expected)


def peak_process_mb():
    if sys.platform != 'win32':
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 if sys.platform != 'darwin' else 1024 ** 2)
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [(name, ctypes.c_size_t) for name in
            ['peak', 'working', 'quota_peak_paged', 'quota_paged', 'quota_peak_nonpaged', 'quota_nonpaged', 'pagefile', 'peak_pagefile']]
    values = Counters()
    values.cb = ctypes.sizeof(values)
    ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    query = ctypes.windll.psapi.GetProcessMemoryInfo
    query.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    if query(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(values), values.cb):
        return round(values.peak / 1024 ** 2, 2)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--synthetic', action='store_true')
    source.add_argument('--manifest', type=Path, help='Permitted labeled audio: [{id,audio,reference,language}]')
    parser.add_argument('--skip-llm', action='store_true')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'storage' / 'evaluations' / 'voice-measurement')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    runtime = get_voice_runtime()
    report = {'measured_at': datetime.now(timezone.utc).isoformat(), 'source': 'synthetic_piper_audio' if args.synthetic else 'permitted_labeled_audio',
              'limitations': 'Small local smoke test; synthetic same-system speech is not an independent human, accent or Urdu benchmark.',
              'runtime': runtime.status(), 'speech': [], 'failures': [], 'llm': {'status': 'not_run'}}
    fixtures = []
    if args.synthetic:
        for identity, reference in SYNTHETIC:
            start = time.perf_counter()
            try:
                audio = runtime.synthesize(reference)
                path = output / (identity + '.wav')
                path.write_bytes(audio)
                fixtures.append({'id': identity, 'audio': str(path), 'reference': reference, 'language': 'en', 'tts_latency_seconds': round(time.perf_counter() - start, 4)})
            except LocalVoiceError as exc:
                report['failures'].append({'stage': 'tts', 'id': identity, 'code': exc.code, 'message': exc.message})
        (output / 'synthetic-manifest.json').write_text(json.dumps([
            item | {'audio': Path(item['audio']).name, 'source_type': 'piper_synthetic'} for item in fixtures], indent=2), 'utf-8')
    else:
        fixtures = json.loads(args.manifest.read_text('utf-8'))
        if fixtures and all(item.get('source_type') == 'piper_synthetic' for item in fixtures):
            report['source'] = 'synthetic_piper_audio'
        for item in fixtures:
            item['audio'] = str((args.manifest.parent / item['audio']).resolve())
    errors, count = 0, 0
    for item in fixtures:
        start = time.perf_counter()
        try:
            result = runtime.transcribe(Path(item['audio']).read_bytes(), item.get('language', 'en'))
            mistakes, length = word_errors(item['reference'], result['text'])
            errors += mistakes
            count += length
            terms = [term for term in ['Python', 'FastAPI', 'PostgreSQL', 'JSON'] if term.casefold() in item['reference'].casefold()]
            report['speech'].append({'id': item['id'], 'word_errors': mistakes, 'reference_words': length,
                'wer': mistakes / length if length else None, 'stt_latency_seconds': round(time.perf_counter() - start, 4),
                'tts_latency_seconds': item.get('tts_latency_seconds'), 'model_version': result['model_version'],
                'technical_terms_expected': terms, 'technical_terms_recognized': [term for term in terms if term.casefold() in result['text'].casefold()]})
        except LocalVoiceError as exc:
            report['failures'].append({'stage': 'stt', 'id': item['id'], 'code': exc.code, 'message': exc.message})
    if not args.skip_llm:
        start = time.perf_counter()
        try:
            report['llm'] = {'status': 'passed', **runtime.choose_followup({'topic': 'Python project'}, 'Describe your project and your contribution.', 'I used Python.')}
        except LocalVoiceError as exc:
            report['llm'] = {'status': 'failed', 'code': exc.code, 'message': exc.message}
            report['failures'].append({'stage': 'llm', 'code': exc.code})
        report['llm']['latency_seconds'] = round(time.perf_counter() - start, 4)
    report['aggregate_wer'] = errors / count if count else None
    report['python_voice_process_peak_rss_mb'] = peak_process_mb()
    report['ollama_peak_memory'] = 'not_measured_by_this_process'
    report['status'] = 'failed' if report['failures'] or not report['speech'] else ('speech_only_passed' if args.skip_llm else 'passed')
    runtime.unload()
    (output / 'results.json').write_text(json.dumps(report, indent=2), 'utf-8')
    print(json.dumps(report, indent=2))
    return 1 if report['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
