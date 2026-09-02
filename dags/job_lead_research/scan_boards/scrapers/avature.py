"""Avature board scraper.

    GET https://{tenant}.avature.net/careers/SearchJobs/?...&jobOffset={n}
    GET https://{tenant}.avature.net/careers/JobDetail/{slug}/{id}

The only scraper here that reads HTML rather than a payload. Avature ships no
public JSON for the careers site: the search page is rendered server-side with
the postings already in the markup, and the network tab shows no XHR behind it
to point at instead. That is the whole reason this module parses a page --
there is nothing else to read. It does mean no JavaScript is needed either: a
plain GET returns the full board, so this stays a ``requests`` scraper like the
rest rather than pulling in a browser.

Five things are worth knowing before changing this:

*The board must be narrowed, and the filter is hardcoded.* Unfiltered,
Bloomberg is 377 postings across every region against 81 for London, so like
IBM this one has to be filtered server-side or a scan walks the entire global
careers site. Avature does that with facets whose ids are per-tenant integers
Avature assigns -- Bloomberg's London filter is ``1845=[162558]``, meaningful
on no other tenant and derivable from nothing.

That leaves the filter with nowhere good to live. It is not a slug, and the
column that could carry a whole URL, ``board_url``, is being retired. So
:data:`LOCATION_FACET` hardcodes Bloomberg's, and ``board_slug`` stays what it
is for every other provider here: the account name, ``bloomberg``. This is
therefore a Bloomberg scraper wearing a provider's name -- the page walking,
the parsing and the dedupe are all plain Avature and would serve any tenant,
but the one constant that says *which* postings is not. A second Avature
company is the point to lift that constant into per-company configuration; it
is deliberately one dict, named and documented, so that change stays small.

*Paging is a fixed twelve.* ``jobRecordsPerPage`` is accepted and ignored -- 24,
50 and 100 all return the same twelve postings -- so the page size is the
board's, not ours, and 81 postings is a seven-request walk. The walk stops on a
page with no postings rather than reading the "N of M results" legend: the
legend is a rendered string that a markup change would silently break, and the
empty page is the same signal without the parsing.

*Postings repeat across pages.* The default order is not stable between
requests: walking Bloomberg's seven pages returns 81 postings of which 80 are
distinct, with one posting appearing on both page two and page three. Offering
the board a sort makes it worse rather than better -- sorting by city returned
54 distinct postings of 81, losing 27 outright -- so the fix is ours: the walk
keeps a ``seen`` set and skips the repeats, exactly as the IBM one does.
Deduping here rather than leaning on the store's ``ON CONFLICT DO NOTHING``
keeps the count this returns honest.

*The description needs the detail page.* The search results carry title,
location and URL and no body text at all, so a scan is one request per posting
on top of the page walk -- the Workable shape. A posting whose detail fetch
fails keeps its list fields and loses only its description.

*The body is nested markup, so it is parsed, not regexed.* The description is a
``field--rich-text`` div containing ``<p>``/``<ul>``/``<li>`` children, and a
non-greedy regex up to the closing ``</div>`` stops at the first *inner* one --
on a live posting that silently returned the 446-character boilerplate intro
instead of the 3251-character body. :class:`Field` therefore tracks tag depth
and closes a block only at its own matching end tag. It is
:mod:`html.parser` from the standard library: no scraper here takes a
dependency outside ``requests``, and this one does not need to.
"""

import re
from html.parser import HTMLParser
from urllib.parse import urlencode

import requests

from job_lead_research.types import JobListing
from .base import ATSScraper

REQUEST_TIMEOUT_SECONDS = 30

# Avature serves the board to an unknown client happily enough, but sends a
# desktop layout only to something that looks like a browser. The parsing below
# assumes that layout.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# The tenant's job search, which ``board_slug`` names (``bloomberg``) exactly as
# it names the account for every other provider here.
SEARCH_URL = "https://{tenant}.avature.net/careers/SearchJobs/"

# The facet narrowing the board to London, and the only query parameter that
# changes what comes back: with it Bloomberg's board is 81 postings, without it
# 377 across every region. The rest of the parameters on the URL a browser
# produces are decoration -- ``1845_format``, ``listFilterMode``,
# ``jobRecordsPerPage`` and the ``utm_*`` tail were each tested out and return
# the same 81 -- so only this one is sent.
#
# ``1845`` is the location facet's field id and ``162558`` London's option id.
# Both are per-tenant integers Avature assigns and neither is derivable from
# anything: a second Avature company needs its own pair, read off the URL its
# careers page produces when the filter is applied in a browser. That is the
# point at which this constant should become per-company configuration -- see
# the module docstring.
LOCATION_FACET = {"1845": "[162558]"}

