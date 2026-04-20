"""Version and pipeline identity.

PIPELINE_VERSION is pinned into every certificate. A cert and a runtime with
mismatched pipeline versions MUST NOT be considered equivalent — verification
semantics can drift between versions. The SDK refuses mismatched verification
by default.
"""

__version__ = "1.0.0"

# Bump when claim DAG semantics, oracle interpretation, or cert schema changes.
PIPELINE_VERSION = "pipeline/1.0.0"
CERT_SCHEMA_VERSION = "trustlens.cert/1.0"
