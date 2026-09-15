from __future__ import annotations


def configure_system_certificates() -> bool:
    """Hace que clientes HTTPS usen el almacén de certificados de Windows cuando existe."""
    try:
        import truststore
    except ImportError:
        return False
    truststore.inject_into_ssl()
    return True
