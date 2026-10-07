"""Public-safe provenance summaries for answer results."""

def provenance_summary(synthesis):
    return {
        "evidence_count": synthesis.evidence_count,
        "evidence_sources": [dict(row) for row in synthesis.sources],
    }
