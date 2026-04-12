#!/usr/bin/env python3
"""
generate_tts_notebook.py — Parse .tts files and generate a single Google Colab
notebook that uses Kokoro TTS to produce all audio files.

Usage:
    python generate_tts_notebook.py tests/pt1-listening-m1.tts tests/pt1-speaking-m1.tts
    python generate_tts_notebook.py tests/*.tts
    python generate_tts_notebook.py tests/*.tts -o my_notebook.ipynb

Output:
    A single .ipynb file (default: tts_generate.ipynb) ready to upload and run
    on Google Colab. Running all cells produces one .zip per .tts file of .ogg
    audio files.

Voice mapping:
    female -> af_heart
    male   -> am_fenrir
"""

import json
import re
import sys
from pathlib import Path


# -- TTS file parser ----------------------------------------------------------

def parse_tts_file(filepath):
    """Parse a .tts file into a list of audio file blocks."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    blocks = []
    current_block = None
    current_segment = None
    segment_lines = []
    segment_index = 0

    for line in content.split('\n'):
        stripped = line.strip()

        m = re.match(r'@@TTS_FILE_BEGIN\s+(.*)', stripped)
        if m:
            attrs = _parse_attrs(m.group(1))
            current_block = {
                'id': attrs.get('id', ''),
                'output': attrs.get('output', ''),
                'segments': [],
                'pauses': [],
                'concat': None,
            }
            segment_index = 0
            continue

        if stripped == '@@TTS_FILE_END':
            if current_block:
                blocks.append(current_block)
                current_block = None
            continue

        if current_block is None:
            continue

        m = re.match(r'@@SEGMENT_BEGIN\s+(.*)', stripped)
        if m:
            attrs = _parse_attrs(m.group(1))
            current_segment = {
                'speaker': attrs.get('speaker', 'female'),
                'segment_file': attrs.get('segment_file', ''),
            }
            segment_lines = []
            continue

        if stripped == '@@SEGMENT_END':
            if current_segment:
                current_segment['text'] = '\n'.join(segment_lines).strip()
                current_block['segments'].append(current_segment)
                current_segment = None
                segment_index += 1
            continue

        m = re.match(r'@@PAUSE\s+(.*)', stripped)
        if m:
            attrs = _parse_attrs(m.group(1))
            seconds = float(attrs.get('seconds', '1'))
            current_block['pauses'].append({
                'after_segment': segment_index - 1,
                'seconds': seconds,
            })
            continue

        m = re.match(r'@@FFMPEG_CONCAT\s+(.*)', stripped)
        if m:
            attrs = _parse_attrs(m.group(1))
            segs = [s.strip() for s in attrs.get('segments', '').split(',') if s.strip()]
            current_block['concat'] = {
                'segments': segs,
                'output': attrs.get('output', ''),
            }
            continue

        if current_segment is not None:
            segment_lines.append(line)

    return blocks


def _parse_attrs(attr_string):
    attrs = {}
    for m in re.finditer(r'(\w+)=(?:"([^"]*?)"|([^\s]+))', attr_string):
        attrs[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
    return attrs


# -- Notebook generation -------------------------------------------------------

def make_notebook(file_groups):
    """Generate a single Colab notebook from multiple parsed TTS file groups.

    file_groups: list of (stem_name, blocks) tuples
    """
    # Build the combined data structure
    all_groups = []
    for stem, blocks in file_groups:
        group_data = []
        for b in blocks:
            group_data.append({
                'id': b['id'],
                'output': b['output'],
                'segments': [
                    {'speaker': s['speaker'], 'segment_file': s['segment_file'], 'text': s['text']}
                    for s in b['segments']
                ],
                'pauses': b['pauses'],
                'concat': b['concat'],
            })
        all_groups.append({'name': stem, 'blocks': group_data})

    data_json = json.dumps(all_groups, indent=2, ensure_ascii=False)

    # Summary for title
    total_files = sum(len(g['blocks']) for g in all_groups)
    names = ', '.join(f'`{g["name"]}`' for g in all_groups)

    cells = []

    # Cell 0: Title
    cells.append(_md_cell(
        f'# TTS Generator\n\n'
        f'This notebook generates audio files for {names} '
        f'using [Kokoro TTS](https://github.com/hexgrad/kokoro) ({total_files} audio files total).\n\n'
        f'**Voices:** `af_heart` (female), `am_fenrir` (male)\n\n'
        f'**Instructions:** Select a **GPU runtime** (Runtime > Change runtime type > T4 GPU), '
        f'then click **Runtime > Run all**. Download the generated .zip files from the last cell.\n\n'
        f'Generated by `generate_tts_notebook.py`.'
    ))

    # Cell 1: Install
    cells.append(_code_cell(
        '# Install dependencies\n'
        '!pip install -q kokoro>=0.9.4 soundfile\n'
        '!apt-get -qq -y install espeak-ng > /dev/null 2>&1'
    ))

    # Cell 2: Init + data
    cells.append(_code_cell(
        'import json\n'
        'import numpy as np\n'
        'import soundfile as sf\n'
        'import subprocess\n'
        'import os\n'
        'import shutil\n'
        'import zipfile\n'
        'from kokoro import KPipeline\n'
        '\n'
        'pipeline = KPipeline(lang_code=\'a\')\n'
        'SAMPLE_RATE = 24000\n'
        'VOICE_MAP = {\'female\': \'af_heart\', \'male\': \'am_fenrir\'}\n'
        '\n'
        f'ALL_GROUPS = json.loads(\'\'\'{data_json}\'\'\')\n'
        '\n'
        'total = sum(len(g["blocks"]) for g in ALL_GROUPS)\n'
        'print(f"Loaded {total} audio files across {len(ALL_GROUPS)} group(s)")\n'
        'for g in ALL_GROUPS:\n'
        '    print(f"  {g[\'name\']}: {len(g[\'blocks\'])} files")'
    ))

    # Cell 3: Helper functions
    cells.append(_code_cell(
        'def generate_segment(text, voice):\n'
        '    """Run Kokoro TTS and return audio as numpy array."""\n'
        '    chunks = []\n'
        '    for _, _, audio in pipeline(text, voice=voice):\n'
        '        chunks.append(audio)\n'
        '    if not chunks:\n'
        '        print(f"  [warn] No audio generated for: {text[:50]}...")\n'
        '        return np.zeros(SAMPLE_RATE, dtype=np.float32)\n'
        '    return np.concatenate(chunks)\n'
        '\n'
        'def make_silence(seconds):\n'
        '    return np.zeros(int(SAMPLE_RATE * seconds), dtype=np.float32)\n'
        '\n'
        'print("Ready")'
    ))

    # Cell 4: Generate all audio
    cells.append(_code_cell(
        'from IPython.display import display, Audio\n'
        '\n'
        'group_outputs = {}  # name -> list of (output_name, audio_array)\n'
        'counter = 0\n'
        'total = sum(len(g["blocks"]) for g in ALL_GROUPS)\n'
        '\n'
        'for group in ALL_GROUPS:\n'
        '    name = group["name"]\n'
        '    outputs = []\n'
        '    for block in group["blocks"]:\n'
        '        counter += 1\n'
        '        file_id = block["id"]\n'
        '        output_name = block["output"]\n'
        '        segments = block["segments"]\n'
        '        pauses = {p["after_segment"]: p["seconds"] for p in block["pauses"]}\n'
        '\n'
        '        print(f"[{counter}/{total}] {name}/{file_id} -> {output_name}")\n'
        '\n'
        '        seg_audios = []\n'
        '        for si, seg in enumerate(segments):\n'
        '            voice = VOICE_MAP.get(seg["speaker"], "af_heart")\n'
        '            text = seg["text"]\n'
        '            preview = text[:60] + ("..." if len(text) > 60 else "")\n'
        '            print(f"  {si+1}/{len(segments)} {seg[\'speaker\']} ({voice}): {preview}")\n'
        '            audio = generate_segment(text, voice)\n'
        '            seg_audios.append(audio)\n'
        '            if si in pauses:\n'
        '                seg_audios.append(make_silence(pauses[si]))\n'
        '\n'
        '        if len(seg_audios) == 1:\n'
        '            final = seg_audios[0]\n'
        '        else:\n'
        '            gap = make_silence(0.4)\n'
        '            parts = []\n'
        '            for i, a in enumerate(seg_audios):\n'
        '                if i > 0: parts.append(gap)\n'
        '                parts.append(a)\n'
        '            final = np.concatenate(parts)\n'
        '\n'
        '        outputs.append((output_name, final))\n'
        '        print(f"  -> {len(final)/SAMPLE_RATE:.1f}s")\n'
        '\n'
        '    group_outputs[name] = outputs\n'
        '\n'
        'print(f"\\nGenerated {counter} audio files")'
    ))

    # Cell 5: Preview
    cells.append(_code_cell(
        '# Preview first audio from each group\n'
        'for name, outputs in group_outputs.items():\n'
        '    if outputs:\n'
        '        fname, audio = outputs[0]\n'
        '        print(f"{name}/{fname} ({len(audio)/SAMPLE_RATE:.1f}s)")\n'
        '        display(Audio(data=audio, rate=SAMPLE_RATE, autoplay=False))'
    ))

    # Cell 6: Save + zip
    cells.append(_code_cell(
        '# Convert to OGG and create zip files\n'
        'zip_files = []\n'
        '\n'
        'for name, outputs in group_outputs.items():\n'
        '    out_dir = name\n'
        '    wav_dir = os.path.join(out_dir, "_wav")\n'
        '    os.makedirs(wav_dir, exist_ok=True)\n'
        '\n'
        '    ogg_files = []\n'
        '    for fname, audio in outputs:\n'
        '        wav_path = os.path.join(wav_dir, fname.replace(".ogg", ".wav"))\n'
        '        sf.write(wav_path, audio, SAMPLE_RATE)\n'
        '        ogg_path = os.path.join(out_dir, fname)\n'
        '        subprocess.run(\n'
        '            ["ffmpeg", "-y", "-i", wav_path, "-c:a", "libvorbis", "-q:a", "6", ogg_path],\n'
        '            capture_output=True\n'
        '        )\n'
        '        ogg_files.append(ogg_path)\n'
        '\n'
        '    shutil.rmtree(wav_dir)\n'
        '\n'
        '    zip_name = f"{name}.zip"\n'
        '    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:\n'
        '        for ogg in ogg_files:\n'
        '            zf.write(ogg, os.path.basename(ogg))\n'
        '    zip_files.append(zip_name)\n'
        '    size = os.path.getsize(zip_name)\n'
        '    print(f"{zip_name}: {len(ogg_files)} files, {size/1024:.0f} KB")\n'
        '\n'
        'print(f"\\nCreated {len(zip_files)} zip file(s)")'
    ))

    # Cell 7: Download
    cells.append(_code_cell(
        '# Download zip files\n'
        'try:\n'
        '    from google.colab import files\n'
        '    for zf in zip_files:\n'
        '        files.download(zf)\n'
        '    print("Downloads started")\n'
        'except ImportError:\n'
        '    print("Not running on Colab. Files are in the current directory:")\n'
        '    for zf in zip_files:\n'
        '        print(f"  {zf}")'
    ))

    return {
        'nbformat': 4,
        'nbformat_minor': 0,
        'metadata': {
            'colab': {'provenance': [], 'gpuType': 'T4'},
            'kernelspec': {'display_name': 'Python 3', 'name': 'python3'},
            'accelerator': 'GPU',
        },
        'cells': cells,
    }


def _md_cell(source):
    return {'cell_type': 'markdown', 'metadata': {}, 'source': [source]}

def _code_cell(source):
    return {'cell_type': 'code', 'execution_count': None, 'metadata': {}, 'outputs': [], 'source': [source]}


# -- Main ---------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print('Usage: python generate_tts_notebook.py <file1.tts> [file2.tts ...] [-o output.ipynb]')
        print()
        print('Generates a single .ipynb Colab notebook from one or more .tts files.')
        print('Upload the notebook to Google Colab, select GPU runtime, and run all cells.')
        sys.exit(1)

    # Parse args
    tts_paths = []
    output_path = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '-o' and i + 1 < len(args):
            output_path = args[i + 1]
            i += 2
        else:
            tts_paths.append(args[i])
            i += 1

    if not tts_paths:
        print('Error: no .tts files specified')
        sys.exit(1)

    # Parse all .tts files
    file_groups = []
    for tts_path in tts_paths:
        p = Path(tts_path)
        if not p.exists():
            print(f'Warning: {p} not found, skipping')
            continue
        blocks = parse_tts_file(str(p))
        stem = p.stem
        file_groups.append((stem, blocks))
        print(f'{p.name}: {len(blocks)} audio files')
        for b in blocks:
            segs = len(b['segments'])
            print(f'  {b["id"]} -> {b["output"]}  ({segs} segment{"s" if segs != 1 else ""})')

    if not file_groups:
        print('Error: no valid .tts files found')
        sys.exit(1)

    # Generate notebook
    notebook = make_notebook(file_groups)

    if output_path is None:
        output_path = 'tts_generate.ipynb'

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(notebook, f, indent=1, ensure_ascii=False)

    total = sum(len(blocks) for _, blocks in file_groups)
    print(f'\n-> {output_path} ({total} audio files from {len(file_groups)} .tts file(s))')
    print('Upload to Google Colab, select GPU runtime (T4), and run all cells.')


if __name__ == '__main__':
    main()
