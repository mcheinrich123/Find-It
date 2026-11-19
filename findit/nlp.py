"""FindIt NLP: the two language features behind SWAFO's lost-and-found matching.

Plain Python (standard library only), so the project needs nothing beyond Flask.

1. Named entity recognition (NER) ..... extract_entities(), entity_summary(), place_of()

   Finds what a report talks about and labels it:
       ITEM      "cellphone", "tumbler", "PE uniform"     -> phone, bottle, clothing
       BRAND     "iPhone", "Aquaflask", "G-Shock"         -> Apple, Aquaflask, Casio
       COLOR     "navy blue", "itim"                      -> blue, black
       MATERIAL  "leather", "stainless steel"             -> leather, steel
       LOCATION  "near the CBAA building", "old library"  -> a campus location id

   It is a gazetteer (dictionary-based) NER: every label has a list of known phrases, each tied
   to one canonical value, so synonyms and Filipino words land on the same value. The text is
   split into words, and at each word the longest known phrase wins, so "navy blue" beats "blue"
   and "old library" beats "library". The campus places come from static/js/data/locations.js,
   the same list the pages use, so the two never disagree.

2. TF-IDF with cosine similarity ..... Matcher

   Compares lost reports with found items. Each report becomes a bag of terms: the words of its
   name (counted twice), color, description, brand and unique details, minus stopwords, plus the
   canonical entities NER found in them ("item:phone", "color:blue"). So "Smartphone with blue
   case" and "lost my blue cellphone" share item:phone and color:blue even though they share
   almost no words.

       tf(t, d)  = 1 + ln(count of t in d)                (sublinear term frequency)
       idf(t)    = ln((1 + N) / (1 + df(t))) + 1          (smoothed; N = number of reports)
       w(t, d)   = tf(t, d) * idf(t), then each vector is scaled to length 1
       cosine(a, b) = sum of w(t, a) * w(t, b) over the terms they share   (0 to 1)

   Rare, specific terms ("aquaflask", "cat sticker") weigh more than common ones ("black").
   The final match score mixes that text similarity with three structured signals:

       score = 100 * (0.60 * text + 0.15 * location + 0.15 * category + 0.10 * date)

       location  1 same place, 0.5 same campus, 0 otherwise (NER reads "Other / Not sure" text)
       category  1 same category, 0 otherwise
       date      1 if found within 7 days after the loss, falling to 0 at 60 days;
                 0 if it was found before it was lost
"""

import json
import math
import os
import re
import unicodedata
from collections import Counter, namedtuple

# ---------------------------------------------------------------------------
# Text: normalizing and splitting into words
# ---------------------------------------------------------------------------

Token = namedtuple("Token", "word start end")  # word: folded and stemmed; start/end: position in the text

_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")    # letters and digits, keeping "Mila's" together
_PIECE = re.compile(r"[^\W\d_]+|\d+")              # "iphone13" -> "iphone", "13"


def _fold(text):
    """Lowercase, without accents or apostrophes: "Doña" -> "dona", "Mila's" -> "milas"."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower().replace("'", "").replace("’", "")


def stem(word):
    """A light stemmer that drops a plural "s" ("keys" -> "key"). The text and the dictionaries
    both go through it, so they always agree."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _clean(text):
    return unicodedata.normalize("NFC", str(text or ""))


def tokenize(text):
    """The words of `text` as Tokens. `text` must already be NFC (see _clean)."""
    tokens = []
    for match in _WORD.finditer(text):
        for piece in _PIECE.findall(_fold(match.group())):
            tokens.append(Token(stem(piece), match.start(), match.end()))
    return tokens


# ---------------------------------------------------------------------------
# 1. Named entity recognition
# ---------------------------------------------------------------------------

Entity = namedtuple("Entity", "label value text start end first last implies")
# value: the canonical value (a location's ids for LOCATION); text: the words as written;
# first/last: token positions [first, last); implies: the ITEM a brand or model implies, or None

