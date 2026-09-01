import tomllib, re
P = r"F:\Projects\snesrecomp\GundamWingEndlessDuelSNESRecomp\translations\endless_duel_dialogue.toml"
d = tomllib.load(open(P, "rb"))

LINES = d["line"]

def strict_sig(line, lang="en"):
    ids = line[lang + "_ids"]
    n = min(8, len(ids))
    if n < 5:
        return None
    return b"".join(bytes(((0x0400 | t) & 0xFF, ((0x0400 | t) >> 8) & 0xFF)) for t in ids[:n])

SIGS = []
for l in LINES:
    s = strict_sig(l)
    if s:
        SIGS.append((l["address"], l["group"], l["en"], s))

# Relaxed: match only the low byte (tile id 0x00-0x7f) at stride 2, ignoring
# the attribute byte. 6 glyphs is already very unlikely by chance.
def relaxed_pat(line, lang="en", n=6):
    ids = line[lang + "_ids"]
    if len(ids) < n:
        return None
    return b"".join(re.escape(bytes([t & 0xFF])) + b"." for t in ids[:n])

REL = []
for i, l in enumerate(LINES):
    p = relaxed_pat(l)
    if p:
        REL.append((i, p))

RELRX = re.compile(b"|".join(b"(?P<L%d>%s)" % (i, p) for i, p in REL), re.S)

def relaxed_scan(v):
    out = []
    for m in RELRX.finditer(v):
        i = int(m.lastgroup[1:])
        out.append((i, m.start()))
    return out

if __name__ == "__main__":
    print(len(SIGS), len(REL))
