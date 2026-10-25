class Blocked(Exception):
    """The site refused the request as automated. Retrying soon makes the block worse."""


class Gone(Exception):
    """The source shows the listing as no longer for rent."""


class Changed(Exception):
    """The site's pages no longer have the shape the site module reads."""
