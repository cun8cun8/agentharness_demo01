from src.slug_generator import slugify


def test_removes_punctuation_and_repeated_spaces():
    assert slugify("Hello,   ResearchForge!") == "hello-researchforge"


def test_strips_outer_separators():
    assert slugify("  API Design  ") == "api-design"

