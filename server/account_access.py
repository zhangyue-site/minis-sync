"""Protect single-owner server resources; ordinary per-user APIs remain account-scoped."""
import owner_features
OWNER_PREFIXES=('/api/vault/','/api/quota/','/api/hub/','/api/qr/','/api/sync/','/api/camera/')
def owner_route(path):
    return any(path.startswith(prefix) for prefix in OWNER_PREFIXES)
def is_owner(user):
    # Resolve only trusted server configuration. Client-supplied role/UID is never consulted.
    return bool(user) and owner_features.owner_allowed(user)
def enforce(handler,connection,path):
    if not owner_route(path):return False
    user,error=handler._auth(connection)
    if not user:
        handler._json(401,{'error':error or 'authentication_required'});return True
    if not is_owner(user):
        handler._json(403,{'error':'owner_account_required'});return True
    return False
