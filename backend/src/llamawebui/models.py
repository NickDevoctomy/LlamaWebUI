"""Initial persistence models."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RuntimeRecord(Base):
    __tablename__ = "runtimes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    executable_path: Mapped[str] = mapped_column(Text, unique=True)
    build: Mapped[str | None] = mapped_column(String(100))
    commit: Mapped[str | None] = mapped_column(String(40))
    backend: Mapped[str | None] = mapped_column(String(50))
    devices: Mapped[list[str]] = mapped_column(JSON, default=list)
    options: Mapped[list[str]] = mapped_column(JSON, default=list)
    option_details: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    help_sha256: Mapped[str] = mapped_column(String(64))
    flags_sha256: Mapped[str | None] = mapped_column(String(64))
    probe_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class SettingRecord(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ModelProfileRecord(Base):
    __tablename__ = "model_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alias: Mapped[str] = mapped_column(String(64), unique=True)
    runtime_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runtimes.id", ondelete="RESTRICT"), index=True
    )
    model_path: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    configuration: Mapped[dict[str, object]] = mapped_column(JSON)
    preset: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # The model needs the machine to itself: nothing else may be loaded beside it.
    exclusive: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    # The bundled recommendation this profile was added from, and the arguments it was
    # added with. Comparing against them tells whether the profile was changed since.
    recommendation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    recommendation_baseline: Mapped[list[dict[str, object]] | None] = mapped_column(
        JSON, nullable=True
    )
    # Download job whose completion enables this profile; cleared once acted on.
    enable_on_download: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Latest completed result per benchmark id. Kept when the runs themselves are deleted.
    benchmark_results: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ProfileSnapshotRecord(Base):
    """A profile's settings as they were at one moment, kept to return to later."""

    __tablename__ = "profile_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("model_profiles.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    created_by: Mapped[str] = mapped_column(String(64))
    # The assistant conversation that took it, if any.
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    runtime_id: Mapped[str] = mapped_column(String(36))
    model_path: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    configuration: Mapped[dict[str, object]] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean)
    exclusive: Mapped[bool] = mapped_column(Boolean)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BenchmarkRunRecord(Base):
    """One benchmark of a profile: its timings, speed samples and generated output."""

    __tablename__ = "benchmark_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("model_profiles.id", ondelete="CASCADE"), index=True
    )
    profile_alias: Mapped[str] = mapped_column(String(64))
    profile_version: Mapped[int] = mapped_column(Integer)
    runtime_id: Mapped[str] = mapped_column(String(36))
    benchmark_id: Mapped[str] = mapped_column(String(64))
    benchmark_title: Mapped[str] = mapped_column(String(200))
    prompt: Mapped[str] = mapped_column(Text)
    # None leaves generation bounded only by the profile's context size.
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Whether the model could read and write files in the run's own folder, and each
    # call it made, in order.
    tools: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # The tools it was given, by name; None for runs from before they could be chosen,
    # which had every tool.
    tool_names: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # The system prompt the run was sent with, if any, and its text at the time.
    system_prompt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Text added after the chosen system prompt, such as a model's own reasoning switch.
    system_prompt_extra: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "off", or an effort level for the chat template; None keeps the model's default.
    reasoning_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tool_calls: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, default=list, server_default=text("'[]'")
    )
    # The agent's task list from the planning tools: each task's number, title, status
    # (open, done or deleted) and whether the prompt or the agent added it.
    tasks: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, default=list, server_default=text("'[]'")
    )
    # For a run with the TypeScript tools, whether check_typescript was strict; None
    # for every other run.
    typescript_strict: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Whether each tool call kept the arguments the model sent and its whole result,
    # rather than a short excerpt, so failed calls can be looked into afterwards.
    verbose_log: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Whether the run compacted its history when the context filled; older runs never did.
    compact_context: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    # What generation spent its time on: reading, thinking, answering and tool calls.
    timeline: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, default=list, server_default=text("'[]'")
    )
    # Context and model memory use while generating, every few seconds: seconds since
    # the prompt was sent, tokens in context and its size, and the model process's RAM
    # (model file pages included) and VRAM in bytes. A reading not available is left out.
    resources: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, default=list, server_default=text("'[]'")
    )
    # running, completed, failed or cancelled; stage is the step a run reached.
    state: Mapped[str] = mapped_column(String(20))
    stage: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    samples: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    output: Mapped[str] = mapped_column(Text, default="")
    reasoning: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # What the profile launched with (model file, runtime and arguments), and a hash of
    # it, so runs of the same settings can be found; None for runs from before.
    launch_settings: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    settings_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The assistant conversation that started the run, if one did.
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # For a failed run, the router's log lines from the run (redacted), newest last.
    server_log: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)


