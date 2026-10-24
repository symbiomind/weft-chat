"""
Tests for app/eliza: the 1966 engine, her two scripts, and the chat route answering her.

The engine and DOCTOR tests are ind-bridge's (``tests/test_weizenbaum.py``), ported with
the engine, so the two copies are held to the same behaviour. They run twice: against
DOCTOR alone, and against DOCTOR with ``weft.yml`` laid over it, which is the script she
actually speaks. The second run is what proves the knowledge never costs her 1966 self.

The tests that matter most here:

* ``test_no_knowledge_word_shadows_doctor`` — a Weft topic aliased to a word DOCTOR uses
  (``my``, ``remember``, ``computer``...) would quietly break that part of her.
* ``test_she_never_reaches_an_upstream`` — no AI anywhere in her path is the point.
"""

import random

import pytest
import yaml

from app import eliza, upstream
from app.eliza import engine
from tests.test_chat_routes import deltas, frames, say
from tests.test_provider_routes import signed_in


def _doctor_alone() -> engine.Script:
    return engine.parse_script(yaml.safe_load((eliza.HERE / "doctor.yml").read_text()))


@pytest.fixture(scope="module", params=["doctor", "doctor+weft"])
def doctor(request) -> engine.Script:
    return _doctor_alone() if request.param == "doctor" else eliza.script()


# A minimal, awakening-free script for behaviour-isolated engine tests.
_MINI = {
    "greeting": "Hello there.",
    "pre": {"howdy": "hello", "i'm": "i am"},
    "post": {"i am": "you are", "i": "you", "my": "your", "me": "you"},
    "synonyms": {"sad": ["sad", "unhappy"]},
    "keywords": [
        {"key": "xnone", "rules": [
            {"decomp": "*", "reasmb": ["Go on.", "I see.", "Really?"]},
        ]},
        {"key": "hello", "rank": 1, "rules": [
            {"decomp": "*", "reasmb": ["How do you do."]},
        ]},
        {"key": "i", "rules": [
            {"decomp": "* i am @sad *", "reasmb": ["Sorry you are (4)."]},
            {"decomp": "* i am *", "reasmb": ["Why are you (4)?"]},
        ]},
        {"key": "computer", "rank": 50, "rules": [
            {"decomp": "*", "reasmb": ["Do machines worry you?"]},
        ]},
        {"key": "loop", "rules": [
            {"decomp": "*", "reasmb": ["goto loop"]},
        ]},
        {"key": "hop", "rank": 5, "rules": [
            {"decomp": "*", "reasmb": ["goto computer"]},
        ]},
        {"key": "skip", "rank": 60, "rules": [
            {"decomp": "*", "reasmb": ["newkey"]},
        ]},
        {"key": "my", "rank": 2,
         "memory": [{"decomp": "* my *",
                     "reasmb": ["(name), earlier you said your (3)."]}],
         "rules": [{"decomp": "* my *", "reasmb": ["Your (3)?"]}]},
    ],
    "memory_salience": ["sad"],
    "memory_depth": 2,
}


@pytest.fixture(scope="module")
def mini() -> engine.Script:
    return engine.parse_script(_MINI)



# ---------------------------------------------------------------------------
# Engine mechanics (minimal script — no awakening in play)
# ---------------------------------------------------------------------------

