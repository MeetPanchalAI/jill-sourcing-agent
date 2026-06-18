"""Apify enrichment mapping — harvestapi record → our Profile.

Locked against the *real* harvestapi/linkedin-profile-scraper shape (confirmed
against a live run): input field ``urls``, ``currentPosition``/``experience`` with
``companyName``/``position``/``companyLinkedinUrl``, ``skills[].name``,
``education[].schoolName``, ``location.linkedinText``. Guards the fields the rubric
+ cross-run promotion depend on.
"""

from __future__ import annotations

from jill.enrich import _apify_to_profile

SAMPLE = {
    "firstName": "Prudhvi", "lastName": "Nakkina",
    "headline": "Agent Engineer @ Vapi",
    "about": "Realtime voice + agents.",
    "linkedinUrl": "https://www.linkedin.com/in/prudhvi-nakkina",
    "location": {"linkedinText": "San Francisco, California, United States"},
    "currentPosition": [{
        "position": "Agent Engineer", "companyName": "Vapi",
        "companyLinkedinUrl": "https://www.linkedin.com/company/vapi-ai/",
        "startDate": {"text": "May 2026"}, "endDate": {"text": "Present"},
    }],
    "experience": [
        {"position": "Agent Engineer", "companyName": "Vapi",
         "companyLinkedinUrl": "https://www.linkedin.com/company/vapi-ai/",
         "startDate": {"text": "May 2026"}, "endDate": {"text": "Present"}},
        {"position": "Solution Engineer", "companyName": "C3 AI",
         "companyLinkedinUrl": "https://www.linkedin.com/company/c3ai/",
         "startDate": {"text": "2022"}, "endDate": {"text": "2024"}},
    ],
    "skills": [{"name": "Python"}, {"name": "MLOps"}, {"name": "Data Pipelines"}],
    "education": [{"schoolName": "Northeastern University", "degree": "MS",
                   "fieldOfStudy": "Computer Information Systems"}],
}


def test_apify_maps_real_shape():
    p = _apify_to_profile(SAMPLE, "https://www.linkedin.com/in/prudhvi-nakkina")
    assert p.full_name == "Prudhvi Nakkina"
    assert p.current_company == "Vapi"
    assert p.current_company_url == "https://www.linkedin.com/company/vapi-ai"  # slash stripped
    assert p.location == "San Francisco, California, United States"
    assert p.skills == ["Python", "MLOps", "Data Pipelines"]
    assert len(p.experiences) == 2
    assert p.education[0]["school"] == "Northeastern University"


def test_apify_present_role_excluded_and_prev_uses_url():
    p = _apify_to_profile(SAMPLE, "https://www.linkedin.com/in/prudhvi-nakkina")
    # The closed C3 AI role is a previous employer; the current Vapi role is not —
    # and prev-employer expansion gets the company *URL*, not the bare name.
    assert p.previous_companies() == ["https://www.linkedin.com/company/c3ai"]


def test_apify_empty_record_is_sparse_not_crash():
    p = _apify_to_profile({"firstName": "X"}, "https://www.linkedin.com/in/x")
    assert p.full_name == "X"
    assert p.experiences == [] and p.skills == []
    assert p.current_company_url == ""
