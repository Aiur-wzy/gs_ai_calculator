"""Static binding shape; importing a schema never instantiates handlers."""

BINDING_CONTRACT = "resolved-reaction-handler-v1"
BINDING_SCHEMA_VERSION = 1
BINDING_FIELDS = frozenset((
    "schema_version", "contract_id", "family", "contract_version", "implementation_version",
    "profile_schema_version", "capabilities", "handler_fingerprint", "registry_fingerprint",
))
