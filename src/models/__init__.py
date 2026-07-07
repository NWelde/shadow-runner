from typing import Optional

from pydantic import BaseModel, ConfigDict


class Job(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    status: str
    # GitHub's outcome field (success/failure/cancelled/...); distinct from the
    # lifecycle ``status`` (queued/in_progress/completed). Defaults to "" so older
    # callers/fixtures that predate this field still construct.
    conclusion: str = ""
    started_at: str
    duration_s: float


class Run(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    gh_run_id: str
    # The Workflow.id (crc32 of the workflow path) this run belongs to, so
    # per-workflow signals can scope to their own runs. 0 when unknown (e.g. data
    # ingested before this field existed).
    workflow_id: int = 0
    status: str
    created_at: str
    jobs: list[Job]


class Workflow(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    filename: str
    raw_yaml: str


class ShadowExperiment(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    hypothesis: str
    baseline_run: Run
    proposed_run: Run
    baseline_duration_s: float
    proposed_duration_s: float
    outcome: Optional[str]


class RepoProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    owner: str
    name: str
    workflows: list[Workflow]
    runs: list[Run]
    experiments: list[ShadowExperiment]
