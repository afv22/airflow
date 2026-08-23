"""Helpers shared by the LLM filter stages when building prompts."""

# A zero-width space, used to widen Jinja's delimiters without changing how the
# text reads.
ZWSP = "​"


def escape_jinja(text: str) -> str:
    """Neutralize Jinja delimiters so scraped text can't be parsed as a template.

    ``LLMOperator.prompt`` is a ``template_field``, so the whole prompt a task
    builds gets rendered through Jinja before the LLM ever sees it. Scraped job
    descriptions are free text from other people's sites and have turned up
    literal ``{{...}}`` (an unrendered salary-range placeholder from the source
    page) that Jinja then fails to parse as its own syntax. Widen the
    delimiters with a zero-width space so Jinja no longer recognizes them,
    while leaving the text visually unchanged for the LLM.
    """
    return (
        text.replace("{{", f"{{{ZWSP}{{")
        .replace("}}", f"}}{ZWSP}}}")
        .replace("{%", f"{{{ZWSP}%")
        .replace("%}", f"%{ZWSP}}}")
        .replace("{#", f"{{{ZWSP}#")
        .replace("#}", f"#{ZWSP}}}")
    )