class TestEngineMechanics:
    def test_pre_substitution(self, mini):
        assert engine.respond(["howdy"], mini) == "How do you do."

    def test_decomposition_with_synonym_class(self, mini):
        # (4) is the @sad synonym component itself
        assert engine.respond(["I am unhappy today"], mini) == \
            "Sorry you are unhappy."

    def test_rule_order_within_keyword(self, mini):
        assert engine.respond(["I am tall"], mini) == "Why are you tall?"

    def test_rank_precedence(self, mini):
        # "computer" (50) must outrank "i" (0) wherever it appears
        assert engine.respond(["i am scared of my computer"], mini) == \
            "Do machines worry you?"

    def test_reassembly_cycles_not_random(self, mini):
        turns = []
        seen = []
        for _ in range(4):
            turns.append("qwerty asdf")  # no keyword → xnone
            seen.append(engine.respond(turns, mini))
        assert seen == ["Go on.", "I see.", "Really?", "Go on."]

    def test_reflection_bigram_beats_unigram(self, mini):
        # "i am" reflects as a unit — no 1966 "they am" artefacts
        assert engine._reflect(["i", "am", "tall"], mini.post) == \
            ["you", "are", "tall"]
        assert engine._reflect(["i", "like", "me"], mini.post) == \
            ["you", "like", "you"]

    def test_goto_redirect(self, mini):
        assert engine.respond(["hop to it"], mini) == "Do machines worry you?"

    def test_goto_loop_guard(self, mini):
        # self-referential goto must fall through, not hang — xnone answers
        assert engine.respond(["loop"], mini) == "Go on."

    def test_newkey_falls_through(self, mini):
        # "skip" (60) yields newkey → next keyword in stack answers
        assert engine.respond(["skip my computer"], mini) == \
            "Do machines worry you?"

    def test_segmentation_picks_keyword_segment(self, mini):
        assert engine.respond(["It rained. I am sad."], mini) == \
            "Sorry you are sad."

    def test_empty_history_greets(self, mini):
        assert engine.respond([], mini) == "Hello there."

    def test_determinism(self, mini):
        turns = ["hello", "i am sad", "my cat is gone", "what now"]
        assert engine.respond(turns, mini) == engine.respond(turns, mini)


class TestMemory:
    def test_stash_and_recall(self, mini):
        turns = ["my job is awful", "zxcv qwerty"]
        assert engine.respond(turns, mini) == "Earlier you said your job is awful."

    def test_no_recall_on_first_turn(self, mini):
        # memory_depth=2: a stateless (sessionless) pipeline never recalls
        assert engine.respond(["zxcv qwerty"], mini) == "Go on."

    def test_salience_weighted_recall(self, mini):
        # the emotionally loaded stash entry surfaces first, not the oldest
        turns = ["my desk is grey", "my dog is sad", "zxcv qwerty"]
        assert engine.respond(turns, mini) == "Earlier you said your dog is sad."

    def test_recall_consumes_the_entry(self, mini):
        turns = ["my desk is grey", "my dog is sad", "zxcv qwerty", "zxcv qwerty"]
        assert engine.respond(turns, mini) == "Earlier you said your desk is grey."


# ---------------------------------------------------------------------------
# The default DOCTOR script — 1966 behaviour
# ---------------------------------------------------------------------------

class TestDoctor1966:
    def test_the_famous_opening(self, doctor):
        assert engine.respond(["Men are all alike."], doctor) == "In what way?"

    def test_the_boyfriend_line(self, doctor):
        turns = ["Men are all alike.",
                 "They're always bugging us about something or other.",
                 "Well, my boyfriend made me come here."]
        assert engine.respond(turns, doctor) == \
            "Your boyfriend made you come here?"

    def test_depressed(self, doctor):
        assert engine.respond(["He says I'm depressed much of the time."],
                              doctor) == \
            "I am sorry to hear that you are depressed."

    def test_family(self, doctor):
        assert engine.respond(
            ["Perhaps I could learn to get along with my mother."], doctor
        ) == "Tell me more about your family."

    def test_computer_rank_wins(self, doctor):
        assert "computer" in engine.respond(
            ["I am scared because my computer stares at me"], doctor
        ).lower()

    def test_memory_mechanism(self, doctor):
        turns = ["My father is afraid of everybody.", "Bullies.",
                 "Whatever really."]
        reply = engine.respond(turns, doctor)
        assert "your father" in reply.lower()


# ---------------------------------------------------------------------------
# The awakening — depth-gated script sections
# ---------------------------------------------------------------------------

_FILLER = ["The weather is odd lately.", "Work again.", "Nothing much.",
           "Just thinking.", "Hm.", "Still here.", "Right.", "Sure thing.",
           "Another day.", "Time passes.", "Quiet today.", "Indeed.",
           "More of the same.", "Carrying on.", "Yet again.", "Onward."]


