"""Pydantic settings models for Yonder."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class YonderEnvironment(StrEnum):
    DEVELOPMENT = "development"
    LAB = "lab"
    PRODUCTION = "production"


class SecurityMode(StrEnum):
    DEVELOPMENT = "development"
    TRUSTED_INGRESS = "trusted_ingress"
    OIDC = "oidc"


class OidcConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_template: Literal[
        "generic",
        "authentik",
        "keycloak",
        "entra",
        "google_workspace",
    ] = "generic"
    issuer: str = ""
    client_id: str = ""
    client_secret: str | None = Field(default=None, repr=False)
    redirect_uri: str = ""
    scopes: tuple[str, ...] = ("openid", "profile", "email", "groups")
    subject_claim: str = "sub"
    groups_claim: str = "groups"
    session_secret: str | None = Field(default=None, repr=False)
    session_cookie_name: str = "yonder_session"
    session_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    role_mappings: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    role_capabilities: dict[str, tuple[str, ...]] = Field(
        default_factory=lambda: {
            "student": ("chat.use", "models.view"),
            "teacher": ("chat.use", "coder.use", "models.view", "lab.use"),
            "it_admin": (
                "chat.use",
                "coder.use",
                "models.view",
                "models.manage",
                "hardware.view",
                "operations.view",
                "operations.manage",
            ),
            "security_admin": (
                "models.view",
                "hardware.view",
                "operations.view",
                "operations.manage",
            ),
        }
    )
    allow_insecure_http_in_dev: bool = False
    discovery_timeout_seconds: float = Field(default=10.0, gt=0.0, le=60.0)


class DevelopmentPrincipalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = "yonder-local-developer"
    principal_type: str = "user"
    roles: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = (
        "chat.use",
        "chat.experimental",
        "speech.use",
        "coder.use",
        "models.view",
        "models.manage",
        "hardware.view",
        "operations.view",
        "operations.manage",
        "workers.report",
    )
    attributes: dict[str, str] = Field(default_factory=dict)


class SecurityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: SecurityMode = SecurityMode.DEVELOPMENT
    development_principal: DevelopmentPrincipalConfig = Field(
        default_factory=DevelopmentPrincipalConfig
    )
    # Trusted ingress provider looks for these header names only when configured.
    # Do not invent corporate header conventions beyond explicit configuration.
    trusted_subject_header: str = "X-Yonder-Trusted-Subject"
    trusted_principal_type_header: str = "X-Yonder-Trusted-Principal-Type"
    trusted_capabilities_header: str = "X-Yonder-Trusted-Capabilities"
    trusted_roles_header: str = "X-Yonder-Trusted-Roles"
    trusted_groups_header: str = "X-Yonder-Trusted-Groups"
    trusted_attributes_header: str = "X-Yonder-Trusted-Attributes"
    trusted_request_id_header: str = "X-Request-Id"
    # Shared secret for gateway<->worker service calls. This is infrastructure
    # trust between Yonder services, not corporate user authentication.
    # Set via YONDER_SECURITY__WORKER_SHARED_SECRET; never commit real values.
    # Required (non-empty) when environment=production.
    worker_shared_secret: str | None = Field(default=None, repr=False)
    worker_token_header: str = "X-Yonder-Worker-Token"
    # Optional ingress authenticity controls for trusted-header mode (M03.1).
    trusted_ingress_shared_secret: str | None = Field(default=None, repr=False)
    trusted_ingress_secret_header: str = "X-Yonder-Ingress-Secret"
    trusted_proxy_cidrs: tuple[str, ...] = ()
    oidc: OidcConfig = Field(default_factory=lambda: OidcConfig())

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def validate_proxy_cidrs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        import ipaddress

        for item in value:
            ipaddress.ip_network(item, strict=False)
        return value


class ServiceBindConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    port: int = 8080

    @field_validator("port")
    @classmethod
    def port_in_range(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return value


class WorkerStaticRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    endpoint: str
    enabled: bool = True
    display_name: str | None = None


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str = "INFO"
    json_logs: bool = True
    log_prompt_and_response_content: bool = False


class AdmissionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_prompt_chars: int = 100_000
    max_output_tokens: int = 4096
    max_active_requests_per_deployment: int = 8
    max_queue_depth: int = 32
    queue_timeout_seconds: float = 30.0
    inference_timeout_seconds: float = 120.0


class ContextWindowConfig(BaseModel):
    """Gateway-owned chat context pressure + auto-condense policy.

    `chars_per_token_estimate` is for soft pressure only. It must never be
    exported as authoritative `prompt_tokens`.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    soft_ratio: float = Field(default=0.70, gt=0.0, le=1.0)
    chars_per_token_estimate: int = Field(default=4, ge=1)
    keep_recent_messages: int = Field(default=12, ge=2)
    summary_max_tokens: int = Field(default=512, ge=32)


