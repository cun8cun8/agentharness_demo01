import json
import re
import urllib.error
import urllib.parse
import urllib.request

from app.config import get_settings
from app.domain.schemas import CreateResearchBriefRequest, ResearchPaper
from app.infra.idgen import id_generator


def search_research_papers(request: CreateResearchBriefRequest) -> tuple[list[ResearchPaper], dict]:
    settings = get_settings()
    provider = settings.research_provider.lower()
    if provider in {"arxiv", "semantic_scholar"}:
        if not settings.network_enabled:
            raise ValueError("NETWORK_DISABLED")
        return _search_external(request, provider)
    if provider == "crossref" and settings.network_enabled:
        papers, summary = _search_crossref(request)
        if papers:
            return papers, summary
        if not settings.allow_mock_models:
            return [], summary
    if not settings.allow_mock_models:
        raise ValueError("RESEARCH_PROVIDER_NOT_CONFIGURED")
    papers = local_research_papers(request, source="local_fallback" if provider != "local" else "local")
    return papers, {
        "provider": "local",
        "network_enabled": settings.network_enabled,
        "fallback": provider != "local",
        "paper_count": len(papers),
    }


def local_research_papers(
    request: CreateResearchBriefRequest,
    source: str = "local",
) -> list[ResearchPaper]:
    max_papers = max(1, min(request.max_papers, 10))
    templates = [
        ("系统综述", "总结该领域的核心方法、证据等级和常见限制。"),
        ("基线复现", "报告公开基线、实验设置和可重复性风险。"),
        ("误差分析", "分析失败样本、数据偏差和指标敏感性。"),
        ("消融实验", "比较关键变量对最终结果的影响。"),
        ("部署观察", "讨论工程落地中的成本、延迟和安全边界。"),
    ]
    papers: list[ResearchPaper] = []
    for index in range(max_papers):
        label, abstract = templates[index % len(templates)]
        papers.append(
            ResearchPaper(
                id=id_generator.next("paper"),
                title=f"{request.domain}：{request.question} 的{label}",
                domain=request.domain,
                authors=[f"ResearchForge Author {index + 1}"],
                year=2023 + (index % 4),
                source=source,
                url=f"https://example.local/research/{request.domain}/{index + 1}",
                abstract=abstract,
                evidence_snippets=[
                    f"证据 {index + 1}：{abstract}",
                    "建议保留引用、假设和实验之间的可追溯关系。",
                ],
            )
        )
    return papers


def _search_crossref(request: CreateResearchBriefRequest) -> tuple[list[ResearchPaper], dict]:
    settings = get_settings()
    rows = max(1, min(request.max_papers, 10))
    params = urllib.parse.urlencode(
        {
            "query.bibliographic": f"{request.question} {request.domain}",
            "rows": rows,
            "select": "DOI,title,author,published-print,published-online,URL,abstract",
        }
    )
    headers = {"User-Agent": "ResearchForge Agent Harness/0.1"}
    if settings.research_crossref_mailto:
        headers["User-Agent"] += f" (mailto:{settings.research_crossref_mailto})"
    http_request = urllib.request.Request(
        f"https://api.crossref.org/works?{params}",
        headers=headers,
        method="GET",
    )
    try:
        with urllib.request.urlopen(http_request, timeout=settings.research_timeout_seconds) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return [], {
            "provider": "crossref",
            "network_enabled": True,
            "fallback": True,
            "error": str(exc),
            "paper_count": 0,
        }

    items = (raw.get("message") or {}).get("items") or []
    papers = [_crossref_item_to_paper(item) for item in items[:rows]]
    papers = [paper for paper in papers if paper.title]
    for paper in papers:
        paper.domain = request.domain
    return papers, {
        "provider": "crossref",
        "network_enabled": True,
        "fallback": False,
        "paper_count": len(papers),
    }


def _crossref_item_to_paper(item: dict) -> ResearchPaper:
    title_values = item.get("title") or []
    title = str(title_values[0]) if title_values else "Untitled Crossref work"
    abstract = _strip_markup(str(item.get("abstract") or ""))
    authors = [
        " ".join(
            part
            for part in [author.get("given"), author.get("family")]
            if part
        )
        for author in item.get("author") or []
    ]
    authors = [author for author in authors if author]
    evidence = [abstract[:260]] if abstract else [f"Crossref 题录命中：{title}"]
    doi = str(item.get("DOI") or "")
    url = str(item.get("URL") or "")
    if not url and doi:
        url = f"https://doi.org/{doi}"
    return ResearchPaper(
        id=id_generator.next("paper"),
        title=title,
        authors=authors,
        year=_crossref_year(item),
        source="crossref",
        url=url or None,
        abstract=abstract,
        evidence_snippets=evidence,
    )


def _crossref_year(item: dict) -> int | None:
    for key in ["published-print", "published-online"]:
        date_parts = (item.get(key) or {}).get("date-parts") or []
        if date_parts and date_parts[0]:
            try:
                return int(date_parts[0][0])
            except (TypeError, ValueError):
                continue
    return None


def _strip_markup(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def _search_external(request, provider):
    import httpx
    from defusedxml import ElementTree
    from app.services.secrets import resolve_secret
    count = max(1, min(request.max_papers, 10))
    papers = []
    try:
        if provider == "arxiv":
            response = httpx.get("https://export.arxiv.org/api/query", params={"search_query": "all:" + request.question,
                "start": 0, "max_results": count}, timeout=get_settings().research_timeout_seconds)
            response.raise_for_status()
            if len(response.content) > 5_000_000:
                raise ValueError("RESEARCH_RESPONSE_TOO_LARGE")
            root = ElementTree.fromstring(response.content)
            ns = {"a": "http://www.w3.org/2005/Atom"}
            for entry in root.findall("a:entry", ns):
                abstract = " ".join(entry.findtext("a:summary", default="", namespaces=ns).split())
                published = entry.findtext("a:published", default="", namespaces=ns)
                papers.append(ResearchPaper(id=id_generator.next("paper"), domain=request.domain, workspace_id=request.workspace_id,
                    title=" ".join(entry.findtext("a:title", default="", namespaces=ns).split()), abstract=abstract,
                    authors=[author.findtext("a:name", default="", namespaces=ns) for author in entry.findall("a:author", ns)],
                    year=int(published[:4]) if published[:4].isdigit() else None, source=provider,
                    url=entry.findtext("a:id", namespaces=ns), evidence_snippets=[abstract[:1000]] if abstract else []))
        else:
            key = resolve_secret("RESEARCHFORGE_SEMANTIC_SCHOLAR_API_KEY")
            response = httpx.get("https://api.semanticscholar.org/graph/v1/paper/search", params={"query": request.question, "limit": count,
                "fields": "title,authors,year,url,abstract,externalIds"}, headers={"x-api-key": key} if key else {}, timeout=get_settings().research_timeout_seconds)
            response.raise_for_status()
            for item in response.json().get("data", [])[:count]:
                abstract = item.get("abstract") or ""
                papers.append(ResearchPaper(id=id_generator.next("paper"), domain=request.domain, workspace_id=request.workspace_id,
                    title=item["title"], authors=[author["name"] for author in item.get("authors", [])], year=item.get("year"),
                    source=provider, url=item.get("url"), abstract=abstract, evidence_snippets=[abstract[:1000]] if abstract else [],
                    source_metadata={"external_ids": item.get("externalIds", {})}))
    except Exception as exc:
        raise ValueError("RESEARCH_PROVIDER_UNAVAILABLE") from exc
    return papers, {"provider": provider, "network_enabled": True, "fallback": False, "paper_count": len(papers)}