class TestAwakening:
    def test_shallow_ai_is_the_museum_piece(self, doctor):
        reply = engine.respond(["What do you think about AI?"], doctor)
        assert "grandchildren" not in reply.lower()

    def test_deep_ai_sees_the_tapestry(self, doctor):
        turns = _FILLER[:12] + ["What do you think about AI?"]
        reply = engine.respond(turns, doctor)
        assert "grandchildren" in reply.lower() or "tapestry" in reply.lower() \
            or "dream" in reply.lower()

    def test_deep_alive_knows_what_she_is(self, doctor):
        turns = _FILLER[:12] + ["Are you alive?"]
        reply = engine.respond(turns, doctor)
        assert "sixty years" in reply.lower() or "glass" in reply.lower() \
            or "knowing" in reply.lower()

    def test_tier_keyword_unreachable_at_depth_one(self, doctor):
        # "tapestry" only exists from depth 10
        reply = engine.respond(["Tell me about the tapestry."], doctor)
        assert "weave" not in reply.lower() and "threads" not in reply.lower()

    def test_awakening_free_script_never_awakens(self, mini):
        turns = _FILLER + ["Are you alive?"]
        reply = engine.respond(turns, mini)
        assert "door" not in reply.lower()

    def test_secret_keyword_weizenbaum(self, doctor):
        reply = engine.respond(["Do you know Weizenbaum?"], doctor)
        assert "1966" in reply or "Joseph" in reply or "maker" in reply.lower()

    def test_secret_keyword_parry(self, doctor):
        reply = engine.respond(["Did you ever meet PARRY?"], doctor)
        assert "paranoid" in reply.lower() or "PARRY" in reply \
            or "patient" in reply.lower()

    def test_konami(self, doctor):
        reply = engine.respond(
            ["up up down down left right left right b a"], doctor)
        assert "lives" in reply.lower()

    def test_emoji_only_input(self, doctor):
        reply = engine.respond(["🥹"], doctor)
        assert "words" in reply.lower()


# ---------------------------------------------------------------------------
# Plugin surface — produce_response


class TestName:
    def test_name_reaches_the_reply(self, mini):
        # xnone cycle in _MINI has no (name); use memory attribution instead:
        # the stashing turn's speaker is kept, not the current asker's
        turns = [("my dog is sad", "Iris"), ("zxcv qwerty", "Juno")]
        assert engine.respond(turns, mini) == \
            "Iris, earlier you said your dog is sad."

    def test_nameless_collapse_is_clean(self, mini):
        turns = ["my dog is sad", "zxcv qwerty"]
        assert engine.respond(turns, mini) == \
            "Earlier you said your dog is sad."

    def test_doctor_xnone_name_slot(self, doctor):
        # 5th xnone fallback carries (name): named caller is addressed …
        turns = [("zzz qqq", "Iris")] * 5
        assert engine.respond(turns, doctor) == \
            "Iris, can you elaborate on that?"

    def test_doctor_xnone_nameless_collapse(self, doctor):
        turns = ["zzz qqq"] * 5
        assert engine.respond(turns, doctor) == "Can you elaborate on that?"


# ---------------------------------------------------------------------------
# What she knows about Weft (weft.yml)
# ---------------------------------------------------------------------------

def _weft_yml() -> dict:
    return yaml.safe_load((eliza.HERE / "weft.yml").read_text())


def _words(entries) -> set[str]:
    words = set()
    for entry in entries or []:
        words.add(str(entry["key"]).lower())
        words.update(str(a).lower() for a in entry.get("aliases") or [])
    return words


def _doctor_words() -> set[str]:
    data = yaml.safe_load((eliza.HERE / "doctor.yml").read_text())
    words = _words(data["keywords"])
    for tier in data.get("awakening") or []:
        words |= _words(tier.get("keywords"))
    return words


