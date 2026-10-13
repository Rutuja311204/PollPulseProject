"""
Open-ended (text) answer analysis — rule-based, no external services.

1. Keywords   : split into words, drop stop words and very short words,
                reduce simple plural/verb endings ("prices" -> "price"),
                count in how many ANSWERS each keyword appears.
2. Phrases    : the same for two-word phrases (bigrams) that appear in at
                least two answers.
3. Topics     : a small built-in lexicon of common feedback topics
                (price, quality, staff ...). An answer is tagged with a topic
                if it contains one of the topic's words. Topics outside the
                lexicon are not detected — keywords cover those.
4. Sentiment  : lexicon-based scoring. +1 for each positive word, -1 for each
                negative word; a negator ("not", "never" ...) within the two
                previous words flips the sign ("not good" counts as negative).
                Score > 0 -> positive, < 0 -> negative, 0 -> neutral.

Limitations (shown in the UI): the lexicons are English-only and small;
sarcasm, context and domain-specific meaning are not understood; results
are an estimate to guide reading, not a classification you can rely on.
"""
import re
from collections import Counter

WORD_RE = re.compile(r"[a-z][a-z']+")

STOP_WORDS = set("""
a about above after again against all also am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further get got had has have
having he her here hers herself him himself his how i if in into is it its itself just me more most my myself
no nor not now of off on once only or other our ours ourselves out over own same she should so some such than
that the their theirs them themselves then there these they this those through to too under until up very was
we were what when where which while who whom why will with would you your yours yourself yourselves im ive
dont didnt isnt wasnt cant wont would could really much many lot lots thing things also even still yet one
two us like well make made way bit quite rather maybe sure ok okay yes please
""".split())

POSITIVE = set("""
good great excellent amazing awesome love loved like liked helpful useful easy clear friendly fast quick
nice happy satisfied pleasant perfect best better enjoy enjoyed wonderful fantastic smooth convenient
recommend reliable affordable clean comfortable impressive efficient polite professional supportive fun
interesting informative valuable worth appreciated thanks thank responsive simple intuitive organised organized
""".split())

NEGATIVE = set("""
bad poor terrible awful hate hated dislike slow difficult hard confusing confused unclear expensive rude
broken bug bugs buggy crash crashes problem problems issue issues disappointed disappointing annoying boring
worst worse useless dirty late delay delayed noisy crowded complicated frustrating frustrated unhelpful
overpriced lacking missing fail failed failure error errors unfriendly uncomfortable waste wasted long
""".split())

NEGATORS = {"not", "no", "never", "dont", "didnt", "isnt", "wasnt", "cant", "wont", "hardly", "nothing", "without"}

TOPICS = {
    "Price and value": {"price", "prices", "cost", "costs", "expensive", "cheap", "affordable", "value", "money", "fee", "fees", "overpriced"},
    "Quality": {"quality", "standard", "taste", "fresh", "durable", "broken", "defect"},
    "Staff and service": {"staff", "service", "teacher", "teachers", "faculty", "employee", "employees", "rude", "polite", "friendly", "helpful", "support"},
    "Speed and waiting time": {"slow", "fast", "quick", "wait", "waiting", "delay", "delayed", "late", "queue"},
    "Ease of use": {"easy", "difficult", "confusing", "intuitive", "simple", "complicated", "navigation", "interface", "usable"},
    "Content and information": {"content", "information", "material", "materials", "topic", "topics", "examples", "explanation", "syllabus"},
    "Facilities and environment": {"room", "rooms", "lab", "labs", "facility", "facilities", "clean", "dirty", "noisy", "seating", "parking", "wifi"},
    "Communication": {"communication", "email", "emails", "update", "updates", "informed", "notice", "announcement", "contact"},
    "Technical problems": {"bug", "bugs", "crash", "crashes", "error", "errors", "login", "loading", "glitch"},
}


def tokenize(text):
    words = [w.replace("'", "") for w in WORD_RE.findall(str(text).lower())]
    return [w for w in words if w]


def stem(word):
    """Tiny plural remover so 'prices' and 'price' are counted together.
    (Only plurals: stripping -ed/-ing without a dictionary causes more
    mistakes than it fixes, e.g. 'loved' -> 'lov'.)"""
    if len(word) <= 4:
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("sses", "ches", "shes", "xes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def content_words(text):
    return [w for w in tokenize(text) if w not in STOP_WORDS and len(w) > 2]


def sentiment_score(text):
    words = tokenize(text)
    score = 0
    for i, word in enumerate(words):
        polarity = 1 if word in POSITIVE else -1 if word in NEGATIVE else 0
        if polarity and any(w in NEGATORS for w in words[max(0, i - 2):i]):
            polarity = -polarity
        score += polarity
    return score


def sentiment_label(score):
    return "positive" if score > 0 else "negative" if score < 0 else "neutral"


def analyze_texts(texts, top=10):
    """Analyse a list of answer strings. Counts are numbers of ANSWERS."""
    texts = [t for t in texts if t and str(t).strip()]
    keyword_counts, display_form = Counter(), {}
    phrase_counts = Counter()
    topic_counts = Counter()
    sentiments = Counter()
    word_total = 0

    for text in texts:
        words = content_words(text)
        word_total += len(tokenize(text))
        stems = set()
        for word in words:
            root = stem(word)
            stems.add(root)
            display_form.setdefault(root, Counter())[word] += 1
        keyword_counts.update(stems)
        phrases = {f"{a} {b}" for a, b in zip(words, words[1:])}
        phrase_counts.update(phrases)
        tokens = set(tokenize(text))
        for topic, vocabulary in TOPICS.items():
            if tokens & vocabulary:
                topic_counts[topic] += 1
        sentiments[sentiment_label(sentiment_score(text))] += 1

    n = len(texts)
    pct = lambda c: round(c * 100 / n, 1) if n else 0.0  # noqa: E731
    keywords = [
        {"word": display_form[root].most_common(1)[0][0], "count": count, "pct": pct(count)}
        for root, count in keyword_counts.most_common(top)
    ]
    return {
        "count": n,
        "avg_words": round(word_total / n, 1) if n else 0,
        "keywords": keywords,
        "phrases": [{"phrase": p, "count": c} for p, c in phrase_counts.most_common(top) if c >= 2][:5],
        "topics": [{"topic": t, "count": c, "pct": pct(c)} for t, c in topic_counts.most_common()],
        "sentiment": {label: {"count": sentiments[label], "pct": pct(sentiments[label])}
                      for label in ("positive", "neutral", "negative")},
    }
