"""Normalize LaTeX so recognizer output and ground truth compare on what was written, not on spacing."""

from __future__ import annotations

import re


def norm_latex(s: str) -> str:
    s = s.strip().strip("$")
    s = re.sub(r"\\(left|right|big|Big|bigg|Bigg)\b\.?", "", s)
    s = re.sub(r"\\[,;!: ]|\\quad|\\qquad|\s+", "", s)
    s = s.replace("\\cdot", "·").replace("\\times", "·").replace("\\dfrac", "\\frac")
    s = s.replace("|_", "]_").replace("|^", "]^")  # evaluation bar ≡ bracket
    s = re.sub(r"^\[|=\[", lambda m: m.group(0)[:-1], s)  # opening "[" of [F]_a^b is optional in handwriting
    prev = None
    while prev != s:  # braces around a single token: x^{2} → x^2, {\frac{a}{b}} → \frac{a}{b}
        prev = s
        s = re.sub(r"\{([^{}\\])\}", r"\1", s)
        s = re.sub(r"(^|[=+\-(·])\{(\\frac\{[^{}]*\}\{[^{}]*\})\}", r"\1\2", s)
    return s.replace("d x", "dx")


def cer(hyp: str, ref: str) -> float:
    """Levenshtein distance / len(ref)."""
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(ref) + 1))
    for i, h in enumerate(hyp, 1):
        cur = [i]
        for j, r in enumerate(ref, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (h != r)))
        prev = cur
    return prev[-1] / len(ref)


def parse_graph_truth(truth: str) -> tuple[list[str], list[tuple[str, str]]]:
    """ "nodes: a, b; edges: a->b (label), b->c" → (nodes, edges)."""
    import re

    nodes_m = re.search(r"nodes:\s*(.*?)(?:;\s*edges:|$)", truth, re.S)
    edges_m = re.search(r"edges:\s*(.*)$", truth, re.S)
    nodes = [n.strip() for n in (nodes_m.group(1) if nodes_m else "").split(",") if n.strip()]
    edges = []
    for e in (edges_m.group(1) if edges_m else "").split(","):
        if "->" in e:
            a, b = e.split("->", 1)
            edges.append((a.strip(), re.sub(r"\s*\(.*\)\s*$", "", b).strip()))
    return nodes, edges


def norm_label(s: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def graph_match(nodes: dict[str, str], edges: list[tuple[str, str]], truth: str, max_cer: float = 0.25) -> float:
    """Share of truth nodes + edges found. Nodes match by label (CER ≤ max_cer); edges through the node mapping."""
    t_nodes, t_edges = parse_graph_truth(truth)
    mapping: dict[str, str] = {}
    free = dict(nodes)
    for tn in t_nodes:
        best = min(free.items(), key=lambda kv: cer(norm_label(kv[1]), norm_label(tn)), default=None)
        if best and cer(norm_label(best[1]), norm_label(tn)) <= max_cer:
            mapping[tn] = best[0]
            free.pop(best[0])
    edge_set = set(edges)
    found = len(mapping) + sum((mapping.get(a), mapping.get(b)) in edge_set for a, b in t_edges)
    total = len(t_nodes) + len(t_edges)
    return found / total if total else 1.0


# ---------------------------------------------------------------- typed / spoken corrections → checker input
# "Tell the tutor what you meant": the student types or says what a line reads. Maths has to reach the checkers
# in the LaTeX-ish form they parse (checkers/math.py), so plain ("x^2 + 3x", "5/3") and spoken ("x squared plus
# three x", "integral from 0 to 1 of x cubed dx", Spanish/Catalan words too) maths is converted by a few rules.
# Deliberately small and rule-based: what it can't convert stays as typed (the checker then says nothing).

_SUPERSCRIPTS = {"²": "^{2}", "³": "^{3}", "⁴": "^{4}", "¹": "^{1}", "⁰": "^{0}", "ⁿ": "^{n}", "**": "^"}
_SUBSCRIPTS = {"₀": "_{0}", "₁": "_{1}", "₂": "_{2}", "₃": "_{3}", "₄": "_{4}", "₅": "_{5}"}
_SYMBOLS = {"−": "-", "–": "-", "×": "*", "·": "*", "÷": "÷", "√": " sqrt ", "π": " pi ", "∫": " int ", "⁄": "/"}
_SYMBOLS |= {"≤": " <= ", "≥": " >= ", "≠": " != ", "½": "1/2", "⅓": "1/3", "¼": "1/4", "¾": "3/4", "⅔": "2/3"}

_UNITS = {
    # English
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
    # Spanish (accents are stripped first)
    "cero": 0, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
    "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16,
    "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
    # Catalan ("u" is left alone: it is a variable far more often than "one")
    "dues": 2, "quatre": 4, "cinc": 5, "sis": 6, "set": 7, "vuit": 8, "nou": 9, "deu": 10, "onze": 11,
    "dotze": 12, "tretze": 13, "catorze": 14, "quinze": 15, "setze": 16, "disset": 17, "divuit": 18, "dinou": 19,
}  # fmt: skip
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "veinte": 20, "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80,
    "noventa": 90, "vint": 20, "trenta": 30, "quaranta": 40, "cinquanta": 50, "seixanta": 60, "setanta": 70,
    "vuitanta": 80, "noranta": 90,
}  # fmt: skip
_HUNDRED = {"hundred", "cien", "ciento", "cent", "cents"}
_DECIMAL_POINT = {"point", "coma"}
_ORDINALS = {
    "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "segunda": 2, "tercera": 3, "cuarta": 4, "quinta": 5, "sexta": 6, "quarta": 4, "cinquena": 5, "sisena": 6,
}  # fmt: skip
# "<n> thirds" → n/3 (and "a half", "un medio", "un terç"…)
_DENOMINATORS = {
    "half": 2, "halves": 2, "third": 3, "thirds": 3, "quarter": 4, "quarters": 4, "fourth": 4, "fourths": 4,
    "fifth": 5, "fifths": 5, "sixth": 6, "sixths": 6, "seventh": 7, "sevenths": 7, "eighth": 8, "eighths": 8,
    "ninth": 9, "ninths": 9, "tenth": 10, "tenths": 10,
    "medio": 2, "medios": 2, "tercio": 3, "tercios": 3, "cuarto": 4, "cuartos": 4, "quinto": 5, "quintos": 5,
    "sexto": 6, "sextos": 6, "octavo": 8, "octavos": 8, "decimo": 10, "decimos": 10,
    "mig": 2, "mitja": 2, "mitjos": 2, "terc": 3, "tercos": 3, "quart": 4, "quarts": 4, "cinque": 5, "cinquens": 5,
    "sise": 6, "sisens": 6, "vuite": 8, "vuitens": 8, "dese": 10, "desens": 10,
}  # fmt: skip

