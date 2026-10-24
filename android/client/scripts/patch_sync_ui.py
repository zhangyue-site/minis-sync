"""Patch the actual sync settings in every DEX, retaining authenticated transfers."""
from pathlib import Path


def patch_sync_ui(folder):
    target = Path(folder) / 'x3/n.smali'
    text = target.read_text()
    # Only the preference switch can be configured before login. The separate
    # immediate-sync button and all transport authentication remain unchanged.
    start = text.index('    if-eqz v12, :cond_54f')
    end = text.index('    :goto_550', start)
    block = text[start:end]
    assert block.count('Lx3/l;->f()Ljava/lang/String;') == 1
    text = text[:start] + '''    move v6, v12

''' + text[end:]
    old = r'\u56de\u5230\u5e94\u7528\u540e\u540c\u6b65\u6700\u65b0\u5185\u5bb9'
    assert text.count(old) == 1
    text = text.replace(old, '开启后，前台和后台均在充电且连接 Wi-Fi 时自动同步；首次完整上传，后续增量上传')
    label = "打开应用时同步".encode("unicode_escape").decode()
    assert text.count(label) == 1
    text = text.replace(label, "充电并连接 Wi-Fi 时自动同步")
    target.write_text(text)
