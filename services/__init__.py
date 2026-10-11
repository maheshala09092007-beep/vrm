"""Service layer: one module per domain area.

Every service function takes a :class:`storage.Repository` as its first
argument, performs validation/business rules, mutates the in-memory records
and persists the affected collection after a successful operation.
"""
