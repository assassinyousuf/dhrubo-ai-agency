import pytest
from dhrubo.workflows.engine import Workflow
from dhrubo.workflows.task import Task
from dhrubo.workflows.task_queue import InProcessTaskQueue
from dhrubo.workflows.website_audit_pipeline import (
    build_website_audit_workflow,
    plan_only,
)


def test_workflow_dag_validates_a_cyclic() -> None:
    wf = Workflow(name="t")
    wf.add(Task(task_id="a", role="x"))
    wf.add(Task(task_id="b", role="y", depends_on=["a"]))
    wf.validate()  # should not raise


def test_workflow_dag_detects_cycle() -> None:
    from dhrubo.core.errors import WorkflowError

    wf = Workflow(name="t")
    wf.add(Task(task_id="a", role="x"))
    wf.add(Task(task_id="b", role="y", depends_on=["a"]))
    # Make 'a' depend on 'b' (only feasible post-hoc via __dict__ mutation).
    wf.tasks[0].depends_on.append("b")
    with pytest.raises(WorkflowError):
        wf.validate()


def test_plan_only_website_audit_runs() -> None:
    plan_only()  # raises on invalid DAG


def test_website_audit_dag_shape() -> None:
    wf = build_website_audit_workflow()
    wf.validate()
    ids = {t.task_id for t in wf.tasks}
    # M2 ships a slim pipeline; later milestones will add more nodes.
    expected = {"plan", "crawl", "seo_review", "report", "export"}
    assert expected.issubset(ids)


def test_in_process_queue_roundtrip() -> None:
    import asyncio

    async def _go() -> None:
        q: InProcessTaskQueue = InProcessTaskQueue()
        t = Task(task_id="t", role="r")
        await q.put(t)
        got = await q.get()
        assert got.task_id == "t"
        await q.mark_done(got)

    asyncio.run(_go())
