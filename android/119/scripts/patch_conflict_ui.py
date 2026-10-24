"""Strict, fail-closed hook for the verified SyncActivity ABI."""
from pathlib import Path
import re


def patch_conflict_ui(folder):
    path = Path(folder) / 'com/openminis/app/letian/SyncActivity.smali'
    source = path.read_text()
    if not re.search(r'^\.class .* Lcom/openminis/app/letian/SyncActivity;$', source, re.M) or source.count('.super Lc/j;') != 1:
        raise ValueError('SyncActivity superclass ABI changed')
    if 'ConflictDialog;' in source or 'onActivityResult(' in source:
        raise ValueError('SyncActivity result hook already exists or ABI changed')
    matches = list(re.finditer(r'^\.method public final onCreate\(Landroid/os/Bundle;\)V\n(?P<body>.*?)^\.end method', source, re.M | re.S))
    if len(matches) != 1:
        raise ValueError('SyncActivity onCreate ABI changed')
    match = matches[0]
    body = match.group('body')
    if not re.match(r'    \.registers 5\n', body) or body.count('return-void') != 1 or 'invoke-super {p0, p1}, Lc/j;->onCreate(Landroid/os/Bundle;)V' not in body or not re.search(r'    return-void\s*$', body):
        raise ValueError('SyncActivity onCreate body ABI changed')
    hook = '    invoke-static {p0}, Lsite/zhangyue/minis/sync/ConflictDialog;->show(Landroid/app/Activity;)V\n'
    patched = match.group(0).replace('    return-void', hook + '    return-void')
    base_path = Path(folder) / 'c/j.smali'
    base = base_path.read_text()
    anchor = '.method public final onActivityResult(IILandroid/content/Intent;)V\n    .registers 5\n'
    parent_matches = list(re.finditer(r'^\.method public final onActivityResult\(IILandroid/content/Intent;\)V\n(?P<body>.*?)^\.end method', base, re.M | re.S))
    if (base.count(anchor) != 1 or len(parent_matches) != 1
            or 'ConflictDialog;' in base or ':conflict_result_continue' in base
            or not re.search(r'^\.class .* Lc/j;$', base, re.M)):
        raise ValueError('ComponentActivity result ABI changed or already patched')
    parent_body = parent_matches[0].group('body')
    if not re.match(r'    \.registers 5\n\s*iget-object v0, p0, Lc/j;->l:Lc/h;', parent_body):
        raise ValueError('ComponentActivity result dispatch ABI changed')
    parent_hook = """    invoke-static {p0, p1, p2, p3}, Lsite/zhangyue/minis/sync/ConflictDialog;->onResult(Landroid/app/Activity;IILandroid/content/Intent;)Z
    move-result v0
    if-eqz v0, :conflict_result_continue
    return-void
    :conflict_result_continue
"""
    # Validate both original files before writing either. The final method belongs
    # to c/j and cannot legally be overridden by SyncActivity.
    patched_base = base.replace(anchor, anchor + parent_hook)
    patched_activity = source[:match.start()] + patched + source[match.end():]
    base_path.write_text(patched_base)
    path.write_text(patched_activity)
