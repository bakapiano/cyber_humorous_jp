from fastapi import APIRouter

from youmo_workflow.domain.batch_plan import BatchPlanState, BatchPlanUpdate, BatchTrigger
from youmo_workflow.domain.models import BatchDTO
from youmo_workflow.features.batch_plan import BatchPlanService
from youmo_workflow.features.batch_trigger import BatchTriggerService
from youmo_workflow.runtime import Runtime


def batch_plan_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    plan = BatchPlanService(runtime.factory, runtime.policy, runtime.tasks.clock)
    ready = runtime.settings.bilibili_ready
    trigger = BatchTriggerService(runtime.factory, runtime.policy, runtime.tasks.clock, ready)

    @router.get("/batch-plan", response_model=BatchPlanState)
    def get_plan() -> BatchPlanState:
        state = plan.state()
        state.publisher_ready = ready()
        if not state.publisher_ready:
            state.message = (
                "投稿登录态未就绪，投稿等待登录；选题和制作在容量内继续。" + state.message
            )
        return state

    @router.put("/batch-plan", response_model=BatchPlanState)
    def update_plan(body: BatchPlanUpdate) -> BatchPlanState:
        plan.update(body)
        return get_plan()

    @router.post("/production-batches", response_model=BatchDTO, status_code=202)
    def trigger_batch(body: BatchTrigger) -> BatchDTO:
        result = trigger.trigger(body)
        assert result is not None
        return result

    return router