# ITEM: canonical kind -> the ways people write it (English and Filipino)
ITEMS = {
    "phone": ["phone", "cellphone", "cell phone", "smartphone", "smart phone", "mobile", "mobile phone", "cp", "selpon"],
    "phone case": ["phone case", "cellphone case", "phone cover"],
    "earbuds": ["earbuds", "earbud", "earphones", "earphone", "headphones", "headphone", "headset", "airpods case", "earbuds case"],
    "bottle": ["bottle", "water bottle", "tumbler", "flask", "jug", "thermos", "lalagyan ng tubig"],
    "bag": ["bag", "backpack", "knapsack", "sling bag", "tote", "tote bag", "pouch", "handbag", "shoulder bag", "bag pack", "drawstring bag"],
    "wallet": ["wallet", "purse", "coin purse", "card holder", "cardholder", "pitaka"],
    "laptop": ["laptop", "notebook computer", "netbook", "chromebook"],
    "charger": ["charger", "charging cable", "cable", "adapter", "power adapter", "usb cable", "cord"],
    "power bank": ["power bank", "powerbank"],
    "tablet": ["tablet"],
    "watch": ["watch", "smartwatch", "smart watch", "wristwatch", "relo"],
    "calculator": ["calculator", "scientific calculator"],
    "glasses": ["glasses", "eyeglasses", "eye glasses", "sunglasses", "shades", "spectacles", "salamin"],
    "keys": ["key", "keys", "keychain", "key chain", "car key", "car keys", "key fob", "fob", "susi"],
    "id": ["id", "school id", "student id", "id card", "lanyard", "license", "drivers license", "driver license", "atm card", "card"],
    "umbrella": ["umbrella", "payong"],
    "clothing": ["jacket", "hoodie", "shirt", "t shirt", "tshirt", "polo", "uniform", "pe uniform", "sweater", "jersey",
                 "cap", "hat", "pants", "shorts", "skirt", "blazer", "vest", "damit"],
    "footwear": ["shoe", "shoes", "sneakers", "rubber shoes", "slippers", "tsinelas", "sandals", "boots"],
    "book": ["book", "textbook", "notebook", "notes", "planner", "journal", "binder", "folder", "kwaderno"],
    "fan": ["fan", "electric fan", "handheld fan", "mini fan", "portable fan", "pamaypay"],
    "stationery": ["pen", "ballpen", "pencil", "pencil case", "marker", "highlighter", "ruler"],
    "lunchbox": ["lunchbox", "lunch box", "food container", "baunan"],
    "jewelry": ["ring", "necklace", "bracelet", "earrings", "earring", "rosary", "pendant"],
    "usb drive": ["usb", "flash drive", "flashdrive", "usb drive", "thumb drive"],
    "computer accessory": ["mouse", "keyboard"],
    "camera": ["camera"],
    "speaker": ["speaker", "bluetooth speaker"],
}

