"""The inspection route is owner-bound and cannot request execution."""
import pytest
from test_grants_http import endpoint


def test_assessment_route_requires_owner_token_origin_and_exact_fields(endpoint):
    app, gate, effects, request = endpoint
    route = '/api/evidence/followups/assess'
    body = dict(job_id='1'*32, project='C:/generated-project')
    for kwargs, status in [({'owner':False},401),({'token':False},403),
                           ({'origin':'https://foreign.example'},403)]:
        assert request(route, body, **kwargs)[0] == status
    for invalid in ({}, [], body | {'command':'run'}, body | {'job_id':'bad'},
                    body | {'project':None}, body | {'project':''}):
        assert request(route, invalid)[0] == 400
    assert not effects