# Fixed by the board, not by us: jobRecordsPerPage is accepted and ignored.
PAGE_SIZE = 12

# A walk that somehow never stops should not run forever against a public site.
# Twelve a page puts this an order of magnitude clear of Bloomberg's unfiltered
# 377, which is the largest board this has been pointed at.
MAX_PAGES = 100

# The posting's own page, linked from every result. Both the title and the
# "View job" button carry it, which is why each posting's id appears twice in
# the markup and the parse keys on the id rather than counting links.
LISTING_URL = re.compile(
    r"https://[^/\"]+\.avature\.net/careers/JobDetail/[^/\"]+/(\d+)"
)

WHITESPACE = re.compile(r"\s+")

# The classes the layout hangs the fields on. Named here rather than inline
# because they are the entire contract with Avature's markup: if a scan starts
# coming back empty, this is the list to check against the live page first.
RESULT_CLASS = "article--result"
TITLE_CLASS = "article__header__text__title"
LOCATION_CLASS = "list-item-location"
DESCRIPTION_CLASS = "field--rich-text"

# Elements that never carry an end tag, and so must not move the depth counter
# the block parsers balance their tags with.
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


def as_text(content: str) -> str:
    """Collapse one run of extracted page text down to a single clean line."""
    return WHITESPACE.sub(" ", content).strip()


