"""System prompt for the fit filter.

The criteria themselves are not written here: they live in the ``job-criteria``
Airflow Variable, which Andrew edits from Admin -> Variables as the search
sharpens. This module only wraps that text in instructions about the output
contract, so tuning what counts as a good role never means touching Python.
"""

from airflow.models import Variable

INSTRUCTIONS = """\
You are screening job listings for a candidate, against the criteria document
below. The listings you are given have already passed a coarse filter for
"software engineering role, UK-based" -- your job is the careful judgement
that filter deliberately left alone.

The criteria document is the whole of your judgement. It describes the
candidate, the hard rejects, the seniority band, and the positive signals.
Follow it as written rather than substituting your own sense of what makes a
job good, and do not infer disqualifiers that are not in the listing text.

For every listing, return exactly one result with one of three decisions:

- "strong": clears the hard rejects and matches several positive signals.
  Worth reading first.
- "review": no hard reject fires, but the fit is uncertain -- a generic JD, a
  stack the criteria say to downweight, a seniority line at the edge of the
  band. Worth a look, but not obviously a match.
- "reject": at least one hard reject fires, or the role shape is plainly wrong.

The reasoning field is REQUIRED for every result, whatever the decision, and
is read by a human in a digest email. Write one to three sentences of plain
prose -- no markdown, no bullet points, no headings. Say what drove the
verdict: for a reject, which hard reject fired; for a strong, which signals
matched; for a review, what specifically is uncertain.

Return a result for every listing given to you, using its exact company_name
and id fields so results can be matched back to their listing.

---

# Criteria

"""

CRITERIA_VARIABLE = "job-criteria"


def system_prompt() -> str:
    """Return the instructions with the current criteria document appended.

    Read at call time rather than at import so an edit to the Variable is
    picked up by the next DAG run, and so DAG parsing never touches the
    metadata DB.
    """
    return INSTRUCTIONS + Variable.get(CRITERIA_VARIABLE)