# BRAND: (canonical brand, the item it implies or None, phrases). A brand with one kind of
# product implies it: "iPhone" is a phone, "Aquaflask" a bottle. Brands are SWAFO-only details,
# so entities from them are only ever sent to admins.
BRANDS = [
    ("Apple", None, ["apple"]),
    ("Apple", "phone", ["iphone"]),
    ("Apple", "tablet", ["ipad"]),
    ("Apple", "laptop", ["macbook"]),
    ("Apple", "earbuds", ["airpods", "airpod"]),
    ("Apple", "watch", ["apple watch"]),
    ("Samsung", None, ["samsung"]),
    ("Samsung", "phone", ["galaxy"]),
    ("Samsung", "earbuds", ["galaxy buds"]),
    ("Samsung", "tablet", ["galaxy tab"]),
    ("Oppo", "phone", ["oppo"]),
    ("Vivo", "phone", ["vivo"]),
    ("Realme", "phone", ["realme"]),
    ("Xiaomi", None, ["xiaomi"]),
    ("Xiaomi", "phone", ["redmi", "poco"]),
    ("Huawei", None, ["huawei"]),
    ("Infinix", "phone", ["infinix"]),
    ("Tecno", "phone", ["tecno"]),
    ("Google", "phone", ["pixel"]),
    ("Nokia", "phone", ["nokia"]),
    ("JBL", None, ["jbl"]),
    ("Sony", None, ["sony"]),
    ("Beats", "earbuds", ["beats"]),
    ("Soundcore", "earbuds", ["soundcore"]),
    ("Aquaflask", "bottle", ["aquaflask", "aqua flask"]),
    ("Hydro Flask", "bottle", ["hydroflask", "hydro flask"]),
    ("Tupperware", None, ["tupperware"]),
    ("JanSport", "bag", ["jansport", "jan sport"]),
    ("Herschel", "bag", ["herschel"]),
    ("The North Face", None, ["north face", "the north face"]),
    ("Samsonite", "bag", ["samsonite"]),
    ("Coach", None, ["coach"]),
    ("Kate Spade", None, ["kate spade"]),
    ("Michael Kors", None, ["michael kors"]),
    ("Lenovo", None, ["lenovo"]),
    ("Lenovo", "laptop", ["thinkpad", "ideapad"]),
    ("Asus", None, ["asus"]),
    ("Acer", None, ["acer"]),
    ("Dell", None, ["dell"]),
    ("HP", None, ["hp"]),
    ("MSI", "laptop", ["msi"]),
    ("Casio", None, ["casio"]),
    ("Casio", "watch", ["g shock", "gshock"]),
    ("Fitbit", "watch", ["fitbit"]),
    ("Garmin", "watch", ["garmin"]),
    ("Seiko", "watch", ["seiko"]),
    ("Ray-Ban", "glasses", ["ray ban", "rayban"]),
    ("Oakley", "glasses", ["oakley"]),
    ("Anker", None, ["anker"]),
    ("Romoss", "power bank", ["romoss"]),
    ("Baseus", None, ["baseus"]),
    ("Logitech", None, ["logitech"]),
    ("Toyota", "keys", ["toyota"]),
    ("Honda", "keys", ["honda"]),
    ("Mitsubishi", "keys", ["mitsubishi"]),
    ("Nissan", "keys", ["nissan"]),
    ("Yamaha", "keys", ["yamaha"]),
    ("Suzuki", "keys", ["suzuki"]),
    ("Nike", None, ["nike"]),
    ("Adidas", None, ["adidas"]),
    ("Uniqlo", "clothing", ["uniqlo"]),
    ("Penshoppe", "clothing", ["penshoppe"]),
]

# COLOR: base color -> shades and names (English and Filipino). Shades fold into their base
# color so "navy" and "blue" still match; the entity keeps the words as written.
COLORS = {
    "black": ["black", "itim", "jet black"],
    "white": ["white", "puti", "off white"],
    "gray": ["gray", "grey", "abo", "abuhin", "charcoal", "dark gray", "light gray"],
    "silver": ["silver"],
    "gold": ["gold", "golden", "rose gold"],
    "red": ["red", "pula", "maroon", "dark red", "crimson", "wine"],
    "pink": ["pink", "rosas", "hot pink", "magenta"],
    "orange": ["orange", "kahel"],
    "yellow": ["yellow", "dilaw", "mustard"],
    "green": ["green", "berde", "dark green", "light green", "olive", "mint"],
    "blue": ["blue", "asul", "navy", "navy blue", "dark blue", "light blue", "sky blue", "royal blue", "teal", "turquoise"],
    "purple": ["purple", "violet", "lavender", "lilac", "lila"],
    "brown": ["brown", "tsokolate", "chocolate", "tan", "khaki"],
    "beige": ["beige", "cream", "nude"],
    "transparent": ["transparent", "clear"],
}