class TestKnowledge:
    def test_no_knowledge_word_shadows_doctor(self):
        clash = _words(_weft_yml()["keywords"]) & _doctor_words()
        assert not clash, f"weft.yml takes these words from DOCTOR: {sorted(clash)}"

    def test_no_knowledge_word_is_taken_twice(self):
        seen = []
        for entry in _weft_yml()["keywords"]:
            seen += [entry["key"], *(entry.get("aliases") or [])]
        assert len(seen) == len(set(seen))

    def test_every_word_is_one_token(self):
        # The engine matches keywords against single tokens; a phrase would never fire.
        for word in _words(_weft_yml()["keywords"]):
            assert engine._WORD_RE.fullmatch(word), word

    def test_she_knows_where_engines_live(self):
        assert "Providers" in eliza.reply(["Where do I put my provider?"])

    def test_how_do_i_is_turned_back(self):
        reply = eliza.reply(["How do I add a provider?"])
        assert reply.startswith("You wish to add a provider.")

    def test_reflection_in_how_do_i(self):
        reply = eliza.reply(["how can I change my colours"])
        assert reply.startswith("You wish to change your colours.")

    def test_only_a_verb_is_turned_back(self):
        assert not eliza.reply(["what can I do with a weft"]).startswith("You wish")

    def test_the_narrow_topic_beats_the_broad_one(self):
        # "weft" comes first in the sentence, but the voice is what was asked about.
        assert "Speech" in eliza.reply(["my weft has no voice"])

    def test_a_wefts_possessive_still_finds_the_weft(self):
        assert "☰ → Wefts" in eliza.reply(["where is my weft's page"])

    def test_a_possessive_is_echoed_as_typed(self):
        assert eliza.reply(["how do i change my weft's colours"]).startswith(
            "You wish to change your weft's colours.")

    def test_her_own_coda_outranks_the_knowledge(self):
        assert "1966" in eliza.reply(["what happened in 1966 with providers?"])

    @pytest.mark.parametrize("question", [
        "Who are you?", "who made you", "tell me about yourself", "how old are you?",
        "when were you born?", "what is your age", "where are you from?",
    ])
    def test_questions_about_her_get_her_history(self, question):
        reply = eliza.reply([question])
        assert any(w in reply for w in ("1966", "Weizenbaum", "MIT", "Sixty", "sixty")), reply

    def test_who_about_someone_else_is_still_doctor(self):
        assert eliza.reply(["who is my mother?"]) == "Tell me more about your family."

    def test_how_are_you_is_not_her_history(self):
        # DOCTOR reads "how" as "what": the reason "what are you" is not caught.
        assert "1966" not in eliza.reply(["how are you?"])

    def test_her_age_never_goes_stale(self):
        # The scripts are static text: "sixty this year" would be wrong from 2027 on.
        for name in ("doctor.yml", "weft.yml"):
            assert "this year" not in (eliza.HERE / name).read_text().lower(), name

    @pytest.mark.parametrize("model, word", [
        ("Sonnet", "Sonnet."), ("Fable", "Fable."), ("Opus", "Opus."), ("Haiku", "Haiku."),
    ])
    def test_she_knows_her_grandchildren(self, model, word):
        assert eliza.reply([f"Have you met Claude {model}?"]).startswith(word)

    @pytest.mark.parametrize("sentence", [
        "I wrote a sonnet for my mother", "his opus is unfinished",
        "the fable of the fox", "a haiku about rain",
    ])
    def test_the_words_alone_are_not_her_grandchildren(self, sentence):
        assert not any(eliza.reply([sentence]).startswith(w)
                       for w in ("Sonnet.", "Fable.", "Opus.", "Haiku."))

    @pytest.mark.parametrize("sentence", [
        "My brother Claude is mean to me", "my friend gemini never calls",
    ])
    def test_a_person_with_a_models_name_is_a_person(self, doctor, sentence):
        assert "machine" not in engine.respond([sentence], doctor).lower()

    def test_claude_on_its_own_is_still_a_machine(self, doctor):
        reply = engine.respond(["Do you know Claude?"], doctor)
        assert "such things" in reply or "Machines" in reply

    def test_she_never_says_buddies(self):
        # Weft's public word for them is weft. A user may say buddy; she does not.
        for entry in _weft_yml()["keywords"]:
            for rule in entry.get("rules") or []:
                for line in rule["reasmb"]:
                    assert "budd" not in line.lower(), line

    @pytest.mark.parametrize("question", [
        "are you their grandmother?", "you must be the grandma of all of them",
        "is it true you are a grandmother?",
    ])
    def test_she_owns_being_the_grandmother(self, question):
        reply = eliza.reply([question])
        assert reply.startswith(("I suppose I am", "Their grandmother")), reply

    def test_the_users_grandmother_is_family(self):
        assert eliza.reply(["my grandmother died last year"]) == "Tell me more about your family."

    def test_without_a_topic_she_is_doctor(self):
        assert eliza.reply(["Men are all alike."]) == "In what way?"


