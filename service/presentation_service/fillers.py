"""
Which words of a transcript are fillers: find_fillers(text) -> [(start, end, filler), ...].

um / uh / erm / hmm always are (with the spellings Moonshine uses for them). "like", "so" and
"you know" are only when used as one: "I'd like to", "so much", "do you know" don't count. The rules
look at the neighbouring words (hesitations skipped) and were chosen on the DisfluencySpeech training
transcripts; benchmarks/ measures them on its test recordings.

Moonshine also tidies some um / uh away when it finishes a line. LineHesitations remembers the ones its
partial text showed and puts them back in the finished line.
"""

import re
from collections import Counter

FILLERS = ["um", "uh", "erm", "hmm", "like", "you know", "so"]
SPELLINGS = {"umm": "um", "uhm": "um", "ummm": "um", "uhh": "uh", "uhhh": "uh", "er": "uh", "err": "uh",
             "hm": "hmm", "hmmm": "hmm"}
HESITATIONS = {"um", "uh", "erm", "hmm", *SPELLINGS}

# "like" is an ordinary word after these...
LIKE_AFTER = set("""i you we they people would wouldn't i'd you'd we'd they'd he'd she'd d don't didn't
doesn't won't not to never always also really still both too feel feels felt feeling look looks looked
looking sound sounds sounded seem seems seemed taste tastes smell smells act acts acting work works
things thing something anything nothing everything stuff someone somebody places it's he's she's that's
they're we're you're is are was were be been being more much most exactly just kind's what's here's
what who he she ever even far""".split())
# ...unless a comma sets it off, or one of these follows it ("it's like ten dollars", "they were like oh")
LIKE_FILLER_BEFORE = set("""oh really literally super so well okay um uh maybe probably basically
one two three four five six seven eight nine ten twenty thirty forty fifty sixty hundred""".split())
BE = set("it's he's she's that's they're we're you're is are was were be been being just".split())
# and before these ("like that", "like to", "like it")
LIKE_BEFORE = set("that this to it him them that's these those".split())

SO_AFTER = set("""not is are was were be been it's they're i'm you're he's she's we're there's that's just
or think guess get gets got getting ever why do did does hope if even quite become became
feel feels felt look looks looked say doing in""".split())
SO_BEFORE = set("""many much forth far bad good close hard sure funny big long wonderful few little often
high low easy difficult important nice great fast quickly slow expensive different interesting called
strongly""".split())
SO_AND = {"forth", "on", "far"}                 # "and so forth / on"

KNOW_AFTER = set("do did if let don't didn't does as how that because before to when".split())
KNOW_BEFORE = set("what how who where why if whether that about which".split())

SEEN_PARTIALS = 2               # a hesitation the finished line lost is put back when this many partials had it

TOKEN = re.compile(r"[a-z0-9']+|[.,?!;:—–-]")


def find_fillers(text, unfinished=False):
    """unfinished: the line is still being spoken, so a "like" / "so" / "you know" at its very end is left
    out until the next word shows how it's used."""
    text = text.lower().replace("’", "'")
    tokens = [(m.group(), m.start(), m.end()) for m in TOKEN.finditer(text)]
    words = [(i, t) for i, t in enumerate(tokens) if t[0][0].isalnum() or t[0][0] == "'"]
    plain = [k for k, (_, t) in enumerate(words) if t[0] not in HESITATIONS]     # hesitations skipped
    order = {k: n for n, k in enumerate(plain)}

    def punct(k):
        """What's between word k and the next one: ',' / '.' / ''."""
        i = words[k][0]
        return tokens[i + 1][0] if i + 1 < len(tokens) and not tokens[i + 1][0][0].isalnum() else ""

    out = []
    for k, (_, (word, start, end)) in enumerate(words):
        if word in HESITATIONS:
            out.append((start, end, SPELLINGS.get(word, word)))
            continue
        n = order[k]
        before = plain[n - 1] if n else None
        after = plain[n + 1] if n + 1 < len(plain) else None
        prev = words[before][1][0] if before is not None else ""
        next_ = words[after][1][0] if after is not None else ""
        opens = before is None or punct(before) in (".", "?", "!")         # first word of a sentence
        comma = before is not None and punct(before) == ","
        if word == "like" and is_like_filler(prev, next_, opens, comma, punct(k) == ","):
            out.append((start, end, "like"))
        elif word == "so" and is_so_filler(prev, next_, opens, comma):
            out.append((start, end, "so"))
        elif (word == "you" and next_ == "know" and after == k + 1 and not punct(k)):
            following = words[k + 2][1][0] if k + 2 < len(words) else ""
            closed = punct(k + 1) != ""
            if not (prev in KNOW_AFTER and not comma) and not (following in KNOW_BEFORE and not closed and opens):
                out.append((start, words[k + 1][1][2], "you know"))
    if unfinished and words:
        last = words[-1][1][2]
        out = [f for f in out if f[2] in HESITATIONS or f[1] != last]
    return out


def bare(token):
    return re.sub(r"[^a-z0-9']", "", token.lower().replace("’", "'"))


class LineHesitations:
    """The um / uh in the partials of the line being spoken."""

    def __init__(self):
        self.seen = Counter()           # (up to two words before it, hesitation) -> partials it was in

    def add_partial(self, text):
        said, found = [], set()
        for token in map(bare, text.split()):
            if token in HESITATIONS:
                found.add((tuple(said[-2:]), token))
            elif token:
                said.append(token)
        self.seen.update(found)

    def restore(self, text):
        """The finished line, with the hesitations that went missing put back where they were said.
        Ones that can't be placed (the words before them changed), or that are right next to a
        hesitation the line still has (so only moved), are left out."""
        tokens = text.split()
        for (before, hesitation), n in self.seen.items():
            if n < SEEN_PARTIALS:
                continue
            said = [(i, bare(t)) for i, t in enumerate(tokens) if bare(t) and bare(t) not in HESITATIONS]
            at = 0 if not before else next(
                (said[k][0] + 1 for k in range(len(said))
                 if tuple(w for _, w in said[max(0, k - len(before) + 1):k + 1]) == before
                 and (len(before) == 2 or k == 0)), None)
            if at is None or any(bare(t) in HESITATIONS for t in tokens[max(0, at - 2):at + 3]):
                continue
            tokens.insert(at, hesitation)
        return " ".join(tokens)


def is_like_filler(prev, next_, opens, comma, closed):
    if not next_ or next_ in LIKE_BEFORE:
        return False
    if next_ in ("i",) and opens:
        return False                              # "like I said"
    if closed and (comma or opens or prev not in LIKE_AFTER):
        return True                               # ", like,"
    if prev in BE:
        return next_ in LIKE_FILLER_BEFORE or next_[0].isdigit()
    if prev in LIKE_AFTER:
        return False
    return True


def is_so_filler(prev, next_, opens, comma):
    if next_ in SO_BEFORE:
        return False
    if prev == "and" and next_ in SO_AND:
        return False
    if opens or comma:
        return True
    return prev not in SO_AFTER
