from dataclasses import dataclass


@dataclass(frozen=True)
class ResearchBenchmarkTask:
    id: str
    domain: str
    question: str


RESEARCH_BENCHMARK_TASKS = [
    ResearchBenchmarkTask("research_001", "agent-eval", "如何评估代码智能体修复失败测试的可靠性？"),
    ResearchBenchmarkTask("research_002", "agent-safety", "如何降低工具调用型智能体的越权风险？"),
    ResearchBenchmarkTask("research_003", "retrieval", "检索增强生成系统应如何评估证据覆盖率？"),
    ResearchBenchmarkTask("research_004", "llm-eval", "大语言模型评测中的数据污染应如何检测？"),
    ResearchBenchmarkTask("research_005", "long-context", "长上下文智能体的记忆召回质量如何测量？"),
    ResearchBenchmarkTask("research_006", "reproducibility", "机器学习实验怎样设计才能提高可复现性？"),
    ResearchBenchmarkTask("research_007", "human-feedback", "人工偏好数据如何支持智能体策略改进？"),
    ResearchBenchmarkTask("research_008", "software-testing", "自动修复系统如何避免修复测试而非修复实现？"),
    ResearchBenchmarkTask("research_009", "research-agents", "自主研究智能体的假设质量如何进行结构化评估？"),
    ResearchBenchmarkTask("research_010", "observability", "智能体 Trace 应包含哪些指标才能支持失败复盘？"),
]


def list_research_benchmark_tasks() -> list[ResearchBenchmarkTask]:
    return list(RESEARCH_BENCHMARK_TASKS)