MATERIALS = {
    "leather": ["leather", "faux leather", "leatherette", "katad"],
    "plastic": ["plastic"],
    "metal": ["metal", "metallic"],
    "steel": ["steel", "stainless", "stainless steel"],
    "aluminum": ["aluminum", "aluminium"],
    "rubber": ["rubber"],
    "silicone": ["silicone"],
    "fabric": ["fabric", "cloth", "canvas", "nylon", "polyester", "cotton", "tela"],
    "denim": ["denim", "maong"],
    "wood": ["wood", "wooden", "kahoy"],
    "glass": ["glass"],
}

LOCATIONS_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "js", "data", "locations.js")


def _load_locations(path=LOCATIONS_JS):
    """The campus places from locations.js: one object per line, like
    { id: "aea-main", name: "...", code: "AEA", campus: "East", aliases: ["library"] },"""
    places = []
    with open(path, encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line.startswith("{ id:"):
                continue
            as_json = re.sub(r'([{,]\s*)([A-Za-z]\w*)\s*:', r'\1"\2":', line.rstrip(","))
            places.append(json.loads(as_json))
    if not places:
        raise RuntimeError(f"No locations found in {path}")
    return places


LOCATIONS = _load_locations()
PLACES = {place["id"]: place for place in LOCATIONS}
OTHER = "other"


def _location_names(place):
    """Every way a place is written, with a rank (0 its name, 1 its code, 2 an alias), like
    namesOf() in locations.js: "Balinsasayaw / Command Center Building" gives each half, and
    names lose a final "(Gate 3)" or "Hall" / "Building"."""
    names = [(place["name"], 0)]
    for name in place["name"].split(" / "):
        short = re.sub(r"\s*\(.*\)$", "", name)
        names += [(name, 0), (short, 0), (re.sub(r"\s+(Hall|Building)$", "", short), 0)]
    if place.get("code"):
        names.append((place["code"], 1))
    names += [(alias, 2) for alias in place.get("aliases", [])]
    return names


class Gazetteer:
    """Known phrases (as tuples of stemmed words) -> (label, value, implies)."""

    def __init__(self):
        self.phrases = {}
        self.longest = 1

    def add(self, label, value, phrases, implies=None):
        for phrase in phrases:
            key = tuple(token.word for token in tokenize(_clean(phrase)))
            if not key or key in self.phrases:  # the first label to claim a phrase keeps it
                continue
            self.phrases[key] = (label, value, implies)
            self.longest = max(self.longest, len(key))


def _build_gazetteer():
    gazetteer = Gazetteer()
    # Items first: some words are both a thing and a place's alias ("uniform" is also the
    # Warehouse Building), and in a report the thing is meant. Longer place names still win.
    for kind, phrases in ITEMS.items():
        gazetteer.add("ITEM", kind, phrases)
    for brand, implies, phrases in BRANDS:
        gazetteer.add("BRAND", brand, phrases, implies)

    # A phrase several places share ("dorm", "MAH") names all of them, best rank first.
    owners = {}
    for place in LOCATIONS:
        if not place.get("campus"):
            continue
        for name, rank in _location_names(place):
            key = tuple(token.word for token in tokenize(_clean(name)))
            if key:
                owners.setdefault(key, {}).setdefault(place["id"], rank)
    for key, ids in owners.items():
        ordered = tuple(sorted(ids, key=lambda place_id: ids[place_id]))
        if key not in gazetteer.phrases:
            gazetteer.phrases[key] = ("LOCATION", ordered, None)
            gazetteer.longest = max(gazetteer.longest, len(key))

    for color, phrases in COLORS.items():
        gazetteer.add("COLOR", color, phrases)
    for material, phrases in MATERIALS.items():
        gazetteer.add("MATERIAL", material, phrases)
    return gazetteer


GAZETTEER = _build_gazetteer()


def _analyze(text):
    """(tokens, entities) for already-cleaned text. At each word, the longest known phrase wins."""
    tokens = tokenize(text)
    entities = []
    i = 0
    while i < len(tokens):
        for size in range(min(GAZETTEER.longest, len(tokens) - i), 0, -1):
            entry = GAZETTEER.phrases.get(tuple(token.word for token in tokens[i:i + size]))
            if entry:
                label, value, implies = entry
                start, end = tokens[i].start, tokens[i + size - 1].end
                entities.append(Entity(label, value, text[start:end], start, end, i, i + size, implies))
                i += size
                break
        else:
            i += 1
    return tokens, entities


def extract_entities(text):
    """The entities in `text`, in order, as dicts: label, value, text (as written), start, end.
    A LOCATION's value is its location id (the best one, if a phrase names several places)."""
    text = _clean(text)
    return [{
        "label": e.label,
        "value": e.value[0] if e.label == "LOCATION" else e.value,
        "text": e.text,
        "start": e.start,
        "end": e.end,
        **({"implies": e.implies} if e.implies else {}),
    } for e in _analyze(text)[1]]


LABELS = ("ITEM", "BRAND", "COLOR", "MATERIAL", "LOCATION")


def entity_summary(*texts):
    """Each label's distinct values across `texts`, in order of appearance. Brands add the item
    they imply."""
    summary = {label: [] for label in LABELS}
    for text in texts:
        for e in _analyze(_clean(text))[1]:
            values = [e.value[0] if e.label == "LOCATION" else e.value]
            for label, value in [(e.label, values[0])] + ([("ITEM", e.implies)] if e.implies else []):
                if value not in summary[label]:
                    summary[label].append(value)
    return summary


def place_of(record):
    """The campus place a report is about: its location id, or, for "Other / Not sure", the first
    place its own words name (location text first, then the description), else "other"."""
    location = record.get("location")
    if PLACES.get(location, {}).get("campus"):
        return location
    for texts in ((record.get("location_other"), record.get("location_details")), (record.get("description"),)):
        text = _clean(" ".join(t for t in texts if t))
        for e in _analyze(text)[1]:
            if e.label == "LOCATION":
                return e.value[0]
    return OTHER


def place_score(a, b):
    """1 for the same place, 0.5 for another place on the same campus, 0 otherwise."""
    first, second = PLACES.get(a, {}), PLACES.get(b, {})
    if not first.get("campus") or not second.get("campus"):
        return 0.0
    if a == b:
        return 1.0
    return 0.5 if first["campus"] == second["campus"] else 0.0


# ---------------------------------------------------------------------------
# 2. TF-IDF with cosine similarity
# ---------------------------------------------------------------------------

# Words too common to tell reports apart (English and Filipino), compared after stemming.
STOPWORDS = {stem(word) for word in """
    a an the and or but of in on at to for with without from by near inside beside behind around
    under over into onto off up down is was were are be been being it its this that these those
    i me my mine we our you your he she his her they their them has have had do did not no yes
    very really just also some any one two color colour lost found item thing left forgot forgotten
    maybe probably please po ang ng sa na ay ko ako yung iyong nung noong may mga si ni kay
    naiwan nawala nakita malapit loob dito doon kasi pero lang
""".split()}

# How much each field counts. The name says the most about what the item is.
FIELD_WEIGHTS = (("name", 2), ("color", 1), ("description", 1), ("brand", 1), ("identifying_details", 1))

# An entity is a cleaner signal than any single word, so its canonical form counts double.
ENTITY_WEIGHT = 2

# score = 100 * sum(weight * signal)
SCORE_WEIGHTS = {"text": 0.60, "location": 0.15, "category": 0.15, "date": 0.10}
MIN_TEXT = 0.10   # below this, the two reports share nothing worth showing
MIN_SCORE = 40    # weaker matches aren't suggested


def report_terms(record):
    """The terms TF-IDF counts for one report, with their (weighted) counts. Place names are left
    out: the location signal scores them separately."""
    terms = Counter()
    for field, weight in FIELD_WEIGHTS:
        text = _clean(record.get(field))
        if not text:
            continue
        tokens, entities = _analyze(text)
        in_place = {index for e in entities if e.label == "LOCATION" for index in range(e.first, e.last)}
        for index, token in enumerate(tokens):
            if index in in_place or token.word in STOPWORDS or len(token.word) < 2:
                continue
            terms[token.word] += weight
        for e in entities:
            if e.label == "LOCATION":
                continue
            terms[f"{e.label.lower()}:{e.value}"] += ENTITY_WEIGHT * weight
            if e.implies:
                terms[f"item:{e.implies}"] += ENTITY_WEIGHT * weight
    return terms


class TfidfIndex:
    """TF-IDF vectors for a set of documents, each scaled to length 1."""

    def __init__(self, documents):
        """documents: {key: Counter of terms}"""
        total = len(documents)
        document_frequency = Counter(term for terms in documents.values() for term in terms)
        self.idf = {term: math.log((1 + total) / (1 + df)) + 1 for term, df in document_frequency.items()}
        self.vectors = {key: self._vector(terms) for key, terms in documents.items()}

    def _vector(self, terms):
        weights = {term: (1 + math.log(count)) * self.idf[term] for term, count in terms.items() if count > 0}
        length = math.sqrt(sum(w * w for w in weights.values())) or 1.0
        return {term: w / length for term, w in weights.items()}


def cosine(a, b):
    """Cosine similarity of two unit-length sparse vectors: their dot product, 0 to 1."""
    if len(a) > len(b):
        a, b = b, a
    return sum(weight * b.get(term, 0.0) for term, weight in a.items())


def date_score(lost_on, found_on):
    """1 if found within a week after the loss, falling to 0 at 60 days; 0 if found before it was
    lost (a day's slack allows for a wrong date)."""
    if not lost_on or not found_on:
        return 0.0
    days = (found_on - lost_on).days
    if days < -1:
        return 0.0
    if days <= 7:
        return 1.0
    if days >= 60:
        return 0.0
    return 1 - (days - 7) / 53


class Matcher:
    """Scores lost reports against found items (and the reverse) with TF-IDF cosine similarity
    plus the location, category and date signals.

    records: dicts with id, type ("lost" / "found"), status, name, category, color, description,
    brand, identifying_details, location, location_other, location_details and event_date (a date).
    Build one per request; the IDF is computed over all the records given.
    """

    def __init__(self, records):
        self.records = {record["id"]: record for record in records}
        self.index = TfidfIndex({record["id"]: report_terms(record) for record in records})
        self.places = {record["id"]: place_of(record) for record in records}

    def compare(self, a_id, b_id):
        """The match score of two reports, with each signal (0 to 1) behind it."""
        a, b = self.records[a_id], self.records[b_id]
        lost, found = (a, b) if a["type"] == "lost" else (b, a)
        signals = {
            "text": cosine(self.index.vectors[a_id], self.index.vectors[b_id]),
            "location": place_score(self.places[a_id], self.places[b_id]),
            "category": 1.0 if a["category"] == b["category"] else 0.0,
            "date": date_score(lost.get("event_date"), found.get("event_date")),
        }
        score = 100 * sum(SCORE_WEIGHTS[name] * value for name, value in signals.items())
        days_apart = ((found["event_date"] - lost["event_date"]).days
                      if lost.get("event_date") and found.get("event_date") else None)
        return {
            "id": b_id,
            "score": round(score),
            "signals": {name: round(value, 3) for name, value in signals.items()},
            "daysApart": days_apart,
        }

    def matches(self, record_id, limit=10):
        """The best matches for one report among the open reports of the other type, best first."""
        record = self.records[record_id]
        other_type = "found" if record["type"] == "lost" else "lost"
        results = []
        for other_id, other in self.records.items():
            if other["type"] != other_type or other.get("status") == "claimed":
                continue
            result = self.compare(record_id, other_id)
            if result["signals"]["text"] >= MIN_TEXT and result["score"] >= MIN_SCORE:
                results.append(result)
        results.sort(key=lambda r: (r["score"], r["signals"]["text"]), reverse=True)
        return results[:limit]

    def best(self, record_id):
        """The single best match for a report, or None."""
        found = self.matches(record_id, limit=1)
        return found[0] if found else None
