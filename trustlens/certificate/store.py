"""Content-addressed certificate storage.

Certificates are immutable and content-addressed by `cert_id`. The default
implementation is filesystem-backed; swap `CertificateStore` for S3, GCS,
or a tenant-partitioned database in production.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Protocol

from trustlens.certificate.schema import Certificate
from trustlens.certificate.signer import canonical_json


class CertificateStore(Protocol):
    """Persistent content-addressed store of certificates."""

    def put(self, cert: Certificate) -> str: ...
    def get(self, cert_id: str) -> Optional[Certificate]: ...
    def exists(self, cert_id: str) -> bool: ...
    def list_by_tenant(self, tenant_id: str, limit: int = 100) -> list[str]: ...


class FilesystemStore:
    """Local-disk certificate store, partitioned by tenant.

    Layout:
        {root}/{tenant_id}/{cert_id[:2]}/{cert_id[2:]}.json

    Two-char prefix avoids huge single directories once the volume grows.
    Writes are atomic (write + rename).
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, tenant_id: str, cert_id: str) -> Path:
        safe_tenant = _sanitize(tenant_id)
        return (
            self.root
            / safe_tenant
            / cert_id[:2]
            / f"{cert_id[2:]}.json"
        )

    def put(self, cert: Certificate) -> str:
        p = self._path(cert.payload.tenant_id, cert.cert_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_bytes(canonical_json(cert))
        tmp.replace(p)
        return cert.cert_id

    def get(self, cert_id: str) -> Optional[Certificate]:
        # Scan tenants — for small deployments; in production, require tenant_id.
        for tenant_dir in self.root.iterdir():
            p = tenant_dir / cert_id[:2] / f"{cert_id[2:]}.json"
            if p.exists():
                return Certificate.model_validate_json(p.read_bytes())
        return None

    def get_for_tenant(self, tenant_id: str, cert_id: str) -> Optional[Certificate]:
        p = self._path(tenant_id, cert_id)
        if not p.exists():
            return None
        return Certificate.model_validate_json(p.read_bytes())

    def exists(self, cert_id: str) -> bool:
        return self.get(cert_id) is not None

    def list_by_tenant(self, tenant_id: str, limit: int = 100) -> list[str]:
        safe_tenant = _sanitize(tenant_id)
        tenant_dir = self.root / safe_tenant
        if not tenant_dir.exists():
            return []
        ids: list[str] = []
        for prefix_dir in sorted(tenant_dir.iterdir()):
            for f in sorted(prefix_dir.iterdir()):
                if f.suffix == ".json":
                    ids.append(prefix_dir.name + f.stem)
                    if len(ids) >= limit:
                        return ids
        return ids


def _sanitize(tenant_id: str) -> str:
    """Keep tenant_id filesystem-safe. Disallow path traversal."""
    clean = "".join(c if c.isalnum() or c in "-_" else "_" for c in tenant_id)
    if not clean or clean in (".", ".."):
        raise ValueError(f"invalid tenant_id: {tenant_id!r}")
    return clean[:128]
