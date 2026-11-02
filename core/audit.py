from core.models import AuditLog


def record(actor, action, target="", **data):
    """Write an audit entry. Values are stringified so Decimals/dates are safe."""
    clean = {k: (v if isinstance(v, (int, float, bool, type(None))) else str(v)) for k, v in data.items()}
    user = actor if getattr(actor, "is_authenticated", False) else None
    return AuditLog.objects.create(actor=user, action=action, target=str(target)[:128], data=clean)
