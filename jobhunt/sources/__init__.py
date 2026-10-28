from . import jobsdb, linkedin

SOURCES = {
    "jobsdb": jobsdb.search,
    "linkedin": linkedin.search,
}
