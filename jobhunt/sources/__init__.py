from . import jobsdb, linkedin

SOURCES = {
    "jobsdb": jobsdb.search,
    "linkedin": linkedin.search,
}

DETAILS = {
    "jobsdb": jobsdb.fetch_details,
    "linkedin": linkedin.fetch_details,
}