# phrase → plain maths, applied in this order (longest phrases first within each group)
_PHRASES: list[tuple[str, str]] = [
    # powers
    r"\b(?:raised )?to the power (?:of )?(-? ?\w+)", r" ^{\1} ",
    r"\b(?:raised )?to the (\d+)(?: ?(?:st|nd|rd|th))?(?: power)?\b", r" ^{\1} ",
    r"\b(?:elevad[oa]|elevat) (?:a )?(?:la )?(-? ?\w+)", r" ^{\1} ",
    r"\b(?:squared|al cuadrado|al quadrat)\b", " ^{2} ",
    r"\b(?:cubed|al cubo|al cub)\b", " ^{3} ",
    # roots
    r"\b(?:the )?(?:square root|sqrt|root|raiz cuadrada|raiz|arrel quadrada|arrel)(?: of| de)?\b", " sqrt ",
    # integrals (limits first)
    r"\b(?:the )?(?:definite )?integral (?:from|between|de|entre|des de) (-? ?[\w.]+) (?:to|and|a|hasta|y|fins a|i) "
    r"(-? ?[\w.]+)(?: of| de)?\b",
    r" int_{\1}^{\2} ",
    r"\b(?:the )?integral(?: of| de)?\b", " int ",
    r"\b(?:with respect to|respecto a|respecto de|respecte a|respecte de) ([a-z])\b", r" d\1 ",
    # relations and operators
    r"\b(?:is equal to|is equal|equals|equal to|equal|es igual a|es igual|igual a|igual|is|son)\b", " = ",
    r"\b(?:divided by|dividido (?:por|entre)|dividit (?:per|entre)|over|entre|sobre)\b", " / ",  # before "per"
    r"\b(?:multiplied by|multiplicado por|multiplicat per|times|por|per)\b", " * ",
    r"\b(?:plus|mas|mes)\b", " + ",
    r"\b(?:minus|menos|menys|negative)\b", " - ",
    r"\b(?:open (?:bracket|paren|parenthesis)|left (?:bracket|paren|parenthesis)|abre parentesis|obre parentesi)\b",
    " ( ",
    r"\b(?:close (?:bracket|paren|parenthesis)|right (?:bracket|paren|parenthesis)|cierra parentesis"
    r"|tanca parentesi)\b",
    " ) ",
    # filler
    r"\b(?:the|of|de|del|el|la|then|um|uh)\b", " ",
]  # fmt: skip

