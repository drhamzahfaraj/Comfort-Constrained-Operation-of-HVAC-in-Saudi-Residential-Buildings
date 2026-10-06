"""Export the study's results as CSV files.

  results/csv/tables/table_NN_<label>.csv   every table of the paper, cell for cell as printed
                                            (read from paper/main.tex; numbers from paper/main.aux, written by `make paper`)
  results/csv/blocks/<block>.csv            every analysis block of results/parts/, flattened to
                                            (key, value) rows, one row per scalar

Run from the repository root after `make paper`:  python src/paper_tools/export_csv.py
"""
import csv, json, re, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "paper"
OUT = ROOT / "results" / "csv"


# ---------------------------------------------------------------- LaTeX cell -> plain text
def macros():
    """\\newcommand definitions of the generated macro file (numbers quoted in the text and tables)."""
    m = {}
    f = PAPER / "main.tex"
    for name, val in re.findall(r"\\newcommand\{\\(\w+)\}\{(.*)\}\s*$", f.read_text(), flags=re.M):
        m[name] = val
    return m


MAC = macros()
CITE = dict(re.findall(r"\\bibcite\{([^}]*)\}\{\{(\d+)\}", (PAPER / "main.aux").read_text())) if (PAPER / "main.aux").exists() else {}
SYMBOLS = {r"\%": "%", r"\&": "&", r"\$": "$", r"\,": " ", r"\;": " ", r"\ ": " ", "~": " ", r"\times": "x",
           r"\to": "->", r"\le": "<=", r"\ge": ">=", r"\pm": "+/-", r"\cdot": "*", r"\degree": "deg",
           r"^{\circ}": "deg", r"^\circ": "deg", r"\circ": "partly", r"\Delta": "Delta", r"\delta": "delta",
           r"\theta": "theta", r"\lambda": "lambda", r"\varphi": "phi", r"\omega": "omega", r"\ast": "*",
           r"\infty": "inf", r"\checkmark": "yes", r"\textendash": "-", "--": "-", r"\ldots": "...",
           r"\dots": "...", r"\quad": " ", r"\mathcal": "", r"\sim": "~"}


