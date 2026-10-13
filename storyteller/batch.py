"""
Batch generation: groups the persons whose anonymized requests are identical,
estimates the cost and runs the pipeline once per group.

This module does not depend on Gramps: reading the persons and writing the
notes are left to the caller.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

# Local imports #
from .anonymizer import build_request
from .http import Cancelled
from .models import ContextRequest, GenerationResult, PersonFacts
from .sources.hierarchy import DEFAULT_BUDGETS

#############
# Constants #
#############

SKIP_EXISTING = "existing_note"

# The batch stops after this many failed groups in a row (missing key,
# unreachable server...), instead of failing the same way for every person
MAX_CONSECUTIVE_ERRORS = 3

# Rough number of characters per token, to give an order of magnitude
CHARS_PER_TOKEN = 3.5
# Rough number of tokens per word of the generated text
TOKENS_PER_WORD = 2.0
# Size of the prompts and instructions around the excerpts
PROMPT_OVERHEAD_CHARS = 4000

###########
# Classes #
###########


@dataclass
class BatchItem:
    """
    One person of a batch.

    Attributes
    ----------
    facts : PersonFacts
        Facts read from the tree (never sent).
    request : ContextRequest or None
        Anonymized request, None when the person is skipped.
    skip_reason : str or None
        Why the person is skipped.
    has_note : bool
        Whether the person already has a Storyteller note.
    """

    facts: PersonFacts
    request: Optional[ContextRequest] = None
    skip_reason: Optional[str] = None
    has_note: bool = False


@dataclass
class BatchGroup:
    """
    Persons sharing the same anonymized request: one generation for all.

    Attributes
    ----------
    request : ContextRequest
        Shared request.
    items : list of BatchItem
        Persons of the group.
    """

    request: ContextRequest
    items: List[BatchItem] = field(default_factory=list)


@dataclass
class BatchEstimate:
    """
    Upper bounds of the cost of a batch.

    Attributes
    ----------
    persons : int
        Persons that will receive a note.
    replaced : int
        Among them, persons whose note will be replaced.
    groups : int
        Distinct contexts, i.e. generations.
    llm_requests : int
        Maximum number of LLM requests (the cache may avoid some).
    input_tokens : int
        Order of magnitude of the maximum input tokens.
    output_tokens : int
        Order of magnitude of the maximum output tokens.
    """

    persons: int
    replaced: int
    groups: int
    llm_requests: int
    input_tokens: int
    output_tokens: int


@dataclass
class BatchPlan:
    """
    Persons of a batch, grouped by identical request.

    Attributes
    ----------
    items : list of BatchItem
        Every person, in the selection order.
    groups : list of BatchGroup
        Groups of the persons to process.
    """

    items: List[BatchItem] = field(default_factory=list)
    groups: List[BatchGroup] = field(default_factory=list)

    @property
    def to_process(self) -> List[BatchItem]:
        """
        Return the persons that will receive a note.

        Returns
        -------
        list of BatchItem
            Persons with a request.
        """
        return [item for item in self.items if item.request is not None]

    def skipped(self) -> Dict[str, int]:
        """
        Count the skipped persons by reason.

        Returns
        -------
        dict
            Reason -> count.
        """
        counts: Dict[str, int] = {}
        for item in self.items:
            if item.skip_reason:
                counts[item.skip_reason] = counts.get(item.skip_reason, 0) + 1
        return counts

    def estimate(self, llm_validation: bool, auto_correction: bool,
                 length_words: int) -> BatchEstimate:
        """
        Compute upper bounds of the cost of the batch.

        Parameters
        ----------
        llm_validation : bool
            Whether each text is checked by a second request.
        auto_correction : bool
            Whether a text with problems is rewritten and checked again.
        length_words : int
            Target length of the texts.

        Returns
        -------
        BatchEstimate
            Estimate.
        """
        source_chars = sum(v for k, v in DEFAULT_BUDGETS.items()
                           if k not in ("commune_lead", "period_item"))
        prompt_tokens = (source_chars + PROMPT_OVERHEAD_CHARS) / CHARS_PER_TOKEN
        text_tokens = length_words * TOKENS_PER_WORD
        # (input, output) tokens of each request of one generation
        requests: List[Tuple[float, float]] = [(prompt_tokens, text_tokens)]
        if llm_validation:
            requests.append((prompt_tokens + text_tokens, 500))
            if auto_correction:
                requests.append((prompt_tokens + text_tokens + 500, text_tokens))
                requests.append((prompt_tokens + text_tokens, 500))
        groups = len(self.groups)
        processed = self.to_process
        return BatchEstimate(
            persons=len(processed),
            replaced=sum(1 for item in processed if item.has_note),
            groups=groups,
            llm_requests=groups * len(requests),
            input_tokens=int(groups * sum(r[0] for r in requests)),
            output_tokens=int(groups * sum(r[1] for r in requests)),
        )


@dataclass
class BatchReport:
    """
    Outcome of a batch run.

    Attributes
    ----------
    created : int
        Notes added.
    replaced : int
        Notes replaced.
    not_written : int
        Persons whose text failed the local checks.
    failed : int
        Persons whose generation or note writing failed.
    with_warnings : int
        Persons whose note was written with warnings.
    cancelled : bool
        Whether the batch was stopped before the end.
    aborted : str or None
        Error that stopped the batch after repeated failures.
    """

    created: int = 0
    replaced: int = 0
    not_written: int = 0
    failed: int = 0
    with_warnings: int = 0
    cancelled: bool = False
    aborted: Optional[str] = None


#############
# Functions #
#############


def group_key(request: ContextRequest) -> Tuple:
    """
    Return the key of the persons sharing the same context.

    Parameters
    ----------
    request : ContextRequest
        Anonymized request.

    Returns
    -------
    tuple
        Key.
    """
    return tuple(request.lines), request.occupation or ""


def build_plan(facts_list: List[PersonFacts], lang: str, include_occupation: bool,
               existing_notes: Optional[set] = None, skip_existing: bool = False) -> BatchPlan:
    """
    Build the anonymized requests and group the identical ones.

    Parameters
    ----------
    facts_list : list of PersonFacts
        Facts of the selected persons, in order.
    lang : str
        Language of the texts.
    include_occupation : bool
        Whether the occupation is sent.
    existing_notes : set of str, optional
        Handles of the persons who already have a Storyteller note.
    skip_existing : bool
        Whether these persons are skipped.

    Returns
    -------
    BatchPlan
        Plan.
    """
    existing_notes = existing_notes or set()
    plan = BatchPlan()
    groups: "OrderedDict[Tuple, BatchGroup]" = OrderedDict()
    for facts in facts_list:
        item = BatchItem(facts=facts, has_note=facts.handle in existing_notes)
        plan.items.append(item)
        if facts.skip_reason:
            item.skip_reason = facts.skip_reason
            continue
        if item.has_note and skip_existing:
            item.skip_reason = SKIP_EXISTING
            continue
        item.request = build_request(facts, lang, include_occupation)
        key = group_key(item.request)
        if key not in groups:
            groups[key] = BatchGroup(request=item.request)
        groups[key].items.append(item)
    plan.groups = list(groups.values())
    return plan


def run_batch(plan: BatchPlan, generate: Callable[[ContextRequest], GenerationResult],
              on_group_done: Callable[[BatchGroup, Optional[GenerationResult],
                                       Optional[Exception]], None],
              should_stop: Callable[[], bool] = lambda: False,
              on_group_start: Optional[Callable[[int, BatchGroup], None]] = None) -> BatchReport:
    """
    Generate the context of every group.

    Parameters
    ----------
    plan : BatchPlan
        Plan to run.
    generate : callable
        Produces the result of a request (usually StoryPipeline.generate).
    on_group_done : callable
        Called with (group, result, error) after each group; it writes the
        notes and updates the report counters.
    should_stop : callable
        Cancellation check.
    on_group_start : callable, optional
        Called with (index, group) before each group.

    Returns
    -------
    BatchReport
        Report; only cancelled and aborted are set here.
    """
    report = BatchReport()
    errors_in_a_row = 0
    for index, group in enumerate(plan.groups):
        if should_stop():
            report.cancelled = True
            break
        if on_group_start is not None:
            on_group_start(index, group)
        try:
            result = generate(group.request)
        except Cancelled:
            report.cancelled = True
            break
        except Exception as exc:  # noqa: BLE001 - reported for each person
            on_group_done(group, None, exc)
            errors_in_a_row += 1
            if errors_in_a_row >= MAX_CONSECUTIVE_ERRORS:
                report.aborted = str(exc)
                break
            continue
        errors_in_a_row = 0
        on_group_done(group, result, None)
    return report
