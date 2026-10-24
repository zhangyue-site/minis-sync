"""Give the isolated test package a visible launcher label; refuse formal manifests."""
import struct


def patch(data):
    b = bytearray(data)
    off = 8
    u32 = lambda p: struct.unpack_from('<I', b, p)[0]
    size, count, flags, start, style_start = (u32(off + p) for p in (4, 8, 16, 20, 24))
    assert struct.unpack_from('<H', b, off)[0] == 1 and not flags & 0x100
    names = []
    for i in range(count):
        p = off + start + u32(off + 28 + i * 4)
        length = struct.unpack_from('<H', b, p)[0]
        assert length < 0x8000
        names.append(bytes(b[p + 2:p + 2 + length * 2]).decode('utf-16-le'))
    cursor, qa_package = off + size, False
    while cursor < len(b):
        typ, _, length = struct.unpack_from('<HHI', b, cursor)
        assert length >= 8
        if typ == 0x102 and names[u32(cursor + 20)] == 'manifest':
            base = cursor + 16
            first, width, total = struct.unpack_from('<HHH', b, base + 8)
            for i in range(total):
                a = base + first + width * i
                if names[u32(a + 4)] == 'package' and b[a + 15] == 3:
                    qa_package = names[u32(a + 16)] == 'test.zhangyue.minis'
        cursor += length
    assert qa_package, 'QA package required'
    label = 'Minis · 验收测试'
    assert label not in names, 'already labeled'
    end = style_start or size
    encoded = struct.pack('<H', len(label)) + label.encode('utf-16-le') + b'\0\0'
    encoded += b'\0' * (-len(encoded) % 4)
    header = bytearray(b[off:off + 28])
    struct.pack_into('<I', header, 4, size + 4 + len(encoded))
    struct.pack_into('<I', header, 8, count + 1)
    struct.pack_into('<I', header, 20, start + 4)
    if style_start:
        struct.pack_into('<I', header, 24, style_start + 4 + len(encoded))
    pool = (header + b[off + 28:off + 28 + count * 4]
            + struct.pack('<I', end - start)
            + b[off + 28 + count * 4:off + end] + encoded + b[off + end:off + size])
    b[off:off + size] = pool
    names.append(label)
    pos, changed = off + len(pool), 0
    while pos < len(b):
        typ, _, length = struct.unpack_from('<HHI', b, pos)
        assert length >= 8
        if typ == 0x102 and names[u32(pos + 20)] in ('application', 'activity', 'activity-alias'):
            base = pos + 16
            first, width, total = struct.unpack_from('<HHH', b, base + 8)
            for i in range(total):
                a = base + first + width * i
                if names[u32(a + 4)] == 'label':
                    struct.pack_into('<I', b, a + 8, count)
                    b[a + 15] = 3
                    struct.pack_into('<I', b, a + 16, count)
                    changed += 1
        pos += length
    assert changed, 'no application/launcher labels found'
    struct.pack_into('<I', b, 4, len(b))
    return bytes(b)
