from pathlib import Path

def patch_cloud(folder):
    path=Path(folder)/'x3/j.smali'
    text=path.read_text()
    anchor='.method public final q(Ljava/lang/Object;)Ljava/lang/Object;\n    .registers 40\n'
    if text.count(anchor)!=1:raise ValueError('cloud coroutine hook ABI changed')
    if 'CloudSync;->run' in text:raise ValueError('cloud hook already patched')
    hook='''
    move-object/from16 v0, p0
    iget-object v1, v0, Lx3/j;->p:Lx3/l;
    iget-boolean v2, v0, Lx3/j;->o:Z
    invoke-static {v1, v2}, Lsite/zhangyue/minis/sync/CloudSync;->run(Ljava/lang/Object;Z)Z
    move-result v2
    if-eqz v2, :cloud_legacy
    sget-object v0, Lt4/y;->a:Lt4/y;
    return-object v0
    :cloud_legacy
'''
    path.write_text(text.replace(anchor,anchor+hook))
