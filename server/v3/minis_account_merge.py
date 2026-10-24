"""Additive, stable-ID union for private provider settings and API-key records.

Client chat ACKs do not authorize configuration deletion. Existing values win
on conflict and the original merge layer retains both versions for recovery.
"""
from minis_store import InvalidBackup

ARRAY_KEYS={'instances':('id',),'modelEntries':('providerInstanceId','model.id'),
            'modelGroups':('id',),'subAgents':('id',),'providers':('instanceId',)}


ABSENT=object()

def merge(local,cloud,missing,base=ABSENT):
    if base is ABSENT:base=missing
    if local is missing:return (base if cloud is missing else cloud),False
    if cloud is missing:return local,False
    if isinstance(local,dict) and isinstance(cloud,dict):
        out={};bad=False
        for key in sorted(set(local)|set(cloud)|(set(base) if isinstance(base,dict) else set())):
            l,c=local.get(key,missing),cloud.get(key,missing)
            b=base.get(key,missing) if isinstance(base,dict) else missing
            if key in ARRAY_KEYS and l is not missing and c is not missing:
                if not isinstance(l,list) or not isinstance(c,list):raise InvalidBackup('invalid_account_config_list')
                def rows(values):
                    result={}
                    for row in values:
                        if not isinstance(row,dict):raise InvalidBackup('invalid_account_config_record')
                        parts=[]
                        for field in ARRAY_KEYS[key]:
                            value=row
                            for part in field.split('.'):value=value.get(part) if isinstance(value,dict) else None
                            if not isinstance(value,str) or not value:raise InvalidBackup('account_config_id_required')
                            parts.append(value)
                        identity=tuple(parts)
                        if identity in result:raise InvalidBackup('duplicate_account_config_id')
                        result[identity]=row
                    return result
                left,right=rows(l),rows(c);before=rows(b) if isinstance(b,list) else {};value=[]
                for identity in dict.fromkeys(list(right)+list(left)+list(before)):
                    item,conflict=merge(left.get(identity,missing),right.get(identity,missing),missing,before.get(identity,missing))
                    value.append(item);bad|=conflict
                out[key]=value
            else:
                value,conflict=merge(l,c,missing,b);out[key]=value;bad|=conflict
        return out,bad
    if base is not missing:
        if local==base:return cloud,False
        if cloud==base:return local,False
    return cloud,local!=cloud
