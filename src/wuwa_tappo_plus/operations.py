"""Progress and outcomes shared by lifecycle services and their frontends."""

from dataclasses import dataclass, field
from typing import Callable, Literal


@dataclass(frozen=True)
class StepResult:
    step: str
    status: Literal["completed", "skipped", "warning", "failed"]
    message: str


@dataclass
class OperationResult:
    status: Literal["completed", "failed", "handed_off"] = "completed"
    steps: list[StepResult] = field(default_factory=list)

    @property
    def exit_code(self):
        return 1 if self.status == "failed" else 0


class Progress:
    def __init__(self, callback: Callable[[StepResult], None] | None = None):
        self.callback = callback
        self.result = OperationResult()
        self.stage = "prepare"

    def add(self, status, message):
        step = StepResult(self.stage, status, message)
        self.result.steps.append(step)
        if status == "failed":
            self.result.status = "failed"
        if self.callback:
            self.callback(step)
        return self.result

    def include(self, result):
        # The nested service already delivered progress through our callback.
        self.result.steps.extend(result.steps)
        if result.status == "failed":
            self.result.status = "failed"
        return result.exit_code == 0