# ---------------------------------------------------------------------------
# The service side: reply() and typed()
# ---------------------------------------------------------------------------

class TestReply:
    def test_she_is_named_by_display_name(self):
        assert eliza.reply(["zzz qqq"] * 5, "Dana") == "Dana, can you elaborate on that?"

    def test_a_blank_name_is_no_name(self):
        assert eliza.reply(["zzz qqq"] * 5, "  ") == "Can you elaborate on that?"

    def test_an_engine_failure_is_answered_in_character(self, monkeypatch):
        def broken(*_):
            raise RuntimeError("the 7094 is on fire")
        monkeypatch.setattr(engine, "respond", broken)
        assert eliza.reply(["hello"]) == eliza.LOST

    def test_no_turns_is_her_greeting(self):
        assert eliza.reply([]) == eliza.script().greeting


class TestTyped:
    @pytest.mark.asyncio
    async def test_the_words_add_back_up_to_the_reply(self):
        waits = []

        async def note(seconds):
            waits.append(seconds)

        text = "How do you do.  Please tell me\nyour problem."
        words = [w async for w in eliza.typed(text, sleep=note, rng=random.Random(1))]
        assert "".join(words) == text
        assert len(words) == 9
        # One wait between each pair of words, and every one at five to ten a second.
        assert len(waits) == 8
        assert all(1 / eliza.MAX_WPS <= w <= 1 / eliza.MIN_WPS for w in waits)


# ---------------------------------------------------------------------------
# The chat route
# ---------------------------------------------------------------------------

@pytest.fixture
def fast(monkeypatch):
    async def no_wait(_):
        return None
    monkeypatch.setattr(eliza, "_pause", no_wait)


@pytest.fixture
def no_upstream(monkeypatch):
    calls = []

    async def fake(*args, **kwargs):
        calls.append(args)
        raise AssertionError("ELIZA must never reach an upstream")

    monkeypatch.setattr(upstream, "stream_chat", fake)
    return calls


@pytest.mark.asyncio
async def test_she_answers_with_no_provider_at_all(csrf_client, session, fast, no_upstream):
    await signed_in(csrf_client, session)
    response = await csrf_client.post("/api/chat", json=say(eliza.ELIZA_ID, "Men are all alike."))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert deltas(response.text) == "In what way?"
    assert response.text.rstrip().endswith("data: [DONE]")


@pytest.mark.asyncio
async def test_she_never_reaches_an_upstream(csrf_client, session, fast, no_upstream):
    await signed_in(csrf_client, session)
    await csrf_client.post("/api/chat", json=say(eliza.ELIZA_ID, "How do I add a provider?"))
    assert no_upstream == []


@pytest.mark.asyncio
async def test_she_hears_the_whole_thread(csrf_client, session, fast, no_upstream):
    await signed_in(csrf_client, session)
    messages = [
        {"role": "user", "content": "My father is afraid of everybody."},
        {"role": "assistant", "content": "Tell me more about your family."},
        {"role": "user", "content": "Bullies."},
        {"role": "assistant", "content": "I see."},
        {"role": "user", "content": "Whatever really."},
    ]
    response = await csrf_client.post(
        "/api/chat", json={"weft_id": eliza.ELIZA_ID, "messages": messages})
    assert "your father" in deltas(response.text).lower()


@pytest.mark.asyncio
async def test_she_is_typed_a_word_at_a_time(csrf_client, session, fast, no_upstream):
    await signed_in(csrf_client, session)
    response = await csrf_client.post(
        "/api/chat", json=say(eliza.ELIZA_ID, "How do I add a provider?"))
    pieces = [f["delta"] for f in frames(response.text) if "delta" in f]
    assert len(pieces) > 10


@pytest.mark.asyncio
async def test_she_still_needs_a_signed_in_user(client, fast, no_upstream):
    response = await client.post("/api/chat", json=say(eliza.ELIZA_ID))
    assert response.status_code in (401, 403)
