from time import perf_counter
from typing import Any

from app.domain.schemas import ToolStatus
from app.tools.base import Tool, ToolContext, ToolResult


class ReportWriteTool(Tool):
    name = "report.write"
    description = "Write the final repair report."
    risk_level = "L1"
    input_schema = {"content": "string"}
    output_schema = {"written": "boolean"}

    async def call(self, input_data: dict[str, Any], context: ToolContext) -> ToolResult:
        started = perf_counter()
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SUCCESS,
            input=input_data,
            output={"written": True},
            duration_ms=int((perf_counter() - started) * 1000),
        )

