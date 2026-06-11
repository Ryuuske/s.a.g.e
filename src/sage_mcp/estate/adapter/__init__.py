"""sage_mcp.estate.adapter — Read adapters for the Estate Model.

Each adapter reads a specific data source and emits a building or sub-object
conforming to estate-model.schema.json.  Adapters are FILE-ONLY in Phase 1
(no ChromaDB, no network, no subprocess); the Nook adapter ships in Phase 3.

Phase 1 adapters:
- ``workshop`` — reads the agents dir + skills/rules/hooks dirs and emits the
  ``workshop`` building dict.

Security invariants (ADR-0003):
- All scanned paths are ``realpath``-confined to the declared root.
- ``followlinks=False`` on every ``os.walk`` call.
- The redactor (``sage.estate.redact``) is the single enforcement point for
  secrets and PII before values enter the model.
"""
