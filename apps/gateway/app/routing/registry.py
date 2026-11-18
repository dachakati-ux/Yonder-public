"""In-memory route and deployment registry for Phase 2."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.routing.models import DeploymentRecord, ModelAliasRoute, RouteState


@dataclass
class RouteRegistry:
    aliases: dict[str, ModelAliasRoute] = field(default_factory=dict)
    deployments: dict[str, DeploymentRecord] = field(default_factory=dict)

    def get_alias(self, alias: str) -> ModelAliasRoute | None:
        return self.aliases.get(alias)

    def list_enabled_aliases(self) -> list[ModelAliasRoute]:
        return [
            route
            for route in self.aliases.values()
            if route.state != RouteState.DISABLED
        ]

    def get_deployment(self, deployment_id: str) -> DeploymentRecord | None:
        return self.deployments.get(deployment_id)

    def upsert_alias(self, route: ModelAliasRoute) -> None:
        self.aliases[route.alias] = route

    def upsert_deployment(self, deployment: DeploymentRecord) -> None:
        self.deployments[deployment.id] = deployment
