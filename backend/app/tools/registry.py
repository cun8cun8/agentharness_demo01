from app.domain.schemas import ToolCall, ToolStatus
from app.infra.idgen import id_generator
from app.infra.store import InMemoryStore
from app.policy.engine import default_policy_engine
from app.tools.base import Tool, ToolContext, ToolResult
from app.tools.file_tool import FileReadTool, FileWritePatchTool
from app.tools.git_tool import GitDiffTool
from app.tools.report_tool import ReportWriteTool
from app.tools.shell_tool import ShellRunTool
from app.tools.test_tool import TestRunTool


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    async def call_tool(
        self,
        name: str,
        input_data: dict,
        context: ToolContext,
        store: InMemoryStore,
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                tool_name=name,
                status=ToolStatus.FAILED,
                input=input_data,
                error_message="TOOL_NOT_FOUND",
            )
        policy = store.get_policy(context.policy_version_id)
        if policy is None:
            raise ValueError(f"Policy not found: {context.policy_version_id}")

        decision = default_policy_engine.evaluate_tool(
            tool_name=name,
            input_data=input_data,
            policy=policy,
            repo_path=context.repo_path,
        )
        approved_request = None
        if decision.requires_approval:
            approved_request = store.consume_approved_tool_approval(
                run_id=context.run_id,
                tool_name=name,
                input_data=input_data,
                policy_version_id=context.policy_version_id,
            )
            if approved_request is not None:
                decision = type(decision)(allowed=True)
        store.add_audit_log(
            action="tool.policy_decision",
            resource_type="tool",
            resource_id=name,
            decision="allow" if decision.allowed else "deny",
            actor_id=f"run:{context.run_id}",
            detail_json={
                "task_id": context.task_id,
                "step_id": context.step_id,
                "policy_version_id": context.policy_version_id,
                "reason": decision.reason,
                "requires_approval": decision.requires_approval,
                "approval_id": approved_request.id if approved_request is not None else None,
                "approval_consumed": approved_request is not None,
            },
        )
        if not decision.allowed:
            if decision.requires_approval:
                store.create_approval_request(
                    run_id=context.run_id,
                    step_id=context.step_id,
                    tool_name=name,
                    input_data=input_data,
                    reason=decision.reason,
                    policy_version_id=context.policy_version_id,
                )
            result = ToolResult(
                tool_name=name,
                status=ToolStatus.POLICY_BLOCKED,
                input=input_data,
                error_message=decision.reason,
            )
        else:
            if name == "file.read":
                input_data = {**input_data, "_protected_patterns": list(policy.protected_read_patterns)}
            result = await tool.call(input_data, context)

        tool_call = store.add_tool_call(
            ToolCall(
                id=id_generator.next("tool"),
                step_id=context.step_id,
                run_id=context.run_id,
                tool_name=name,
                input=result.input,
                output=result.output,
                status=result.status,
                duration_ms=result.duration_ms,
                error_message=result.error_message,
            )
        )
        store.add_audit_log(
            action="tool.call",
            resource_type="tool_call",
            resource_id=tool_call.id,
            decision=str(tool_call.status),
            actor_id=f"run:{context.run_id}",
            detail_json={
                "tool_name": name,
                "step_id": context.step_id,
                "task_id": context.task_id,
                "duration_ms": tool_call.duration_ms,
            },
        )
        return result


default_registry = ToolRegistry(
    tools=[
        FileReadTool(),
        FileWritePatchTool(),
        ShellRunTool(),
        GitDiffTool(),
        TestRunTool(),
        ReportWriteTool(),
    ]
)