def plain(s):
    s = s.strip()
    for _ in range(3):   # macros may expand to other macros
        s = re.sub(r"\\(\w+)\b", lambda g: MAC.get(g.group(1), g.group(0)), s)
    s = re.sub(r"\\(?:citep|citet|citey|cite)\{([^}]*)\}", lambda g: "[" + ", ".join(sorted((CITE.get(k.strip(), k.strip()) for k in g.group(1).split(",")), key=lambda x: (not x.isdigit(), int(x) if x.isdigit() else 0, x))) + "]", s)
    s = re.sub(r"\\(?:ref|eqref)\{([^}]*)\}", r"[\1]", s)
    s = re.sub(r"\\textsuperscript\{([^}]*)\}", r"^\1", s)
    s = re.sub(r"\\(?:mathrm|text|textbf|textit|emph|mathbf|operatorname|makecell|underline)\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\(?:hat|bar|tilde)\{?(\w)\}?", r"\1", s)
    for k in sorted(SYMBOLS, key=len, reverse=True):
        s = s.replace(k, SYMBOLS[k])
    s = s.replace("{,}", ",").replace("$", "")
    s = re.sub(r"\\[a-zA-Z]+\*?", "", s)          # any remaining command
    s = s.replace("{", "").replace("}", "").replace("\\", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def split_cells(row):
    cells, depth, cur, i = [], 0, "", 0
    while i < len(row):
        ch = row[i]
        if ch == "\\" and i + 1 < len(row):
            cur += row[i:i + 2]; i += 2; continue
        if ch == "{": depth += 1
        if ch == "}": depth -= 1
        if ch == "&" and depth == 0:
            cells.append(cur); cur = ""
        else:
            cur += ch
        i += 1
    cells.append(cur)
    out = []
    for c in cells:   # \multicolumn{n}{spec}{text} -> text, then n-1 empty cells
        m = re.match(r"\s*\\multicolumn\{(\d+)\}\{[^}]*\}\{(.*)\}\s*$", c, flags=re.S)
        if m:
            out += [m.group(2)] + [None] * (int(m.group(1)) - 1)   # None marks a spanned cell
        else:
            out.append(c)
    return out


def table_rows(body):
    body = re.sub(r"(?<!\\)%.*", "", body)
    body = re.sub(r"\\(?:cmidrule|cline)(\([^)]*\))?\{[^}]*\}", "", body)
    body = re.sub(r"\\(?:addlinespace|hline|toprule|bottomrule)(\[[^\]]*\])?", "", body)
    head, rows, seen_mid = [], [], False
    for raw in re.split(r"\\\\(?:\[[^\]]*\])?", body):
        parts = raw.split(r"\midrule")
        for k, part in enumerate(parts):
            if k > 0: seen_mid = True
            if not part.strip(): continue
            cells = split_cells(part)
            (rows if seen_mid else head).append(cells)
    return head, rows


def combine_header(head, ncol):
    if not head: return [f"col{j + 1}" for j in range(ncol)]
    names = [""] * ncol
    for hr in head:
        group = ""
        for j in range(ncol):
            c = hr[j] if j < len(hr) else ""
            if c is None:
                txt = group
            else:
                txt = plain(c); group = txt if (j + 1 < len(hr) and hr[j + 1] is None) else ""
            if txt: names[j] = f"{names[j]}: {txt}" if names[j] else txt
    return [n or f"col{j + 1}" for j, n in enumerate(names)]


def skip_group(s, i):
    """Index just after the brace group that starts at s[i] == '{'."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{": depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0: return j + 1
    raise ValueError("unbalanced braces")


def tabular_body(env):
    m = re.search(r"\\begin\{(tabularx|tabular\*|tabular)\}", env)
    if not m: return None
    i = m.end(); groups = 2 if m.group(1) in ("tabularx", "tabular*") else 1   # width + column spec
    for _ in range(groups):
        while env[i].isspace(): i += 1
        i = skip_group(env, i)
    end = env.index("\\end{%s}" % m.group(1), i)
    return env[i:end]


def table_numbers():
    aux = PAPER / "main.aux"
    num = {}
    if aux.exists():
        for lab, n in re.findall(r"\\newlabel\{((?:tab:|S:)[^}]*)\}\{\{(\d+)\}", aux.read_text()):
            num[lab] = int(n)
    return num


def export_tables():
    src = (PAPER / "main.tex").read_text()
    num = table_numbers(); d = OUT / "tables"; d.mkdir(parents=True, exist_ok=True); n_out = 0
    for k, env in enumerate(re.findall(r"\\begin\{table\*?\}(.*?)\\end\{table\*?\}", src, flags=re.S)):
        lab = re.search(r"\\label\{((?:tab:|S:)[^}]*)\}", env); lab = lab.group(1) if lab else f"tab:unlabelled{k + 1}"
        cap = re.search(r"\\caption\{(.*?)\}\s*\\label", env, flags=re.S)
        tab = tabular_body(env)
        if tab is None: continue
        head, rows = table_rows(tab)
        ncol = max(len(r) for r in head + rows)
        header = combine_header(head, ncol)
        body, last = [], ""
        for r in rows:
            cells = [plain(c) if c is not None else "" for c in r] + [""] * (ncol - len(r))
            if not cells[0] and any(cells[1:]): cells[0] = last   # repeat a grouping label (e.g. the city)
            last = cells[0] or last
            if any(cells): body.append(cells)
        no = num.get(lab, k + 1)
        f = d / f"table_{no:02d}_{lab.split(':')[-1]}.csv"
        with open(f, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if cap: fh.write("# Table %d. %s\n" % (no, plain(cap.group(1))))
            w.writerow(header); w.writerows(body)
        n_out += 1
    return n_out


# ---------------------------------------------------------------- result blocks -> long CSV
def flatten(obj, key=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from flatten(v, f"{key}.{k}" if key else str(k))
    elif isinstance(obj, list):
        if obj and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in obj) and len(obj) > 24:
            yield key, json.dumps(obj)   # long numeric series stay in one cell
        else:
            for i, v in enumerate(obj):
                yield from flatten(v, f"{key}[{i}]")
    else:
        yield key, obj


def export_blocks():
    d = OUT / "blocks"; d.mkdir(parents=True, exist_ok=True); n = 0
    for p in sorted((ROOT / "results" / "parts").glob("*.json")):
        with open(d / (p.stem + ".csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh); w.writerow(["key", "value"])
            w.writerows(flatten(json.load(open(p))))
        n += 1
    return n


if __name__ == "__main__":
    if OUT.exists(): shutil.rmtree(OUT)
    t = export_tables(); b = export_blocks()
    print(f"results/csv: {t} paper tables, {b} result blocks")
