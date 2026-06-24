"""
civitas.governance.lifecycle.states
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Document lifecycle state machine.

Valid transitions:
  DRAFT            → PENDING_REVIEW, REJECTED
  PENDING_REVIEW   → IN_REVIEW, REJECTED
  IN_REVIEW        → APPROVED, REJECTED
  APPROVED         → ACTIVE, DEPRECATED
  ACTIVE           → DEPRECATED
  DEPRECATED       → ARCHIVED, ACTIVE (reactivation)
  ARCHIVED         → PURGED
  REJECTED         → DRAFT (revision)
  Any              → PURGED (admin override only)
"""

from __future__ import annotations

from civitas.core.models.metadata import DocumentLifecycleState as State

# Transition map: from_state → set of allowed to_states
ALLOWED_TRANSITIONS: dict[State, set[State]] = {
    State.DRAFT:           {State.PENDING_REVIEW, State.REJECTED, State.PURGED},
    State.PENDING_REVIEW:  {State.IN_REVIEW, State.REJECTED, State.DRAFT, State.PURGED},
    State.IN_REVIEW:       {State.APPROVED, State.REJECTED, State.DRAFT, State.PURGED},
    State.APPROVED:        {State.ACTIVE, State.DEPRECATED, State.PURGED},
    State.ACTIVE:          {State.DEPRECATED, State.PURGED},
    State.DEPRECATED:      {State.ARCHIVED, State.ACTIVE, State.PURGED},
    State.ARCHIVED:        {State.PURGED},
    State.REJECTED:        {State.DRAFT, State.PURGED},
    State.PURGED:          set(),   # Terminal state
}

# States considered "accessible" for retrieval
RETRIEVABLE_STATES: set[State] = {State.APPROVED, State.ACTIVE}

# States considered "immutable" — mutations require versioning
IMMUTABLE_STATES: set[State] = {State.ACTIVE, State.ARCHIVED, State.PURGED}


def is_transition_allowed(from_state: State, to_state: State) -> bool:
    return to_state in ALLOWED_TRANSITIONS.get(from_state, set())


def can_retrieve(state: State) -> bool:
    return state in RETRIEVABLE_STATES


def requires_versioning(state: State) -> bool:
    return state in IMMUTABLE_STATES