_FUNCS = {"sin", "cos", "tan", "ln", "log", "exp", "pi", "theta", "alpha", "beta", "lambda", "mu", "infty"}
_FUNC_ALIASES = {"sen": "sin", "sine": "sin", "cosine": "cos", "tangent": "tan", "infinity": "infty", "inf": "infty"}


def _strip_accents(s: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _numbers(words: list[str]) -> list[str]:
    """Number words → digits: "twenty one" → 21, "treinta y dos" → 32, "two point five" → 2.5, "a half" → 1 half."""
    out: list[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if w in ("a", "an", "un", "una") and nxt in _DENOMINATORS:
            out.append("1")
            i += 1
            continue
        if w not in _UNITS and w not in _TENS and not (w in _HUNDRED and not out):
            out.append(w)
            i += 1
            continue
        n, j = 0, i
        if words[j] in _TENS:
            n = _TENS[words[j]]
            j += 1
            k = j + 1 if j < len(words) and words[j] in ("y", "i", "-", "and") else j
            if k < len(words) and words[k] in _UNITS and _UNITS[words[k]] < 10:
                n += _UNITS[words[k]]
                j = k + 1
        elif words[j] in _UNITS:
            n = _UNITS[words[j]]
            j += 1
        if j < len(words) and words[j] in _HUNDRED:
            n = max(n, 1) * 100
            j += 1
            rest = _numbers(words[j + (words[j] in ("and", "y", "i") if j < len(words) else 0) :])
            if rest and rest[0].isdigit() and int(rest[0]) < 100:
                n += int(rest[0])
                rest = rest[1:]
            out.append(str(n))
            out += rest
            return out
        text = str(n)
        if j + 1 < len(words) and words[j] in _DECIMAL_POINT and words[j + 1] in _UNITS:
            digits = []
            j += 1
            while j < len(words) and words[j] in _UNITS and _UNITS[words[j]] < 10:
                digits.append(str(_UNITS[words[j]]))
                j += 1
            text += "." + "".join(digits)
        out.append(text)
        i = j
    return out


def spoken_to_plain(text: str) -> str:
    """Spoken or typed words → plain maths: "x squared plus three x" → "x ^{2} + 3 x"."""
    s = text
    for k, v in (_SUPERSCRIPTS | _SUBSCRIPTS).items():  # before NFKD, which would turn x² into x2
        s = s.replace(k, v)
    for k, v in _SYMBOLS.items():
        s = s.replace(k, v)
    s = _strip_accents(s)
    s = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", s)  # 3x → 3 x (re-joined in plain_to_latex)
    words = re.findall(r"\\[A-Za-z]+|\d+(?:\.\d+)?|[A-Za-z]+|\S", s)
    words = [w if w.startswith("\\") or len(w) == 1 else w.lower() for w in words]
    for k, w in enumerate(words):
        if w.lower() in _UNITS or w.lower() in _TENS:
            words[k] = w.lower()
    s = " ".join(_numbers(words))
    s = re.sub(r"\bto the (" + "|".join(_ORDINALS) + r")(?: power)?\b", lambda m: f" ^{{{_ORDINALS[m[1]]}}} ", s)
    s = re.sub(r"\ba la (" + "|".join(_ORDINALS) + r")\b", lambda m: f" ^{{{_ORDINALS[m[1]]}}} ", s)
    s = re.sub(r"\b(\d+) (" + "|".join(_DENOMINATORS) + r")\b", lambda m: f" {m[1]}/{_DENOMINATORS[m[2]]} ", s)
    pairs = iter(_PHRASES)
    for pattern, repl in zip(pairs, pairs, strict=True):
        s = re.sub(pattern, repl, s)
    s = re.sub(r"\b(?:int(?:_\{[^}]*\}\^\{[^}]*\})?) .*", lambda m: re.sub(r"\bd ([a-z])\b", r"d\1", m[0]), s)
    s = re.sub(r"- (?=\d)", "-", s) if re.match(r"^\s*-", s) else s
    return re.sub(r"\s+", " ", s).strip()


def _tokens(s: str) -> list[str]:
    return re.findall(r"\\[A-Za-z]+|\d+(?:\.\d+)?|[A-Za-z]+|<=|>=|!=|\*\*|\S", s)


class _Latex:
    """Plain maths tokens → LaTeX the checkers parse: powers and fractions get braces, √ and ∫ become commands."""

    def __init__(self, toks: list[str]) -> None:
        self.t, self.i = toks, 0

    def peek(self) -> str:
        return self.t[self.i] if self.i < len(self.t) else ""

    def take(self) -> str:
        tok = self.peek()
        self.i += 1
        return tok

    def seq(self, until: str = "") -> list[str]:
        pieces: list[str] = []
        while self.i < len(self.t) and self.peek() != until:
            tok = self.peek()
            if tok == "/" and pieces and pieces[-1] not in _OPS:
                self.take()
                left = pieces.pop()
                pieces.append(f"\\frac{{{_ungroup(left)}}}{{{_ungroup(self.atom())}}}")
            elif tok in ("^", "**") and pieces:
                self.take()
                pieces[-1] += f"^{{{_ungroup(self.atom())}}}"
            elif tok == "_" and pieces:
                self.take()
                pieces[-1] += f"_{{{_ungroup(self.piece())}}}"  # int_0^1: the "^" is the upper limit
            elif tok in (")", "}", "]"):
                self.take()  # unbalanced closer: drop it
            else:
                pieces.append(self.piece())
        return pieces

    def atom(self) -> str:
        if self.peek() == "-":
            self.take()
            return "-" + self.atom()
        p = self.piece()
        while self.peek() in ("^", "**"):  # x^2/2: the power belongs to the numerator
            self.take()
            p += f"^{{{_ungroup(self.atom())}}}"
        return p

    def piece(self) -> str:
        tok = self.take()
        low = tok.lower()
        if tok in ("(", "{", "["):
            close = {"(": ")", "{": "}", "[": "]"}[tok]
            inner = "".join(self.seq(until=close))
            self.take()
            return f"({inner})" if tok != "[" else f"[{inner}]"
        if low in ("sqrt", "\\sqrt"):
            return f"\\sqrt{{{_ungroup(self.atom())}}}"
        if low in ("int", "integral", "\\int"):
            return "\\int"
        if low in ("*", "\\cdot", "\\times"):
            return "\\cdot"
        if tok == "÷":
            return "\\div"
        if tok in ("<=", ">=", "!="):
            return {"<=": "\\le", ">=": "\\ge", "!=": "\\neq"}[tok]
        low = _FUNC_ALIASES.get(low, low)
        if low in _APPLIED and self.peek() and self.peek() not in _OPS and self.peek() not in ("^", "_", ")", "/"):
            return f"\\{low}({_ungroup(self.atom())})"  # sin x → \sin(x)
        if low in _FUNCS:
            return "\\" + low
        return tok


_APPLIED = {"sin", "cos", "tan", "ln", "log", "exp"}
_OPS = {"+", "-", "=", "\\cdot", "\\div", "\\le", "\\ge", "\\neq", "<", ">", ","}


def _ungroup(s: str) -> str:
    """Drop one pair of outer parentheses (they become the braces of a fraction / power / root)."""
    if s.startswith("(") and s.endswith(")"):
        depth = 0
        for k, c in enumerate(s):
            depth += c == "("
            depth -= c == ")"
            if depth == 0 and k < len(s) - 1:
                return s  # (a)(b): not one group
        return s[1:-1]
    return s


def _join(pieces: list[str]) -> str:
    out = ""
    for p in pieces:
        if out and re.search(r"\\[A-Za-z]+$", out) and re.match(r"[A-Za-z0-9\\]", p):
            out += " "  # \sin x, not \sinx
        elif out and re.fullmatch(r"d[a-z]", p) and "\\int" in out:
            out += " "  # the differential stays a separate token: x^{3} dx
        out += p
    return out


def plain_to_latex(text: str) -> str:
    """Plain maths → checker LaTeX: "x^2 + 3x = 5/3" → "x^{2}+3x=\\frac{5}{3}". LaTeX input passes through."""
    s = text.strip().strip("$").strip()
    if not s or re.search(r"\\(frac|int|sqrt|left|cdot|times)\b", s):
        return s
    pieces = _Latex(_tokens(s)).seq()
    out = _join(pieces)
    if "\\int" in out and not re.search(r"\bd[a-z]\b", out):
        # an integral needs its differential; add d<var> before the first "=" after the integral
        k = out.index("\\int")
        eq = out.find("=", k)
        body = out[k + 4 : eq if eq > 0 else None]
        var = min(re.findall(r"(?<![\\A-Za-z])[a-z](?![A-Za-z])", body), default="x")
        out = out[:eq] + f" d{var}" + out[eq:] if eq > 0 else out + f" d{var}"
    return out


def math_from_text(text: str) -> str:
    """A typed or spoken maths correction → what the maths checkers parse."""
    if re.search(r"\\[A-Za-z]+", text):
        return text.strip().strip("$").strip()  # typed LaTeX: the checkers read it as it is
    return plain_to_latex(spoken_to_plain(text))


def digits_from_text(text: str) -> str:
    """Grid cells (sudoku): "five" / "cinco" / "cinc" → "5"; anything else as typed."""
    words = _numbers([w.lower() for w in re.findall(r"\w+|\S", _strip_accents(text))])
    return " ".join(words).strip()
