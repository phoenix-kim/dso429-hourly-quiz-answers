"""Deterministic solvers for the DSO 429 hourly quiz question templates.

Each solver matches one question template with a regex, computes the exact
answer, and returns it as a number (or date). `pick()` then chooses the option
closest to that value. Unrecognised questions return None so the caller can
fall back to an LLM.
"""

import re
from datetime import date, datetime, timedelta

NUM = r"([\d,]+(?:\.\d+)?)"


def num(s):
    return float(s.replace(",", ""))


def parse_option(opt):
    """Parse an option into a float or a date. Returns None if neither."""
    s = opt.strip()
    try:
        return datetime.strptime(s, "%d %B %Y").date()
    except ValueError:
        pass
    m = re.search(r"-?[\d,]*\.?\d+", s.replace("$", ""))
    if not m:
        return None
    return float(m.group(0).replace(",", ""))


def _compound(q):
    m = re.search(rf"investment of \${NUM} grows at {NUM}% compounded annually.*?after {NUM} years", q)
    if m:
        p, r, n = map(num, m.groups())
        return p * (1 + r / 100) ** n


def _gross_margin(q):
    m = re.search(rf"generates \${NUM} in revenue against \${NUM} in cost of goods sold.*gross margin", q)
    if m:
        rev, cogs = map(num, m.groups())
        return (rev - cogs) / rev * 100


def _series(q):
    m = re.search(r"series runs ([\d,\s.\-]+?)\.\s+If the pattern continues", q)
    if m:
        xs = [num(x) for x in re.findall(r"-?[\d.]+", m.group(1))]
        diffs = {round(b - a, 9) for a, b in zip(xs, xs[1:])}
        if len(diffs) == 1:
            return xs[-1] + diffs.pop()
        ratios = {round(b / a, 9) for a, b in zip(xs, xs[1:]) if a}
        if len(ratios) == 1:
            return xs[-1] * ratios.pop()


def _discount(q):
    m = re.search(rf"listed at \${NUM} is discounted by {NUM}%.*sale price", q)
    if m:
        p, d = map(num, m.groups())
        return p * (1 - d / 100)


def _revenue(q):
    m = re.search(rf"sells {NUM} units at \${NUM} each.*total revenue", q)
    if m:
        u, p = map(num, m.groups())
        return u * p


def _fx(q):
    m = re.search(rf"invoice for \${NUM} is converted to euros at {NUM} euros per US dollar", q)
    if m:
        amt, rate = map(num, m.groups())
        return amt * rate
    m = re.search(rf"invoice for \${NUM} is converted to euros at {NUM} US dollars? per euro", q)
    if m:
        amt, rate = map(num, m.groups())
        return amt / rate


def _weighted_avg(q):
    m = re.search(rf"had {NUM} orders averaging \${NUM}; region B had {NUM} orders averaging \${NUM}.*overall average", q)
    if m:
        n1, a1, n2, a2 = map(num, m.groups())
        return (n1 * a1 + n2 * a2) / (n1 + n2)


def _break_even(q):
    m = re.search(rf"Fixed costs are \${NUM}\. Each unit sells for \${NUM} with a variable cost of \${NUM}.*break even", q)
    if m:
        f, p, v = map(num, m.groups())
        return round(f / (p - v))


def _probability(q):
    m = re.search(r"holds (.+?)\. If one is drawn at random, what is the probability it is an? (\w+) ticket", q)
    if m:
        counts = {name: num(n) for n, name in re.findall(r"([\d,]+) (\w+) tickets?", m.group(1))}
        target = m.group(2)
        if target in counts:
            return counts[target] / sum(counts.values()) * 100


def _pct_change(q):
    m = re.search(rf"moved from \${NUM} to \${NUM}.*percentage change", q)
    if m:
        a, b = map(num, m.groups())
        return (b - a) / a * 100


def _ratio_split(q):
    m = re.search(rf"\${NUM} .*?split between two channels in the ratio {NUM}:{NUM}.*?(first|second) channel receive", q)
    if m:
        total, a, b = map(num, m.groups()[:3])
        share = a if m.group(4) == "first" else b
        return total * share / (a + b)


def _settlement(q):
    m = re.search(r"signed on (\d{1,2} \w+ \d{4}) has an? (\d+)-day settlement period", q)
    if m:
        d = datetime.strptime(m.group(1), "%d %B %Y").date()
        return d + timedelta(days=int(m.group(2)))


SOLVERS = [_compound, _gross_margin, _series, _discount, _revenue, _fx, _weighted_avg,
           _break_even, _probability, _pct_change, _ratio_split, _settlement]


def solve(question, options):
    """Return (letter, method) or (None, None) if no template matched cleanly."""
    for f in SOLVERS:
        try:
            target = f(question)
        except (ValueError, ZeroDivisionError):
            target = None
        if target is None:
            continue
        letter = pick(target, options)
        if letter:
            return letter, f.__name__[1:]
    return None, None


def pick(target, options):
    """Pick the option closest to target. Returns None if no option is a close match."""
    best, best_err = None, None
    for letter, opt in zip("ABCD", options):
        v = parse_option(opt)
        if v is None:
            continue
        if isinstance(target, date) != isinstance(v, date):
            continue
        err = abs((v - target).days) if isinstance(target, date) else abs(v - target)
        if best_err is None or err < best_err:
            best, best_err = letter, err
    if best is None:
        return None
    # Sanity check: the chosen option must be essentially exact (rounding only).
    if isinstance(target, date):
        return best if best_err == 0 else None
    # Tolerance is a little over half a unit in the option's last displayed digit.
    shown = options["ABCD".index(best)]
    decimals = len(shown.split(".")[1].rstrip("%").strip()) if "." in shown else 0
    tol = 0.6 * 10 ** -decimals
    return best if best_err <= tol else None
