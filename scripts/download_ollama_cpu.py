"""Fetch only CPU files from the official portable Ollama ZIP using checked HTTP ranges."""
import argparse
import io
from pathlib import Path
import urllib.request
import zipfile


class Archive(io.RawIOBase):
    def __init__(self, url, size):
        self.url, self.size, self.position, self.blocks = url, size, 0, []

    def fetch(self, start, end):
        request = urllib.request.Request(self.url, headers={'Range': f'bytes={start}-{end}', 'User-Agent': 'HR-Local-CPU-Setup'})
        with urllib.request.urlopen(request, timeout=120) as response:
            if response.status != 206 or not response.headers.get('Content-Range', '').startswith(f'bytes {start}-'):
                raise RuntimeError('The server does not support bounded downloads; use the official full Ollama installer.')
            content = response.read(end - start + 2)
            if len(content) != end - start + 1:
                raise RuntimeError('The archive range is incomplete; rerun setup.')
        self.blocks.append((start, content))
        return content

    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else (self.position + offset if whence == 1 else self.size + offset)
        return self.position

    def tell(self):
        return self.position

    def seekable(self):
        return True

    def read(self, count=-1):
        count = min(self.size - self.position, count if count >= 0 else self.size)
        if count <= 0:
            return b''
        for start, data in self.blocks:
            if start <= self.position and self.position + count <= start + len(data):
                offset = self.position - start
                self.position += count
                return data[offset:offset + count]
        data = self.fetch(self.position, self.position + count - 1)
        self.position += len(data)
        return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    destination = args.destination.resolve()
    version = 'v0.40.16'
    url = f'https://github.com/ollama/ollama/releases/download/{version}/ollama-windows-amd64.zip'
    # Pinned official release size; the range/ZIP CRC checks reject any changed asset.
    source = Archive(url, 1618244995)
    source.fetch(source.size - 65536, source.size - 1)
    with zipfile.ZipFile(source) as archive:
        chosen = [info for info in archive.infolist() if not info.is_dir() and
            (info.filename == 'ollama.exe' or (info.filename.startswith('lib/ollama/') and info.filename.count('/') == 2))]
        if not any(info.filename == 'ollama.exe' for info in chosen) or sum(info.file_size for info in chosen) > 256 * 1024 ** 2:
            raise RuntimeError('The official portable archive layout changed; inspect it before installing.')
        groups = []
        for info in sorted(chosen, key=lambda item: item.header_offset):
            start = info.header_offset
            end = min(source.size - 1, start + 30 + len(info.filename.encode()) + len(info.extra) + info.compress_size + 4096)
            if groups and start <= groups[-1][1] + 1048576:
                groups[-1][1] = max(groups[-1][1], end)
            else:
                groups.append([start, end])
        for start, end in groups:
            print(f'Downloading CPU component range: {(end - start + 1) / 1024 ** 2:.1f} MB', flush=True)
            source.fetch(start, end)
        destination.mkdir(parents=True, exist_ok=True)
        for info in chosen:
            target = (destination / info.filename).resolve()
            if not target.is_relative_to(destination):
                raise RuntimeError('Unsafe path in the release archive.')
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = archive.read(info)  # ZipFile validates the release entry's CRC.
            target.write_bytes(payload)
    print(f'Official Ollama {version} CPU components installed. CUDA/Vulkan files were not needed.')


if __name__ == '__main__':
    main()
