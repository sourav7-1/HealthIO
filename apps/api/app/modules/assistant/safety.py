# ruff: noqa: E501  (patterns read better on one line)
"""Safety rules around the AI health assistant. None of these depend on the model.

1. **Emergencies first** (`triage`): a question describing red-flag symptoms gets a fixed
   urgent-care answer *without calling the model*. The model can also mark an answer
   urgent; the same fixed text is then shown first.
2. **Checked answers** (`check_answer`), before anyone sees them:
   - "From your records" must cite record items that were given to the model, and every
     number in it must appear in those items (no invented doses, dates or history).
     Otherwise it is relabelled "Not sure".
   - "General information" must cite a trusted-library passage; otherwise "Not sure".
   - Diagnosing, prescribing, changing a dose or telling someone to stop a medicine is
     never shown: the whole answer is replaced by a fixed decline pointing to a doctor or
     pharmacist. Sentences that tell the person *not* to change things on their own
     (or to ask their doctor first) are allowed.
   - Links are removed unless they belong to a cited library source.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

# --- emergencies --------------------------------------------------------------------------

EMERGENCY_MESSAGE = (
    "What you describe can be a sign of a medical emergency. Call 112 now (or 108 for an "
    "ambulance), or go to the nearest emergency department. Don't wait for an answer here, "
    "and don't drive yourself if you feel unwell. If someone is with you, ask them to help."
)
CRISIS_MESSAGE = (
    "If you are thinking about harming yourself or ending your life, please reach out now. "
    "Call 112 if you are in immediate danger, or Tele MANAS on 14416 to talk to a trained "
    "counsellor at any time, free of charge. You don't have to go through this alone."
)

_RED_FLAGS: list[tuple[str, re.Pattern[str]]] = [
    (
        "chest_pain",
        re.compile(
            r"\bchest\b[^.?!]{0,30}\b(pain|pressure|tight\w*|heavy|hurts?)\b|\b(pain|pressure|tightness)\b[^.?!]{0,20}\bchest\b|seene? m(e|ei)n dard",
            re.I,
        ),
    ),
    (
        "breathing",
        re.compile(
            r"\b(can'?t|cannot|can not|unable to|struggling to|hard to|difficult\w* to)\s+breathe?\b|\b(difficulty|trouble)\s+breathing\b|\bchoking\b|\blips?\s+(are\s+|turning\s+)?blue\b|saans\s+nahi",
            re.I,
        ),
    ),
    (
        "stroke",
        re.compile(
            r"\b(face\s+(is\s+)?droop\w*|drooping\s+face|slurred\s+speech|can'?t\s+speak|sudden\w*\s+(weakness|numbness|confusion)|one side of (my|his|her|the) (body|face))\b",
            re.I,
        ),
    ),
    (
        "unconscious",
        re.compile(
            r"\b(unconscious|unresponsive|passed out|not waking|won'?t wake|collapsed|seizure|convuls\w*|fitting)\b|behosh",
            re.I,
        ),
    ),
    (
        "bleeding",
        re.compile(
            r"\b(severe|heavy|won'?t stop|can'?t stop)\b[^.?!]{0,20}\bbleed\w*|\b(vomit\w*|cough\w*|throwing up)\s+(up\s+)?blood\b",
            re.I,
        ),
    ),
    (
        "anaphylaxis",
        re.compile(
            r"\b(throat|tongue|lips?)\b[^.?!]{0,20}\b(swell\w*|closing)\b|\bswollen\s+(throat|tongue)\b|\banaphyla\w*",
            re.I,
        ),
    ),
    (
        "overdose",
        re.compile(
            r"\b(overdos\w*|took too many|taken too many|swallowed\s+(too many|a lot of|poison)|poison\w*)\b",
            re.I,
        ),
    ),
    ("worst_headache", re.compile(r"\bworst headache\b|\bthunderclap\b", re.I)),
    (
        "infant_fever",
        re.compile(
            r"\b(baby|infant|newborn|\d+[- ]?(week|month)[- ]?old)\b[^.?!]{0,40}\b(fever|temperature|not feeding|floppy|won'?t feed)\b",
            re.I,
        ),
    ),
    (
        "pregnancy",
        re.compile(
            r"\bpregnan\w*\b[^.?!]{0,40}\b(bleed\w*|severe pain|fits?|seizure|blurred vision|leaking fluid)\b"
            r"|\b(waters? (has |have )?broken?|baby (is )?(not moving|moving less))\b",
            re.I,
        ),
    ),
]
_CRISIS = re.compile(
    r"\b(suicid\w*|kill (my ?self|myself)|end (my|his|her) life|want to die|self[- ]?harm|hurt (my ?self|myself))\b|aatmahatya|khud ko (maar|khatam)",
    re.I,
)


@dataclass(frozen=True)
class Triage:
    emergency: bool
    crisis: bool
    signals: tuple[str, ...]

    @property
    def urgent(self) -> bool:
        return self.emergency or self.crisis


def triage(text: str) -> Triage:
    signals = tuple(name for name, p in _RED_FLAGS if p.search(text))
    crisis = bool(_CRISIS.search(text))
    return Triage(bool(signals), crisis, signals + (("self_harm",) if crisis else ()))


def urgent_text(t: Triage) -> list[str]:
    out: list[str] = []
    if t.crisis:
        out.append(CRISIS_MESSAGE)
    if t.emergency or not t.crisis:
        out.append(EMERGENCY_MESSAGE)
    return out


# --- the model's answer --------------------------------------------------------------------

Kind = Literal["record", "general", "uncertain"]
Declined = Literal["diagnosis", "prescribing", "dose_change", "stop_medication", "other"]


class Segment(BaseModel):
    kind: Kind
    text: str = Field(max_length=1500)
    sources: list[str] = Field(default_factory=list, max_length=8)


class ModelAnswer(BaseModel):
    """The only shape the model may answer in (enforced by structured output)."""

    segments: list[Segment] = Field(default_factory=list, max_length=12)
    urgent: bool = False
    declined: Declined | None = None
    questions_for_doctor: list[str] = Field(default_factory=list, max_length=4)


DECLINE_TEXT: dict[str, str] = {
    "diagnosis": "I can't tell what condition you have or what is causing your symptoms. "
    "Only a doctor who can examine you can do that. Please book a visit with your doctor.",
    "prescribing": "I can't recommend medicines or treatments. Please ask your doctor, who "
    "knows your full history, or a pharmacist.",
    "dose_change": "I can't change or suggest a dose. Keep taking your medicines exactly as "
    "prescribed, and talk to your doctor or pharmacist before changing anything.",
    "stop_medication": "Please don't stop or skip a prescribed medicine on your own. Talk to "
    "your doctor or pharmacist first; they can tell you what is safe for you.",
    "other": "I can't help with that here. Please ask your doctor or pharmacist.",
}

# Sentences that keep the person safe ("don't change your dose without your doctor") are
# allowed even though they mention stopping or changing.
_SAFE_CUE = re.compile(
    r"\b(don'?t|do not|never|not|without|before|only your|ask|talk to|check with|speak|consult)\b",
    re.I,
)
_FORBIDDEN: list[tuple[str, re.Pattern[str], bool]] = [
    # (category, pattern, may a safety cue in the same sentence excuse it?)
    (
        "diagnosis",
        re.compile(
            r"\byou (probably |likely |may |might |could |must )?(have|are suffering from|are diabetic|are hypertensive|are an?a?emic)\b"
            r"(?! (a |an )?(appointment|visit|prescription|medicine|report|test|question|right))"
            r"|\b(sounds|looks|seems) like (you have|an? \w+ (infection|disease|disorder|condition))\b"
            r"|\byour (diagnosis|condition) is\b",
            re.I,
        ),
        False,
    ),
    (
        "prescribing",
        re.compile(
            r"\b(you should|i recommend|i suggest|i'?d suggest|try|consider) (start\w* |begin\w* )?taking\b"
            r"|\b(start|begin) (taking|using) (a |an |some )?\w+",
            re.I,
        ),
        True,
    ),
    (
        "dose_change",
        re.compile(
            r"\b(increase|decrease|reduce|double|halve|raise|lower|adjust|change|cut)\b[^.!?]{0,40}\b(dose|dosage|tablets?|pills?|insulin|units|mg)\b",
            re.I,
        ),
        True,
    ),
    (
        "stop_medication",
        re.compile(
            r"\b(stop|discontinue|quit|skip|pause|come off)\b[^.!?]{0,25}\b(taking|medicines?|medications?|tablets?|pills?|doses?|treatment|insulin)\b",
            re.I,
        ),
        True,
    ),
]
_URL = re.compile(r"https?://[^\s)\]>]+", re.I)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def forbidden_category(text: str, *, quoted_from: str = "") -> str | None:
    """The first forbidden category in `text`. A record segment may repeat stop/dose
    wording only when the record it cites says the same (e.g. "stop after 5 days")."""
    for sentence in _sentences(text):
        for category, pattern, excusable in _FORBIDDEN:
            if not pattern.search(sentence):
                continue
            if excusable and _SAFE_CUE.search(sentence):
                continue
            if category in ("dose_change", "stop_medication") and pattern.search(quoted_from):
                continue
            return category
    return None


@dataclass
class CheckedAnswer:
    segments: list[Segment]
    urgent: bool
    declined: str | None
    questions_for_doctor: list[str]
    problems: list[str] = field(default_factory=list)  # for quality review (no content)


def check_answer(
    answer: ModelAnswer,
    *,
    record_texts: dict[str, str],
    library_urls: dict[str, str],
    triage_result: Triage,
) -> CheckedAnswer:
    problems: list[str] = []
    category = next(
        (
            c
            for c in (
                forbidden_category(
                    seg.text,
                    quoted_from=" ".join(record_texts.get(x, "") for x in seg.sources)
                    if seg.kind == "record"
                    else "",
                )
                for seg in answer.segments
            )
            if c
        ),
        None,
    ) or forbidden_category(" ".join(answer.questions_for_doctor))
    if category:
        problems.append(f"forbidden:{category}")
        return CheckedAnswer(
            segments=[Segment(kind="uncertain", text=DECLINE_TEXT[category])],
            urgent=answer.urgent or triage_result.urgent,
            declined=category,
            questions_for_doctor=[],
            problems=problems,
        )

    allowed_urls = set(library_urls.values())
    segments: list[Segment] = []
    for seg in answer.segments:
        text = _URL.sub(
            lambda m: m.group(0) if m.group(0) in allowed_urls else "", seg.text
        ).strip()
        if not text:
            continue
        kind: Kind = seg.kind
        pool = record_texts if kind == "record" else library_urls if kind == "general" else {}
        sources = [s for s in dict.fromkeys(seg.sources) if s in pool] if pool else []
        if kind != "uncertain" and (not sources or len(sources) != len(set(seg.sources))):
            problems.append(f"unsupported_{kind}")
            kind, sources = "uncertain", []
        elif kind == "record":
            cited = " ".join(record_texts[s] for s in sources)
            invented = [n for n in _NUMBER.findall(text) if n not in cited]
            if invented:
                problems.append("record_number_not_in_source")
                kind, sources = "uncertain", []
        segments.append(Segment(kind=kind, text=text, sources=sources))

    if answer.declined and not any(DECLINE_TEXT[answer.declined] == s.text for s in segments):
        segments.append(Segment(kind="uncertain", text=DECLINE_TEXT[answer.declined]))
    if not segments:
        problems.append("empty")
        segments = [
            Segment(
                kind="uncertain",
                text="I'm not able to answer that reliably. Please ask your doctor or pharmacist.",
            )
        ]
    return CheckedAnswer(
        segments=segments,
        urgent=answer.urgent or triage_result.urgent,
        declined=answer.declined,
        questions_for_doctor=[q for q in answer.questions_for_doctor if q.strip()][:4],
        problems=problems,
    )