class DownloadJobRecord(Base):
    __tablename__ = "download_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repo_id: Mapped[str] = mapped_column(String(400))
    revision: Mapped[str] = mapped_column(String(64))
    group_key: Mapped[str] = mapped_column(Text)
    files: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    destination: Mapped[str] = mapped_column(Text)
    total_bytes: Mapped[int] = mapped_column()
    completed_bytes: Mapped[int] = mapped_column(default=0)
    state: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class LogicalModelRecord(Base):
    __tablename__ = "logical_models"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    canonical_path: Mapped[str] = mapped_column(Text, unique=True)
    primary_path: Mapped[str] = mapped_column(Text)
    files: Mapped[list[str]] = mapped_column(JSON, default=list)
    attributes: Mapped[dict[str, object]] = mapped_column("metadata", JSON, default=dict)
    validation_state: Mapped[str] = mapped_column(String(20), default="valid")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class LogicalModelProfileRecord(Base):
    __tablename__ = "logical_model_profiles"

    logical_model_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("logical_models.id", ondelete="CASCADE"), primary_key=True
    )
    profile_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("model_profiles.id", ondelete="CASCADE"), primary_key=True
    )


class ServerRunRecord(Base):
    __tablename__ = "server_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    runtime_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("runtimes.id", ondelete="SET NULL"), index=True
    )
    endpoint: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20))
    pid: Mapped[int | None] = mapped_column()
    exit_code: Mapped[int | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class AccessTokenRecord(Base):
    __tablename__ = "access_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    last_four: Mapped[str] = mapped_column(String(4))
    expiry_note: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class RoleRecord(Base):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    protected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class PrivilegeRecord(Base):
    __tablename__ = "privileges"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    resource: Mapped[str] = mapped_column(String(50), index=True)
    access: Mapped[str] = mapped_column(String(10))


class RolePrivilegeRecord(Base):
    __tablename__ = "role_privileges"

    role_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    privilege_key: Mapped[str] = mapped_column(
        String(100), ForeignKey("privileges.key", ondelete="CASCADE"), primary_key=True
    )


class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    role_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("roles.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class SessionRecord(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class AssistantConversationRecord(Base):
    """A chat with the assistant, and the optimisation brief it works to."""

    __tablename__ = "assistant_conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[str] = mapped_column(String(64))
    # The OpenRouter model of the latest turn; a conversation can outlive a model change.
    model_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    brief: Mapped[dict[str, object]] = mapped_column(JSON)
    # idle, running, paused (spend cap reached), interrupted or failed.
    state: Mapped[str] = mapped_column(String(20), default="idle")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What OpenRouter reported the conversation's replies cost, in US dollars.
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0"))
    # Spending stops at this; continuing after a pause raises it by the configured cap.
    spend_limit_usd: Mapped[float] = mapped_column(Float)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # The router as the first turn began: {"runtime_id": <id, or None if stopped>}. The
    # assistant leaves it as it found it.
    router_at_start: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class AssistantMessageRecord(Base):
    """One entry of a conversation: a user message, a reply, a tool result or an event."""

    __tablename__ = "assistant_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("assistant_conversations.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    # user, assistant, tool or event (brief edits, pauses and other notes for the reader).
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    reasoning: Mapped[str] = mapped_column(Text, default="")
    # A reply's tool calls as sent back to the model: id, function name and arguments.
    tool_calls: Mapped[list[dict[str, object]] | None] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # What the UI shows for a tool result or event, apart from the text the model saw.
    summary: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    usage: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AssistantApprovalRecord(Base):
    """An action the assistant asked the operator to approve before taking it."""

    __tablename__ = "assistant_approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("assistant_conversations.id", ondelete="CASCADE"), index=True
    )
    tool_call_id: Mapped[str] = mapped_column(String(100))
    tool_name: Mapped[str] = mapped_column(String(100))
    # downloads, runtimes, profiles or benchmarks.
    category: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(300))
    details: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    # pending, approved, rejected, or expired when its turn ended before a decision.
    state: Mapped[str] = mapped_column(String(20), default="pending")
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
