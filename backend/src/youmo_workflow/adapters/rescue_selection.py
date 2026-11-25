from youmo_workflow.adapters.rescue_composition import (
    compose_output,
    rescue_call,
    validate_baseline,
)
from youmo_workflow.adapters.rescue_profile import V008Profile
from youmo_workflow.adapters.selection_completion import DeepSeekCompletion
from youmo_workflow.adapters.selection_journal import SelectionJournal, atomic_save
from youmo_workflow.adapters.selection_models import FrozenInitialCall, SavedCompletion
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.selection import InitialOutput, InitialRequest


class V008InitialSelector:
    def __init__(
        self,
        profile: V008Profile,
        journals: tuple[SelectionJournal, SelectionJournal],
        model: DeepSeekCompletion,
    ) -> None:
        self.profile, self.journals, self.model = profile, journals, model

    def response(self, journal: SelectionJournal, call: FrozenInitialCall) -> SavedCompletion:
        cached = journal.cached(call)
        if cached is not None:
            return cached
        key = self.model.credential()
        resume = (lambda: self.model.complete(call, key, True)) if self.model.retry_policy else None
        return journal.execute(call, lambda: self.model.complete(call, key), resume)

    def rank(self, request: InitialRequest) -> InitialOutput:
        baseline_journal, rescue_journal = self.journals
        base = baseline_journal.freeze(request, self.profile.freeze)
        if base.version != "v008":
            raise WorkflowError("SELECTION_VERSION_CHANGED", "v008 账本不能使用旧版冻结请求。")
        baseline = self.response(baseline_journal, base)
        baseline = self.model.checked(
            base,
            baseline,
            lambda item: validate_baseline(base, item),
            baseline_journal.folder(request.batch_id),
        )
        expected = rescue_call(base, baseline)
        frozen = rescue_journal.freeze(request, lambda _request: expected)
        if frozen != expected:
            raise WorkflowError(
                "SELECTION_INPUT_CHANGED", "补漏请求与已保存的基础响应不一致，未调用模型。"
            )
        rescue = self.response(rescue_journal, frozen)
        if rescue.finish_reason != "content_filter":
            rescue = self.model.checked(
                frozen,
                rescue,
                lambda item: compose_output(base, baseline, item, frozen.prompt_sha256),
                rescue_journal.folder(request.batch_id),
            )
        output = compose_output(base, baseline, rescue, frozen.prompt_sha256)
        # Composition is not an upstream response: retain it separately from both raw responses.
        atomic_save(
            rescue_journal.folder(request.batch_id) / "ranking.json", output.model_dump_json()
        )
        return output
