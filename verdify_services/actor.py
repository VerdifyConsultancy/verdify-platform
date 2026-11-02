"""Who writes: the actor every write service takes (architecture §5.6 and §6, R4).

Adapters build it: the operator API from the signed-in session (#1148), MCP from the caller's token in the
request context (#1172). A service never reads ``actor``, ``source`` or a ``*_by`` value from request
arguments, and a write without an actor is refused with ``forbidden``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from verdify_schemas.activity import ACTOR_NAME_MAX_LENGTH, ACTOR_SOURCE_PATTERN

Role = Literal["operator", "viewer"]


class ActorRef(BaseModel):
    """Who wrote, as responses carry it: the OpenAPI ``Actor`` schema (``source`` and ``name``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(pattern=ACTOR_SOURCE_PATTERN)
    name: str = Field(min_length=1, max_length=ACTOR_NAME_MAX_LENGTH)


class Actor(BaseModel):
    """The caller of a service: ``(source, name, role)``.

    ``source`` is ``dashboard``, ``iris``, ``system`` or ``agent:<name>``. ``role`` is ``operator`` or
    ``viewer`` for a ``dashboard`` person and None for every other source. Use the constructors.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(pattern=ACTOR_SOURCE_PATTERN)
    name: str = Field(min_length=1, max_length=ACTOR_NAME_MAX_LENGTH)
    role: Role | None = None

    @model_validator(mode="after")
    def _role_only_for_people(self) -> Actor:
        if self.source == "dashboard" and self.role is None:
            raise ValueError("a dashboard actor needs a role: operator or viewer")
        if self.source != "dashboard" and self.role is not None:
            raise ValueError("only a dashboard actor has a role")
        return self

    @classmethod
    def person(cls, username: str, role: Role) -> Actor:
        """A signed-in person: (``dashboard``, username, role)."""
        return cls(source="dashboard", name=username, role=role)

    @classmethod
    def iris(cls) -> Actor:
        """Iris: (``iris``, ``iris``, None)."""
        return cls(source="iris", name="iris")

    @classmethod
    def agent(cls, name: str) -> Actor:
        """A named MCP agent: (``agent:<name>``, name, None). The admin MCP token is ``Actor.agent("admin")``."""
        return cls(source=f"agent:{name}", name=name)

    @classmethod
    def system(cls, name: str) -> Actor:
        """The platform itself: (``system``, name, None), for example ``forecast-engine``."""
        return cls(source="system", name=name)

    def ref(self) -> ActorRef:
        """The ``Actor`` a response shows for this caller."""
        return ActorRef(source=self.source, name=self.name)
