"""Bounded inspection of private performance backgrounds; no filesystem paths or executable formats."""
import struct
import zlib

MAX_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 16_777_216


def _dimensions(w, h):
    if not (64 <= w <= 4096 and 64 <= h <= 4096 and w * h <= MAX_PIXELS):
        raise ValueError('Media dimensions must be 64–4096 pixels, at most 16 megapixels')
    return {'width': int(w), 'height': int(h)}


def inspect_media(data: bytes) -> dict:
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Backgrounds must be at most 10 MB')
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError('Images must be at most 5 MB')
        offset, dimensions, ended, image_data = 8, None, False, False
        while offset + 12 <= len(data):
            size = int.from_bytes(data[offset:offset+4], 'big')
            kind, payload = data[offset+4:offset+8], data[offset+8:offset+8+size]
            if offset + size + 12 > len(data): raise ValueError('Truncated PNG')
            crc = int.from_bytes(data[offset+8+size:offset+12+size], 'big')
            if zlib.crc32(kind + payload) & 0xffffffff != crc: raise ValueError('Invalid PNG checksum')
            if offset == 8 and (kind != b'IHDR' or size != 13): raise ValueError('Invalid PNG header')
            if kind == b'IHDR': dimensions = _dimensions(*struct.unpack('>II', payload[:8]))
            if kind == b'IDAT': image_data = True
            if kind == b'acTL': raise ValueError('Use a static PNG or an MP4 video')
            offset += size + 12
            if kind == b'IEND': ended = size == 0 and offset == len(data); break
        if not ended or not dimensions or not image_data: raise ValueError('Invalid PNG image')
        return {'mime':'image/png', **dimensions, 'duration':None}
    if data.startswith(b'\xff\xd8'):
        if len(data) > MAX_IMAGE_BYTES: raise ValueError('Images must be at most 5 MB')
        pos, dimensions = 2, None
        while pos + 4 <= len(data):
            if data[pos] != 255: raise ValueError('Invalid JPEG marker')
            while pos < len(data) and data[pos] == 255: pos += 1
            if pos >= len(data): break
            marker = data[pos]; pos += 1
            if marker == 0xda: break
            size = int.from_bytes(data[pos:pos+2], 'big')
            if size < 2 or pos + size > len(data): raise ValueError('Invalid JPEG segment')
            if marker in {0xc0,0xc1,0xc2}:
                if size < 8: raise ValueError('Invalid JPEG frame')
                h,w = struct.unpack('>HH',data[pos+3:pos+7]); dimensions = _dimensions(w,h)
            pos += size
        if not dimensions or not data.endswith(b'\xff\xd9'): raise ValueError('Invalid JPEG image')
        return {'mime':'image/jpeg', **dimensions, 'duration':None}
    if len(data) >= 24 and data[4:8] == b'ftyp':
        dimensions, duration, has_video, has_data = None, None, False, False
        def atoms(start, end, depth=0):
            nonlocal dimensions, duration, has_video, has_data
            if depth > 5: raise ValueError('Invalid MP4 nesting')
            pos = start
            while pos + 8 <= end:
                size = int.from_bytes(data[pos:pos+4], 'big'); kind = data[pos+4:pos+8]; header = 8
                if size == 1:
                    if pos + 16 > end: raise ValueError('Invalid MP4 atom')
                    size = int.from_bytes(data[pos+8:pos+16], 'big'); header = 16
                elif size == 0: size = end-pos
                if size < header or pos + size > end: raise ValueError('Invalid MP4 atom size')
                value = data[pos+header:pos+size]
                if kind in {b'moov',b'trak',b'mdia'}: atoms(pos+header,pos+size,depth+1)
                elif kind == b'mvhd':
                    off = 20 if value and value[0] == 1 else 12
                    width = 8 if value and value[0] == 1 else 4
                    if len(value) < off+4+width: raise ValueError('Invalid MP4 duration')
                    timescale = int.from_bytes(value[off:off+4],'big')
                    duration = int.from_bytes(value[off+4:off+4+width],'big') / max(timescale,1)
                elif kind == b'tkhd' and len(value) >= 84:
                    w,h = struct.unpack('>II',value[-8:]); w,h = w >> 16,h >> 16
                    if w and h: dimensions = _dimensions(w,h)
                elif kind == b'hdlr' and len(value) >= 12 and value[8:12] == b'vide': has_video = True
                elif kind == b'mdat' and value: has_data = True
                pos += size
            if pos != end: raise ValueError('Invalid MP4 trailing data')
        atoms(0,len(data))
        if not dimensions or not has_video or not has_data or duration is None or not 0 < duration <= 30:
            raise ValueError('MP4 backgrounds need a video track, valid dimensions and a duration of 30 seconds or less')
        return {'mime':'video/mp4', **dimensions, 'duration':round(duration,3)}
    raise ValueError('Use a static PNG/JPEG image or an MP4 video')
