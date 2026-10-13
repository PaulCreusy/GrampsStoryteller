"""
Tests of the batch grouping, estimate and run.
"""

###########
# Imports #
###########

# Local imports #
from storyteller.batch import (
    MAX_CONSECUTIVE_ERRORS, SKIP_EXISTING, build_plan, run_batch)
from storyteller.http import Cancelled
from storyteller.models import Landmark, PersonFacts, PlaceInfo

#############
# Constants #
#############

BREST = PlaceInfo(name="Brest", country="France", code="29200")
QUIMPER = PlaceInfo(name="Quimper", country="France", code="29000")

#############
# Functions #
#############


def _facts(handle, landmarks, skip_reason=None, occupation=None):
    return PersonFacts(handle=handle, gramps_id=handle.upper(), name="Nom " + handle,
                       landmarks=[Landmark(year=y, place=p) for y, p in landmarks],
                       occupation=occupation, skip_reason=skip_reason)


def _plan(**kwargs):
    people = [
        _facts("a", [(1820, BREST), (1880, BREST)]),
        _facts("b", [(1880, BREST), (1820, BREST)], occupation="marin"),
        _facts("c", [(1850, QUIMPER)]),
        _facts("d", [], skip_reason="living"),
        _facts("e", [(1850, QUIMPER)]),
    ]
    return build_plan(people, "fr", **kwargs)


def test_identical_requests_are_grouped():
    plan = _plan(include_occupation=False)
    assert [[i.facts.handle for i in g.items] for g in plan.groups] == [["a", "b"], ["c", "e"]]
    assert plan.skipped() == {"living": 1}
    assert len(plan.to_process) == 4

    # The occupation makes the request of "b" different
    plan = _plan(include_occupation=True)
    assert [[i.facts.handle for i in g.items] for g in plan.groups] == [["a"], ["b"], ["c", "e"]]


def test_existing_notes_are_replaced_or_skipped():
    plan = _plan(include_occupation=False, existing_notes={"a", "c"})
    assert plan.estimate(False, False, 250).replaced == 2
    plan = _plan(include_occupation=False, existing_notes={"a", "c"}, skip_existing=True)
    assert plan.skipped() == {"living": 1, SKIP_EXISTING: 2}
    assert [[i.facts.handle for i in g.items] for g in plan.groups] == [["b"], ["e"]]


def test_estimate_counts_the_optional_requests():
    plan = _plan(include_occupation=False)
    simple = plan.estimate(False, False, 250)
    assert (simple.persons, simple.groups, simple.llm_requests) == (4, 2, 2)
    assert simple.output_tokens == 2 * 500
    assert plan.estimate(True, False, 250).llm_requests == 4
    corrected = plan.estimate(True, True, 250)
    assert corrected.llm_requests == 8
    assert corrected.input_tokens > simple.input_tokens * 3
    # The correction is only possible with the validation
    assert plan.estimate(False, True, 250).llm_requests == 2


def test_run_generates_once_per_group():
    plan = _plan(include_occupation=False)
    done, started = [], []
    report = run_batch(plan, lambda request: "texte %s" % request.lines[0],
                       lambda group, result, error: done.append((len(group.items), result, error)),
                       on_group_start=lambda index, group: started.append(index))
    assert started == [0, 1]
    assert [d[0] for d in done] == [2, 2]
    assert done[0][1].startswith("texte")
    assert not report.cancelled and report.aborted is None


def test_run_stops_on_cancel_and_on_repeated_errors():
    plan = _plan(include_occupation=True)
    calls = []

    def cancelled(request):
        calls.append(request)
        raise Cancelled()

    report = run_batch(plan, cancelled, lambda *args: None)
    assert report.cancelled and len(calls) == 1

    report = run_batch(plan, lambda request: "x", lambda *args: None, should_stop=lambda: True)
    assert report.cancelled

    errors = []

    def failing(request):
        raise RuntimeError("connexion refusée")

    many = build_plan([_facts(str(i), [(1800 + i, BREST)]) for i in range(10)], "fr", False)
    report = run_batch(many, failing, lambda group, result, error: errors.append(error))
    assert len(errors) == MAX_CONSECUTIVE_ERRORS
    assert report.aborted == "connexion refusée"


def test_isolated_errors_do_not_abort():
    many = build_plan([_facts(str(i), [(1800 + i, BREST)]) for i in range(6)], "fr", False)
    outcomes = []

    def sometimes(request):
        if "1801" in request.lines[0] or "1803" in request.lines[0]:
            raise RuntimeError("erreur")
        return "ok"

    report = run_batch(many, sometimes,
                       lambda group, result, error: outcomes.append(error is None))
    assert outcomes == [True, False, True, False, True, True]
    assert report.aborted is None
