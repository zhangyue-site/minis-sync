#!/usr/bin/env python3
# AXML 版本补丁（参数化）：versionCode/vName 原地改写
import struct, sys

path, new_vc, old_vn, new_vn, out_path = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]

with zipfile.ZipFile(path) if False else open('/dev/null') as _:
    pass
import zipfile
with zipfile.ZipFile(path) as z:
    axml = z.read('AndroidManifest.xml')

def u32(b, o): return struct.unpack_from('<I', b, o)[0]
def u16(b, o): return struct.unpack_from('<H', b, o)[0]

assert u16(axml, 0) == 0x0003, 'not AXML'
off = 8
assert u16(axml, off) == 0x0001, 'first chunk not string pool'
sp_size = u32(axml, off + 4)
sp_string_count = u32(axml, off + 8)
sp_flags = u32(axml, off + 16)
sp_strings_start = off + u32(axml, off + 20)
offsets_off = off + 28

def get_string(idx):
    s_off = sp_strings_start + u32(axml, offsets_off + 4 * idx)
    if sp_flags & 0x100:
        n1 = axml[s_off]
        if n1 != 0:
            blen = u16(axml, s_off + 2)
            body = axml[s_off + 4:s_off + 4 + blen]
        else:
            blen = axml[s_off + 1]
            body = axml[s_off + 2:s_off + 2 + blen]
        return body.decode('utf-8', 'replace')
    else:
        clen = u16(axml, s_off)
        return axml[s_off + 2:s_off + 2 + 2 * clen].decode('utf-16-le', 'replace')

names = {i: get_string(i) for i in range(sp_string_count)}
vc_idx = next((i for i, s in names.items() if s == 'versionCode'), None)
assert vc_idx is not None, 'versionCode not in pool'

out = bytearray(axml)
pos = off + sp_size
patched_vc = 0
end = len(axml)
while pos < end - 8:
    ctype = u16(out, pos)
    csize = u32(out, pos + 4)
    if csize <= 0 or pos + csize > end:
        break
    if ctype == 0x0102:
        base = pos + 16
        attr_start = u16(out, base + 8)
        attr_size = u16(out, base + 10)
        attr_count = u16(out, base + 12)
        aoff = base + attr_start
        for k in range(attr_count):
            ao = aoff + k * attr_size
            name_ref = u32(out, ao + 4)
            data_type = out[ao + 15]
            if name_ref == vc_idx and data_type == 0x10:
                struct.pack_into('<I', out, ao + 16, new_vc)
                patched_vc += 1
    pos += csize

print('versionCode ->', new_vc, ':', patched_vc)
if patched_vc == 0:
    sys.exit(1)

old16 = old_vn.encode('utf-16-le')
new16 = new_vn.encode('utf-16-le')
if len(old16) != len(new16):
    sys.exit('versionName length must match')
cnt = out.count(old16)
out = out.replace(old16, new16)
print('versionName', old_vn, '->', new_vn, ':', cnt)
if cnt == 0:
    sys.exit(1)

open(out_path, 'wb').write(bytes(out))
print('OK ->', out_path)
