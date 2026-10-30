from . import boards, jobsdb, linkedin

SOURCES = {
    "boards": boards.search,
    "jobsdb": jobsdb.search,
    "linkedin": linkedin.search,
}

DETAILS = {
    "boards": boards.fetch_details,
    "jobsdb": jobsdb.fetch_details,
    "linkedin": linkedin.fetch_details,
}
