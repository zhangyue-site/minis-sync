"""Align only expanded ZIP total with the server's bounded 4GiB policy."""
from pathlib import Path

def patch_backup_limits(folder):
    path=Path(folder)/'e2/a.smali'
    text=path.read_text()
    start=text.index('.method public static g0(Ljava/io/File;)Ljava/lang/String;')
    end=text.index('.end method',start)
    block=text[start:end]
    anchor='    add-long/2addr v8, v13\n\n    cmp-long v13, v8, v15'
    if block.count(anchor)!=1 or block.count('const-wide v15, 0x80000000L')!=1:
        raise ValueError('backup total/member limit ABI changed')
    block=block.replace(anchor,'    add-long/2addr v8, v13\n\n    const-wide v15, 0x100000000L\n\n    cmp-long v13, v8, v15')
    path.write_text(text[:start]+block+text[end:])