class Field(HTMLParser):
    """Every element whose class contains ``target``, as text and as markup.

    ``blocks`` holds each match's text, flattened; ``markup_blocks`` holds the
    same matches as the source they were cut from, for callers that need to
    search inside one.

    Depth-tracking rather than pattern-matching: an element is closed at the end
    tag that balances its own start tag, so a block containing nested markup
    comes back whole. See the module docstring for what the regex version of
    this did to descriptions.

    Nested matches are not collected separately -- while one block is open its
    children are its content, which is what makes a match on an outer wrapper
    return the whole section rather than a run of fragments.
    """

    def __init__(self, target: str, markup: str = ""):
        super().__init__(convert_charrefs=True)
        self.target = target
        self.blocks: list[str] = []
        # The same matches as ``blocks``, as the raw markup they were cut from,
        # for callers that need to search inside a block rather than read it --
        # the results page, whose title and location live in child elements and
        # whose job link is an attribute the text form necessarily drops.
        self.markup_blocks: list[str] = []
        self._depth = 0
        self._opened_at: int | None = None
        self._buffer: list[str] = []
        self._start: int | None = None
        self._markup = markup
        # Start offset of each line, so ``getpos``'s (line, column) can be
        # turned back into an index into the original markup.
        self._line_starts = [0]
        for line in markup.splitlines(keepends=True):
            self._line_starts.append(self._line_starts[-1] + len(line))

        if markup:
            self.feed(markup)

    def source_index(self) -> int:
        """The current parse position as an index into the source markup."""
        line, column = self.getpos()
        return self._line_starts[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in VOID_TAGS:
            return

        self._depth += 1
        if self._opened_at is not None:
            return

        classes = dict(attrs).get("class") or ""
        if self.target in classes:
            self._opened_at = self._depth
            self._buffer = []
            self._start = self.source_index()

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return

        if self._opened_at == self._depth:
            self.blocks.append(as_text(" ".join(self._buffer)))
            # ``getpos`` sits at the "<" of this end tag, so the block runs to
            # the ">" that closes it. Measuring the tag from the source rather
            # than assuming ``len("</div>")`` keeps whitespace inside the tag
            # ("</div >") from clipping the slice.
            close = self._markup.find(">", self.source_index())
            end = close + 1 if close != -1 else len(self._markup)
            self.markup_blocks.append(self._markup[self._start : end])
            self._opened_at = None
            self._buffer = []
            self._start = None

        # Avature's markup is machine-generated and balanced, but a stray end
        # tag must not drive the counter negative and start matching wrongly.
        self._depth = max(self._depth - 1, 0)

    def handle_data(self, data: str) -> None:
        if self._opened_at is not None:
            self._buffer.append(data)

    @classmethod
    def first(cls, markup: str, target: str) -> str:
        """The first such block's text, or empty if the class is not present."""
        blocks = cls(target, markup).blocks
        return blocks[0] if blocks else ""


def page_url(tenant: str, offset: int) -> str:
    """One page of ``tenant``'s filtered job search.

    ``[`` and ``]`` are left unencoded in the facet value. Avature does accept
    the percent-encoded form, but the unencoded one is what its own pages
    produce, and staying byte-identical to the request a browser makes is the
    cheaper side of the bet against a board that is picky about its own URLs.
    """
    query = urlencode(LOCATION_FACET | {"jobOffset": str(offset)}, safe="[]")

    return f"{SEARCH_URL.format(tenant=tenant)}?{query}"


class AvatureScraper(ATSScraper):
    def fetch_jobs(self) -> list[dict]:
        """Return every posting in the configured search, walking the pages.

        Repeats are dropped as they are found rather than at the end, so a
        posting that shifts between pages mid-walk is stored once and counted
        once. See the module docstring on why the board's order makes this
        necessary.
        """
        if not self.company.board_slug:
            raise ValueError(f"{self.company.name} has no board_slug.")

        postings: list[dict] = []
        seen: set[str] = set()

        for page in range(MAX_PAGES):
            results = self.fetch_page(page * PAGE_SIZE)
            if not results:
                break

            for posting in results:
                if posting["id"] in seen:
                    continue

                seen.add(posting["id"])
                postings.append(self.with_description(posting))

        return postings

    def fetch_page(self, offset: int) -> list[dict]:
        """Return the postings rendered on one page of the search results."""
        response = requests.get(
            page_url(self.company.board_slug, offset),
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        return self.parse_results(response.text)

    @staticmethod
    def parse_results(markup: str) -> list[dict]:
        """Pull one blob per posting out of a rendered search page.

        A result with no recognisable job link is skipped: the page renders an
        empty ``article`` shell past the end of the results, and treating that
        as a posting would put a listing with no id or URL into the walk.
        """
        postings = []

        for result in Field(RESULT_CLASS, markup).markup_blocks:
            link = LISTING_URL.search(result)
            if not link:
                continue

            postings.append(
                {
                    "id": link.group(1),
                    "url": link.group(0),
                    "title": Field.first(result, TITLE_CLASS),
                    "location": Field.first(result, LOCATION_CLASS),
                }
            )

        return postings

    def with_description(self, posting: dict) -> dict:
        """Return the posting with its detail page's body text merged in.

        A failed detail fetch costs this posting its description and nothing
        else, for the reason the Workable scraper gives: the list fields are
        already in hand, and dropping the role outright over a body we could
        not read would hide it entirely.
        """
        try:
            response = requests.get(
                posting["url"],
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            print(
                f"No description for {self.company.name} posting "
                f"{posting['id']}: {error}"
            )
            return posting

        return posting | {"description": self.description(response.text)}

    @staticmethod
    def description(markup: str) -> str:
        """The posting's body sections, stitched into one plain-text block.

        Every rich-text section is kept rather than only the first. On every
        posting sampled these are two -- the body, then an identical 168
        character note about years of experience -- and the note is left in
        rather than special-cased: it is short, it is genuinely part of the
        posting, and matching on its wording would rot the moment Bloomberg
        reworded it.
        """
        return "\n\n".join(Field(DESCRIPTION_CLASS, markup).blocks)

    def parse_job(self, blob: dict) -> JobListing:
        """Flatten one Avature posting onto the common listing shape.

        The id is the trailing integer of the posting's URL, which is what
        Avature keys the detail page on. The other candidate is the "Ref #" on
        the detail page (a longer number, ``10053424``), but that is only
        readable after a fetch that may have failed, and would leave a posting
        whose detail fetch broke with no id at all.

        ``published_at`` is left empty: neither the results page nor the detail
        page carries a posted date -- the only date-shaped text on a posting is
        prose inside the description -- and ``added_at`` already records when we
        first saw it, so filling it would claim a fact Avature never stated.
        """
        return JobListing(
            id=blob["id"],
            company_name=self.company.name,
            location=blob.get("location", ""),
            title=blob.get("title", ""),
            description=blob.get("description", ""),
            listing_url=blob.get("url", ""),
            published_at="",
        )