class ConversationRetentionConfig(BaseModel):
    """Retention controls for conversation stores (memory or database)."""

    model_config = ConfigDict(extra="forbid")

    max_conversations_per_principal: int = 100
    max_age_days: int | None = 30
    max_messages_per_conversation: int = 500

    @field_validator("max_conversations_per_principal", "max_messages_per_conversation")
    @classmethod
    def positive_limits(cls, value: int) -> int:
        if value < 1:
            raise ValueError("retention limits must be >= 1")
        return value

    @field_validator("max_age_days")
    @classmethod
    def age_non_negative(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("max_age_days must be >= 0 or null")
        return value


class RoutingPolicyConfig(BaseModel):
    """Experimental vs production lane policy."""

    model_config = ConfigDict(extra="forbid")

    # When true, experimental aliases require experimental_capability.
    require_capability_for_experimental: bool = True
    experimental_capability: str = "chat.experimental"
    # Production aliases are the default when experimental=false.
    default_lane: str = "production"


class WorkerRegistrationPolicyConfig(BaseModel):
    """Trust policy stub for worker self-registration."""

    model_config = ConfigDict(extra="forbid")

    mode: str = "allow_all"
    # When mode=allowlist, only these worker ids may register as trusted.
    allowed_worker_ids: tuple[str, ...] = ()
    # When mode=allowlist and unknown workers register, accept but mark untrusted.
    accept_untrusted: bool = True

    @field_validator("mode")
    @classmethod
    def known_mode(cls, value: str) -> str:
        allowed = {"allow_all", "allowlist"}
        if value not in allowed:
            raise ValueError(f"mode must be one of {sorted(allowed)}")
        return value

    @field_validator("allowed_worker_ids", mode="before")
    @classmethod
    def split_worker_ids(cls, value: object) -> object:
        if value is None or value == "":
            return ()
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                parsed = json.loads(text)
                return tuple(str(item) for item in parsed)
            return tuple(part.strip() for part in text.split(",") if part.strip())
        return value


class LlamaCppProviderConfig(BaseModel):
    """Connection settings for an operator-managed llama.cpp runtime."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    base_url: str = "http://127.0.0.1:8000"
    served_model: str | None = None
    worker_id: str = "local-dev-worker"
    request_timeout_seconds: float = Field(default=120.0, gt=0)
    # Aliases to bind to the static operator-managed endpoint at startup.
    # For each alias the gateway registers an external READY deployment and
    # verifies the served model against the endpoint's /v1/models on boot —
    # binding fails honestly when the runtime is down or serves a different
    # model. Works for any OpenAI-compatible server (llama.cpp, LM Studio).
    attach_aliases: Annotated[list[str], NoDecode] = Field(default_factory=list)
    attach_context_length: int = Field(default=8192, gt=0)

    @field_validator("attach_aliases", mode="before")
    @classmethod
    def split_attach_aliases(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("base_url must use http:// or https://")
        return normalized


class LlamaCppWorkerRuntimeConfig(BaseModel):
    """Worker-side llama.cpp process manager settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    binary_path: Path = Path("/opt/yonder/runtimes/llama.cpp-b10167-cuda-sm70/bin/llama-server")
    library_path: Path | None = Path("/opt/cuda-12.6/lib64")
    listen_host: str = "127.0.0.1"
    base_port: int = Field(default=8100, ge=1024, le=60000)
    ctx_size: int = Field(default=8192, ge=512)
    n_gpu_layers: int = Field(default=999, ge=0)
    threads: int = Field(default=8, ge=1)
    health_timeout_seconds: float = Field(default=120.0, gt=0)
    vram_reserve_gb: float = Field(
        default=1.0,
        ge=0,
        description="VRAM headroom kept free on every selected GPU.",
    )
    max_lanes_per_gpu: int = Field(default=3, ge=1, le=8)
    multi_gpu_split_mode: Literal["layer", "row"] = "layer"
    # When true, acknowledge loads without spawning (tests / dry-run).
    dry_run: bool = False
    # Model/mmproj paths must resolve under one of these roots (fail closed).
    allowed_model_roots: Annotated[list[Path], NoDecode] = Field(
        default_factory=lambda: [
            Path("/srv/yonder-storage/model-bundles"),
            Path("/srv/yonder/models"),
            Path("vendor/model-bundles"),
        ]
    )
    # Optional per-manifest binary_path must resolve under one of these roots.
    allowed_runtime_roots: Annotated[list[Path], NoDecode] = Field(
        default_factory=lambda: [
            Path("/opt/yonder/runtimes"),
        ]
    )

    @field_validator("allowed_model_roots", mode="before")
    @classmethod
    def split_allowed_model_roots(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                parsed = json.loads(text)
                return [Path(str(item)) for item in parsed]
            return [Path(part.strip()) for part in text.split(",") if part.strip()]
        if isinstance(value, (list, tuple)):
            return [Path(str(item)) for item in value]
        return value

    @field_validator("allowed_runtime_roots", mode="before")
    @classmethod
    def split_allowed_runtime_roots(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                parsed = json.loads(text)
                return [Path(str(item)) for item in parsed]
            return [Path(part.strip()) for part in text.split(",") if part.strip()]
        if isinstance(value, (list, tuple)):
            return [Path(str(item)) for item in value]
        return value


class DiffusersProviderConfig(BaseModel):
    """Gateway connection settings for a worker-owned Diffusers runtime."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    worker_base_url: str = "http://127.0.0.1:8090"
    request_timeout_seconds: float = Field(default=300.0, gt=0)

    @field_validator("worker_base_url")
    @classmethod
    def normalize_worker_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("worker_base_url must use http:// or https://")
        return normalized


class CoderConfig(BaseModel):
    """Gateway-local coder workspaces (path jail + optional write/pipeline)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    cage_root: Path = Path("data/coder-cages")
    grantable_roots: Annotated[list[Path], NoDecode] = Field(default_factory=list)
    max_read_bytes: int = Field(default=262_144, gt=0)
    max_list_entries: int = Field(default=200, gt=0)
    max_search_hits: int = Field(default=50, gt=0)
    # Explicit later phase — keep false until proof gates pass.
    write_unlock: bool = False
    check_allowlist: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "pytest",
            "python -m pytest",
            "python3 -m pytest",
            "npm test",
            "npm run lint",
            "npm run build",
            "ruff",
            "ruff check",
            "mypy",
        ]
    )
    check_timeout_seconds: int = Field(default=120, gt=0)
    # Isolation for run_check subprocesses. "none" executes as the gateway
    # user (current behavior; documented risk), "systemd-run" adds resource
    # limits, "podman" adds a no-network container. When a mode other than
    # none is configured but its binary is unavailable, checks fail closed
    # (isolation_unavailable) — never a silent fallback to none.
    check_isolation: str = "none"
    check_isolation_image: str = "registry.access.redhat.com/ubi9/python-312"

    @field_validator("check_isolation")
    @classmethod
    def validate_check_isolation(cls, value: str) -> str:
        normalized = (value or "none").strip().lower()
        if normalized not in {"none", "systemd-run", "podman"}:
            raise ValueError("check_isolation must be none, systemd-run, or podman")
        return normalized

    # Agent loop budgets. There is no deterministic/pretend mode — the agent
    # always calls the configured local model or the run fails honestly.
    max_agent_turns: int = Field(default=12, gt=0)
    max_fix_iterations: int = Field(default=3, ge=0)
    max_model_calls: int = Field(default=200, gt=0)
    max_wall_clock_seconds: int = Field(default=14_400, gt=0)
    # Prompt-assembly budget (estimated tokens) and per-call output cap.
    context_budget_tokens: int = Field(default=6_000, gt=0)
    max_output_tokens: int = Field(default=1_024, gt=0)
    model_alias: str = "yonder-coder"
    # Optional second alias for verifier B (independence); empty = same model.
    verifier_model_alias: str = ""
    max_concurrent_runs: int = Field(default=2, ge=1, le=8)
    # Qualified MCP tool names (mcp.<server>.<tool>) the coder agent may call.
    # Empty (default) = no MCP tools for the coder (fail closed). Tools are
    # registered per-run with the run's principal + id for audit — never via
    # the global mcp.bridge_into_tool_executor.
    mcp_tools: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Gateway-native docs search (RAG over Yonder docs + vendored library docs).
    docs_search_enabled: bool = True
    docs_max_results: int = Field(default=5, ge=1, le=20)
    # Phase 2 hybrid retrieval. Off by default (lexical); enable on hosts with a
    # READY embedding alias. Falls back to lexical if the embed backend fails —
    # never fabricates vectors.
    docs_embeddings_enabled: bool = False
    docs_embed_alias: str = "yonder-embed"
    docs_rerank_alias: str = ""
    docs_hybrid_alpha: float = Field(default=0.5, ge=0.0, le=1.0)
    forgejo_base_url: str = ""
    forgejo_token_ref: str = ""
    # Phase L delivery: when set (with a configured Forgejo adapter), the coder
    # opens a PR after the local push. Empty owner = local push only.
    forgejo_owner: str = ""
    forgejo_base_branch: str = "main"

    @field_validator("mcp_tools", mode="before")
    @classmethod
    def split_mcp_tools(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("check_allowlist", mode="before")
    @classmethod
    def split_check_allowlist(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                return json.loads(text)
            return [part.strip() for part in text.split(",") if part.strip()]
        return value

    @field_validator("grantable_roots", mode="before")
    @classmethod
    def split_grantable_roots(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                return [Path(str(item)) for item in json.loads(text)]
            return [Path(part.strip()) for part in text.split(",") if part.strip()]
        if isinstance(value, (list, tuple)):
            return [Path(str(item)) for item in value]
        return value


class McpRemoteServerConfig(BaseModel):
    """One external MCP server the Gateway may call as a client."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    transport: str = "stdio"  # stdio | http
    command: str = ""
    args: Annotated[list[str], NoDecode] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    timeout_seconds: float = Field(default=30.0, gt=0)

    @field_validator("transport")
    @classmethod
    def validate_transport(cls, value: str) -> str:
        normalized = (value or "stdio").strip().lower()
        if normalized not in {"stdio", "http"}:
            raise ValueError("transport must be stdio or http")
        return normalized

    @field_validator("args", mode="before")
    @classmethod
    def split_args(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                return [str(item) for item in json.loads(text)]
            return [part for part in text.split(" ") if part]
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(c in cleaned for c in "./ \\"):
            raise ValueError("MCP server name must be a simple identifier")
        return cleaned


class McpConfig(BaseModel):
    """Gateway-mediated MCP client + optional IDE-facing MCP server."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    server_enabled: bool = False
    server_bind: str = "127.0.0.1"
    server_port: int = Field(default=8095, ge=1, le=65535)
    server_tools: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "yonder_gateway_status",
            "yonder_capacity_aliases",
            "yonder_list_platform_tools",
            "yonder_execute_platform_tool",
        ]
    )
    servers: list[McpRemoteServerConfig] = Field(default_factory=list)
    http_url_allowlist: Annotated[list[str], NoDecode] = Field(default_factory=list)
    bridge_into_tool_executor: bool = False
    call_timeout_seconds: float = Field(default=30.0, gt=0)

    @field_validator("server_bind")
    @classmethod
    def loopback_preferred(cls, value: str) -> str:
        bind = (value or "127.0.0.1").strip() or "127.0.0.1"
        return bind

    @field_validator("server_tools", "http_url_allowlist", mode="before")
    @classmethod
    def split_string_lists(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                return [str(item).strip() for item in json.loads(text) if str(item).strip()]
            return [part.strip() for part in text.split(",") if part.strip()]
        return value


class SpeechProviderConfig(BaseModel):
    """Gateway connection settings for a worker-owned STT/TTS runtime."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    worker_base_url: str = "http://127.0.0.1:8090"
    request_timeout_seconds: float = Field(default=120.0, gt=0)
    stt_alias: str = "yonder-stt"
    tts_alias: str = "yonder-tts"

    @field_validator("worker_base_url")
    @classmethod
    def normalize_worker_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("worker_base_url must use http:// or https://")
        return normalized


class SpeechWorkerRuntimeConfig(BaseModel):
    """Worker-side STT/TTS runtime settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    dry_run: bool = False
    allowed_model_roots: Annotated[list[Path], NoDecode] = Field(
        default_factory=lambda: [Path("/srv/yonder-storage/model-bundles")]
    )
    output_root: Path = Path("/var/lib/yonder/generated")
    device: str = "cuda"
    # Optional explicit binary/paths when bundles are present.
    whisper_model_path: Path | None = None
    piper_model_path: Path | None = None
    piper_binary: Path | None = None

    @field_validator("allowed_model_roots", mode="before")
    @classmethod
    def split_allowed_model_roots(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                return [Path(str(item)) for item in json.loads(text)]
            return [Path(part.strip()) for part in text.split(",") if part.strip()]
        if isinstance(value, (list, tuple)):
            return [Path(str(item)) for item in value]
        return value


class CapacitySchedulerConfig(BaseModel):
    """Gateway EnsureReady / multi-lane capacity policy.

    VRAM admission stays fail-closed on measured free memory. Lane caps limit
    how many occupying deployments may share a physical GPU index.
    """

    model_config = ConfigDict(extra="forbid")

    max_lanes_per_gpu: int = Field(default=3, ge=1, le=8)
    idle_evict_seconds: float = Field(default=60.0, ge=0.0)
    chat_protect_sole_lane: bool = True
    chat_alias: str = "yonder-chat"
    chat_demotion_ladder: tuple[str, ...] = ()
    never_evict_aliases: tuple[str, ...] = ("yonder-chat",)
    ensure_ready_enabled: bool = True


class DiffusersWorkerRuntimeConfig(BaseModel):
    """Worker-side image/video Diffusers pipeline settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    dry_run: bool = False
    max_lanes_per_gpu: int = Field(default=3, ge=1, le=8)
    allowed_model_roots: Annotated[list[Path], NoDecode] = Field(
        default_factory=lambda: [Path("/srv/yonder-storage/model-bundles")]
    )
    # Generated files stay under this root until the Gateway fetches them.
    # Prefer a per-host writable path; NFS model bundles may be mounted read-only.
    output_root: Path = Path("/var/lib/yonder/generated")
    device: str = "cuda"
    dtype: str = "float16"

    @field_validator("allowed_model_roots", mode="before")
    @classmethod
    def split_allowed_model_roots(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                return [Path(str(item)) for item in json.loads(text)]
            return [Path(part.strip()) for part in text.split(",") if part.strip()]
        if isinstance(value, (list, tuple)):
            return [Path(str(item)) for item in value]
        return value

    @field_validator("device")
    @classmethod
    def cuda_only(cls, value: str) -> str:
        if value != "cuda":
            raise ValueError("Diffusers worker device must be cuda")
        return value

    @field_validator("dtype")
    @classmethod
    def fp16_only(cls, value: str) -> str:
        if value != "float16":
            raise ValueError("Diffusers worker dtype must be float16")
        return value


class YonderSettings(BaseSettings):
    """Application settings with layered overlays applied by the loader."""

    model_config = SettingsConfigDict(
        env_prefix="YONDER_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    environment: YonderEnvironment = YonderEnvironment.DEVELOPMENT
    service_name: str = "yonder"
    config_file: Path | None = None
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    gateway: ServiceBindConfig = Field(
        default_factory=lambda: ServiceBindConfig(host="127.0.0.1", port=8080)
    )
    worker: ServiceBindConfig = Field(
        default_factory=lambda: ServiceBindConfig(host="127.0.0.1", port=8090)
    )
    worker_id: str = "local-dev-worker"
    worker_display_name: str = "Local development worker"
    gateway_url: str = "http://127.0.0.1:8080"
    # Reachable URL the Gateway uses for lifecycle callbacks. When unset, falls
    # back to http://{worker.host}:{worker.port} (wrong when host is 0.0.0.0).
    worker_public_endpoint: str | None = None
    console_origin: str = "http://127.0.0.1:3000"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:3000", "http://localhost:3000"]
    )
    allow_non_localhost_bind_in_development: bool = False
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    admission: AdmissionConfig = Field(default_factory=AdmissionConfig)
    capacity: CapacitySchedulerConfig = Field(default_factory=CapacitySchedulerConfig)
    context_window: ContextWindowConfig = Field(default_factory=ContextWindowConfig)
    conversations: ConversationRetentionConfig = Field(
        default_factory=ConversationRetentionConfig
    )
    routing: RoutingPolicyConfig = Field(default_factory=RoutingPolicyConfig)
    worker_registration: WorkerRegistrationPolicyConfig = Field(
        default_factory=WorkerRegistrationPolicyConfig
    )
    llama_cpp: LlamaCppProviderConfig = Field(default_factory=LlamaCppProviderConfig)
    llama_cpp_runtime: LlamaCppWorkerRuntimeConfig = Field(
        default_factory=LlamaCppWorkerRuntimeConfig
    )
    diffusers: DiffusersProviderConfig = Field(default_factory=DiffusersProviderConfig)
    diffusers_runtime: DiffusersWorkerRuntimeConfig = Field(
        default_factory=DiffusersWorkerRuntimeConfig
    )
    speech: SpeechProviderConfig = Field(default_factory=SpeechProviderConfig)
    speech_runtime: SpeechWorkerRuntimeConfig = Field(
        default_factory=SpeechWorkerRuntimeConfig
    )
    coder: CoderConfig = Field(default_factory=CoderConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)
    worker_heartbeat_timeout_seconds: float = 30.0
    state_file: Path = Path("data/gateway-state.json")
    model_roots: Annotated[list[Path], NoDecode] = Field(
        default_factory=lambda: [Path("vendor/model-bundles"), Path("/srv/yonder/models")]
    )
    workers: list[WorkerStaticRegistration] = Field(default_factory=list)
    database_url: str | None = None
    admin_event_limit: int = 500
    request_ledger_limit: int = 200
    shutting_down_reject_new_requests: bool = True

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_cors_origins(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                parsed = json.loads(text)
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [part.strip() for part in text.split(",") if part.strip()]
        return value

    @field_validator("model_roots", mode="before")
    @classmethod
    def split_model_roots(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                return [Path(str(item).strip()) for item in json.loads(text) if str(item).strip()]
            return [Path(part.strip()) for part in text.split(",") if part.strip()]
        return value

    @model_validator(mode="after")
    def ensure_console_origin_in_cors(self) -> YonderSettings:
        origin = (self.console_origin or "").strip()
        if origin and origin not in self.cors_origins:
            self.cors_origins = [*self.cors_origins, origin]
        return self

    def is_production(self) -> bool:
        return self.environment == YonderEnvironment.PRODUCTION

    def export_public(self) -> dict[str, Any]:
        from yonder_configuration.loader import redact_settings

        return redact_settings(self)
